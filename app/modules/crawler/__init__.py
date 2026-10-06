"""关键词采集统一入口（PRD 第二阶段：抓取能力抽离进 modules/）。

**全部平台均为纯 Python 直连**（bilibili/douyin/xhs/kuaishou/zhihu/weibo/tieba，见 client.py），
不依赖活浏览器、不再子进程调 MediaCrawler。

签名/接口逻辑来源见 client.py、bili_sign.py、douyin_sign.py、xhs_sign.py、zhihu_sign.py、
weibo.py、tieba.py 各文件头（MediaCrawler NON-COMMERCIAL LEARNING LICENSE 1.1，仅自用学习）。
"""

from app.modules.crawler import client as _client

# 已抽离为纯 Python 抓取的平台（现覆盖全部支持的采集平台）
_NATIVE = {"bilibili", "douyin", "xhs", "kuaishou", "zhihu", "weibo", "tieba"}


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
                         top_n: int = 20, interval: float = 1.0) -> list[dict]:
    """当前仅抖音支持评论抓取。"""
    return await _client.fetch_comments(
        aweme_id=aweme_id, cookie=cookie, pages=pages, per_page=per_page,
        top_n=top_n, interval=interval,
    )
