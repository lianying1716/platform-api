"""存储目录可视化选择（只读列目录）API 校验。

覆盖：白名单内可浏览、只列目录不列文件、越界（软链/`..`）拒绝、未登录 401。
"""

import os

from app.core import fs_browse


def _mk_tree(tmp_path):
    """造一个目录树：root/alpha/、root/beta/、root/note.txt（文件不应出现）。"""
    root = tmp_path / "media"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    (root / "note.txt").write_text("x")
    (root / ".hidden").mkdir()  # 隐藏目录也应可见（用户可能就要选它）
    return root


def test_list_within_whitelist_lists_dirs_only(client, monkeypatch, tmp_path):
    root = _mk_tree(tmp_path)
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", str(root))

    r = client.get("/api/system/fs/list", params={"path": str(root)})
    assert r.status_code == 200, r.text
    body = r.json()
    names = {e["name"] for e in body["entries"]}
    assert names == {"alpha", "beta", ".hidden"}  # 文件 note.txt 不出现
    assert all(e["is_dir"] for e in body["entries"])
    assert body["path"] == os.path.normpath(str(root))
    # 根自身没有可用的「上一级」（越界即不给）
    assert body["parent"] is None


def test_list_empty_path_falls_back_to_first_root(client, monkeypatch, tmp_path):
    root = _mk_tree(tmp_path)
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", str(root))
    r = client.get("/api/system/fs/list")
    assert r.status_code == 200, r.text
    assert r.json()["path"] == os.path.normpath(str(root))


def test_list_rejects_outside_whitelist(client, monkeypatch, tmp_path):
    root = _mk_tree(tmp_path)
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", str(root))
    outside = tmp_path / "outside"
    outside.mkdir()
    r = client.get("/api/system/fs/list", params={"path": str(outside)})
    assert r.status_code == 400
    assert "范围" in r.json()["detail"]


def test_list_rejects_parent_traversal(client, monkeypatch, tmp_path):
    root = _mk_tree(tmp_path)
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", str(root))
    r = client.get("/api/system/fs/list", params={"path": str(root / ".." / "outside")})
    assert r.status_code == 400


def test_list_rejects_symlink_escape(client, monkeypatch, tmp_path):
    root = _mk_tree(tmp_path)
    outside = tmp_path / "secret"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", str(root))
    # 通过软链进到白名单外 → realpath 判断应拒绝
    r = client.get("/api/system/fs/list", params={"path": str(root / "link")})
    assert r.status_code == 400, r.text


def test_parent_offered_when_still_inside(client, monkeypatch, tmp_path):
    root = _mk_tree(tmp_path)
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", str(root))
    r = client.get("/api/system/fs/list", params={"path": str(root / "alpha")})
    assert r.status_code == 200, r.text
    assert r.json()["parent"] == os.path.normpath(str(root))


def test_roots_endpoint_reports_whitelist(client, monkeypatch, tmp_path):
    root = _mk_tree(tmp_path)
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", str(root))
    r = client.get("/api/system/fs/roots")
    assert r.status_code == 200, r.text
    paths = [x["path"] for x in r.json()["roots"]]
    assert os.path.normpath(str(root)) in paths


def test_contained_roots_survive_dedup(client, monkeypatch, tmp_path):
    root = _mk_tree(tmp_path)
    # 同一路径重复列两次，应去重
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", f"{root}{os.pathsep}{root}")
    roots = fs_browse.allowed_roots()
    assert len(roots) == 1


def test_browse_requires_admin(anon_client, monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_BROWSE_ROOTS", str(tmp_path))
    assert anon_client.get("/api/system/fs/list").status_code == 401
    assert anon_client.get("/api/system/fs/roots").status_code == 401
