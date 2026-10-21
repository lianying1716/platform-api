"""知乎指标刷新：解析详情页内嵌的 ``js-initialData`` JSON。

知乎详情没有稳定的独立 XHR 指标接口，页面把数据内嵌在 ``js-initialData`` 里；
这里沿用受管浏览器取页面 HTML，再解析该 JSON，不复制签名实现。
"""

from __future__ import annotations

import html as _html
import json
import re

_INITIAL_DATA_RE = re.compile(
    r'<script[^>]*id="js-initialData"[^>]*>(.*?)</script>', re.DOTALL
)

_ENTITY_BY_TYPE = {
    "answer": "answers",
    "article": "articles",
    "video": "zvideos",
    "zvideo": "zvideos",
}


def extract_initial_data(page_html: str) -> dict | None:
    """从页面 HTML 抽出 js-initialData JSON；没有/解析失败返回 None。"""
    match = _INITIAL_DATA_RE.search(page_html or "")
    if not match:
        return None
    raw = _html.unescape(match.group(1)).strip()
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _to_int(value) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def metrics_from_html(page_html: str, content_type: str, content_id: str) -> dict | None:
    """从详情页解析指标。content_type: answer/article/video。"""
    data = extract_initial_data(page_html)
    if not data:
        return None
    entities = ((data.get("initialState") or {}).get("entities")) or {}
    bucket = entities.get(_ENTITY_BY_TYPE.get(content_type, "answers")) or {}
    entity = bucket.get(str(content_id))
    if not isinstance(entity, dict):
        if isinstance(bucket, dict) and len(bucket) == 1:
            entity = next(iter(bucket.values()))
        if not isinstance(entity, dict):
            return None
    return {
        "play": _to_int(entity.get("play_count")),
        "like": _to_int(entity.get("voteup_count")),
        "comment": _to_int(entity.get("comment_count")),
        "favorite": _to_int(entity.get("favlists_count")),
        "share": None,
    }


async def fetch_metrics(content_id: str, cookie: str, extra: dict | None = None) -> dict | None:
    """取知乎内容详情页并解析指标。"""
    from app.modules import browser_fetch

    extra = extra or {}
    url = extra.get("url") or ""
    if not url:
        return None
    content_type = extra.get("content_type") or _guess_type(url)
    page_html = await browser_fetch.fetch_page_html(
        page_url=url, cookie=cookie, domain=".zhihu.com", platform="zhihu"
    )
    return metrics_from_html(page_html, content_type, content_id)


def _guess_type(url: str) -> str:
    if "zhuanlan.zhihu.com/p/" in url:
        return "article"
    if "/zvideo/" in url:
        return "video"
    return "answer"
