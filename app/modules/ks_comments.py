"""快手一级评论抓取：REST v2 ``/rest/v/photo/comment/list`` 分页，按点赞降序取 Top-N。"""

from __future__ import annotations

import asyncio

_COMMENT_LIST = "https://www.kuaishou.com/rest/v/photo/comment/list"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


async def fetch_comments(
    *, photo_id: str, cookie: str, pages: int = 2, per_page: int = 20,
    top_n: int = 20, interval: float = 1.0,
) -> list[dict]:
    """抓快手作品一级评论。REST v2 以 result==1 判定成功。"""
    from app.core import rate_limit

    headers = {
        "User-Agent": _UA,
        "Cookie": cookie,
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.kuaishou.com",
        "Referer": "https://www.kuaishou.com",
    }
    collected: list[dict] = []
    cursor = ""
    async with rate_limit.http_client("kuaishou", headers=headers, timeout=20) as c:
        for _ in range(max(1, pages)):
            body = {"photoId": str(photo_id), "pcursor": cursor}
            resp = await c.post(_COMMENT_LIST, json=body)
            payload = resp.json() or {}
            if payload.get("result") != 1:
                raise RuntimeError(f"快手评论抓取失败: {payload.get('message', payload.get('result'))}")
            roots = payload.get("rootCommentsV2") or payload.get("rootComments") or []
            for it in roots:
                head = it.get("headurl") or it.get("headUrl") or ""
                if head.startswith("//"):
                    head = "https:" + head
                collected.append({
                    "comment_id": str(it.get("commentId", "")),
                    "parent_comment_id": "0",
                    "user_id": str(it.get("authorId", "")),
                    "nickname": it.get("authorName", ""),
                    "avatar": head,
                    "content": it.get("content", ""),
                    "like_count": int(it.get("likedCount") or it.get("realLikedCount") or 0),
                    "ip_location": "",
                    "created_at": it.get("timestamp"),
                })
            cursor = str(payload.get("pcursorV2") or payload.get("pcursor") or "")
            if not cursor or cursor == "no_more":
                break
            if interval:
                await asyncio.sleep(interval)
    collected.sort(key=lambda x: x["like_count"], reverse=True)
    return collected[:top_n]
