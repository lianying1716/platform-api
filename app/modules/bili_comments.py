"""B站一级评论抓取：bvid -> aid -> /x/v2/reply/wbi/main 分页，按点赞降序取 Top-N。

B站评论接口的 oid 必须用 aid（数字 ID）而非 bvid；接口参数按公开契约实现，
cookie 与限速沿用本仓库 B站通道。
"""

from __future__ import annotations

import asyncio

_VIEW = "https://api.bilibili.com/x/web-interface/view"
_REPLY_MAIN = "https://api.bilibili.com/x/v2/reply/wbi/main"
_REPLY_REPLY = "https://api.bilibili.com/x/v2/reply/reply"


def _normalize_reply(it: dict, parent_comment_id: str = "0") -> dict:
    member = it.get("member") or {}
    content = it.get("content") or {}
    avatar = member.get("avatar") or ""
    if avatar.startswith("//"):
        avatar = "https:" + avatar
    return {
        "comment_id": str(it.get("rpid", "")),
        "parent_comment_id": parent_comment_id,
        "user_id": str(member.get("mid", "")),
        "nickname": member.get("uname", ""),
        "avatar": avatar,
        "content": content.get("message", ""),
        "like_count": int(it.get("like") or 0),
        "ip_location": (it.get("reply_control") or {}).get("location", ""),
        "created_at": it.get("ctime"),
    }


async def _fetch_sub_comments(client, credential: dict, aid: int, root_id,
                             per_page: int, interval: float) -> list[dict]:
    """某条一级评论下的二级评论（/x/v2/reply/reply，pn 翻页）。"""
    from app.modules.bilibili import _cookies

    out: list[dict] = []
    pn = 1
    while True:
        resp = await client.get(
            _REPLY_REPLY,
            params={"oid": aid, "type": 1, "root": str(root_id), "pn": pn, "ps": per_page},
            cookies=_cookies(credential),
        )
        payload = resp.json()
        if payload.get("code") != 0:
            break
        data = payload.get("data") or {}
        rows = data.get("replies") or []
        for it in rows:
            out.append(_normalize_reply(it, parent_comment_id=str(root_id)))
        total = int((data.get("page") or {}).get("count") or 0)
        if not rows or len(out) >= total:
            break
        pn += 1
        if interval:
            await asyncio.sleep(interval)
    return out


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
    top_n: int = 20, interval: float = 1.0, with_sub_comments: bool = False,
) -> list[dict]:
    """抓 B站作品评论：默认一级；with_sub_comments=True 时附带二级评论。"""
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
            roots = data.get("replies") or []
            for it in roots:
                collected.append(_normalize_reply(it))
            if with_sub_comments:
                for it in roots:
                    root_id = it.get("rpid")
                    if not root_id or not int(it.get("rcount") or 0):
                        continue
                    collected.extend(
                        await _fetch_sub_comments(client, credential, aid, root_id, per_page, interval)
                    )
            cursor = data.get("cursor") or {}
            if cursor.get("is_end"):
                break
            next_page = int(cursor.get("next") or 0)
            if interval:
                await asyncio.sleep(interval)
    collected.sort(key=lambda x: x["like_count"], reverse=True)
    return collected[:top_n]
