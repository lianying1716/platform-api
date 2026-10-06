"""知乎关键词搜索（纯 Python + JS 签名直连，无 MediaCrawler 子进程）。

接口路径/参数与归一化字段来源：MediaCrawler media_platform/zhihu/client.py:get_note_by_keyword
与 media_platform/zhihu/help.py:ZhihuExtractor.extract_contents_from_search
（NON-COMMERCIAL LEARNING LICENSE 1.1, Copyright (c) 2024 relakkes@gmail.com，仅自用学习）

签名算法来源见 zhihu_sign.py（execjs 调 libs/zhihu.js 的 get_sign，同源，仅自用学习）。
真实抓取需有效 Cookie（须含 d_c0）；无 Cookie 或被风控时抛出可读异常，由上层记 error。
"""

import html
import re
from urllib.parse import urlencode

import httpx

from app.modules.crawler import zhihu_sign

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

    search_v3 接口 offset/lc_idx 翻页，paging.is_end 为真时停止。
    """
    sort_value = "created_time" if sort == "latest" else ""
    headers = {
        "User-Agent": _UA,
        "Referer": "https://www.zhihu.com/search",
        "Cookie": cookie,
        "x-zse-93": "101_3_3.0",
        "x-api-version": "3.0.91",
        "x-app-za": "OS=Web",
        "x-requested-with": "fetch",
    }
    items: list[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=20) as c:
        while len(items) < limit:
            offset = (page - 1) * _PAGE_SIZE
            params = {
                "gk_version": "gz-gaokao",
                "t": "general",
                "q": keyword,
                "correction": 1,
                "offset": offset,
                "limit": _PAGE_SIZE,
                "filter_fields": "",
                "lc_idx": offset,
                "show_all_topics": 0,
                "search_source": "Filter",
                "time_interval": "",
                "sort": sort_value,
                "vertical": "",
            }
            # 签名覆盖「路径+查询串」（与 MediaCrawler help.py 一致；服务器按此重建签名字符串）。
            # 手工拼查询串，避免 httpx 重编码导致签名与服务端不一致。
            query = urlencode(params)
            signed_url = f"{_SEARCH_PATH}?{query}"
            sig = zhihu_sign.sign(signed_url, cookie)
            resp = await c.get(f"https://www.zhihu.com{signed_url}", headers={**headers, **sig})
            try:
                data = resp.json()
            except ValueError:
                raise RuntimeError("知乎搜索失败：Cookie 可能失效或被风控")

            rows = data.get("data") or []
            before = len(items)
            for row in rows:
                norm = _normalize_item(row)
                if norm is None:
                    continue
                items.append(norm)
                if len(items) >= limit:
                    break

            paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
            if paging.get("is_end"):
                break
            # 防死循环：本页没有任何**可用**条目（全部被过滤/类型未知）时停止，
            # 否则 offset 持续推进会让 while 永不退出、拖死 worker。
            if not rows or len(items) == before:
                break
            page += 1
    return items[:limit]
