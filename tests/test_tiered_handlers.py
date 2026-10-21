"""分档加工 worker handlers：注册 + 透传 + 幂等/异常语义。

handler 约定：形如 async def(db, payload) -> None，抛异常即失败交给 runner 重试。
- handle_understand_video：调 run_understand，透传 force_category / use_cover。
- asset 不存在时静默返回（与既有 handler 一致），不抛错。
"""

import asyncio

import app.services.understand_service as svc
from app.workers import handlers


def _seed(db):
    from app.models import ContentItem, MediaAsset

    c = ContentItem(account_id=1, platform="douyin", external_id="aw1", content_type="video")
    db.add(c)
    db.flush()
    a = MediaAsset(content_id=c.id, relative_path="douyin/a/1", video_file="")
    db.add(a)
    db.commit()
    return a


def test_handlers_registered():
    # 两档简化：handler 表只剩真实存在的加工 handler，无抽帧残留。
    assert "understand_video" in handlers.HANDLERS
    assert all("frame" not in name for name in handlers.HANDLERS)


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


def test_handle_understand_video_model_config_error_is_non_retryable(monkeypatch, session):
    from app.core.queue import NonRetryableJobError
    from app.services.model_access_service import ModelConfigError

    a = _seed(session)

    async def missing_config(*args, **kwargs):
        raise ModelConfigError(
            "视频理解没有配置可用模型",
            attempt_group_id="mg_test",
            primary_model_id=None,
            primary_model_key="",
            fallback_model_id=None,
            fallback_model_key="",
            primary_error_class="not_configured",
            primary_error_message="该工作尚未配置模型",
            fallback_error_class="",
            fallback_error_message="",
        )

    monkeypatch.setattr(svc, "run_understand", missing_config)
    try:
        asyncio.run(handlers.handle_understand_video(session, {"asset_id": a.id}))
    except NonRetryableJobError as exc:
        assert "没有配置可用模型" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("模型配置错误不应进入自动重试")


def test_handle_breakdown_model_config_error_is_non_retryable(monkeypatch, session):
    import app.services.breakdown_service as breakdown
    from app.core.queue import NonRetryableJobError
    from app.services.model_access_service import ModelConfigError

    a = _seed(session)

    async def missing_config(*args, **kwargs):
        raise ModelConfigError(
            "文字分析没有配置可用模型",
            attempt_group_id="mg_test",
            primary_model_id=None,
            primary_model_key="",
            fallback_model_id=None,
            fallback_model_key="",
            primary_error_class="not_configured",
            primary_error_message="该工作尚未配置模型",
            fallback_error_class="",
            fallback_error_message="",
        )

    monkeypatch.setattr(breakdown, "run_breakdown", missing_config)
    try:
        asyncio.run(handlers.handle_breakdown(session, {"asset_id": a.id}))
    except NonRetryableJobError as exc:
        assert "没有配置可用模型" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("模型配置错误不应进入自动重试")


def test_handle_analyze_model_config_error_is_non_retryable(monkeypatch, session):
    import app.services.model_access_service as model_access
    from app.core.queue import NonRetryableJobError
    from app.models import Transcript
    from app.services.model_access_service import ModelConfigError

    a = _seed(session)
    session.add(Transcript(asset_id=a.id, full_text="可分析文本", language="zh", model="base"))
    session.commit()

    async def missing_config(*args, **kwargs):
        raise ModelConfigError(
            "文字分析没有配置可用模型",
            attempt_group_id="mg_test",
            primary_model_id=None,
            primary_model_key="",
            fallback_model_id=None,
            fallback_model_key="",
            primary_error_class="not_configured",
            primary_error_message="该工作尚未配置模型",
            fallback_error_class="",
            fallback_error_message="",
        )

    monkeypatch.setattr(model_access.ModelAccessService, "call_with_fallback", missing_config)
    try:
        asyncio.run(handlers.handle_analyze(session, {"asset_id": a.id}))
    except NonRetryableJobError as exc:
        assert "没有配置可用模型" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("文字分析的模型配置错误不应进入自动重试")
