"""外部 Skill API `/api/skills/import-article` 的验收测试。

覆盖三点：
- scope 控权：只有 read 的 Key 被拒（403），operate 的 Key 放行；
- 错误翻译：非法 URL（非微信域名）返回 422，且不触网；
- 调用审计：成功/失败各落一行 api_call_log（tool=import_article，status=ok/error）。

网络隔离：monkeypatch `app.modules.article_import.fetch_article`，
成功路径直接喂 tests/fixtures/wechat_article.html 的解析结果。
"""

from pathlib import Path

import pytest
from sqlalchemy import select

FIXTURE = Path(__file__).parent / "fixtures" / "wechat_article.html"
VALID_URL = "https://mp.weixin.qq.com/s/AbCdEf123456"
BAD_URL = "https://evil.example.com/s/x"


@pytest.fixture(autouse=True)
def _reset_rate_window():
    """每个测试重置进程内限流窗口，避免内存库主键复用导致跨测试串扰。"""
    from app.core import api_auth

    api_auth._rate_window.clear()
    yield
    api_auth._rate_window.clear()


def _make_key(client, scopes: list[str]) -> tuple[int, str]:
    """用管理员端点创建 API Key，返回 (id, 明文 key)。"""
    r = client.post("/api/ai/keys", json={"name": "skills-test", "scopes": scopes})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], body["api_key"]


def _bearer(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def _fake_fetch(html: str):
    """伪造抓取：先做真实 URL 校验（非法即抛），合法则解析本地 fixture。"""

    async def fake_fetch(url: str, cookie: str = ""):
        from app.modules.article_import import normalize_article_url, parse_article_html

        normalize_article_url(url)  # 非微信/非 https 在这里抛 ArticleImportError
        return parse_article_html(html, url)

    return fake_fetch


def test_import_article_rejects_read_only_scope(client):
    """只有 read scope 的 Key 不能触发导入（需要 operate/admin）。"""
    _, raw = _make_key(client, ["read"])
    r = client.post("/api/skills/import-article", json={"url": VALID_URL}, headers=_bearer(raw))
    assert r.status_code == 403, r.text


def test_import_article_accepts_operate_scope(client, monkeypatch):
    """operate scope 的 Key 可以导入文章，返回 asset/content id。"""
    html = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr("app.modules.article_import.fetch_article", _fake_fetch(html))
    _, raw = _make_key(client, ["operate"])

    r = client.post("/api/skills/import-article", json={"url": VALID_URL}, headers=_bearer(raw))
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["asset_id"], int) and isinstance(body["content_id"], int)


def test_import_article_bad_url_returns_422(client, monkeypatch):
    """非法域名（非 mp.weixin.qq.com）应翻译成 422，且不触网。"""
    html = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr("app.modules.article_import.fetch_article", _fake_fetch(html))
    _, raw = _make_key(client, ["operate"])

    r = client.post("/api/skills/import-article", json={"url": BAD_URL}, headers=_bearer(raw))
    assert r.status_code == 422, r.text
    assert "微信" in r.text


def test_import_article_writes_ok_and_error_call_logs(client, monkeypatch, session):
    """成功 + 失败各调用一次，api_call_log 应各落一行（tool=import_article）。"""
    from app.models import ApiCallLog

    html = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr("app.modules.article_import.fetch_article", _fake_fetch(html))
    key_id, raw = _make_key(client, ["operate"])

    ok = client.post("/api/skills/import-article", json={"url": VALID_URL}, headers=_bearer(raw))
    assert ok.status_code == 200, ok.text
    bad = client.post("/api/skills/import-article", json={"url": BAD_URL}, headers=_bearer(raw))
    assert bad.status_code == 422, bad.text

    session.expire_all()
    logs = session.scalars(select(ApiCallLog).where(ApiCallLog.tool == "import_article")).all()
    statuses = {log.status for log in logs}
    assert statuses == {"ok", "error"}, [log.status for log in logs]
    assert all(log.key_id == key_id for log in logs)
    assert any(log.params_summary.startswith(VALID_URL) for log in logs)
    assert any(log.params_summary.startswith(BAD_URL) for log in logs)
