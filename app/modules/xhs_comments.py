"""小红书一级评论抓取：GET /api/sns/web/v2/comment/page（需 xsec_token），按点赞降序。"""

from __future__ import annotations

import asyncio

_COMMENT_PAGE = "/api/sns/web/v2/comment/page"
_COMMENT_SUB = "/api/sns/web/v2/comment/sub/page"
_HOST = "https://edith.xiaohongshu.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _normalize(it: dict, parent_comment_id: str = "0") -> dict:
    user = it.get("user_info") or {}
    image = user.get("image") or ""
    if image.startswith("//"):
        image = "https:" + image
    return {
        "comment_id": str(it.get("id", "")),
        "parent_comment_id": parent_comment_id,
        "user_id": str(user.get("user_id", "")),
        "nickname": user.get("nickname", ""),
        "avatar": image,
        "content": it.get("content", ""),
        "like_count": int(it.get("like_count") or 0),
        "ip_location": it.get("ip_location", ""),
        "created_at": it.get("create_time"),
    }


async def _fetch_sub_comments(client, note_id: str, xsec_token: str, root_id,
                              cookie: str, interval: float) -> list[dict]:
    """某条一级评论下的二级评论（/api/sns/web/v2/comment/sub/page）。"""
    from app.modules.crawler import xhs_sign

    out: list[dict] = []
    cursor = ""
    while True:
        params = {
            "note_id": note_id,
            "root_comment_id": str(root_id),
            "num": "10",
            "cursor": cursor,
            "image_formats": "jpg,webp,avif",
            "top_comment_id": "",
            "xsec_token": xsec_token,
        }
        sig = xhs_sign.sign(_COMMENT_SUB, params, cookie, "GET")
        resp = await client.get(f"{_HOST}{_COMMENT_SUB}", params=params, headers=sig)
        data = resp.json()
        if not data.get("success"):
            break
        payload = data.get("data") or {}
        rows = payload.get("comments") or []
        for it in rows:
            out.append(_normalize(it, parent_comment_id=str(root_id)))
        if not payload.get("has_more"):
            break
        cursor = str(payload.get("cursor") or "")
        if not rows or not cursor:
            break
        if interval:
            await asyncio.sleep(interval)
    return out


async def fetch_comments(
    *, note_id: str, xsec_token: str, cookie: str, pages: int = 2, per_page: int = 20,
    top_n: int = 20, interval: float = 1.0, with_sub_comments: bool = False,
) -> list[dict]:
    """抓小红书笔记一级评论。xsec_token 由搜索阶段留存。"""
    from app.core import rate_limit
    from app.modules.crawler import xhs_sign

    base_headers = {
        "User-Agent": _UA,
        "Referer": "https://www.xiaohongshu.com/",
        "Cookie": cookie,
        "Origin": "https://www.xiaohongshu.com",
    }
    collected: list[dict] = []
    cursor = ""
    async with rate_limit.http_client("xhs", headers=base_headers, timeout=20) as c:
        for _ in range(max(1, pages)):
            params = {
                "note_id": note_id,
                "cursor": cursor,
                "top_comment_id": "",
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
            }
            sig = xhs_sign.sign(_COMMENT_PAGE, params, cookie, "GET")
            resp = await c.get(f"{_HOST}{_COMMENT_PAGE}", params=params, headers={**base_headers, **sig})
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"小红书评论抓取失败: {data.get('msg', 'Cookie 可能失效')}")
            payload = data.get("data") or {}
            roots = payload.get("comments") or []
            for it in roots:
                collected.append(_normalize(it))
            if with_sub_comments:
                for it in roots:
                    root_id = it.get("id")
                    if not root_id or not int(it.get("sub_comment_count") or 0):
                        continue
                    collected.extend(
                        await _fetch_sub_comments(c, note_id, xsec_token, root_id, cookie, interval)
                    )
            if not payload.get("has_more"):
                break
            cursor = str(payload.get("cursor") or "")
            if not cursor:
                break
            if interval:
                await asyncio.sleep(interval)
    collected.sort(key=lambda x: x["like_count"], reverse=True)
    return collected[:top_n]
