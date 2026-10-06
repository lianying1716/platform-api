"""爆款拆解 + 对标账号 API（Task 4）。

- POST/GET /api/assets/{id}/breakdown：入队（可 force）/ 读取（pending/结果），鉴权 404/422/200。
- GET /api/analytics/creator/{author_id}/report：账号级聚合（概览/节奏/Top/爆款规律/支柱/基准/缺口）。
- 中位数口径：未拍到快照的作品不以 0 计入分母（T3 review Minor① 定稿）。
"""

import asyncio
from datetime import datetime

import pytest


def _seed_asset(db, *, author_id="A", platform="douyin", external_id="aw1", title="标题"):
    from app.models import ContentItem, MediaAsset

    c = ContentItem(
        platform=platform,
        external_id=external_id,
        content_type="video",
        title=title,
        author_name="小红",
        author_id=author_id,
        url="https://x/v",
        duration_sec=30,
        published_at=datetime(2026, 10, 1, 9, 0, 0),
    )
    db.add(c)
    db.flush()
    a = MediaAsset(content_id=c.id, relative_path=f"douyin/{c.id}", video_file="v.mp4")
    db.add(a)
    db.commit()
    return c, a


def _seed_metric(db, content_id, *, play, like=0, comment=0, favorite=0, share=0):
    from app.models import ContentMetricSnapshot

    db.add(
        ContentMetricSnapshot(
            content_id=content_id,
            play=play,
            like=like,
            comment=comment,
            favorite=favorite,
            share=share,
            captured_at=datetime.utcnow(),
        )
    )
    db.commit()


def _session(_engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=_engine)()


# ============================================================
# 鉴权
# ============================================================


def test_breakdown_endpoints_require_auth(anon_client, _engine):
    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.close()

    assert anon_client.post(f"/api/assets/{asset_id}/breakdown", json={}).status_code == 401
    assert anon_client.get(f"/api/assets/{asset_id}/breakdown").status_code == 401
    assert anon_client.get("/api/analytics/creator/A/report?platform=douyin").status_code == 401


# ============================================================
# POST 入队
# ============================================================


def test_post_breakdown_enqueues(client, _engine):
    from app.models import JobQueue, ViralBreakdown

    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.close()

    r = client.post(f"/api/assets/{asset_id}/breakdown", json={})
    assert r.status_code == 200
    assert r.json()["job_id"]

    db = _session(_engine)
    jobs = db.query(JobQueue).filter(JobQueue.job_type == "breakdown").all()
    assert len(jobs) == 1
    assert jobs[0].payload == {"asset_id": asset_id, "force": False}
    row = db.query(ViralBreakdown).filter(ViralBreakdown.asset_id == asset_id).one()
    assert row.status == "pending"  # 入队即置 pending，GET 立刻可读
    db.close()


def test_post_breakdown_force_payload(client, _engine):
    from app.models import JobQueue

    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.close()

    assert client.post(f"/api/assets/{asset_id}/breakdown", json={"force": True}).status_code == 200
    db = _session(_engine)
    jobs = db.query(JobQueue).filter(JobQueue.job_type == "breakdown").all()
    assert jobs[0].payload["force"] is True
    db.close()


def test_post_breakdown_force_string_false_normalized(client, _engine):
    """字符串 "false" 必须归一为 False（与 process/reclassify 同款 Pydantic 归一）。"""
    from app.models import JobQueue

    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.close()

    client.post(f"/api/assets/{asset_id}/breakdown", json={"force": "false"})
    db = _session(_engine)
    jobs = db.query(JobQueue).filter(JobQueue.job_type == "breakdown").all()
    assert jobs[0].payload["force"] is False
    db.close()


def test_post_breakdown_rejects_bad_force(client, _engine):
    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.close()
    r = client.post(f"/api/assets/{asset_id}/breakdown", json={"force": "maybe"})
    assert r.status_code == 422


def test_post_breakdown_unknown_asset(client):
    assert client.post("/api/assets/999999/breakdown", json={}).status_code == 404


def test_post_breakdown_keeps_done_without_force(client, _engine):
    """已 done 且 force=False：不重置状态（worker 短路返回既有结果）。"""
    from app.models import ViralBreakdown

    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.add(ViralBreakdown(asset_id=asset_id, status="done", verdict="已完成"))
    db.commit()
    db.close()

    assert client.post(f"/api/assets/{asset_id}/breakdown", json={}).status_code == 200
    db = _session(_engine)
    row = db.query(ViralBreakdown).filter(ViralBreakdown.asset_id == asset_id).one()
    assert row.status == "done"
    db.close()


def test_post_breakdown_force_resets_done(client, _engine):
    from app.models import ViralBreakdown

    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.add(ViralBreakdown(asset_id=asset_id, status="done", verdict="旧"))
    db.commit()
    db.close()

    assert client.post(f"/api/assets/{asset_id}/breakdown", json={"force": True}).status_code == 200
    db = _session(_engine)
    row = db.query(ViralBreakdown).filter(ViralBreakdown.asset_id == asset_id).one()
    assert row.status == "pending"
    db.close()


# ============================================================
# GET 读取
# ============================================================


def test_get_breakdown_pending(client, _engine):
    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.close()
    body = client.get(f"/api/assets/{asset_id}/breakdown").json()
    assert body["status"] == "pending"


def test_get_breakdown_error_exposes_message(client, _engine):
    from app.models import ViralBreakdown

    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.add(ViralBreakdown(asset_id=asset_id, status="error", error="LLM 超时"))
    db.commit()
    db.close()
    body = client.get(f"/api/assets/{asset_id}/breakdown").json()
    assert body["status"] == "error"
    assert body["error"] == "LLM 超时"
    assert "score" not in body  # 失败不返回半成品报告


def test_get_breakdown_returns_report(client, _engine):
    from app.models import ViralBreakdown

    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    db.add(
        ViralBreakdown(
            asset_id=asset_id,
            status="done",
            score=88,
            verdict="钩子反常识",
            sections={"hook": {"hook_type": "反常识"}, "blueprint": ["第一步"]},
            comment_insights={"top_themes": ["求链接"]},
            data_context={"play": 1000, "play_vs_median": 3.2},
            understanding_available=True,
            model="gw-model",
            prompt_version="v1",
            usage={"total_tokens": 123},
        )
    )
    db.commit()
    db.close()

    body = client.get(f"/api/assets/{asset_id}/breakdown").json()
    assert body["status"] == "done"
    assert body["score"] == 88
    assert body["verdict"] == "钩子反常识"
    assert body["sections"]["hook"]["hook_type"] == "反常识"
    assert body["comment_insights"] == {"top_themes": ["求链接"]}
    assert body["data_context"]["play"] == 1000
    assert body["understanding_available"] is True
    assert body["model"] == "gw-model" and body["prompt_version"] == "v1"
    assert body["usage"] == {"total_tokens": 123}
    assert body["created_at"]


def test_get_breakdown_unknown_asset(client):
    assert client.get("/api/assets/999999/breakdown").status_code == 404


# ============================================================
# 对标账号报告
# ============================================================


def _seed_creator(db, *, n_snapshot=3, plays=(1000, 200, 30), no_snapshot_extra=0):
    contents = []
    for i, p in enumerate(plays):
        c, a = _seed_asset(db, external_id=f"c{i}", title=f"作品{i}")
        _seed_metric(db, c.id, play=p, like=p // 10, comment=p // 50)
        contents.append((c, a))
    for j in range(no_snapshot_extra):
        c, a = _seed_asset(db, external_id=f"n{j}", title=f"无快照{j}")
        contents.append((c, a))
    return contents


def test_creator_report_shape_and_math(client, _engine):
    db = _session(_engine)
    _seed_creator(db, plays=(1000, 200, 30))
    db.close()

    body = client.get("/api/analytics/creator/A/report?platform=douyin").json()
    assert body["author_id"] == "A" and body["platform"] == "douyin"
    assert body["author_name"] == "小红"

    ov = body["overview"]
    assert ov["content_count"] == 3
    assert ov["snapshot_count"] == 3
    assert ov["median_play"] == 200.0  # 三个数中位
    assert ov["avg_play"] == pytest.approx((1000 + 200 + 30) / 3)
    assert ov["avg_engagement_rate"] > 0

    # 发布节奏：全部集中在 09 点、周四
    cadence = body["cadence"]
    assert len(cadence["by_weekday"]) == 7 and len(cadence["by_hour"]) == 24
    assert cadence["by_hour"][9]["count"] == 3
    assert cadence["best_hours"][0] == 9

    # Top 按播放降序
    assert [t["play"] for t in body["top"]] == [1000, 200, 30]

    # 默认阈值 3：1000 >= 200*3 → 唯一爆款
    vr = body["viral_rules"]
    assert vr["threshold_multiple"] == 3.0
    assert vr["count"] == 1
    assert vr["items"][0]["play"] == 1000
    assert vr["top_hours"] == [9]

    # 内容支柱（无 L3/无 auto_category → 未分类）
    assert body["content_pillars"][0]["category"] == "未分类"
    assert body["content_pillars"][0]["share"] == pytest.approx(1.0)

    # 对标基准：作者 vs 平台（平台即该作者，中位数一致）
    bench = body["benchmark"]
    assert bench["platform_median_play"] == 200.0
    assert bench["author_vs_platform"] == pytest.approx(1.0)
    assert bench["platform_content_count"] == 3

    assert body["gaps"] == []


def test_creator_report_median_excludes_no_snapshot(client, _engine):
    """中位数只统计有快照内容：无快照作品不得以 0 拉低中位数、虚高爆款倍数。"""
    from app.models import SystemSetting

    db = _session(_engine)
    # 阈值设为 2，使口径差异可观测：有快照 median=1000 vs 混入 0 后 median=500。
    db.add(SystemSetting(key="breakdown_viral_multiple", value="2"))
    _seed_creator(db, plays=(1000,), no_snapshot_extra=2)
    db.commit()
    db.close()

    body = client.get("/api/analytics/creator/A/report?platform=douyin").json()
    assert body["overview"]["content_count"] == 3
    assert body["overview"]["snapshot_count"] == 1
    assert body["overview"]["median_play"] == 1000.0  # 而非 (1000+0+0)/3=333
    # 1000 >= 1000*2 为假 → 不误判为爆款（旧口径 median=333 会误判）
    assert body["viral_rules"]["count"] == 0


def test_creator_report_platform_median_and_gap(client, _engine):
    """平台基准取同平台全体；平台高热度类型里作者没做的 → 缺口。"""
    db = _session(_engine)
    c1, _ = _seed_asset(db, author_id="A", external_id="a1")
    c1.auto_category = "commerce"
    _seed_metric(db, c1.id, play=300, like=30)
    # 另一作者的高热度「美妆」内容
    c2, _ = _seed_asset(db, author_id="B", external_id="b1")
    c2.auto_category = "beauty"
    _seed_metric(db, c2.id, play=100000, like=9000)
    db.commit()
    db.close()

    body = client.get("/api/analytics/creator/A/report?platform=douyin").json()
    # 平台中位数 = median([300, 100000]) = (300 + 100000) / 2 = 50150
    assert body["benchmark"]["platform_median_play"] == pytest.approx(50150.0)
    assert body["benchmark"]["platform_content_count"] == 2
    # 缺口：beauty 中位 100000 >= 平台中位；作者只有 commerce → beauty 为缺口
    assert [g["category"] for g in body["gaps"]] == ["beauty"]
    # 作者内容支柱为 commerce
    assert body["content_pillars"][0]["category"] == "commerce"


def test_creator_report_unknown_author(client):
    assert client.get("/api/analytics/creator/NOPE/report?platform=douyin").status_code == 404


def test_creator_report_defaults_platform_to_most_content(client, _engine):
    """platform 省略时取内容最多的平台。"""
    db = _session(_engine)
    _seed_asset(db, author_id="A", platform="douyin", external_id="d1")
    _seed_asset(db, author_id="A", platform="bilibili", external_id="b1")
    _seed_asset(db, author_id="A", platform="bilibili", external_id="b2")
    db.commit()
    db.close()

    body = client.get("/api/analytics/creator/A/report").json()
    assert body["platform"] == "bilibili"


# ============================================================
# 中位数口径（T3 review Minor①）：_data_context 计入
# ============================================================


def test_data_context_excludes_no_snapshot_from_median(session):
    import app.services.breakdown_service as svc

    from app.models import ContentItem, MediaAsset

    c1 = ContentItem(platform="douyin", external_id="m1", content_type="video", author_id="M")
    session.add(c1)
    session.flush()
    a1 = MediaAsset(content_id=c1.id)
    session.add(a1)
    _seed_metric(session, c1.id, play=1000)
    c2 = ContentItem(platform="douyin", external_id="m2", content_type="video", author_id="M")
    session.add(c2)
    session.flush()  # 无快照
    session.commit()

    ctx = svc._data_context(session, c1, a1)
    assert ctx["median_play"] == 1000.0  # 而非 (1000+0)/2=500
    assert ctx["play_vs_median"] == pytest.approx(1.0)


def test_breakdown_endpoint_end_to_end_done(client, _engine, monkeypatch):
    """POST 入队后跑 worker handler（mock LLM）→ GET 拿到完整报告。"""
    import app.services.breakdown_service as svc
    from app.workers import handlers

    db = _session(_engine)
    _, asset = _seed_asset(db)
    asset_id = asset.id
    from app.models import SystemSetting

    db.add(SystemSetting(key="llm_base_url", value="http://x/v1"))
    db.add(SystemSetting(key="llm_api_key", value="k"))
    db.add(SystemSetting(key="llm_model", value="m"))
    _seed_metric(db, asset.content_id, play=500, like=50)
    db.commit()
    db.close()

    async def fake_run_llm(system, user, *, base_url, api_key, model, timeout=90):
        return {
            "data": {"verdict": "钩子好", "score": 77, "hook": {"hook_type": "反常识"}},
            "usage": {"total_tokens": 9},
            "model": "gw",
        }

    monkeypatch.setattr(svc.breakdown, "run_llm", fake_run_llm)

    assert client.post(f"/api/assets/{asset_id}/breakdown", json={}).status_code == 200

    db = _session(_engine)
    asyncio.run(handlers.handle_breakdown(db, {"asset_id": asset_id, "force": False}))
    db.close()

    body = client.get(f"/api/assets/{asset_id}/breakdown").json()
    assert body["status"] == "done"
    assert body["verdict"] == "钩子好"
    assert body["score"] == 77
    assert body["sections"]["hook"] == {"hook_type": "反常识"}
    assert body["data_context"]["play"] == 500
    assert body["model"] == "gw"


# ---------- 拆解配置读写 API（T5 补齐：viral_multiple 写侧校验 + 读侧 NaN 兜底） ----------
def test_breakdown_settings_get_defaults(client):
    from app.core.breakdown_settings import DEFAULT_BREAKDOWN_PROMPT

    body = client.get("/api/system/breakdown-settings").json()
    assert body["prompt"] == DEFAULT_BREAKDOWN_PROMPT
    assert body["viral_multiple"] == 3.0


def test_breakdown_settings_put_roundtrip(client):
    r = client.put("/api/system/breakdown-settings", json={"prompt": "自定义提示词", "viral_multiple": 2.5})
    assert r.status_code == 200
    body = client.get("/api/system/breakdown-settings").json()
    assert body["prompt"] == "自定义提示词"
    assert body["viral_multiple"] == 2.5


@pytest.mark.parametrize("bad", [0, -1, True, "x", None])
def test_breakdown_settings_put_rejects_bad_multiple(client, bad):
    r = client.put("/api/system/breakdown-settings", json={"viral_multiple": bad})
    assert r.status_code == 422


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_breakdown_settings_put_rejects_nonfinite_literal(client, literal):
    """非有限数只能以原始 JSON 字面量送达（json=dumps 会拒绝），仍须 422。"""
    r = client.put(
        "/api/system/breakdown-settings",
        content='{"viral_multiple": ' + literal + "}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422


@pytest.mark.parametrize("bad", ["", "   ", 123, None])
def test_breakdown_settings_put_rejects_bad_prompt(client, bad):
    r = client.put("/api/system/breakdown-settings", json={"prompt": bad})
    assert r.status_code == 422


def test_breakdown_settings_read_guards_nan_kv(client, _engine):
    """手改 KV 成 nan/inf/<=0 时，读侧应回退默认，绝不让对标报告 500。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import SystemSetting

    Session = sessionmaker(bind=_engine)
    db = Session()
    row = db.get(SystemSetting, "breakdown_viral_multiple")
    if row is None:
        row = SystemSetting(key="breakdown_viral_multiple")
        db.add(row)
    row.value = "nan"
    db.commit()
    db.close()

    body = client.get("/api/system/breakdown-settings").json()
    assert body["viral_multiple"] == 3.0
