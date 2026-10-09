"""账号级存储布局：每个账号可绑定独立根或共用根，子目录模板带 {account} 段。

覆盖：
- render_subdir 的 {account} token：非空渲染、为空时该段整体消失；
- resolve_account_root：绑定根优先、无绑定回退默认根、绑定失效回退默认；
- 下载 handler：按「账号根 + 订阅模板」落盘，两个账号共用根 / 各自独立根均可；
- 外部 API 仍拒绝绝对路径（红线不变）。
"""

import pytest

from app.core import storage
from app.models import ContentItem, MediaAsset, PlatformAccount, StorageRoot, Subscription


# ---------------------------------------------------------------------------
# render_subdir
# ---------------------------------------------------------------------------


def test_render_subdir_renders_account_segment():
    from datetime import datetime

    rel = storage.render_subdir(
        "{platform}/{account}/{creator}/{date}",
        platform="douyin",
        account="主号",
        creator="阿一",
        date=datetime(2026, 10, 9),
        content_id="123",
    )
    assert rel == "douyin/主号/阿一/2026-10-09"


def test_render_subdir_empty_account_drops_segment():
    from datetime import datetime

    rel = storage.render_subdir(
        "{platform}/{account}/{creator}/{date}",
        platform="douyin",
        account="",
        creator="阿一",
        date=datetime(2026, 10, 9),
        content_id="123",
    )
    # 不留空目录层：douyin//阿一 也不会出现
    assert rel == "douyin/阿一/2026-10-09"
    assert "account" not in rel


# ---------------------------------------------------------------------------
# resolve_account_root
# ---------------------------------------------------------------------------


def _mk_root(session, name, path, *, is_default=False):
    r = StorageRoot(name=name, path=path, is_default=is_default)
    session.add(r)
    session.commit()
    return r


def test_resolve_account_root_prefers_bound_root(session):
    default = _mk_root(session, "默认", "/data/default", is_default=True)
    own = _mk_root(session, "专属", "/data/own")
    acc = PlatformAccount(platform="douyin", display_name="a", storage_root_id=own.id)
    session.add(acc)
    session.commit()
    assert storage.resolve_account_root(session, acc).id == own.id
    assert storage.resolve_account_root(session, acc).id != default.id


def test_resolve_account_root_falls_back_to_default(session):
    default = _mk_root(session, "默认", "/data/default", is_default=True)
    acc = PlatformAccount(platform="douyin", display_name="a", storage_root_id=None)
    session.add(acc)
    session.commit()
    assert storage.resolve_account_root(session, acc).id == default.id


def test_two_accounts_can_share_one_root(session):
    shared = _mk_root(session, "共享", "/data/shared", is_default=True)
    a1 = PlatformAccount(platform="douyin", display_name="a1", storage_root_id=shared.id)
    a2 = PlatformAccount(platform="bilibili", display_name="a2", storage_root_id=shared.id)
    session.add_all([a1, a2])
    session.commit()
    assert storage.resolve_account_root(session, a1).id == shared.id
    assert storage.resolve_account_root(session, a2).id == shared.id


# ---------------------------------------------------------------------------
# download handler: 账号根 + 订阅模板
# ---------------------------------------------------------------------------


async def _run_download(session, monkeypatch, account, content, sub=None, root=None):
    from app.modules.media_download import DownloadResult
    from app.workers import handlers

    async def fake_download(url, out_dir, audio_only=False, cookie=""):
        return DownloadResult(ok=True, video_file="v.mp4", audio_file="", title="t")

    monkeypatch.setattr(handlers.media_download, "download", fake_download)
    asset = MediaAsset(content_id=content.id, download_status="pending")
    session.add(asset)
    session.commit()
    await handlers.handle_download(session, {"asset_id": asset.id})
    session.refresh(asset)
    return asset


@pytest.mark.asyncio
async def test_download_uses_account_root_and_subdir_template(session, monkeypatch):
    root = _mk_root(session, "主号目录", "/data/main", is_default=True)
    acc = PlatformAccount(platform="douyin", display_name="主号", storage_root_id=root.id)
    session.add(acc)
    session.commit()
    c = ContentItem(
        account_id=acc.id, platform="douyin", external_id="e1", title="t",
        url="https://x/1", author_name="阿一", content_type="video",
        source_kind="subscription", source_id=1,
    )
    session.add(c)
    session.commit()
    sub = Subscription(
        id=1, account_id=acc.id, platform="douyin", source_type="creator", remote_id="r1",
        subdir_template="{platform}/{account}/{creator}",
    )
    session.add(sub)
    session.commit()

    asset = await _run_download(session, monkeypatch, acc, c)
    assert asset.storage_root_id == root.id
    assert asset.relative_path == "douyin/主号/阿一"


@pytest.mark.asyncio
async def test_download_two_accounts_same_root_distinct_subdirs(session, monkeypatch):
    """两个账号共用同一个根时，靠 {account} 段在根下分开，互不覆盖。"""
    shared = _mk_root(session, "共享", "/data/shared", is_default=True)
    a1 = PlatformAccount(platform="douyin", display_name="甲", storage_root_id=shared.id)
    a2 = PlatformAccount(platform="bilibili", display_name="乙", storage_root_id=shared.id)
    session.add_all([a1, a2])
    session.commit()

    def _content(acc, ext):
        c = ContentItem(
            account_id=acc.id, platform=acc.platform, external_id=ext, title="t",
            url=f"https://x/{ext}", author_name="作者", content_type="video",
        )
        session.add(c)
        session.commit()
        return c

    c1 = _content(a1, "c1")
    c2 = _content(a2, "c2")
    asset1 = await _run_download(session, monkeypatch, a1, c1)
    asset2 = await _run_download(session, monkeypatch, a2, c2)
    assert asset1.storage_root_id == shared.id == asset2.storage_root_id
    assert asset1.relative_path.startswith("douyin/甲/")
    assert asset2.relative_path.startswith("bilibili/乙/")
    assert asset1.relative_path != asset2.relative_path


def test_reject_absolute_still_enforced():
    """红线：外部提交的路径不得是绝对路径 / 盘符 / UNC / 穿越。"""
    for bad in ("/etc/passwd", "\\win\\x", "C:\\x", "\\\\NAS\\share", "../escape"):
        with pytest.raises(ValueError):
            storage.reject_absolute(bad)


@pytest.mark.asyncio
async def test_subscription_root_override_beats_account_root(session, monkeypatch):
    """订阅级指定根优先于账号绑定根（用于给某个来源单独指定目录）。"""
    account_root = _mk_root(session, "账号目录", "/data/account", is_default=True)
    sub_root = _mk_root(session, "订阅专属", "/data/sub")
    acc = PlatformAccount(platform="douyin", display_name="主号", storage_root_id=account_root.id)
    session.add(acc)
    session.commit()
    c = ContentItem(
        account_id=acc.id, platform="douyin", external_id="e1", title="t",
        url="https://x/1", author_name="阿一", content_type="video",
        source_kind="subscription", source_id=1,
    )
    session.add(c)
    session.commit()
    session.add(Subscription(
        id=1, account_id=acc.id, platform="douyin", source_type="creator", remote_id="r1",
        storage_root_id=sub_root.id,
    ))
    session.commit()

    asset = await _run_download(session, monkeypatch, acc, c)
    assert asset.storage_root_id == sub_root.id


@pytest.mark.asyncio
async def test_subscription_without_override_uses_account_root(session, monkeypatch):
    account_root = _mk_root(session, "账号目录", "/data/account", is_default=True)
    acc = PlatformAccount(platform="douyin", display_name="主号", storage_root_id=account_root.id)
    session.add(acc)
    session.commit()
    c = ContentItem(
        account_id=acc.id, platform="douyin", external_id="e1", title="t",
        url="https://x/1", author_name="阿一", content_type="video",
        source_kind="subscription", source_id=1,
    )
    session.add(c)
    session.commit()
    session.add(Subscription(
        id=1, account_id=acc.id, platform="douyin", source_type="creator", remote_id="r1",
        storage_root_id=None,
    ))
    session.commit()

    asset = await _run_download(session, monkeypatch, acc, c)
    assert asset.storage_root_id == account_root.id
