"""分档加工 worker handlers：注册 + 透传 + 幂等/异常语义。

handler 约定：形如 async def(db, payload) -> None，抛异常即失败交给 runner 重试。
- handle_extract_frames：调 run_extract_frames。
- handle_understand_video：调 run_understand，透传 force_category / use_cover。
- asset 不存在时静默返回（与既有 handler 一致），不抛错。
"""

import asyncio

import app.services.understand_service as svc
from app.workers import handlers


def _seed(db):
    from app.models import ContentItem, MediaAsset

    c = ContentItem(platform="douyin", external_id="aw1", content_type="video")
    db.add(c)
    db.flush()
    a = MediaAsset(content_id=c.id, relative_path="douyin/a/1", video_file="")
    db.add(a)
    db.commit()
    return a


def test_handlers_registered():
    assert "extract_frames" in handlers.HANDLERS
    assert "understand_video" in handlers.HANDLERS


def test_handle_extract_frames_delegates(monkeypatch, session):
    a = _seed(session)
    seen = {}

    async def fake_run(db, asset_id, **kw):
        seen["asset_id"] = asset_id
        return 3

    monkeypatch.setattr(svc, "run_extract_frames", fake_run)
    asyncio.run(handlers.handle_extract_frames(session, {"asset_id": a.id}))
    assert seen["asset_id"] == a.id


def test_handle_understand_video_passes_options(monkeypatch, session):
    a = _seed(session)
    seen = {}

    async def fake_run(db, asset_id, **kw):
        seen["asset_id"] = asset_id
        seen.update(kw)
        return object()

    monkeypatch.setattr(svc, "run_understand", fake_run)
    asyncio.run(
        handlers.handle_understand_video(session, {"asset_id": a.id, "force_category": "drama", "use_cover": True})
    )
    assert seen["asset_id"] == a.id
    assert seen["force_category"] == "drama"
    assert seen["use_cover"] is True


def test_handle_understand_video_missing_asset_is_noop(session):
    asyncio.run(handlers.handle_understand_video(session, {"asset_id": 999999}))  # 不抛


def test_handle_extract_frames_missing_asset_is_noop(session):
    asyncio.run(handlers.handle_extract_frames(session, {"asset_id": 999999}))  # 不抛


def test_handle_understand_video_input_error_is_non_retryable(monkeypatch, session):
    """数据问题（UnderstandInputError）→ 包成 NonRetryableJobError，让 worker 不再重试。"""
    from app.core.queue import NonRetryableJobError

    a = _seed(session)

    async def boom(db, asset_id, **kw):
        raise svc.UnderstandInputError("素材未关联内容")

    monkeypatch.setattr(svc, "run_understand", boom)
    try:
        asyncio.run(handlers.handle_understand_video(session, {"asset_id": a.id}))
    except NonRetryableJobError as exc:
        assert "未关联内容" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应抛出 NonRetryableJobError")


def test_handle_understand_video_generic_error_propagates(monkeypatch, session):
    """普通异常原样抛出（worker 正常重试）。"""
    a = _seed(session)

    async def boom(db, asset_id, **kw):
        raise RuntimeError("网络炸了")

    monkeypatch.setattr(svc, "run_understand", boom)
    try:
        asyncio.run(handlers.handle_understand_video(session, {"asset_id": a.id}))
    except RuntimeError as exc:
        assert "网络炸了" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应抛出 RuntimeError")
