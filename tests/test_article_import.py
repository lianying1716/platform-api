"""单篇公众号文章导入：URL 校验、解析、入库 API。"""
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "wechat_article.html"


def test_normalize_rejects_non_wechat_host():
    from app.modules.article_import import ArticleImportError, normalize_article_url

    with pytest.raises(ArticleImportError):
        normalize_article_url("https://evil.example.com/s/abc")


def test_normalize_rejects_http_and_non_article_path():
    from app.modules.article_import import ArticleImportError, normalize_article_url

    with pytest.raises(ArticleImportError):
        normalize_article_url("http://mp.weixin.qq.com/s/abc")
    with pytest.raises(ArticleImportError):
        normalize_article_url("https://mp.weixin.qq.com/cgi-bin/home")


def test_normalize_keeps_query_and_strips_fragment():
    from app.modules.article_import import normalize_article_url

    u = normalize_article_url("https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1#wechat_redirect")
    assert u == "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1"


def test_parse_extracts_fields():
    from app.modules.article_import import parse_article_html

    html = FIXTURE.read_text(encoding="utf-8")
    out = parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")
    assert out["title"] == "测试文章标题"
    assert out["author_name"] == "测试公众号"
    assert out["cover_url"] == "https://mmbiz.qpic.cn/cover.jpg"
    assert "第一段正文" in out["text"]
    assert "第二段正文" in out["text"]
    assert out["external_id"]
    assert out["published_at"] is not None
    assert "js_content" in out["html"]


def test_parse_falls_back_title_to_og_when_activity_name_missing():
    from app.modules.article_import import parse_article_html

    html = FIXTURE.read_text(encoding="utf-8").replace('id="activity-name"', 'id="x"')
    out = parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")
    assert out["title"] == "测试文章标题"
