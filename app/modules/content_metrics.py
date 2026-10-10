"""内容指标刷新：按平台拉取最新互动数，用于趋势分析与评论门控。

采集/同步时只写入一次指标快照；作品热度会持续变化，评论门控与看板需要最新值。
本模块按平台拉详情并归一化为统一的 metrics dict，失败返回 None（由调用方决定重试）。
"""

from __future__ import annotations

_BILI_VIEW = "https://api.bilibili.com/x/web-interface/view"
_DOUYIN_DETAIL = "/aweme/v1/web/aweme/detail/"
_XHS_FEED = "/api/sns/web/v1/feed"
_KS_GRAPHQL = "https://www.kuaishou.com/graphql"
_KS_DETAIL_QUERY = """
query visionVideoDetail($photoId: String, $page: String) {
  visionVideoDetail(photoId: $photoId, page: $page) {
    status
    photo {
      id
      viewCount
      likeCount
      commentCount
    }
  }
}
"""


def _bili_http():
    from app.modules.bilibili import _http

    return _http()


def _bili_credential(cookie: str) -> dict:
    cred: dict = {}
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k in ("SESSDATA", "bili_jct", "buvid3", "DedeUserID", "ac_time_value"):
            cred[k.lower() if k != "DedeUserID" else "dedeuserid"] = v.strip()
    return cred


async def fetch_bilibili_metrics(bvid: str, credential: dict) -> dict | None:
    """B站视频指标：view/like/reply/favorite/share。"""
    from app.modules.bilibili import _cookies

    async with _bili_http() as client:
        resp = await client.get(_BILI_VIEW, params={"bvid": bvid}, cookies=_cookies(credential))
        payload = resp.json()
    if payload.get("code") != 0:
        return None
    stat = (payload.get("data") or {}).get("stat") or {}
    return {
        "play": int(stat.get("view") or 0),
        "like": int(stat.get("like") or 0),
        "comment": int(stat.get("reply") or 0),
        "favorite": int(stat.get("favorite") or 0),
        "share": int(stat.get("share") or 0),
    }


async def fetch_douyin_metrics(aweme_id: str, cookie: str) -> dict | None:
    """抖音作品指标：走受管浏览器同源请求（平台自身完成签名）。"""
    from app.modules import douyin_browser_fetch

    data = await douyin_browser_fetch.fetch_json(
        _DOUYIN_DETAIL, {"aweme_id": str(aweme_id)}, cookie,
        method="GET", referer="https://www.douyin.com/",
    )
    aw = data.get("aweme_detail") or {}
    stats = aw.get("statistics") or {}
    if not stats:
        return None
    return {
        "play": int(stats.get("play_count") or 0),
        "like": int(stats.get("digg_count") or 0),
        "comment": int(stats.get("comment_count") or 0),
        "favorite": int(stats.get("collect_count") or 0),
        "share": int(stats.get("share_count") or 0),
    }


async def fetch_metrics(platform: str, external_id: str, cookie: str, extra: dict | None = None) -> dict | None:
    """统一入口：不支持的平台返回 None。"""
    if platform == "bilibili":
        return await fetch_bilibili_metrics(external_id, _bili_credential(cookie))
    if platform == "douyin":
        return await fetch_douyin_metrics(external_id, cookie)
    if platform == "xhs":
        return await fetch_xhs_metrics(external_id, cookie, extra)
    if platform == "kuaishou":
        return await fetch_kuaishou_metrics(external_id, cookie)
    if platform == "zhihu":
        from app.modules import zhihu_metrics

        return await zhihu_metrics.fetch_metrics(external_id, cookie, extra)
    return None


async def fetch_xhs_metrics(note_id: str, cookie: str, extra: dict | None = None) -> dict | None:
    """小红书笔记指标：/api/sns/web/v1/feed，需搜索阶段留存的 xsec_token。"""
    from app.modules.crawler import xhs_sign
    from app.core import rate_limit

    extra = extra or {}
    payload = {
        "source_note_id": note_id,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": 1},
        "xsec_source": extra.get("xsec_source") or "pc_search",
        "xsec_token": extra.get("xsec_token") or "",
    }
    sig = xhs_sign.sign(_XHS_FEED, payload, cookie, "POST")
    async with rate_limit.http_client("xhs", headers={"Cookie": cookie}, timeout=20) as c:
        resp = await c.post(f"https://edith.xiaohongshu.com{_XHS_FEED}", json=payload, headers=sig)
        data = resp.json()
    if not data.get("success"):
        return None
    items = (data.get("data") or {}).get("items") or []
    if not items:
        return None
    inter = (items[0].get("note_card") or {}).get("interact_info") or {}
    return {
        "play": 0,
        "like": int(inter.get("liked_count") or 0),
        "comment": int(inter.get("comment_count") or 0),
        "favorite": int(inter.get("collected_count") or 0),
        "share": int(inter.get("share_count") or 0),
    }


async def fetch_kuaishou_metrics(photo_id: str, cookie: str) -> dict | None:
    """快手视频指标：GraphQL visionVideoDetail（查询文档在仓库内自行定义）。"""
    from app.core import rate_limit

    body = {
        "operationName": "visionVideoDetail",
        "variables": {"photoId": str(photo_id), "page": "search"},
        "query": _KS_DETAIL_QUERY,
    }
    headers = {
        "Cookie": cookie,
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.kuaishou.com",
        "Referer": "https://www.kuaishou.com",
    }
    async with rate_limit.http_client("kuaishou", headers=headers, timeout=20) as c:
        resp = await c.post(_KS_GRAPHQL, json=body)
        payload = resp.json() or {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    photo = ((data.get("visionVideoDetail") or {}).get("photo")) or {}
    if not photo:
        return None
    return {
        "play": int(photo.get("viewCount") or 0),
        "like": int(photo.get("likeCount") or 0),
        "comment": int(photo.get("commentCount") or 0),
        "favorite": 0,
        "share": 0,
    }
