"""B站多P（分P）解析：view.pages 每页有独立 cid，需逐个解析+下载。"""

import pytest


def _view_payload():
    return {"code": 0, "data": {
        "aid": 1, "cid": 100,
        "pages": [
            {"cid": 100, "page": 1, "part": "第一集", "duration": 60},
            {"cid": 101, "page": 2, "part": "第二集", "duration": 70},
        ],
    }}


def _play(cid):
    return {"code": 0, "data": {"dash": {
        "video": [{"id": 80, "bandwidth": 1000, "baseUrl": f"https://v/{cid}", "backupUrl": []}],
        "audio": [{"id": 30280, "bandwidth": 100, "baseUrl": f"https://a/{cid}", "backupUrl": []}],
    }}}


def _patch(monkeypatch):
    from app.modules import bilibili

    calls = []

    class Resp:
        def __init__(self, payload):
            self._p = payload
            self.status_code = 200

        def json(self):
            return self._p

    class Client:
        async def get(self, url, params=None, cookies=None):
            calls.append({"url": url, "params": params or {}})
            if "web-interface/view" in url:
                return Resp(_view_payload())
            return Resp(_play((params or {}).get("cid")))

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(bilibili, "_http", lambda: Ctx())
    return calls


@pytest.mark.asyncio
async def test_resolve_returns_pages(monkeypatch):
    from app.modules import bilibili

    _patch(monkeypatch)
    resolved = await bilibili.resolve_play_urls({"sessdata": "s"}, "BV1")
    assert resolved["cid"] == 100
    assert [p["cid"] for p in resolved["pages"]] == [100, 101]
    assert resolved["pages"][1]["part"] == "第二集"
    assert resolved["pages"][1]["video"] == ["https://v/101"]


@pytest.mark.asyncio
async def test_download_multi_pages_writes_each(monkeypatch, tmp_path):
    """多P视频应逐个分P下载并分别命名，而不是只下第一条。"""
    from app.workers import handlers
    from app.modules import media_download
    from app.models import ContentItem

    downloaded = []

    async def fake_download_dash(video, audio, out_dir, name, cookie="", *, merge=True):
        downloaded.append({"video": list(video), "name": name})
        return media_download.DownloadResult(ok=True, video_file=f"{name}.mp4")

    async def fake_extras(*a, **k):
        return None

    monkeypatch.setattr(media_download, "download_dash", fake_download_dash)
    monkeypatch.setattr(handlers, "_write_bili_extras", fake_extras)

    content = ContentItem(account_id=1, platform="bilibili", external_id="BV1",
                          title="合集", content_type="video")
    resolved = {
        "cid": 100,
        "video": ["https://v/100"], "audio": ["https://a/100"],
        "pages": [
            {"cid": 100, "page": 1, "part": "第一集", "duration": 60,
             "video": ["https://v/100"], "audio": ["https://a/100"]},
            {"cid": 101, "page": 2, "part": "第二集", "duration": 70,
             "video": ["https://v/101"], "audio": ["https://a/101"]},
        ],
    }
    result = await handlers._download_bili_pages(content, "SESSDATA=s", resolved, tmp_path)
    assert result.ok
    assert len(downloaded) == 2
    assert downloaded[0]["video"] == ["https://v/100"]
    assert downloaded[1]["video"] == ["https://v/101"]
    assert "第一集" in downloaded[0]["name"] and "第二集" in downloaded[1]["name"]
