"""平台抓取客户端：用各平台接口与第三方宽松许可证签名库完成搜索。

设计：每个平台一个 async 函数 search_<platform>(keyword, cookie, limit, sort) -> list[item]，
item 为归一化 dict（与 services/crawl_service 约定一致）。
真实抓取需有效 Cookie；无 Cookie 或被风控时抛出可读异常，由上层记 error。
"""

import asyncio
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx

from app.modules.crawler import bili_sign, xhs_sign
from app.modules.crawler.kuaishou import search_kuaishou
from app.modules.crawler.zhihu import search_zhihu

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


@asynccontextmanager
async def _http(platform: str, headers: dict):
    """限速 + httpx 客户端。爬取/评论等出站请求统一走这里。

    这里曾是限速的漏网之鱼：评论抓取直接用裸 httpx + 自己的 interval，
    实测一次同步里 1.4 秒内连发 4 个 comment/list 请求。现在统一过平台桶。
    """
    from app.core import rate_limit
    from app.core import outbound

    # 逐请求限速：评论抓取在一个会话里 for 循环翻页，只在会话开始限速等于没限。
    client = httpx.AsyncClient(headers=headers, timeout=20, **outbound.httpx_proxy_kwargs(platform))
    async with rate_limit._ThrottledClient.wrap(platform, client) as c:
        yield c


def _cookie_dict(cookie_str: str) -> dict:
    out = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            out[k] = v
    return out


# ---------------- B站 ----------------
async def search_bilibili(keyword: str, cookie: str, limit: int, sort: str) -> list[dict]:
    cookies = _cookie_dict(cookie)
    img_key, sub_key = await bili_sign.fetch_wbi_keys(cookies)
    order = "totalrank" if sort == "popular" else "pubdate"
    items: list[dict] = []
    page = 1
    headers = {"User-Agent": _UA, "Referer": "https://www.bilibili.com"}
    async with _http("bilibili", headers) as c:
        while len(items) < limit:
            params = bili_sign.sign(
                {"search_type": "video", "keyword": keyword, "page": page, "page_size": 20, "order": order},
                img_key, sub_key,
            )
            url = f"https://api.bilibili.com/x/web-interface/wbi/search/type?{urlencode(params)}"
            resp = await c.get(url, cookies=cookies)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"B站搜索失败: {data.get('message', data.get('code'))}")
            results = (data.get("data") or {}).get("result") or []
            if not results:
                break
            for r in results:
                title = (r.get("title") or "").replace('<em class="keyword">', "").replace("</em>", "")
                items.append({
                    "external_id": r.get("bvid", ""),
                    "content_type": "video",
                    "title": title,
                    "author_name": r.get("author", ""),
                    "author_id": str(r.get("mid", "")),
                    "url": f"https://www.bilibili.com/video/{r.get('bvid', '')}",
                    "cover_url": ("https:" + r["pic"]) if r.get("pic", "").startswith("//") else r.get("pic", ""),
                    "metrics": {"play": r.get("play", 0), "like": r.get("like", 0), "comment": r.get("review", 0)},
                })
            page += 1
    return items[:limit]


# ---------------- 抖音 ----------------
async def search_douyin(keyword: str, cookie: str, limit: int, sort: str) -> list[dict]:
    items: list[dict] = []
    offset = 0
    base = "https://www.douyin.com/aweme/v1/web/general/search/single/"
    sort_type = "0" if sort == "popular" else "1"
    from app.modules import douyin_browser_fetch

    while len(items) < limit:
        query = {
            "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
            "search_channel": "aweme_general", "keyword": keyword, "search_source": "normal_search",
            "query_correct_type": "1", "is_filter_search": "0", "offset": str(offset),
            "count": "15", "sort_type": sort_type, "publish_time": "0",
        }
        data = await douyin_browser_fetch.fetch_json(
            "/aweme/v1/web/general/search/single/", query, cookie,
            method="GET", referer="https://www.douyin.com/",
        )
        results = data.get("data") or []
        if not results:
            break
        for r in results:
            aw = r.get("aweme_info") or {}
            if not aw:
                continue
            stats = aw.get("statistics") or {}
            author = aw.get("author") or {}
            items.append({
                "external_id": str(aw.get("aweme_id", "")),
                "content_type": "video",
                "title": aw.get("desc", ""),
                "author_name": author.get("nickname", ""),
                "author_id": author.get("sec_uid", ""),
                "url": f"https://www.douyin.com/video/{aw.get('aweme_id', '')}",
                "cover_url": (((aw.get("video") or {}).get("cover") or {}).get("url_list") or [""])[0],
                "metrics": {
                    "play": stats.get("play_count", 0), "like": stats.get("digg_count", 0),
                    "comment": stats.get("comment_count", 0), "favorite": stats.get("collect_count", 0),
                    "share": stats.get("share_count", 0),
                },
            })
        if not data.get("has_more"):
            break
        offset += 15
    return items[:limit]


# ---------------- 小红书 ----------------
async def search_xhs(keyword: str, cookie: str, limit: int, sort: str) -> list[dict]:
    uri = "/api/sns/web/v1/search/notes"
    sort_map = {"popular": "popularity_descending", "latest": "time_descending"}
    items: list[dict] = []
    page = 1
    headers_base = {"User-Agent": _UA, "Referer": "https://www.xiaohongshu.com/", "Cookie": cookie,
                    "Origin": "https://www.xiaohongshu.com"}
    async with _http("xhs", headers_base) as c:
        while len(items) < limit:
            payload = {
                "keyword": keyword, "page": page, "page_size": 20,
                "search_id": str(int(time.time() * 1000)), "sort": sort_map.get(sort, "general"),
                "note_type": 0,
            }
            sig = xhs_sign.sign(uri, payload, cookie, "POST")
            resp = await c.post(f"https://edith.xiaohongshu.com{uri}", json=payload, headers=sig)
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"小红书搜索失败: {data.get('msg', '未知，Cookie 可能失效')}")
            notes = (data.get("data") or {}).get("items") or []
            if not notes:
                break
            for n in notes:
                card = n.get("note_card") or {}
                if not card:
                    continue
                inter = card.get("interact_info") or {}
                user = card.get("user") or {}
                items.append({
                    "external_id": n.get("id", ""),
                    "content_type": "video" if card.get("type") == "video" else "image",
                    "title": card.get("display_title", ""),
                    "author_name": user.get("nickname", ""),
                    "author_id": user.get("user_id", ""),
                    "url": f"https://www.xiaohongshu.com/explore/{n.get('id', '')}",
                    "cover_url": ((card.get("cover") or {}).get("url_default") or ""),
                    "metrics": {"like": int(inter.get("liked_count", 0) or 0)},
                    # 后续详情/评论接口必须带 xsec_token + xsec_source，搜索时就要留存。
                    "extra": {
                        "xsec_token": n.get("xsec_token", ""),
                        "xsec_source": n.get("xsec_source", "pc_search"),
                    },
                })
            page += 1
    return items[:limit]


_SEARCHERS = {
    "bilibili": search_bilibili,
    "douyin": search_douyin,
    "xhs": search_xhs,
    "kuaishou": search_kuaishou,
    "zhihu": search_zhihu,
}


# ---------------- 抖音评论 ----------------
async def fetch_comments(
    *, aweme_id: str, cookie: str, pages: int = 2, per_page: int = 20,
    top_n: int = 20, interval: float = 1.0,
) -> list[dict]:
    """抓抖音作品一级评论：默认排序抓 pages 页，本地按点赞降序取 top_n。"""
    from app.modules.douyin import BASE_PARAMS
    from app.modules import douyin_browser_fetch

    base = "https://www.douyin.com/aweme/v1/web/comment/list/"
    collected: list[dict] = []
    cursor = "0"
    for _ in range(max(1, pages)):
        query = dict(BASE_PARAMS, aweme_id=aweme_id, cursor=cursor, count=str(per_page), item_type="0")
        data = await douyin_browser_fetch.fetch_json(
            "/aweme/v1/web/comment/list/", query, cookie,
            method="GET", referer="https://www.douyin.com/",
        )
        for it in data.get("comments") or []:
            user = it.get("user") or {}
            avatar = (((user.get("avatar_thumb") or {}).get("url_list") or [""])[0])
            collected.append({
                "comment_id": str(it.get("cid", "")),
                "parent_comment_id": "0",
                "user_id": str(user.get("uid", "")),
                "nickname": user.get("nickname", ""),
                "avatar": avatar,
                "content": it.get("text", ""),
                "like_count": int(it.get("digg_count") or 0),
                "ip_location": it.get("ip_label", ""),
                "created_at": it.get("create_time"),
            })
        if not data.get("has_more"):
            break
        cursor = str(data.get("cursor", "0"))
        if interval:
            await asyncio.sleep(interval)
    collected.sort(key=lambda x: x["like_count"], reverse=True)
    return collected[:top_n]


async def search(*, platform: str, keyword: str, cookie: str, limit: int = 50, sort: str = "popular") -> list[dict]:
    """统一抓取入口。platform 支持 bilibili/douyin/xhs/kuaishou/zhihu（纯 Python）。"""
    fn = _SEARCHERS.get(platform)
    if fn is None:
        raise ValueError(f"暂不支持纯 Python 抓取的平台: {platform}（可走 browser_runtime 兜底）")
    if not cookie:
        raise RuntimeError(f"{platform} 抓取需要有效 Cookie，请先在账号中心登录该平台")
    return await fn(keyword, cookie, limit, sort)
