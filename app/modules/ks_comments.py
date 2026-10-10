"""快手一级评论抓取：REST v2 ``/rest/v/photo/comment/list`` 分页，按点赞降序取 Top-N。"""

from __future__ import annotations

import asyncio

_COMMENT_LIST = "https://www.kuaishou.com/rest/v/photo/comment/list"
_COMMENT_SUB = "https://www.kuaishou.com/rest/v/photo/comment/sublist"


def _normalize_comment(it: dict, parent_comment_id: str = "0") -> dict:
    head = it.get("headurl") or it.get("headUrl") or ""
    if head.startswith("//"):
        head = "https:" + head
    return {
        "comment_id": str(it.get("commentId", "")),
        "parent_comment_id": parent_comment_id,
        "user_id": str(it.get("authorId", "")),
        "nickname": it.get("authorName", ""),
        "avatar": head,
        "content": it.get("content", ""),
        "like_count": int(it.get("likedCount") or it.get("realLikedCount") or 0),
        "ip_location": "",
        "created_at": it.get("timestamp"),
    }


async def _fetch_sub_comments(client, photo_id: str, root_id, interval: float) -> list[dict]:
    """某条一级评论下的二级评论（/rest/v/photo/comment/sublist）。"""
    out: list[dict] = []
    cursor = ""
    while True:
        resp = await client.post(
            _COMMENT_SUB,
            json={"photoId": str(photo_id), "pcursor": cursor, "rootCommentId": int(root_id)},
        )
        payload = resp.json() or {}
        if payload.get("result") != 1:
            break
        rows = payload.get("subCommentsV2") or payload.get("subComments") or []
        for it in rows:
            out.append(_normalize_comment(it, parent_comment_id=str(root_id)))
        cursor = str(payload.get("pcursorV2") or payload.get("pcursor") or "")
        if not rows or not cursor or cursor == "no_more":
            break
        if interval:
            await asyncio.sleep(interval)
    return out
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


async def fetch_comments(
    *, photo_id: str, cookie: str, pages: int = 2, per_page: int = 20,
    top_n: int = 20, interval: float = 1.0, with_sub_comments: bool = False,
) -> list[dict]:
    """抓快手作品评论：默认一级；with_sub_comments=True 时附带二级评论。"""
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
                collected.append(_normalize_comment(it))
            if with_sub_comments:
                for it in roots:
                    root_id = it.get("commentId")
                    if not root_id or not int(it.get("subCommentCount") or 0):
                        continue
                    collected.extend(await _fetch_sub_comments(c, photo_id, root_id, interval))
            cursor = str(payload.get("pcursorV2") or payload.get("pcursor") or "")
            if not cursor or cursor == "no_more":
                break
            if interval:
                await asyncio.sleep(interval)
    collected.sort(key=lambda x: x["like_count"], reverse=True)
    return collected[:top_n]
