"""Static SPA fallback must never serve files outside the build directory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import mount_frontend


def _app(tmp_path: Path) -> TestClient:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("INDEX", "utf-8")
    (static / "assets").mkdir()
    (static / "assets" / "a.js").write_text("JS", "utf-8")
    (tmp_path / "secret.txt").write_text("TOPSECRET", "utf-8")
    app = FastAPI()
    mount_frontend(app, static)
    return TestClient(app)


def test_encoded_parent_segments_cannot_escape_static_root(tmp_path):
    client = _app(tmp_path)
    for path in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/%2e%2e%2f%2e%2e%2fsecret.txt"):
        response = client.get(path)
        assert "TOPSECRET" not in response.text, path
        assert response.text == "INDEX", path


def test_real_asset_is_still_served(tmp_path):
    client = _app(tmp_path)
    response = client.get("/assets/a.js")
    assert response.status_code == 200
    assert response.text == "JS"


def test_null_byte_path_does_not_500(tmp_path):
    client = _app(tmp_path)
    for path in ("/..%00/secret.txt", "/%2e%2e%00/secret.txt"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "TOPSECRET" not in response.text, path


def test_mounting_after_import_replaces_dev_root_and_serves_spa(tmp_path):
    """The production host must not be shadowed by the dev JSON root route."""
    from app.main import app

    static = tmp_path / "late-static"
    static.mkdir()
    (static / "index.html").write_text("SPA-INDEX", "utf-8")
    (static / "assets").mkdir()
    (static / "assets" / "a.js").write_text("LATE-JS", "utf-8")

    mount_frontend(app, static)
    client = TestClient(app)
    root = client.get("/")
    assert root.headers["content-type"].startswith("text/html")
    assert root.text == "SPA-INDEX"
    assert client.get("/dashboard/deep-link").text == "SPA-INDEX"
    assert client.get("/assets/a.js").text == "LATE-JS"
