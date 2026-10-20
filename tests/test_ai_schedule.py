"""AI 任务运行时段：配置校验、跨午夜判断、队列领取门控与 API。"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select


def _schedule(**overrides):
    from app.core.ai_schedule import default_ai_schedule

    data = default_ai_schedule()
    data.update(overrides)
    return data


def test_default_is_unrestricted(session):
    from app.core.ai_schedule import get_ai_schedule, is_ai_allowed

    schedule = get_ai_schedule(session)
    assert schedule["enabled"] is False
    assert is_ai_allowed(schedule, now=datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc))


def test_normalize_rejects_bad_values():
    from app.core.ai_schedule import AiScheduleError, normalize_ai_schedule

    with pytest.raises(AiScheduleError):
        normalize_ai_schedule({"enabled": True, "timezone": "Asia/Shanghai", "windows": []})
    with pytest.raises(AiScheduleError):
        normalize_ai_schedule(
            {"enabled": True, "windows": [{"days": [0], "start": "09:00", "end": "09:00"}]}
        )
    with pytest.raises(AiScheduleError):
        normalize_ai_schedule(
            {"enabled": True, "windows": [{"days": [7], "start": "09:00", "end": "18:00"}]}
        )
    with pytest.raises(AiScheduleError):
        normalize_ai_schedule({"enabled": True, "timezone": "Mars/Base", "windows": [
            {"days": [0], "start": "09:00", "end": "18:00"}
        ]})
    with pytest.raises(AiScheduleError):
        normalize_ai_schedule(
            {"enabled": True, "windows": [{"days": [0], "start": "24:00", "end": "18:00"}]}
        )


def test_window_is_left_closed_right_open():
    from app.core.ai_schedule import is_ai_allowed, normalize_ai_schedule

    s = normalize_ai_schedule(
        {"enabled": True, "timezone": "Asia/Shanghai", "windows": [
            {"days": [0], "start": "09:00", "end": "18:00"}
        ]}
    )
    # 2026-10-19 是周一；UTC 01:00 = 上海 09:00
    assert is_ai_allowed(s, now=datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)) is True
    assert is_ai_allowed(s, now=datetime(2026, 10, 19, 9, 59, tzinfo=timezone.utc)) is True
    assert is_ai_allowed(s, now=datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)) is False


def test_cross_midnight_uses_start_day():
    from app.core.ai_schedule import is_ai_allowed, normalize_ai_schedule

    # 周一 23:00 - 周二 07:00
    s = normalize_ai_schedule(
        {"enabled": True, "timezone": "Asia/Shanghai", "windows": [
            {"days": [0], "start": "23:00", "end": "07:00"}
        ]}
    )
    # 周一 23:30 上海 = 周一 15:30 UTC
    assert is_ai_allowed(s, now=datetime(2026, 10, 19, 15, 30, tzinfo=timezone.utc)) is True
    # 周二 06:30 上海 = 周一 22:30 UTC（仍属于周一启动的窗口）
    assert is_ai_allowed(s, now=datetime(2026, 10, 19, 22, 30, tzinfo=timezone.utc)) is True
    # 周二 08:00 上海 = 周二 00:00 UTC，窗口已关
    assert is_ai_allowed(s, now=datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)) is False


def test_next_open_at_returns_future_window():
    from app.core.ai_schedule import next_ai_open_at, normalize_ai_schedule

    s = normalize_ai_schedule(
        {"enabled": True, "timezone": "Asia/Shanghai", "windows": [
            {"days": [1], "start": "09:00", "end": "18:00"}
        ]}
    )
    # 周一 10:00 上海，下一次是周二 09:00
    nxt = next_ai_open_at(s, now=datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc))
    assert nxt is not None
    assert nxt.isoformat().startswith("2026-10-20T09:00:00+08:00")


def test_describe_windows_compacts_weekdays():
    from app.core.ai_schedule import describe_windows, normalize_ai_schedule

    s = normalize_ai_schedule(
        {"enabled": True, "windows": [
            {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"},
            {"days": [6], "start": "20:00", "end": "23:00"},
        ]}
    )
    assert describe_windows(s) == ["周一至周五 09:00-18:00", "周日 20:00-23:00"]


def _job(session, job_type, payload):
    from app.models import JobQueue

    j = JobQueue(job_type=job_type, payload=payload, status="queued")
    session.add(j)
    session.commit()
    return j


def test_claim_skips_ai_jobs_when_closed(session, monkeypatch):
    from app.core import ai_schedule, queue

    _job(session, "transcribe", {"asset_id": 1})
    _job(session, "analyze", {"asset_id": 2})

    def closed(_db):
        return ai_schedule.normalize_ai_schedule(
            {"enabled": True, "windows": [{"days": [0], "start": "09:00", "end": "10:00"}]}
        )

    monkeypatch.setattr(ai_schedule, "get_ai_schedule", closed)
    job = queue.claim(session, ["transcribe", "analyze"])
    assert job is not None and job.job_type == "transcribe"
    assert queue.claim(session, ["analyze"]) is None


def test_claim_picks_ai_job_when_open(session, monkeypatch):
    from app.core import ai_schedule, queue

    _job(session, "analyze", {"asset_id": 3})
    monkeypatch.setattr(
        ai_schedule,
        "get_ai_schedule",
        lambda _db: {"enabled": False, "timezone": "Asia/Shanghai", "windows": []},
    )
    job = queue.claim(session, ["analyze"])
    assert job is not None and job.job_type == "analyze"


def test_claim_without_job_types_excludes_ai_jobs_when_closed(session, monkeypatch):
    from app.core import ai_schedule, queue

    _job(session, "breakdown", {"asset_id": 4})
    _job(session, "download", {"asset_id": 5})
    monkeypatch.setattr(
        ai_schedule,
        "get_ai_schedule",
        lambda _db: ai_schedule.normalize_ai_schedule(
            {"enabled": True, "windows": [{"days": [0], "start": "09:00", "end": "10:00"}]}
        ),
    )
    job = queue.claim(session)
    assert job is not None and job.job_type == "download"


def test_ai_schedule_api_roundtrip(client):
    body = {
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "windows": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"}],
    }
    r = client.put("/api/system/ai-schedule", json=body)
    assert r.status_code == 200, r.text
    saved = r.json()["schedule"]
    assert saved["enabled"] is True
    assert saved["windows"][0]["start"] == "09:00"

    got = client.get("/api/system/ai-schedule").json()
    assert got["windows"] == saved["windows"]
    assert "is_open" in got and "controlled_jobs" in got
    assert got["summary"] == ["周一至周五 09:00-18:00"]
    if got["next_open_at"]:
        assert got["next_open_local"] is not None


def test_ai_schedule_api_rejects_invalid(client):
    r = client.put(
        "/api/system/ai-schedule",
        json={"enabled": True, "windows": [{"days": [0], "start": "09:00", "end": "09:00"}]},
    )
    assert r.status_code == 422


def test_ai_schedule_api_requires_auth(anon_client):
    assert anon_client.get("/api/system/ai-schedule").status_code == 401


def test_closed_schedule_does_not_block_downloads(client, session, monkeypatch):
    """时段关闭 AI 时，下载任务照常执行；AI 任务保持 queued。"""
    from app.core import ai_schedule
    from app.models import JobQueue, MediaAsset, ContentItem

    client.put(
        "/api/system/ai-schedule",
        json={
            "enabled": True,
            "timezone": "Asia/Shanghai",
            "windows": [{"days": [0], "start": "09:00", "end": "10:00"}],
        },
    )
    monkeypatch.setattr(
        ai_schedule,
        "is_ai_allowed",
        lambda schedule, now=None: False,
    )
    c = ContentItem(account_id=1, platform="douyin", external_id="e", title="t")
    session.add(c)
    session.flush()
    a = MediaAsset(content_id=c.id, download_status="done", transcribe_status="done")
    session.add(a)
    session.flush()
    from app.models import Transcript

    session.add(Transcript(asset_id=a.id, full_text="已有转录"))
    session.commit()
    client.post(f"/api/assets/{a.id}/analyze")
    from app.core.queue import claim

    job = claim(session, ["download", "analyze"])
    # 只有 analyze 排队；窗口关闭时应领不到
    assert job is None
    assert session.scalars(select(JobQueue).where(JobQueue.job_type == "analyze")).one().status == "queued"
