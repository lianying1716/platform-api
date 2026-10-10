"""知乎一级评论抓取：导航到内容页，监听平台自身发出的 comment_v5 响应。

知乎评论接口需要页面签名，因此沿用搜索同样的受管浏览器监听方案：
由平台页面自己发请求，我们只读取响应，不复制签名实现。
"""

from __future__ import annotations

_TYPE_BY_URL = ((("zhuanlan.zhihu.com/p/"), "articles"), (("zvideo"), "zvideos"))


def _comment_path(content_url: str, content_id: str) -> str:
    """按内容 URL/ID 推断评论接口路径。"""
    url = content_url or ""
    if "zhuanlan.zhihu.com/p/" in url or "/p/" in url:
        return f"/api/v4/comment_v5/articles/{content_id}/root_comment"
    if "/zvideo/" in url:
        return f"/api/v4/comment_v5/zvideos/{content_id}/root_comment"
    return f"/api/v4/comment_v5/answers/{content_id}/root_comment"


async def fetch_comments(
    *, content_url: str, content_id: str, cookie: str, pages: int = 2, per_page: int = 20,
    top_n: int = 20, interval: float = 1.0,
) -> list[dict]:
    """抓知乎一级评论（回答/文章/视频），按点赞降序取 Top-N。"""
    from app.modules import browser_fetch

    data = await browser_fetch.fetch_until(
        page_url=content_url or f"https://www.zhihu.com",
        api_token=_comment_path(content_url, content_id),
        cookie=cookie,
        domain=".zhihu.com",
        platform="zhihu",
        pages=pages,
    )
    collected: list[dict] = []
    for it in data.get("data") or []:
        author = it.get("author") or {}
        avatar = author.get("avatar_url") or ""
        if avatar.startswith("//"):
            avatar = "https:" + avatar
        collected.append({
            "comment_id": str(it.get("id", "")),
            "parent_comment_id": "0",
            "user_id": str(author.get("id", "")),
            "nickname": author.get("name", ""),
            "avatar": avatar,
            "content": it.get("content", ""),
            "like_count": int(it.get("like_count") or 0),
            "ip_location": (it.get("comment_tag") or [{}])[0].get("text", "") if isinstance(it.get("comment_tag"), list) else "",
            "created_at": it.get("created_time"),
        })
    collected.sort(key=lambda x: x["like_count"], reverse=True)
    return collected[:top_n]
