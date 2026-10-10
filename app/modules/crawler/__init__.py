"""关键词采集统一入口。

各平台抓取实现见 ``client.py`` 及同目录模块；签名依赖第三方库或平台页面自身完成，
不打包或改写非商业许可的第三方抓取代码。
"""

from app.modules.crawler import client as _client

# 已抽离为纯 Python 抓取的平台（现覆盖全部支持的采集平台）
_NATIVE = {"bilibili", "douyin", "xhs", "kuaishou", "zhihu"}


async def search(
    *,
    platform: str,
    keyword: str,
    sort: str = "popular",
    limit: int = 50,
    with_comments: bool = False,
    cookie: str = "",
) -> list[dict]:
    """统一抓取入口。crawl_service 调用此函数。全部平台走纯 Python 直连（需 cookie）。"""
    if platform not in _NATIVE:
        raise ValueError(f"不支持的采集平台: {platform}")
    return await _client.search(platform=platform, keyword=keyword, cookie=cookie, limit=limit, sort=sort)


async def fetch_comments(*, aweme_id: str, cookie: str, pages: int = 2, per_page: int = 20,
                         top_n: int = 20, interval: float = 1.0, platform: str = "douyin",
                         extra: dict | None = None, with_sub_comments: bool = False) -> list[dict]:
    """评论抓取分发：抖音走浏览器同源，B站走 aid + reply/wbi/main。"""
    if platform == "bilibili":
        from app.modules import bili_comments

        return await bili_comments.fetch_comments(
            bvid=aweme_id, cookie=cookie, pages=pages, per_page=per_page,
            top_n=top_n, interval=interval, with_sub_comments=with_sub_comments,
        )
    if platform == "kuaishou":
        from app.modules import ks_comments

        return await ks_comments.fetch_comments(
            photo_id=aweme_id, cookie=cookie, pages=pages, per_page=per_page,
            top_n=top_n, interval=interval, with_sub_comments=with_sub_comments,
        )
    if platform == "xhs":
        from app.modules import xhs_comments

        extra = extra or {}
        return await xhs_comments.fetch_comments(
            note_id=aweme_id, xsec_token=extra.get("xsec_token", ""), cookie=cookie,
            pages=pages, per_page=per_page, top_n=top_n, interval=interval,
        )
    if platform == "zhihu":
        from app.modules import zhihu_comments

        extra = extra or {}
        return await zhihu_comments.fetch_comments(
            content_url=extra.get("url", ""), content_id=aweme_id, cookie=cookie,
            pages=pages, per_page=per_page, top_n=top_n, interval=interval,
        )
    return await _client.fetch_comments(
        aweme_id=aweme_id, cookie=cookie, pages=pages, per_page=per_page,
        top_n=top_n, interval=interval,
    )
