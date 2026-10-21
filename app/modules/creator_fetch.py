"""创作者维度抓取：知乎 / 小红书按博主拉内容。

这两家的列表接口都需要平台页面运行时生成的签名（知乎 ``x-zse-96``、小红书
``x-s``），因此不复制第三方签名实现，而是在受管浏览器里打开博主主页，监听
平台自己发出的 JSON 响应。返回结构与 ``fetch_source`` 约定保持一致，后续
同步、指标快照、评论富集可以直接复用。
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

_PAGE_SIZE = 20


def _to_int(value) -> int | None:
    """Absent/unparseable counters are unknown, not a measured zero."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _ts_to_iso(value) -> str | None:
    """Unix 秒/毫秒 -> ISO 字符串；缺失/非法返回 None。"""
    try:
        ts = int(value)
        if ts <= 0:
            return None
        # 知乎用秒，小红书用毫秒；大于 10^11 的按毫秒处理。
        if ts > 100_000_000_000:
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _clean_url(value) -> str:
    value = str(value or "")
    return "https:" + value if value.startswith("//") else value


_ZHIHU_PATHS = {
    "answer": ("answers", "https://www.zhihu.com/people/{token}/answers"),
    "article": ("articles", "https://www.zhihu.com/people/{token}/posts"),
    "video": ("zvideos", "https://www.zhihu.com/people/{token}/videos"),
}


def _normalize_zhihu(obj: dict, content_kind: str, fallback_author_id: str) -> dict | None:
    if not isinstance(obj, dict):
        return None
    content_id = obj.get("id") or obj.get("token")
    if not content_id:
        return None
    content_id = str(content_id)
    author = obj.get("author") if isinstance(obj.get("author"), dict) else {}
    if not author.get("id") and isinstance(author.get("member"), dict):
        author = author["member"]
    author_id = str(author.get("id") or author.get("url_token") or fallback_author_id or "")
    metrics = {
        "like": _to_int(obj.get("voteup_count")),
        "comment": _to_int(obj.get("comment_count")),
        "play": _to_int(obj.get("play_count")),
        "favorite": _to_int(obj.get("favlists_count")),
        "share": None,
    }

    if content_kind == "answer":
        question = obj.get("question") if isinstance(obj.get("question"), dict) else {}
        question_id = question.get("id") or obj.get("question_id")
        url = (
            f"https://www.zhihu.com/question/{question_id}/answer/{content_id}"
            if question_id
            else f"https://www.zhihu.com/answer/{content_id}"
        )
        title = obj.get("title") or question.get("title") or obj.get("excerpt") or ""
        content_type = "answer"
        cover_url = ""
    elif content_kind == "article":
        url = f"https://zhuanlan.zhihu.com/p/{content_id}"
        title = obj.get("title") or obj.get("excerpt") or ""
        content_type = "article"
        cover_url = _clean_url(obj.get("image_url") or obj.get("title_image") or "")
    else:
        video = obj.get("video") if isinstance(obj.get("video"), dict) else {}
        url = obj.get("video_url") or f"https://www.zhihu.com/zvideo/{content_id}"
        title = obj.get("title") or obj.get("excerpt") or ""
        content_type = "video"
        cover_url = _clean_url(video.get("thumbnail") or obj.get("thumbnail") or "")
        play = video.get("play_count")
        if play is not None:
            metrics["play"] = _to_int(play)

    return {
        "external_id": content_id,
        "content_type": content_type,
        "title": str(title),
        "author_name": author.get("name") or "",
        "author_id": author_id,
        "url": url,
        "cover_url": cover_url,
        "published_at": _ts_to_iso(obj.get("created_time") or obj.get("created") or obj.get("updated_time")),
        "metrics": metrics,
        "extra": {"content_kind": content_kind},
    }


async def fetch_zhihu_creator(
    url_token: str,
    cookie: str,
    *,
    content_kind: str = "answer",
    limit: int = 50,
) -> list[dict]:
    """拉取知乎博主的内容列表（回答/文章/视频）。

    由平台主页自己发起带签名请求，我们只读取响应。当前读取平台首屏；后续若
    平台改为滚动分页，可在受管浏览器层统一扩展，而不改变本函数的返回结构。
    """
    from app.modules import browser_fetch

    token = str(url_token or "").strip()
    if not token or limit <= 0:
        return []
    kind = content_kind if content_kind in _ZHIHU_PATHS else "answer"
    path, page_url = _ZHIHU_PATHS[kind]
    data = await browser_fetch.fetch_until(
        page_url=page_url.format(token=quote(token, safe="")),
        api_token=f"/api/v4/members/{token}/{path}",
        cookie=cookie,
        domain=".zhihu.com",
        platform="zhihu",
        pages=max(1, (limit + _PAGE_SIZE - 1) // _PAGE_SIZE),
    )
    out: list[dict] = []
    for obj in data.get("data") or []:
        item = _normalize_zhihu(obj, kind, token)
        if item is not None:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _normalize_xhs(note: dict, fallback_author_id: str) -> dict | None:
    if not isinstance(note, dict):
        return None
    note_id = note.get("note_id") or note.get("id")
    if not note_id:
        return None
    note_id = str(note_id)
    card = note if note.get("display_title") or note.get("cover") else (note.get("note_card") or {})
    user = card.get("user") if isinstance(card.get("user"), dict) else {}
    interact = card.get("interact_info") if isinstance(card.get("interact_info"), dict) else {}
    cover = card.get("cover") if isinstance(card.get("cover"), dict) else {}
    note_type = str(card.get("type") or note.get("type") or "normal")
    xsec_token = note.get("xsec_token") or card.get("xsec_token") or ""
    xsec_source = note.get("xsec_source") or card.get("xsec_source") or "pc_user"
    return {
        "external_id": note_id,
        "content_type": "video" if note_type == "video" else "image",
        "title": card.get("display_title") or card.get("title") or "",
        "author_name": user.get("nickname") or user.get("nick_name") or "",
        "author_id": str(user.get("user_id") or user.get("id") or fallback_author_id or ""),
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "cover_url": _clean_url(cover.get("url_default") or cover.get("url_pre") or cover.get("url") or ""),
        "published_at": _ts_to_iso(card.get("time") or note.get("time")),
        "metrics": {
            "like": _to_int(interact.get("liked_count")),
            "comment": _to_int(interact.get("comment_count")),
            "favorite": _to_int(interact.get("collected_count")),
            "share": _to_int(interact.get("share_count")),
            "play": _to_int(interact.get("view_count")),
        },
        # 评论/详情接口必须带 xsec_token；这里逐条保留，不能被后续合并丢掉。
        "extra": {"xsec_token": xsec_token, "xsec_source": xsec_source},
    }


async def fetch_xhs_creator(user_id: str, cookie: str, *, limit: int = 50) -> list[dict]:
    """拉取小红书博主主页笔记列表。"""
    from app.modules import browser_fetch

    uid = str(user_id or "").strip()
    if not uid or limit <= 0:
        return []
    response = await browser_fetch.fetch_until(
        page_url=f"https://www.xiaohongshu.com/user/profile/{quote(uid, safe='')}",
        api_token="/api/sns/web/v1/user_posted",
        cookie=cookie,
        domain=".xiaohongshu.com",
        platform="xhs",
        pages=max(1, (limit + _PAGE_SIZE - 1) // _PAGE_SIZE),
    )
    # 线上响应常见形状：{success, data: {notes, has_more, cursor}}；
    # 旧版/测试替身也可能直接给 {notes: [...]}，两种都兼容。
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    out: list[dict] = []
    for note in data.get("notes") or []:
        item = _normalize_xhs(note, uid)
        if item is not None:
            out.append(item)
        if len(out) >= limit:
            break
    return out
