"""自动转录 / 自动分析：两级开关（订阅级优先、全局默认兜底），下载完成后入队。

覆盖：
- auto_process_flags：订阅级开关覆盖全局默认；无订阅内容用全局默认；
- 下载 handler：开了自动转录会入队 transcribe；关了不入队；
- 关了自动转录但开了自动分析：先入队转录，不直接入队 analyze（analyze 依赖转录）；
- 转录完成链式补 analyze：开了自动分析才会补；
- 关闭状态不得凭空入队。
"""

import pytest
from sqlalchemy import select

from app.models import (
    ContentItem,
    JobQueue,
    MediaAsset,
    PlatformAccount,
    StorageRoot,
    Subscription,
)


def _setup(session, *, auto_transcribe=None, auto_analyze=None, content_type="video"):
    root = StorageRoot(name="r", path="/tmp/ap", is_default=True)
    session.add(root)
    session.commit()
    acc = PlatformAccount(platform="douyin", display_name="a", storage_root_id=root.id)
    session.add(acc)
    session.commit()
    sub = Subscription(
        id=1, account_id=acc.id, platform="douyin", source_type="creator", remote_id="r1",
        auto_transcribe=auto_transcribe, auto_analyze=auto_analyze,
    )
    session.add(sub)
    session.commit()
    c = ContentItem(
        account_id=acc.id, platform="douyin", external_id="e1", title="t",
        url="https://x/1", author_name="阿一", content_type=content_type,
        source_kind="subscription", source_id=sub.id,
    )
    session.add(c)
    session.commit()
    return acc, sub, c


def _jobs(session, job_type):
    return session.scalars(select(JobQueue).where(JobQueue.job_type == job_type)).all()


def test_flags_subscription_overrides_global(session):
    from app.services.auto_process import auto_process_flags

    _acc, _sub, c = _setup(session, auto_transcribe=False, auto_analyze=False)
    # 订阅级 False 覆盖全局 True
    t, a = auto_process_flags(session, c, {"auto_transcribe": True, "auto_analyze": True})
    assert (t, a) == (False, False)


def test_flags_inherit_global_true(session):
    from app.services.auto_process import auto_process_flags

    _acc, _sub, c = _setup(session, auto_transcribe=None, auto_analyze=None)
    t, a = auto_process_flags(session, c, {"auto_transcribe": True, "auto_analyze": True})
    assert (t, a) == (True, True)


def test_flags_subscription_true_overrides_global_false(session):
    from app.services.auto_process import auto_process_flags

    _acc, _sub, c = _setup(session, auto_transcribe=True, auto_analyze=True)
    t, a = auto_process_flags(session, c, {"auto_transcribe": False, "auto_analyze": False})
    assert (t, a) == (True, True)


@pytest.mark.asyncio
async def test_download_enqueues_transcribe_when_auto_on(session, monkeypatch):
    from app.modules.media_download import DownloadResult
    from app.workers import handlers

    async def fake_download(url, out_dir, audio_only=False, cookie=""):
        return DownloadResult(ok=True, video_file="v.mp4", audio_file="", title="t")

    monkeypatch.setattr(handlers.media_download, "download", fake_download)
    _acc, _sub, c = _setup(session, auto_transcribe=True)
    asset = MediaAsset(content_id=c.id, download_status="pending")
    session.add(asset)
    session.commit()
    await handlers.handle_download(session, {"asset_id": asset.id})
    session.refresh(asset)
    assert asset.download_status == "done"
    assert len(_jobs(session, "transcribe")) == 1
    assert asset.transcribe_status == "pending"


@pytest.mark.asyncio
async def test_download_no_enqueue_when_auto_off(session, monkeypatch):
    from app.modules.media_download import DownloadResult
    from app.workers import handlers

    async def fake_download(url, out_dir, audio_only=False, cookie=""):
        return DownloadResult(ok=True, video_file="v.mp4", audio_file="", title="t")

    monkeypatch.setattr(handlers.media_download, "download", fake_download)
    _acc, _sub, c = _setup(session, auto_transcribe=False, auto_analyze=False)
    asset = MediaAsset(content_id=c.id, download_status="pending")
    session.add(asset)
    session.commit()
    await handlers.handle_download(session, {"asset_id": asset.id})
    assert _jobs(session, "transcribe") == []
    assert _jobs(session, "analyze") == []


@pytest.mark.asyncio
async def test_auto_analyze_only_enqueues_after_transcribe(session, monkeypatch):
    """开了自动分析但未转录：下载完成只入队转录，不直接入队 analyze。"""
    from app.modules.media_download import DownloadResult
    from app.workers import handlers

    async def fake_download(url, out_dir, audio_only=False, cookie=""):
        return DownloadResult(ok=True, video_file="v.mp4", audio_file="", title="t")

    monkeypatch.setattr(handlers.media_download, "download", fake_download)
    _acc, _sub, c = _setup(session, auto_transcribe=False, auto_analyze=True)
    asset = MediaAsset(content_id=c.id, download_status="pending")
    session.add(asset)
    session.commit()
    await handlers.handle_download(session, {"asset_id": asset.id})
    session.refresh(asset)
    # 自动分析但没自动转录 → 需要转录做前置
    assert len(_jobs(session, "transcribe")) == 1
    assert _jobs(session, "analyze") == []


def test_maybe_chain_analyze_enqueues_when_transcript_done(session):
    from app.services.auto_process import maybe_chain_analyze

    _acc, _sub, c = _setup(session, auto_transcribe=False, auto_analyze=True)
    asset = MediaAsset(content_id=c.id, download_status="done", transcribe_status="done")
    session.add(asset)
    session.flush()
    from app.models import Transcript

    session.add(Transcript(asset_id=asset.id, full_text="已有转录"))
    session.commit()
    maybe_chain_analyze(session, asset, c, {"auto_transcribe": False, "auto_analyze": True})
    session.commit()
    assert len(_jobs(session, "analyze")) == 1
    assert asset.analyze_status == "pending"


def test_maybe_chain_analyze_noop_when_auto_off(session):
    from app.services.auto_process import maybe_chain_analyze

    _acc, _sub, c = _setup(session, auto_transcribe=False, auto_analyze=False)
    asset = MediaAsset(content_id=c.id, download_status="done", transcribe_status="done")
    session.add(asset)
    session.commit()
    maybe_chain_analyze(session, asset, c, {"auto_transcribe": False, "auto_analyze": False})
    session.commit()
    assert _jobs(session, "analyze") == []
