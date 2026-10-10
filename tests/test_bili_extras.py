"""B站媒体库增强：字幕 SRT、弹幕 XML、NFO 生成（mock httpx，不联网）。"""

import pytest


def test_srt_time_format():
    from app.modules import bili_extras

    assert bili_extras._srt_time(0.0) == "00:00:00,000"
    assert bili_extras._srt_time(3661.5) == "01:01:01,500"


def test_build_srt_from_subtitle_body():
    from app.modules import bili_extras

    body = [
        {"from": 0.0, "to": 1.5, "content": "第一句"},
        {"from": 1.5, "to": 3.0, "content": "第二句"},
    ]
    srt = bili_extras.build_srt(body)
    assert "1\n00:00:00,000 --> 00:00:01,500\n第一句" in srt
    assert "2\n00:00:01,500 --> 00:00:03,000\n第二句" in srt


def test_danmaku_xml_from_legacy_payload():
    """旧版弹幕 XML 接口：<d p="时间,模式,...">文本</d>，需转标准 XML。"""
    from app.modules import bili_extras

    raw = '<i><d p="1.5,1,25,16711680,0,0,0,0">哈哈</d><d p="2.0,1,25,0,0,0,0,0">牛</d></i>'
    xml = bili_extras.build_danmaku_xml(raw)
    assert "<d p=\"1.500,1,25,16711680" in xml
    assert "哈哈" in xml and "牛" in xml


def test_nfo_contains_core_fields():
    from app.modules import bili_extras

    nfo = bili_extras.build_nfo(
        title="露营装备",
        plot="视频简介",
        author="UP主A",
        published_at="2024-01-01T00:00:00",
        bvid="BV1xx",
        tags=["露营", "户外"],
    )
    assert "<title>露营装备</title>" in nfo
    assert "<plot>视频简介</plot>" in nfo
    assert "<studio>UP主A</studio>" in nfo
    assert "<uniqueid type=\"bilibili\">BV1xx</uniqueid>" in nfo
    assert "<tag>露营</tag>" in nfo


@pytest.mark.asyncio
async def test_fetch_subtitles_writes_srt(tmp_path, monkeypatch):
    from app.modules import bili_extras

    class Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    calls = []

    class Client:
        async def get(self, url, params=None, cookies=None):
            calls.append(url)
            if "player/wbi/v2" in url:
                return Resp({"code": 0, "data": {"subtitle": {"subtitles": [
                    {"lan": "zh-CN", "subtitle_url": "//aisubtitle.hdslb.com/bfs/subtitle/x.json"},
                    {"lan": "ai-CN", "subtitle_url": "//aisubtitle.hdslb.com/bfs/ai_subtitle/y.json"},
                ]}}})
            return Resp({"body": [{"from": 0.0, "to": 1.0, "content": "你好"}]})

    monkeypatch.setattr(bili_extras, "_http", lambda cred: _ctx(Client()))
    cred = {"sessdata": "s", "bili_jct": "j"}
    files = await bili_extras.fetch_subtitles(cred, "BV1", 123, tmp_path)
    assert len(files) == 1, "AI 字幕应被过滤"
    assert files[0].endswith("zh-CN.srt")
    assert "你好" in (tmp_path / "BV1.zh-CN.srt").read_text(encoding="utf-8")


class _ctx:
    def __init__(self, client):
        self._c = client

    async def __aenter__(self):
        return self._c

    async def __aexit__(self, *a):
        return False
