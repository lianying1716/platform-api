"""Storage lifecycle contract: roots, two-phase deletion and atomic downloads."""

from datetime import datetime

from sqlalchemy import select

from app.models import (
    ContentItem,
    DeletionAudit,
    JobQueue,
    MediaAsset,
    PlatformAccount,
    StorageRoot,
)


def _asset(session, tmp_path, *, deleted=False, suffix="x"):
    root = StorageRoot(name=f"root-{suffix}", path=str(tmp_path), is_default=True)
    session.add(root)
    session.flush()
    account = PlatformAccount(platform="douyin", display_name="a")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id, platform="douyin", external_id=f"x-{suffix}", title="t"
    )
    session.add(content)
    session.flush()
    relative = "douyin/a/x"
    target = tmp_path / relative
    target.mkdir(parents=True, exist_ok=True)
    video = target / "v.mp4"
    video.write_bytes(b"video")
    asset = MediaAsset(
        content_id=content.id,
        storage_root_id=root.id,
        relative_path=relative,
        video_file="v.mp4",
        deleted_at=datetime.utcnow() if deleted else None,
    )
    session.add(asset)
    session.commit()
    return root, asset, video


def test_root_validation_rejects_empty_relative_and_traversal():
    from app.core import storage

    for path in ("", "relative/path", "../escape"):
        try:
            storage.validate_root_path(path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"应拒绝 {path!r}")
    assert storage.validate_root_path("/mnt/nas/xsai") == "/mnt/nas/xsai"
    assert storage.validate_root_path(r"C:\media") == r"C:\media"


def test_asset_filename_cannot_escape_asset_directory(tmp_path):
    import pytest
    from app.core import storage

    for name in ("../secret.mp4", "sub/v.mp4", r"sub\v.mp4", "..", "."):
        with pytest.raises(ValueError):
            storage.safe_asset_filename(name)

    resolved = storage.resolve_asset_file_path(str(tmp_path), "v.mp4")
    assert resolved.endswith("v.mp4")


def test_media_endpoint_does_not_serve_escaped_filename(client, session, tmp_path):
    _root, asset, _video = _asset(session, tmp_path)
    outside = tmp_path.parent / "outside-secret.mp4"
    outside.write_bytes(b"secret")
    asset.video_file = "../outside-secret.mp4"
    session.commit()
    response = client.get(f"/api/assets/{asset.id}/media")
    assert response.status_code == 404


def test_no_root_raises_instead_of_writing_cwd(session):
    import pytest
    from app.core.storage import StorageUnavailable, resolve_asset_dir

    account = PlatformAccount(platform="douyin", display_name="a")
    session.add(account)
    session.flush()
    content = ContentItem(account_id=account.id, platform="douyin", external_id="no-root")
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id, relative_path="douyin/a")
    session.add(asset)
    session.commit()
    with pytest.raises(StorageUnavailable):
        resolve_asset_dir(session, asset)


def test_default_root_used_when_asset_has_empty_relative_path_is_protected(
    client, session, tmp_path
):
    """Deleting a legacy root-level asset must never remove the whole default root."""
    root = StorageRoot(name="legacy-root", path=str(tmp_path), is_default=True)
    session.add(root)
    session.flush()
    account = PlatformAccount(platform="douyin", display_name="a")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id, platform="douyin", external_id="legacy-root-asset"
    )
    session.add(content)
    session.flush()
    other = tmp_path / "unrelated" / "keep.txt"
    other.parent.mkdir(parents=True)
    other.write_text("keep", "utf-8")
    asset = MediaAsset(
        content_id=content.id,
        storage_root_id=None,
        relative_path="",
        video_file="v.mp4",
    )
    (tmp_path / "v.mp4").write_bytes(b"video")
    session.add(asset)
    session.commit()

    response = client.delete(f"/api/assets/{asset.id}")
    assert response.status_code == 202
    job = session.get(JobQueue, response.json()["job_id"])
    from app.services.storage_lifecycle import complete_asset_deletion

    complete_asset_deletion(session, job.payload)
    session.expire_all()
    assert session.get(MediaAsset, asset.id) is None
    assert not (tmp_path / "v.mp4").exists()
    assert other.exists(), "根目录保护必须保留其他素材，不能 rmtree 整个存储根"


def test_symlink_inside_asset_dir_cannot_escape_storage_root(session, tmp_path):
    """The deletion boundary is the storage root, even through a symlinked subdir."""
    import pytest

    from app.core.storage import StorageUnavailable, resolve_asset_dir

    root = StorageRoot(name="symlink-root", path=str(tmp_path / "root"), is_default=True)
    session.add(root)
    session.flush()
    account = PlatformAccount(platform="douyin", display_name="a")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id, platform="douyin", external_id="symlink-escape"
    )
    session.add(content)
    session.flush()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", "utf-8")
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "link").symlink_to(outside, target_is_directory=True)
    asset = MediaAsset(
        content_id=content.id,
        storage_root_id=root.id,
        relative_path="link",
    )
    session.add(asset)
    session.commit()

    with pytest.raises(StorageUnavailable, match="越出"):
        resolve_asset_dir(session, asset)


def test_relative_or_missing_bound_root_never_falls_back_to_cwd(session, tmp_path):
    import pytest
    from app.core.storage import StorageUnavailable, resolve_asset_dir

    account = PlatformAccount(platform="douyin", display_name="a")
    session.add(account)
    session.flush()
    content = ContentItem(account_id=account.id, platform="douyin", external_id="bad-root")
    session.add(content)
    session.flush()
    bad = StorageRoot(name="bad", path="relative/storage", is_default=True)
    session.add(bad)
    session.flush()
    asset = MediaAsset(content_id=content.id, storage_root_id=bad.id, relative_path="douyin/a")
    session.add(asset)
    session.commit()
    with pytest.raises(StorageUnavailable):
        resolve_asset_dir(session, asset)

    missing = MediaAsset(content_id=content.id, storage_root_id=999999, relative_path="douyin/a")
    session.add(missing)
    session.commit()
    with pytest.raises(StorageUnavailable, match="不存在"):
        resolve_asset_dir(session, missing)


def test_referenced_root_returns_409(client, session, tmp_path):
    root, _asset_row, _video = _asset(session, tmp_path)
    response = client.delete(f"/api/system/storage-roots/{root.id}")
    assert response.status_code == 409
    assert "引用" in response.text


def test_default_root_is_protected_by_implicit_legacy_references(client, session, tmp_path):
    """Legacy NULL bindings still resolve to the default root and must block deletion."""
    root = StorageRoot(name="implicit-default", path=str(tmp_path), is_default=True)
    session.add(root)
    session.flush()
    account = PlatformAccount(platform="douyin", display_name="inherit-default")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id, platform="douyin", external_id="implicit-default-asset"
    )
    session.add(content)
    session.flush()
    session.add(MediaAsset(content_id=content.id, storage_root_id=None, relative_path="legacy/x"))
    session.commit()

    response = client.delete(f"/api/system/storage-roots/{root.id}")
    assert response.status_code == 409
    assert "引用" in response.text
    assert session.get(StorageRoot, root.id) is not None


def test_account_inheriting_default_root_blocks_deletion_even_without_assets(client, session, tmp_path):
    root = StorageRoot(name="account-default", path=str(tmp_path), is_default=True)
    session.add_all([root, PlatformAccount(platform="bilibili", display_name="no-asset-yet")])
    session.commit()

    response = client.delete(f"/api/system/storage-roots/{root.id}")
    assert response.status_code == 409
    assert "accounts" in response.text
    assert session.get(StorageRoot, root.id) is not None


def test_asset_deletion_is_two_phase_and_cleans_files(client, session, tmp_path):
    _root, asset, video = _asset(session, tmp_path)
    again = asset.id
    asset_id = asset.id
    # Use the admin DELETE API once it exists; the skills API is the external contract.
    created = client.post("/api/ai/keys", json={"name": "agent2", "scopes": ["operate"]})
    headers = {"Authorization": f"Bearer {created.json()['api_key']}"}
    response = client.post(
        f"/api/skills/assets/{asset_id}/delete",
        json={"confirm": True, "reason": "清理"},
        headers=headers,
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    session.expire_all()
    assert session.get(MediaAsset, again).deleted_at is not None
    assert video.exists(), "API 不应同步删除文件"
    job = session.get(JobQueue, job_id)
    assert job.job_type == "delete_asset" and job.status == "queued"

    from app.services.storage_lifecycle import complete_asset_deletion

    complete_asset_deletion(session, job.payload)
    session.expire_all()
    assert session.get(MediaAsset, again) is None
    assert not video.exists()
    audit = session.scalar(select(DeletionAudit).where(DeletionAudit.asset_id == again))
    assert audit is not None and audit.status == "completed"


def test_deletion_removes_asset_children_and_cover_cache(client, session, tmp_path, monkeypatch):
    from app.config import settings
    from app.models import AnalysisResult, Transcript, VideoUnderstanding, ViralBreakdown

    _root, asset, _video = _asset(session, tmp_path)
    session.add(Transcript(asset_id=asset.id, full_text="t"))
    session.add(AnalysisResult(asset_id=asset.id, summary="s"))
    session.add(VideoUnderstanding(asset_id=asset.id, summary="u"))
    session.add(ViralBreakdown(asset_id=asset.id, verdict="v"))
    session.commit()
    cache = tmp_path / "cover_cache"
    cache.mkdir()
    (cache / f"{asset.id}.json").write_text("{}", "utf-8")
    (cache / f"{asset.id}.bin").write_bytes(b"img")
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    response = client.delete(f"/api/assets/{asset.id}")
    assert response.status_code == 202
    job = session.get(JobQueue, response.json()["job_id"])
    from app.services.storage_lifecycle import complete_asset_deletion

    complete_asset_deletion(session, job.payload)
    session.expire_all()
    assert session.get(MediaAsset, asset.id) is None
    assert session.scalar(select(Transcript).where(Transcript.asset_id == asset.id)) is None
    assert session.scalar(select(AnalysisResult).where(AnalysisResult.asset_id == asset.id)) is None
    assert session.scalar(select(VideoUnderstanding).where(VideoUnderstanding.asset_id == asset.id)) is None
    assert session.scalar(select(ViralBreakdown).where(ViralBreakdown.asset_id == asset.id)) is None
    assert not (cache / f"{asset.id}.json").exists()
    assert not (cache / f"{asset.id}.bin").exists()


def test_admin_asset_delete_returns_202_and_hides_from_list(client, session, tmp_path):
    _root, asset, _video = _asset(session, tmp_path)
    response = client.request("DELETE", f"/api/assets/{asset.id}", json={"reason": "管理端删除"})
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    session.expire_all()
    assert session.get(MediaAsset, asset.id).deleted_at is not None
    assert client.get("/api/assets").json() == []
    assert client.get(f"/api/assets/{asset.id}").status_code == 404
    assert client.get(f"/api/assets/{asset.id}/comments").status_code == 404
    assert client.get(f"/api/assets/{asset.id}/model-calls").status_code == 404
    assert client.get(f"/api/assets/{asset.id}/cover").status_code == 404


def test_delete_removes_queued_work_for_the_asset(client, session, tmp_path):
    _root, asset, _video = _asset(session, tmp_path)
    session.add(JobQueue(job_type="transcribe", payload={"asset_id": asset.id}, status="queued"))
    session.commit()

    response = client.delete(f"/api/assets/{asset.id}")
    assert response.status_code == 202
    session.expire_all()
    jobs = session.scalars(select(JobQueue).where(JobQueue.job_type == "transcribe")).all()
    assert jobs == []
    delete_jobs = session.scalars(select(JobQueue).where(JobQueue.job_type == "delete_asset")).all()
    assert len(delete_jobs) == 1 and delete_jobs[0].status == "queued"


def test_detail_never_returns_absolute_path(client, session, tmp_path):
    _root, asset, _video = _asset(session, tmp_path)
    body = client.get(f"/api/assets/{asset.id}").json()
    assert body["file"]["full_dir"] == asset.relative_path
    assert str(tmp_path) not in str(body)


def test_orphan_report_is_relative_and_report_only(session, tmp_path):
    _root, asset, _video = _asset(session, tmp_path)
    orphan = tmp_path / "douyin" / "orphan.bin"
    orphan.write_bytes(b"leftover")
    from app.services.storage_lifecycle import scan_orphan_files

    report = scan_orphan_files(session)
    assert report["orphan_count"] == 1
    assert report["orphans"] == ["douyin/orphan.bin"]
    assert orphan.exists(), "扫描必须只报告，不得删除文件"


def test_orphan_report_api_returns_only_relative_paths(client, session, tmp_path):
    _root, _asset_row, video = _asset(session, tmp_path)
    orphan = tmp_path / "douyin" / "orphan.bin"
    orphan.write_bytes(b"leftover")
    response = client.get("/api/system/storage-roots/orphans")
    assert response.status_code == 200
    body = response.json()
    assert body["orphans"] == ["douyin/orphan.bin"]
    assert str(tmp_path) not in response.text
    assert video.exists() and orphan.exists()


def test_direct_download_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    import asyncio

    import httpx

    from app.modules import media_download

    class BrokenStream:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        async def aiter_bytes(self, _n):
            yield b"half"
            raise RuntimeError("connection reset")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return BrokenStream()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = asyncio.run(media_download.download_direct("https://x/video/12345678901", tmp_path))
    assert result.ok is False
    assert list(tmp_path.glob("*.mp4")) == []
    assert list(tmp_path.glob("*.part")) == []


def test_janitor_recovers_pending_soft_delete(session, tmp_path):
    from app.services.storage_lifecycle import recover_pending_deletions

    _root, asset, _video = _asset(session, tmp_path, deleted=True)
    assert recover_pending_deletions(session) == 1
    job = session.scalar(select(JobQueue).where(JobQueue.job_type == "delete_asset"))
    assert job is not None and job.status == "queued"
    assert recover_pending_deletions(session) == 0, "已有活任务时不能重复入队"


def test_recovery_is_per_asset_not_global(session, tmp_path):
    from app.services.storage_lifecycle import recover_pending_deletions

    _root, first, _video1 = _asset(session, tmp_path, deleted=True)
    _root2, second, _video2 = _asset(session, tmp_path, deleted=True, suffix="y")
    live = JobQueue(
        job_type="delete_asset",
        payload={"asset_id": first.id},
        status="running",
        heartbeat_at=datetime.utcnow(),
    )
    session.add(live)
    session.commit()

    assert recover_pending_deletions(session) == 1
    jobs = session.scalars(select(JobQueue).where(JobQueue.job_type == "delete_asset")).all()
    assert {int((job.payload or {})["asset_id"]) for job in jobs} == {first.id, second.id}
