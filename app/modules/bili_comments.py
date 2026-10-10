"""B站一级评论抓取：bvid -> aid -> /x/v2/reply/wbi/main 分页，按点赞降序取 Top-N。

B站评论接口的 oid 必须用 aid（数字 ID）而非 bvid；接口参数按公开契约实现，
cookie 与限速沿用本仓库 B站通道。
"""

from __future__ import annotations

import asyncio

_VIEW = "https://api.bilibili.com/x/web-interface/view"
_REPLY_MAIN = "https://api.bilibili.com/x/v2/reply/wbi/main"


async def _resolve_aid(client, credential: dict, bvid: str) -> int:
    from app.modules.bilibili import _cookies

    resp = await client.get(_VIEW, params={"bvid": bvid}, cookies=_cookies(credential))
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站视频信息拉取失败: {data.get('message', data.get('code'))}")
    aid = (data.get("data") or {}).get("aid")
    if not aid:
        raise RuntimeError("B站视频信息缺少 aid，无法抓评论")
    return int(aid)


async def fetch_comments(
    *, bvid: str, cookie: str, pages: int = 2, per_page: int = 20,
    top_n: int = 20, interval: float = 1.0,
) -> list[dict]:
    """抓 B站作品一级评论：按点赞降序取 top_n。"""
    from app.modules.bilibili import _cookies, _http

    credential: dict = {}
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k in ("SESSDATA", "bili_jct", "buvid3", "DedeUserID", "ac_time_value"):
            credential[k.lower() if k != "DedeUserID" else "dedeuserid"] = v.strip()

    collected: list[dict] = []
    async with _http() as client:
        aid = await _resolve_aid(client, credential, bvid)
        next_page = 0
        for _ in range(max(1, pages)):
            params = {"oid": aid, "mode": 3, "type": 1, "ps": per_page, "next": next_page}
            resp = await client.get(_REPLY_MAIN, params=params, cookies=_cookies(credential))
            payload = resp.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"B站评论抓取失败: {payload.get('message', payload.get('code'))}")
            data = payload.get("data") or {}
            for it in data.get("replies") or []:
                member = it.get("member") or {}
                content = it.get("content") or {}
                avatar = member.get("avatar") or ""
                if avatar.startswith("//"):
                    avatar = "https:" + avatar
                collected.append({
                    "comment_id": str(it.get("rpid", "")),
                    "parent_comment_id": "0",
                    "user_id": str(member.get("mid", "")),
                    "nickname": member.get("uname", ""),
                    "avatar": avatar,
                    "content": content.get("message", ""),
                    "like_count": int(it.get("like") or 0),
                    "ip_location": (it.get("reply_control") or {}).get("location", ""),
                    "created_at": it.get("ctime"),
                })
            cursor = data.get("cursor") or {}
            if cursor.get("is_end"):
                break
            next_page = int(cursor.get("next") or 0)
            if interval:
                await asyncio.sleep(interval)
    collected.sort(key=lambda x: x["like_count"], reverse=True)
    return collected[:top_n]
