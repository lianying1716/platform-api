"""正式包静态托管：缓存头必须正确，否则升级后用户看到旧界面。

回归背景：/assets 与 index.html 都只带 ETag、没有 Cache-Control，浏览器按启发式
把 index.html 缓存住，升级后仍加载旧的 hashed bundle——用户在新版本里看到旧 UI
（实测踩到：升级到新版后前端仍显示"手填版本号"的旧升级交互）。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import mount_frontend


def _client(tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(
        '<!doctype html><script src="/assets/index-ABC123.js"></script>', encoding="utf-8"
    )
    (static / "assets" / "index-ABC123.js").write_text("console.log(1)", encoding="utf-8")
    app = FastAPI()
    mount_frontend(app, static)
    return TestClient(app)


def test_index_html_is_no_cache(tmp_path):
    """index.html 必须 no-cache：升级后要能拿到引用新 bundle 的新 index。"""
    with _client(tmp_path) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "no-cache" in r.headers.get("cache-control", "")


def test_hashed_asset_is_immutable(tmp_path):
    """带 hash 的 /assets 资源可长缓存 immutable。"""
    with _client(tmp_path) as c:
        r = c.get("/assets/index-ABC123.js")
        assert r.status_code == 200
        cc = r.headers.get("cache-control", "")
        assert "immutable" in cc and "max-age=31536000" in cc


def test_spa_fallback_is_no_cache(tmp_path):
    """深链回退也回 index.html，同样 no-cache。"""
    with _client(tmp_path) as c:
        r = c.get("/settings/deep/link")
        assert r.status_code == 200
        assert "no-cache" in r.headers.get("cache-control", "")


def test_missing_static_dir_raises(tmp_path):
    """开启托管但产物不存在时明确报错，而不是静默返回空白。"""
    import pytest

    with pytest.raises(RuntimeError):
        mount_frontend(FastAPI(), tmp_path / "nope")
