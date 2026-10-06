"""微博关键词搜索（纯 Python 直连，无 MediaCrawler 子进程）。

接口路径/参数与归一化字段来源：MediaCrawler media_platform/weibo/client.py:get_note_by_keyword
与 media_platform/weibo/help.py:filter_search_result_card
（NON-COMMERCIAL LEARNING LICENSE 1.1, Copyright (c) 2024 relakkes@gmail.com，仅自用学习）

m.weibo.cn 走 H5 接口，**无独立签名**，只需有效移动端 Cookie；无 Cookie 或被风控时抛出可读异常，
由上层记 error。
"""

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

_HOST = "https://m.weibo.cn"
_SEARCH_PATH = "/api/container/getIndex"

# 移动端 UA（m.weibo.cn 的 H5 接口按移动端 UA 返回，PC UA 会被降级）。
_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

# 排序 -> search_type（与 MediaCrawler weibo/field.py:SearchType 一致）
_SORT_MAP = {"popular": "60", "latest": "61"}
_DEFAULT_SEARCH_TYPE = "1"  # 综合

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text) -> str:
    """去 HTML 标签并反转义。"""
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub("", str(text)))


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_iso(value) -> str | None:
    """RFC2822（如 ``Sat Dec 23 17:12:54 +0800 2023``）-> ISO UTC；失败返回 None。"""
    if not value:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S %Y"):
        try:
            dt = datetime.strptime(str(value), fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return None


def filter_search_result_card(card_list: list) -> list[dict]:
    """只保留 card_type==9 的卡片（含 card_group 内嵌的同类卡片）。"""
    notes: list[dict] = []
    for card in card_list or []:
        if not isinstance(card, dict):
            continue
        if card.get("card_type") == 9:
            notes.append(card)
        group = card.get("card_group")
        if isinstance(group, list):
            for item in group:
                if isinstance(item, dict) and item.get("card_type") == 9:
                    notes.append(item)
    return notes


def _normalize_card(card: dict) -> dict | None:
    """把单张 type-9 卡片归一化为与 crawl_service 约定的 item dict；缺 mblog/id 则丢弃。"""
    mblog = card.get("mblog")
    if not isinstance(mblog, dict):
        return None
    note_id = mblog.get("id")
    if not note_id:
        return None

    user = mblog.get("user") if isinstance(mblog.get("user"), dict) else {}
    page_info = mblog.get("page_info") if isinstance(mblog.get("page_info"), dict) else {}
    pics = mblog.get("pics") if isinstance(mblog.get("pics"), list) else []

    object_type = str(page_info.get("object_type") or page_info.get("type") or "").lower()
    if object_type in {"video", "story"}:
        content_type = "video"
    elif pics:
        content_type = "image"
    else:
        content_type = "article"

    cover_url = ""
    if content_type == "video":
        cover_url = ((page_info.get("page_pic") or {}).get("url") or "") if isinstance(
            page_info.get("page_pic"), dict
        ) else ""
    if not cover_url and pics:
        first = pics[0] if isinstance(pics[0], dict) else {}
        cover_url = first.get("url") or ""

    return {
        "external_id": str(note_id),
        "content_type": content_type,
        "title": _clean(mblog.get("text")),
        "author_name": user.get("screen_name") or "",
        "author_id": str(user.get("id") or ""),
        "url": f"https://m.weibo.cn/detail/{note_id}",
        "cover_url": cover_url,
        "published_at": _to_iso(mblog.get("created_at")),
        "metrics": {
            "like": _to_int(mblog.get("attitudes_count")),
            "comment": _to_int(mblog.get("comments_count")),
            "share": _to_int(mblog.get("reposts_count")),
            "play": 0,
        },
    }


async def search_weibo(keyword: str, cookie: str, limit: int, sort: str) -> list[dict]:
    """按关键词搜索微博，返回归一化 item 列表（最多 limit 条）。

    containerid 携带 search_type 与关键词，page 递增翻页；cards 为空 / 本页无可用条目时停止。
    """
    search_type = _SORT_MAP.get(sort, _DEFAULT_SEARCH_TYPE)
    headers = {
        "User-Agent": _UA,
        "Cookie": cookie,
        "Referer": "https://m.weibo.cn/",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
    }
    items: list[dict] = []
    page = 1
    async with httpx.AsyncClient(headers=headers, timeout=20) as c:
        while len(items) < limit:
            params = {
                "containerid": f"100103type={search_type}&q={keyword}",
                "page_type": "searchall",
                "page": page,
            }
            resp = await c.get(f"{_HOST}{_SEARCH_PATH}?{urlencode(params)}")
            try:
                data = resp.json()
            except ValueError:
                raise RuntimeError("微博搜索失败：Cookie 可能失效或被风控")
            if data.get("ok") not in (1, "1"):
                raise RuntimeError(f"微博搜索失败: {data.get('msg', 'Cookie 可能失效或被风控')}")

            cards = (data.get("data") or {}).get("cards") or []
            before = len(items)
            for card in filter_search_result_card(cards):
                norm = _normalize_card(card)
                if norm is None:
                    continue
                items.append(norm)
                if len(items) >= limit:
                    break

            # 防死循环：本页没有新增**可用**条目（无 type-9 卡片 / 字段漂移）时停止，
            # 否则 page 持续推进会让 while 永不退出、拖死 worker。
            if not cards or len(items) == before:
                break
            page += 1
    return items[:limit]
