"""下载 handler：必须用真实 DownloadResult，且要带平台 Cookie、失败不谎报成功。

回归背景（实测发现）：
- handle_download 用 result.get(...) 读返回值，但 download() 返回的是 dataclass
  DownloadResult → 每次下载都 AttributeError 崩溃。旧测试把 download mock 成 dict，
  正好掩盖了这个 bug。
- 没把账号 Cookie 传给 yt-dlp，而抖音 extractor 明确要求 fresh cookies，
  必然下载失败。
"""

import pytest

from app.models import ContentItem, MediaAsset, PlatformAccount, StorageRoot


def _setup(session, platform="douyin"):
    acc = PlatformAccount(platform=platform, display_name="d")
    session.add(acc)
    session.commit()
    root = StorageRoot(name="r", path="/tmp/dltest")
    session.add(root)
    session.commit()
    c = ContentItem(
        platform=platform, external_id="123", title="t",
        url=f"https://www.douyin.com/video/123", author_name="a", content_type="video",
    )
    session.add(c)
    session.commit()
    a = MediaAsset(content_id=c.id, storage_root_id=root.id, download_status="pending")
    session.add(a)
    session.commit()
    return acc, c, a


@pytest.mark.asyncio
async def test_download_handler_uses_dataclass_result(session, monkeypatch):
    from app.modules.media_download import DownloadResult
    from app.workers import handlers

    _acc, _c, asset = _setup(session)

    async def fake_download(url, out_dir, audio_only=False, cookie=""):
        return DownloadResult(ok=True, video_file="123.mp4", audio_file="", title="t")

    monkeypatch.setattr(handlers.media_download, "download", fake_download)
    await handlers.handle_download(session, {"asset_id": asset.id})
    session.refresh(asset)
    assert asset.video_file == "123.mp4"
    assert asset.download_status == "done"


@pytest.mark.asyncio
async def test_download_handler_passes_cookie(session, monkeypatch):
    """账号 Cookie 必须传给下载器（抖音 yt-dlp 要 fresh cookies）。"""
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential
    from app.modules.media_download import DownloadResult
    from app.workers import handlers

    acc, _c, asset = _setup(session)
    session.add(AccountCredential(
        account_id=acc.id, ciphertext=encrypt_credential({"cookie": "sessionid=SID; ttwid=T"})
    ))
    acc.status = "active"
    session.commit()

    seen = {}

    async def fake_download(url, out_dir, audio_only=False, cookie=""):
        seen["cookie"] = cookie
        return DownloadResult(ok=True, video_file="123.mp4")

    monkeypatch.setattr(handlers.media_download, "download", fake_download)
    await handlers.handle_download(session, {"asset_id": asset.id})
    assert "sessionid=SID" in seen.get("cookie", ""), "下载必须带上账号 Cookie"


@pytest.mark.asyncio
async def test_download_failure_marks_error_not_done(session, monkeypatch):
    """下载失败要如实记 error，不能谎报 done（否则看板显示完成却没有文件）。"""
    from app.modules.media_download import DownloadResult
    from app.workers import handlers

    _acc, _c, asset = _setup(session)

    async def fake_download(url, out_dir, audio_only=False, cookie=""):
        return DownloadResult(ok=False, message="Fresh cookies are needed")

    monkeypatch.setattr(handlers.media_download, "download", fake_download)
    await handlers.handle_download(session, {"asset_id": asset.id})
    session.refresh(asset)
    assert asset.download_status == "error"
    assert "Fresh cookies" in (asset.error or "")
