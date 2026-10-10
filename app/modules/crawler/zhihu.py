"""知乎关键词搜索。

搜索响应由知乎页面自身的签名逻辑产生：本模块在受管浏览器里打开搜索页并监听
平台自己发出的 ``search_v3`` 响应，不打包或执行第三方签名脚本。
真实抓取需有效 Cookie（须含 d_c0）；无 Cookie 或被风控时抛出可读异常，由上层记 error。
"""

import html
import re

_SEARCH_PATH = "/api/v4/search_v3"  # 签名只覆盖路径+查询串（不含 host）

# 与 client._UA 保持一致；各平台模块各自持有常量，避免与 client 相互 import。
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

_PAGE_SIZE = 20

# 知乎 object.type -> 归一化 content_type（answer/article/video）
_TYPE_MAP = {"answer": "answer", "article": "article", "zvideo": "video"}

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text) -> str:
    """去 HTML 标签并反转义，用于标题等展示文本（对应 extract_text_from_html）。"""
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub("", str(text)))


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_item(item: dict) -> dict | None:
    """把单条搜索结果归一化为与 crawl_service 约定的 item dict；缺 id/类型未知则丢弃。"""
    if not isinstance(item, dict):
        return None
    obj = item.get("object")
    if not isinstance(obj, dict):
        obj = item
    content_type = obj.get("type") or item.get("type")
    if content_type not in _TYPE_MAP:
        return None
    content_id = obj.get("id")
    if not content_id:
        return None

    highlight = item.get("highlight") if isinstance(item.get("highlight"), dict) else {}
    author = obj.get("author")
    if not isinstance(author, dict):
        author = {}
    if not author.get("id") and isinstance(author.get("member"), dict):
        author = author["member"]

    metrics = {
        "like": _to_int(obj.get("voteup_count")),
        "comment": _to_int(obj.get("comment_count")),
        "play": 0,
    }

    if content_type == "answer":
        question = obj.get("question") if isinstance(obj.get("question"), dict) else {}
        question_id = question.get("id") or obj.get("question_id")
        url = (
            f"https://www.zhihu.com/question/{question_id}/answer/{content_id}"
            if question_id
            else f"https://www.zhihu.com/answer/{content_id}"
        )
        title = obj.get("title") or question.get("title") or highlight.get("title") or ""
        cover_url = ""
    elif content_type == "article":
        url = f"https://zhuanlan.zhihu.com/p/{content_id}"
        title = obj.get("title") or highlight.get("title") or ""
        cover_url = obj.get("image_url") or obj.get("title_image") or ""
    else:  # zvideo
        video = obj.get("video") if isinstance(obj.get("video"), dict) else {}
        url = obj.get("video_url") or f"https://www.zhihu.com/zvideo/{content_id}"
        title = obj.get("title") or highlight.get("title") or ""
        cover_url = video.get("thumbnail") or obj.get("thumbnail") or obj.get("cover_url") or ""
        metrics["play"] = _to_int(video.get("play_count") or obj.get("play_count"))

    return {
        "external_id": str(content_id),
        "content_type": _TYPE_MAP[content_type],
        "title": _clean(title),
        "author_name": author.get("name") or "",
        "author_id": str(author.get("id") or author.get("url_token") or ""),
        "url": url,
        "cover_url": cover_url,
        "metrics": metrics,
    }


async def search_zhihu(keyword: str, cookie: str, limit: int, sort: str) -> list[dict]:
    """按关键词搜索知乎内容（回答/文章/视频），返回归一化 item 列表（最多 limit 条）。

    通过受管浏览器打开搜索页并监听平台自身的 search_v3 响应，由平台页面完成签名。
    """
    from app.modules import browser_fetch

    sort_value = "created_time" if sort == "latest" else ""
    search_url = browser_fetch.build_search_url("https://www.zhihu.com", keyword, sort)
    pages = max(1, (limit + _PAGE_SIZE - 1) // _PAGE_SIZE)
    responses = await browser_fetch.fetch_search(
        search_url=search_url,
        api_token="/api/v4/search_v3",
        cookie=cookie,
        domain=".zhihu.com",
        pages=pages,
    )

    items: list[dict] = []
    for data in responses:
        for row in data.get("data") or []:
            norm = _normalize_item(row)
            if norm is None:
                continue
            items.append(norm)
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
    return items[:limit]
