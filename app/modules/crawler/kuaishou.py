"""快手关键词搜索（纯 Python GraphQL 直连，无 MediaCrawler 子进程）。

接口路径/参数与归一化字段来源：MediaCrawler media_platform/kuaishou/client.py:search_info_by_keyword
与 media_platform/kuaishou/core.py:KuaishouCrawler.search
（NON-COMMERCIAL LEARNING LICENSE 1.1, Copyright (c) 2024 relakkes@gmail.com，仅自用学习）

GraphQL 查询文档随包携带于 graphql/kuaishou/search_query.graphql（同源，仅自用学习）。
真实抓取需有效 Cookie；无 Cookie 或被风控时抛出可读异常，由上层记 error。
"""

import asyncio
import functools
from pathlib import Path

import httpx

_GRAPHQL_URL = "https://www.kuaishou.com/graphql"
_GRAPHQL_PATH = Path(__file__).parent / "graphql" / "kuaishou" / "search_query.graphql"

# 与 client._UA 保持一致；各平台模块各自持有常量，避免与 client 相互 import。
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# 翻页间隔（秒），避免请求过于频繁。
_PAGE_SLEEP = 1.0


@functools.lru_cache(maxsize=1)
def _search_query() -> str:
    """读取并缓存打包的 GraphQL 查询文本（仅读一次）。"""
    return _GRAPHQL_PATH.read_text(encoding="utf-8")


def _normalize_feed(feed: dict) -> dict | None:
    """把单条 feed 归一化为与 crawl_service 约定的 item dict；缺 id 则丢弃。"""
    photo = feed.get("photo") or {}
    author = feed.get("author") or {}
    photo_id = photo.get("id")
    if not photo_id:
        return None
    return {
        "external_id": str(photo_id),
        "content_type": "video",
        "title": photo.get("caption") or "",
        "author_name": author.get("name") or "",
        "author_id": str(author.get("id") or ""),
        "url": f"https://www.kuaishou.com/short-video/{photo_id}",
        "cover_url": photo.get("coverUrl") or "",
        "metrics": {
            "play": photo.get("viewCount", 0),
            "like": photo.get("likeCount", 0),
            "comment": photo.get("commentCount", 0),
        },
    }


async def search_kuaishou(keyword: str, cookie: str, limit: int, sort: str) -> list[dict]:
    """按关键词搜索快手视频，返回归一化 item 列表（最多 limit 条）。

    GraphQL operationName=visionSearchPhoto，pcursor 翻页，searchSessionId 跨页携带。
    """
    headers = {
        "User-Agent": _UA,
        "Cookie": cookie,
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.kuaishou.com",
        "Referer": "https://www.kuaishou.com",
    }
    query = _search_query()
    items: list[dict] = []
    cursor = "1"
    prev_cursor = ""
    session_id = ""
    async with httpx.AsyncClient(headers=headers, timeout=20) as c:
        while len(items) < limit:
            body = {
                "operationName": "visionSearchPhoto",
                "variables": {
                    "keyword": keyword,
                    "pcursor": cursor,
                    "page": "search",
                    "searchSessionId": session_id,
                },
                "query": query,
            }
            resp = await c.post(_GRAPHQL_URL, json=body)
            payload = resp.json() or {}
            # GraphQL 响应为 {"data": {"visionSearchPhoto": ...}}；兼容直接给内层的构造
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            result = data.get("visionSearchPhoto") or {}
            if result.get("result") != 1 or result.get("feeds") is None:
                raise RuntimeError(
                    "快手搜索失败：Cookie 可能失效或被风控（result != 1 或缺失 feeds）"
                )
            session_id = result.get("searchSessionId") or session_id
            for feed in result.get("feeds") or []:
                norm = _normalize_feed(feed)
                if norm is None:
                    continue
                items.append(norm)
                if len(items) >= limit:
                    break
            cursor = result.get("pcursor") or ""
            if not cursor or cursor == "no_more":
                break
            # 防死循环：游标原地不动（服务端软限流/异常）时立即停止，避免 worker 无限翻页。
            if cursor == prev_cursor:
                break
            prev_cursor = cursor
            if len(items) < limit:
                await asyncio.sleep(_PAGE_SLEEP)
    return items[:limit]
