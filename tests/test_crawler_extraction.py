"""关键词抓取抽离的专项测试（PRD 第二阶段）。

验证：
1. 三平台签名纯 Python 可算（不出网、不依赖浏览器）。
2. crawler.search 对各平台的 HTTP 响应归一化解析正确（mock httpx，不发真实请求）。
3. 不支持的平台给出可读错误。
"""

import httpx
import pytest


def test_bili_wbi_sign_pure_python():
    from app.modules.crawler import bili_sign

    signed = bili_sign.sign({"keyword": "露营", "page": 1}, "i" * 32, "s" * 32)
    assert len(signed["w_rid"]) == 32  # md5
    assert "wts" in signed


def test_douyin_signature_uses_browser_same_origin_module():
    """抖音签名不再本地打包/执行，改由受管浏览器同源请求完成。"""
    from app.modules import douyin_browser_fetch

    assert callable(douyin_browser_fetch.fetch_json)


def test_xhs_sign_via_xhshow():
    from app.modules.crawler import xhs_sign

    h = xhs_sign.sign("/api/sns/web/v1/search/notes", {"keyword": "x"}, "a1=abc;web_session=z", "POST")
    assert h["x-s"] and h["x-t"]


@pytest.mark.asyncio
async def test_search_bilibili_normalizes(monkeypatch):
    """mock B站 nav + 搜索响应，验证归一化字段正确。"""
    from app.modules.crawler import bili_sign, client

    async def fake_wbi(cookies=None):
        return ("a" * 32, "b" * 32)  # 真实 WBI key 为 32 位 hex

    monkeypatch.setattr(bili_sign, "fetch_wbi_keys", fake_wbi)

    nav_call = {"n": 0}

    class FakeResp:
        def json(self):
            return {
                "code": 0,
                "data": {
                    "result": [
                        {
                            "bvid": "BV1xx", "title": '露营<em class="keyword">装备</em>',
                            "author": "UP主A", "mid": 123, "pic": "//i.hdslb.com/x.jpg",
                            "play": 5000, "like": 300, "review": 20,
                        }
                    ]
                },
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, cookies=None):
            nav_call["n"] += 1
            # 第二页返回空，终止分页
            if nav_call["n"] > 1:
                class Empty:
                    def json(self):
                        return {"code": 0, "data": {"result": []}}
                return Empty()
            return FakeResp()

    monkeypatch.setattr(client.httpx, "AsyncClient", FakeClient)

    items = await client.search_bilibili("露营", "SESSDATA=x", limit=10, sort="popular")
    assert len(items) == 1
    it = items[0]
    assert it["external_id"] == "BV1xx"
    assert it["title"] == "露营装备"  # <em> 标签已清除
    assert it["url"] == "https://www.bilibili.com/video/BV1xx"
    assert it["cover_url"].startswith("https://")  # // 补全协议
    assert it["metrics"]["play"] == 5000


@pytest.mark.asyncio
async def test_search_requires_cookie():
    from app.modules import crawler

    with pytest.raises(RuntimeError, match="需要有效 Cookie"):
        await crawler.search(platform="bilibili", keyword="x", cookie="", limit=5)


@pytest.mark.asyncio
async def test_unsupported_platform_raises():
    """全部平台已原生；未知平台直接抛可读异常（不再有子进程兜底）。"""
    from app.modules import crawler

    assert crawler._NATIVE == {"bilibili", "douyin", "xhs", "kuaishou", "zhihu"}
    assert not hasattr(crawler, "_search_via_subprocess")
    with pytest.raises(ValueError, match="不支持的采集平台"):
        await crawler.search(platform="unknown_platform", keyword="x", cookie="", limit=5)
