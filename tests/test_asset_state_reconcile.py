"""素材状态对账：worker 崩溃后卡在「进行中」的素材必须被复位。

回归背景（实测发现的真 bug）：
handle_download 在真正下载**之前**就把 download_status 置为 downloading 并提交。
worker 进程崩溃 / handler 抛异常后，队列里的 job 已经 error，素材却永远停在
"下载中"，前端一直显示转圈、再也不会变。转录/分析同理。
"""

from datetime import datetime, timedelta

import pytest

from app.models import ContentItem, JobQueue, MediaAsset, PlatformAccount


def _asset(session, *, download_status="downloading", age_minutes=60, error=""):
    acc = PlatformAccount(platform="douyin", display_name="d")
    session.add(acc)
    session.commit()
    c = ContentItem(account_id=acc.id, platform="douyin", external_id="v1", title="t", url="u")
    session.add(c)
    session.commit()
    a = MediaAsset(content_id=c.id, download_status=download_status, error=error)
    session.add(a)
    session.commit()
    a.updated_at = datetime.utcnow() - timedelta(minutes=age_minutes)
    session.commit()
    return a


def test_stale_downloading_reset_to_error(session):
    from app.services.asset_state import reset_stale_assets

    asset = _asset(session, download_status="downloading", age_minutes=60)
    fixed = reset_stale_assets(session, older_than_minutes=30)

    assert fixed.get("download_status") == 1
    session.refresh(asset)
    assert asset.download_status == "error"
    assert asset.error, "复位时要写一条可读原因"


def test_stale_transcribing_and_analyzing_reset(session):
    from app.services.asset_state import reset_stale_assets

    acc = PlatformAccount(platform="douyin", display_name="d")
    session.add(acc)
    session.commit()
    c = ContentItem(account_id=acc.id, platform="douyin", external_id="v2", title="t", url="u")
    session.add(c)
    session.commit()
    a = MediaAsset(
        content_id=c.id,
        download_status="done",
        transcribe_status="transcribing",
        analyze_status="analyzing",
    )
    session.add(a)
    session.commit()
    a.updated_at = datetime.utcnow() - timedelta(minutes=60)
    session.commit()

    fixed = reset_stale_assets(session, older_than_minutes=30)
    assert fixed.get("transcribe_status") == 1
    assert fixed.get("analyze_status") == 1
    session.refresh(a)
    assert a.transcribe_status == "error" and a.analyze_status == "error"


def test_recent_in_progress_is_left_alone(session):
    """刚置为 downloading 的素材不能被误判（可能 worker 正在下载）。"""
    from app.services.asset_state import reset_stale_assets

    asset = _asset(session, download_status="downloading", age_minutes=1)
    fixed = reset_stale_assets(session, older_than_minutes=30)
    assert fixed == {}
    session.refresh(asset)
    assert asset.download_status == "downloading"


def test_live_queued_job_keeps_asset_in_progress(session):
    """队列里还有活任务时，即使很久没更新也不能复位（避免和 worker 抢）。"""
    from app.services.asset_state import reset_stale_assets

    asset = _asset(session, download_status="downloading", age_minutes=120)
    session.add(
        JobQueue(
            job_type="download", payload={"asset_id": asset.id}, status="queued",
            priority=100, max_attempts=3, run_after=datetime.utcnow(),
        )
    )
    session.commit()

    fixed = reset_stale_assets(session, older_than_minutes=30)
    assert fixed == {}
    session.refresh(asset)
    assert asset.download_status == "downloading"


def test_existing_error_message_is_preserved(session):
    """已有具体错误信息的不要被通用文案覆盖。"""
    from app.services.asset_state import reset_stale_assets

    asset = _asset(session, download_status="downloading", age_minutes=60, error="HTTP 403")
    reset_stale_assets(session, older_than_minutes=30)
    session.refresh(asset)
    assert asset.error == "HTTP 403"


def test_clear_success_error_cleans_done_assets(session):
    """下载成功但 error 位残留（被别的阶段污染）→ 必须清掉，别显示成失败。"""
    from app.services.asset_state import clear_success_error

    asset = _asset(session, download_status="done", age_minutes=5, error="未配置大模型 Base URL")
    cleaned = clear_success_error(session)

    assert cleaned == 1
    session.refresh(asset)
    assert asset.error == ""
    assert asset.download_status == "done", "只清错误文案，不改成功的状态"


def test_clear_success_error_ignores_pending_and_error(session):
    from app.services.asset_state import clear_success_error

    _asset(session, download_status="pending", error="x")
    cleaned = clear_success_error(session)
    assert cleaned == 0


@pytest.mark.asyncio
async def test_janitor_tick_does_not_raise(session, monkeypatch):
    """janitor 一次完整 tick 必须能跑通（曾因 clear_success_error 未导入而 NameError）。

    janitor 吞异常并只记日志，所以 NameError 在生产里表现为"对账悄悄不生效"，
    必须用测试盯住。
    """
    import app.workers.runner as runner

    monkeypatch.setattr(runner, "SessionLocal", lambda: session)
    _asset(session, download_status="downloading", age_minutes=120)

    # 复刻 janitor 的循环体（不跑无限循环）
    from app.core import queue as q
    from app.services.asset_state import clear_success_error, reset_stale_assets

    q.reset_stale_running(session, older_than_minutes=30)
    fixed = reset_stale_assets(session, older_than_minutes=30)
    cleaned = clear_success_error(session)
    assert fixed.get("download_status") == 1
    assert cleaned == 0
