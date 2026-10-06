"""百度贴吧关键词搜索（纯 Python 直连，无 MediaCrawler 子进程）。

接口路径/参数与归一化字段来源：MediaCrawler media_platform/tieba/client.py:get_notes_by_keyword
与 media_platform/tieba/help.py:TieBaExtractor.extract_search_note_list_from_api
（NON-COMMERCIAL LEARNING LICENSE 1.1, Copyright (c) 2024 relakkes@gmail.com，仅自用学习）

PC 搜索接口 /mo/q/search/multsearch 需要 PC 签名（见 tieba_sign.py，纯 MD5），
真实抓取需有效 Cookie；无 Cookie 或被风控时抛出可读异常，由上层记 error。
"""

from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from app.modules.crawler import tieba_sign

_HOST = "https://tieba.baidu.com"
_SEARCH_PATH = "/mo/q/search/multsearch"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# 排序 -> st（与 MediaCrawler tieba/field.py:SearchSortType 一致）
_SORT_MAP = {"popular": "2", "latest": "1"}
_DEFAULT_SORT = "2"  # 相关度

_PAGE_SIZE = 20


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_iso(value) -> str | None:
    """Unix 秒（或毫秒）-> ISO UTC；缺失/非法返回 None。"""
    ts = _to_int(value)
    if ts <= 0:
        return None
    if ts > 1000000000000:  # 毫秒
        ts //= 1000
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_thread(card: dict) -> dict | None:
    """把单条 thread 卡片归一化为与 crawl_service 约定的 item dict；缺 tid 则丢弃。"""
    item = card.get("data") if isinstance(card.get("data"), dict) else {}
    tid = item.get("tid")
    if not tid:
        return None
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    forum = (item.get("forum_name") or "").strip()
    return {
        "external_id": str(tid),
        "content_type": "article",
        "title": (item.get("title") or "").strip(),
        "author_name": user.get("show_nickname") or user.get("user_name") or "",
        "author_id": str(user.get("portrait") or user.get("id") or ""),
        "url": f"{_HOST}/p/{tid}",
        "cover_url": "",
        "published_at": _to_iso(item.get("time") or item.get("create_time")),
        "extra": {"forum_name": forum} if forum else {},
        "metrics": {
            "play": 0,
            "like": 0,
            "comment": _to_int(item.get("post_num")),
        },
    }


def _is_thread_card(card: dict) -> bool:
    return card.get("cardInfo") == "thread" or card.get("cardStyle") == "thread"


async def search_tieba(keyword: str, cookie: str, limit: int, sort: str) -> list[dict]:
    """按关键词搜索贴吧帖子，返回归一化 item 列表（最多 limit 条）。

    pn 递增翻页；card_list 为空 / 本页无可用条目时停止。
    """
    st = _SORT_MAP.get(sort, _DEFAULT_SORT)
    headers = {
        "User-Agent": _UA,
        "Cookie": cookie,
        "Referer": "https://tieba.baidu.com/",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    items: list[dict] = []
    page = 1
    async with httpx.AsyncClient(headers=headers, timeout=20) as c:
        while len(items) < limit:
            params = {
                "rn": max(_PAGE_SIZE, 20),
                "st": st,
                "word": keyword,
                "needbrand": 1,
                "sug_type": 2,
                "pn": page,
                "come_from": "search",
                "subapp_type": "pc",
                "_client_type": "20",
            }
            params["sign"] = tieba_sign.sign(params)
            resp = await c.get(f"{_HOST}{_SEARCH_PATH}?{urlencode(params)}")
            try:
                data = resp.json()
            except ValueError:
                raise RuntimeError("贴吧搜索失败：Cookie 可能失效或被风控")

            error_code = data.get("error_code", data.get("no", 0))
            if str(error_code) not in {"0", "None"}:
                raise RuntimeError(f"贴吧搜索失败: {data.get('error') or data.get('error_msg') or error_code}")

            card_list = (data.get("data") or {}).get("card_list") or []
            before = len(items)
            for card in card_list:
                if not isinstance(card, dict) or not _is_thread_card(card):
                    continue
                norm = _normalize_thread(card)
                if norm is None:
                    continue
                items.append(norm)
                if len(items) >= limit:
                    break

            # 防死循环：本页没有新增**可用**条目（无 thread 卡片 / 字段漂移）时停止，
            # 否则 pn 持续推进会让 while 永不退出、拖死 worker。
            if not card_list or len(items) == before:
                break
            page += 1
    return items[:limit]
