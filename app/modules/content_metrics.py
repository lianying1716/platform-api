"""内容指标刷新：按平台拉取最新互动数，用于趋势分析与评论门控。

采集/同步时只写入一次指标快照；作品热度会持续变化，评论门控与看板需要最新值。
本模块按平台拉详情并归一化为统一的 metrics dict，失败返回 None（由调用方决定重试）。
"""

from __future__ import annotations

_BILI_VIEW = "https://api.bilibili.com/x/web-interface/view"
_DOUYIN_DETAIL = "/aweme/v1/web/aweme/detail/"


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


async def fetch_metrics(platform: str, external_id: str, cookie: str) -> dict | None:
    """统一入口：不支持的平台返回 None。"""
    if platform == "bilibili":
        return await fetch_bilibili_metrics(external_id, _bili_credential(cookie))
    if platform == "douyin":
        return await fetch_douyin_metrics(external_id, cookie)
    return None
