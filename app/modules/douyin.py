"""抖音适配器。协议来源：dysync.net service/DouyinHttpClientService.cs +
utils/DouyinRequestParamManager.cs（MIT，纯 HTTP + Cookie，无签名依赖）。

凭证 JSON: { cookie, sec_user_id, my_user_id }
体检逻辑与 dysync CheckCookie 一致：
- 有 sec_user_id：调关注列表接口，StatusCode==0 即在线
- 无 sec_user_id：调收藏列表接口判断
扫码：抖音无公开扫码 API，走浏览器方案（browser_qr，需安装 playwright），
手动粘 Cookie 是兜底。
"""

import logging

import httpx

from app.modules.base import CheckResult, QrPoll, QrStart, register
from app.modules.crawler import douyin_sign

logger = logging.getLogger("platform.douyin")


def _ts_to_iso(value) -> str | None:
    """Unix 秒 -> ISO 字符串；缺失/非法返回 None（同步侧 _parse_published_at 可解析）。"""
    try:
        from datetime import datetime, timezone

        ts = int(value)
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None

_HOST = "https://www.douyin.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

# 对应 dysync DouyinRequestParamManager._baseParamTemplate
BASE_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "pc_libra_divert": "Windows",
    "cookie_enabled": "true",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_online": "true",
    "engine_name": "Blink",
    "os_name": "Windows",
    "os_version": "10",
    "device_memory": "8",
    "platform": "PC",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "0",
    "update_version_code": "170400",
    "whale_cut_token": "",
}


# ---------------------------------------------------------------------------
# 各接口参数模板 —— 逐项照抄 dysync.net utils/DouyinRequestParamManager.cs。
# 抖小云实测长期稳定、无风控问题，这里不再自行简化参数：
# 抖音会校验参数完整度，缺项容易被判成异常请求。
# 凡模板里显式为空的键（uifid/msToken/a_bogus/verifyFp/fp/...）都保留空串，
# 因为「带这个键但为空」与「完全不带」在服务端是两种处理。
# ---------------------------------------------------------------------------

def _with(base: dict | None = None, **over) -> dict:
    """在基础参数上覆盖/追加，避免各模板重复写公共项（对应 InitBaseParams()）。"""
    d = dict(BASE_PARAMS if base is None else base)
    d.update(over)
    return d


# 博主发布作品（SyncUpderPostVideos）—— 我们最需要的那条
POST_PARAMS = _with(
    version_code="290100", version_name="29.1.0",
    screen_width="1707", screen_height="1067",
    browser_name="Chrome", browser_version="142.0.0.0", engine_version="142.0.0.0",
    cpu_core_num="32", support_h265="1", support_dash="1",
    webid="7574080345697584675",
    locate_item_id="7576282367263807451", locate_query="false",
    show_live_replay_strategy="1", need_time_list="1", time_list_query="0",
    publish_video_strategy_type="2", from_user_page="1",
    uifid="", msToken="", a_bogus="", verifyFp="", fp="",
    **{"x-secsdk-web-expire": "", "x-secsdk-web-signature": ""},
    cut_version="1",
    sec_user_id="", max_cursor="0", count="18",
)

# 我关注的博主（SyncMyFollows）
FOLLOW_PARAMS = _with(
    version_code="170400", version_name="17.4.0",
    screen_width="1707", screen_height="1067",
    browser_name="Edge", browser_version="141.0.0.0", engine_version="141.0.0.0",
    cpu_core_num="32", support_h265="1", support_dash="1",
    webid="7577203855940994560",
    min_time="0", max_time="0", source_type="4",
    gps_access="0", address_book_access="0", is_top="1",
    user_id="", sec_user_id="", offset="0", count="20",
)

# 我的收藏（SyncCollectVideos）
COLLECT_PARAMS = _with(
    version_code="290100", version_name="29.1.0",
    screen_width="1920", screen_height="1080",
    browser_version="130.0.0.0", engine_version="130.0.0.0",
    cpu_core_num="12",
    from_user_page="1", locate_query="false", need_time_list="1",
    show_live_replay_strategy="1", time_list_query="0",
    cursor="0", count="10",
)

# 我点赞的作品（SyncFavoriteVideos）
FAVORITE_PARAMS = _with(
    version_code="170400", version_name="17.4.0",
    screen_width="1536", screen_height="960",
    browser_version="140.0.0.0", engine_version="140.0.0.0",
    cpu_core_num="20", support_h265="1", support_dash="1",
    min_cursor="0", cut_version="1", count="18",
)


def _pick_play_url(aw: dict) -> str:
    """从 aweme 结构里取可直连下载的播放地址。

    照抄抖小云 DouyinBasicSyncJob：优先取 video.play_addr.url_list 里
    ``https://www.douyin.com/aweme/v1/play`` 开头的直链（配 Cookie 可直接下载），
    否则回退 url_list 第一项。

    有了这个就不必依赖 yt-dlp（抖音页面下载要 fresh cookies，很脆），
    也不需要任何签名 —— 抖小云就是这么长期稳定下载的。
    """
    video = aw.get("video") or {}
    addr = video.get("play_addr") or {}
    urls = addr.get("url_list") or []
    for u in urls:
        if isinstance(u, str) and u.startswith("https://www.douyin.com/aweme/v1/play"):
            return u
    if urls:
        return str(urls[0])
    # 兜底：下载地址字段（部分内容只有这个）
    dl = (video.get("download_addr") or {}).get("url_list") or []
    return str(dl[0]) if dl else ""


def _client(cookie: str, referer: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_HOST,
        headers={"User-Agent": _UA, "Referer": referer, "Cookie": cookie},
        timeout=15,
    )


async def list_collection(cookie: str, cursor: str = "0", count: str = "10") -> dict | None:
    """收藏列表（dysync SyncCollectVideos）。"""
    params = dict(COLLECT_PARAMS, cursor=cursor, count=count)
    async with _client(cookie, _HOST) as client:
        resp = await client.post("/aweme/v1/web/aweme/listcollection", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            # Cookie 无效时抖音常返回空体或 HTML
            return None


async def following_list(cookie: str, sec_user_id: str, offset: str = "0", count: str = "20") -> dict | None:
    """关注列表。

    完全照抄抖小云 SyncMyFollows：
    - 参数用 FOLLOW_PARAMS 模板（含 min_time/max_time/source_type/is_top/webid 等）
    - ``source_type=4``（抖小云值；我们原先用 1）
    - ``offset`` 从 **0** 起（抖小云 job 里初始 offset="0"）
    - referer ``https://www.douyin.com/user/self?showTab=like``
    返回体里的 ``offset`` 字段就是下一页要传的值，按它翻页。
    """
    params = dict(
        FOLLOW_PARAMS,
        user_id="",
        sec_user_id=sec_user_id,
        offset=offset,
        count=count,
    )
    referer = "https://www.douyin.com/user/self?showTab=like"
    async with _client(cookie, referer) as client:
        resp = await client.get("/aweme/v1/web/user/following/list", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None


class DouyinAdapter:
    platform = "douyin"
    label = "抖音"
    credential_kind = "cookie"
    supports_qr = True  # 经 browser_qr（Playwright），未装浏览器时 API 会返回明确报错
    credential_fields = [
        {"key": "cookie", "label": "Cookie 字符串（整段粘贴）", "required": True},
        {"key": "sec_user_id", "label": "SecUserId（主页地址 /user/ 后面那串）", "required": False},
        {"key": "my_user_id", "label": "MyUserId", "required": False},
    ]

    def parse_manual_input(self, raw: dict) -> dict:
        cookie = (raw.get("cookie") or "").strip()
        if not cookie:
            raise ValueError("抖音凭证需要整段 Cookie 字符串")
        return {
            "cookie": cookie,
            "sec_user_id": (raw.get("sec_user_id") or "").strip(),
            "my_user_id": (raw.get("my_user_id") or "").strip(),
        }

    async def check(self, credential: dict) -> CheckResult:
        cookie = credential.get("cookie", "")
        sec_user_id = credential.get("sec_user_id", "")
        try:
            if sec_user_id:
                data = await following_list(cookie, sec_user_id)
            else:
                data = await list_collection(cookie)
        except httpx.HTTPError as exc:
            return CheckResult(ok=False, message=f"请求失败: {exc}")
        if data is not None and data.get("status_code") == 0:
            return CheckResult(ok=True, message="在线")
        return CheckResult(ok=False, message="Cookie 已失效或被风控")

    async def qr_start(self) -> QrStart:
        from app.modules.browser_qr import browser_qr_start

        return await browser_qr_start("douyin")

    async def qr_poll(self, session_data: dict) -> QrPoll:
        from app.modules.browser_qr import browser_qr_poll

        return await browser_qr_poll("douyin", session_data)

    async def list_sources(self, credential: dict, source_type: str) -> list[dict]:
        """列出可订阅的源，返回 [{remote_id, title}]。

        source_type：
        - favorite：抖音没有“收藏夹列表”接口，返回虚拟项“我的收藏”。
        - creator：自己关注的博主（需 sec_user_id，分页到 ~50 条）。
        其它类型或缺少必需凭证字段时返回 []（不抛异常）。
        """
        if source_type == "favorite":
            return [{"remote_id": credential.get("sec_user_id") or "self", "title": "我的收藏"}]
        if source_type == "creator":
            sec_user_id = (credential.get("sec_user_id") or "").strip()
            if not sec_user_id:
                return []
            return await self._list_followings(credential.get("cookie", ""), sec_user_id)
        return []

    async def _list_followings(self, cookie: str, sec_user_id: str, limit: int = 50) -> list[dict]:
        """关注列表。解析防御式：字段缺失/请求失败都只返回已拿到的部分。"""
        out: list[dict] = []
        offset = 0
        count = 20
        try:
            while len(out) < limit:
                data = await following_list(cookie, sec_user_id, offset=str(offset), count=str(count))
                if not data or data.get("status_code") != 0:
                    break
                followings = data.get("followings") or []
                if not followings:
                    break
                for f in followings:
                    sec_uid = f.get("sec_uid") or ""
                    if not sec_uid:
                        continue
                    out.append({"remote_id": sec_uid, "title": f.get("nickname", "")})
                    if len(out) >= limit:
                        break
                if not data.get("has_more"):
                    break
                # 抖小云按服务端返回的 offset 翻页（不是自己累加）
                offset = int(data.get("offset") or (offset + count))
        except (httpx.HTTPError, ValueError):
            return out[:limit]
        return out[:limit]

    async def list_followings(self, credential: dict, limit: int = 200) -> list[dict]:
        """关注的博主**富列表**（供「关注博主」页）：真头像/昵称/签名/认证/主页。

        列表：``/aweme/v1/web/user/following/list`` 分页（offset/count）。
        粉丝/获赞：需逐用户 ``profile/other``（要 a_bogus 签名），留 0，由「刷新」时单独补齐。
        字段缺失跳过；**认证失效/风控抛可读异常**（便于上层进入冷却）。
        """
        sec_user_id = (credential.get("sec_user_id") or "").strip()
        if not sec_user_id:
            return []
        cookie = credential.get("cookie", "")
        out: list[dict] = []
        offset = 0
        count = 20
        try:
            while len(out) < limit:
                data = await following_list(cookie, sec_user_id, offset=str(offset), count=str(count))
                if data is None:
                    break
                if data.get("status_code") not in (0, None):
                    sc = data.get("status_code")
                    # 抖音用 status_code 表示失败；带"风控/登录"语义的走异常
                    raise RuntimeError(f"抖音关注列表拉取失败（status_code={sc}），可能登录失效或被风控")
                followings = data.get("followings") or []
                if not followings:
                    break
                for f in followings:
                    suid = f.get("sec_uid") or ""
                    if not suid:
                        continue
                    avatar = ""
                    av = f.get("avatar_larger") or f.get("avatar_thumb") or {}
                    if isinstance(av, dict):
                        urls = av.get("url_list") or []
                        avatar = urls[0] if urls else ""
                    out.append(
                        {
                            "author_id": suid,
                            "author_name": f.get("nickname", ""),
                            "avatar": avatar,
                            "home_url": f"https://www.douyin.com/user/{suid}",
                            "signature": f.get("signature", ""),
                            "verified": bool(f.get("enterprise_verify_reason")),
                            "follower_count": 0,  # best-effort，由刷新补齐
                            "like_count": 0,
                            "raw": f,
                        }
                    )
                    if len(out) >= limit:
                        break
                if not data.get("has_more"):
                    break
                offset = int(data.get("offset") or (offset + count))
        except (httpx.HTTPError, ValueError):
            pass
        return out[:limit]

    async def fetch_source(self, credential: dict, source_type: str, remote_id: str, limit: int) -> list[dict]:
        """拉取订阅源内容。

        - favorite/collection：我的收藏（/aweme/v1/web/aweme/listcollection）
        - creator：博主主页视频（/aweme/v1/web/aweme/post，无尾斜杠，无需签名）
        """
        cookie = credential.get("cookie", "")
        if source_type == "creator":
            return await self._fetch_creator_videos(cookie, remote_id, limit)
        if source_type not in ("favorite", "collection"):
            return []
        items: list[dict] = []
        cursor = "0"
        while len(items) < limit:
            data = await list_collection(cookie, cursor=cursor, count="20")
            if not data or data.get("status_code") != 0:
                break
            for aw in data.get("aweme_list") or []:
                stats = aw.get("statistics") or {}
                author = aw.get("author") or {}
                items.append(
                    {
                        "external_id": str(aw.get("aweme_id", "")),
                        "content_type": "video",
                        "title": aw.get("desc", ""),
                        "author_name": author.get("nickname", ""),
                        "author_id": author.get("sec_uid", ""),
                        "url": f"https://www.douyin.com/video/{aw.get('aweme_id', '')}",
                        "cover_url": (((aw.get("video") or {}).get("cover") or {}).get("url_list") or [""])[0],
                        # 直连播放地址：下载走它 + Cookie，不依赖 yt-dlp
                        "extra": {"download_url": _pick_play_url(aw)},
                        # create_time 为 Unix 秒；缺失留 None（同步侧 publish_after_days 依赖它）
                        "published_at": _ts_to_iso(aw.get("create_time")),
                        "metrics": {
                            "play": stats.get("play_count", 0),
                            "like": stats.get("digg_count", 0),
                            "comment": stats.get("comment_count", 0),
                            "favorite": stats.get("collect_count", 0),
                            "share": stats.get("share_count", 0),
                        },
                    }
                )
            if not data.get("has_more"):
                break
            cursor = str(data.get("cursor", "0"))
        return items[:limit]

    async def _fetch_creator_videos(self, cookie: str, sec_user_id: str, limit: int) -> list[dict]:
        """博主主页视频列表。

        路径必须**无尾斜杠**（与抖小云一致）；带尾斜杠会被 Argus 403。
        与 a_bogus/uifid/msToken 无关，实测确认。
        """
        from urllib.parse import urlencode

        sec_user_id = str(sec_user_id or "").strip()
        if not sec_user_id:
            return []
        base = "/aweme/v1/web/aweme/post"
        referer = f"https://www.douyin.com/user/{sec_user_id}"
        items: list[dict] = []
        max_cursor = "0"
        async with _client(cookie, referer) as client:
            while len(items) < limit:
                params = dict(
                    POST_PARAMS,
                    sec_user_id=sec_user_id,
                    count="18",
                    max_cursor=max_cursor,
                )
                qs = urlencode(params)
                # 不算签名：抖小云(vulture 稳定在跑)同样不签名，路径正确即可 200。
                # 保留 a_bogus 相关代码只在被 Argus 拦时才需要，见下方兜底分支。
                resp = await client.get(f"{base}?{qs}")
                if resp.status_code in (403, 412) or "ArgusSecurityPlugin" in resp.text:
                    # 兜底：正常情况下不会走到这里（路径无尾斜杠即 200，实测）。
                    # 万一平台以后又收紧，就真算一个 a_bogus 覆盖掉模板里的空值重试一次。
                    signed = dict(params)
                    qs2 = urlencode(signed)
                    signed["a_bogus"] = douyin_sign.get_a_bogus(base, qs2, _UA)
                    resp = await client.get(f"{base}?{urlencode(signed)}")
                try:
                    data = resp.json()
                except ValueError:
                    raise RuntimeError("抖音博主视频拉取失败：Cookie 可能失效或被风控")
                if data.get("status_code") not in (0, None):
                    raise RuntimeError(f"抖音博主视频拉取失败: status_code={data.get('status_code')}")
                aweme_list = data.get("aweme_list") or []
                if not aweme_list:
                    break
                for aw in aweme_list:
                    stats = aw.get("statistics") or {}
                    author = aw.get("author") or {}
                    items.append(
                        {
                            "external_id": str(aw.get("aweme_id", "")),
                            "content_type": "video",
                            "title": aw.get("desc", ""),
                            "author_name": author.get("nickname", ""),
                            "author_id": author.get("sec_uid", "") or sec_user_id,
                            "url": f"https://www.douyin.com/video/{aw.get('aweme_id', '')}",
                            "cover_url": (((aw.get("video") or {}).get("cover") or {}).get("url_list") or [""])[0],
                            # 直连播放地址：下载走它 + Cookie，不依赖 yt-dlp
                            "extra": {"download_url": _pick_play_url(aw)},
                            "published_at": _ts_to_iso(aw.get("create_time")),
                            "metrics": {
                                "play": stats.get("play_count", 0),
                                "like": stats.get("digg_count", 0),
                                "comment": stats.get("comment_count", 0),
                                "favorite": stats.get("collect_count", 0),
                                "share": stats.get("share_count", 0),
                            },
                        }
                    )
                    if len(items) >= limit:
                        break
                if not data.get("has_more"):
                    break
                max_cursor = str(data.get("max_cursor", "0"))
        return items[:limit]


register(DouyinAdapter())
