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
from contextlib import asynccontextmanager

import httpx

from app.modules.base import CheckResult, QrPoll, QrStart, register

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

# 收藏夹列表（SyncCollectFolderList -> /aweme/v1/web/collects/list）—— 抖小云 DouyinCollectListParams
COLLECT_FOLDER_LIST_PARAMS = _with(
    version_code="170400", version_name="17.4.0",
    screen_width="1707", screen_height="1067",
    browser_name="Edge", browser_version="141.0.0.0", engine_version="141.0.0.0",
    cpu_core_num="32", support_h265="1", support_dash="1",
    webid="7584476599598188067",
    cursor="0", count="10",
)

# 收藏夹内作品（SyncCollectVideosByCollectId -> /aweme/v1/web/collects/video/list）—— 抖小云 DouyinFolderCollectParams
COLLECT_FOLDER_VIDEO_PARAMS = _with(
    version_code="170400", version_name="17.4.0",
    screen_width="1707", screen_height="1067",
    browser_name="Edge", browser_version="141.0.0.0", engine_version="141.0.0.0",
    cpu_core_num="32", support_h265="1", support_dash="1",
    webid="7584476599598188067",
    collects_id="", cursor="", count="15",
)

# 合集列表（SyncMixList -> /aweme/v1/web/mix/listcollection）—— 抖小云 DouyinMixListParams
MIX_LIST_PARAMS = _with(
    version_code="170400", version_name="17.4.0",
    screen_width="1707", screen_height="1067",
    browser_name="Edge", browser_version="141.0.0.0", engine_version="141.0.0.0",
    cpu_core_num="32", support_h265="1", support_dash="1",
    webid="7584476599598188067",
    cursor="0", count="10",
)

# 合集内作品（SyncMixViedosByMixId -> /aweme/v1/web/mix/aweme）—— 抖小云 DouyinMixVideoParams
MIX_VIDEO_PARAMS = _with(
    version_code="170400", version_name="17.4.0",
    screen_width="1707", screen_height="1067",
    browser_name="Edge", browser_version="141.0.0.0", engine_version="141.0.0.0",
    cpu_core_num="32", support_h265="1", support_dash="1",
    webid="7584476599598188067",
    mix_id="", cursor="0", count="15",
)

# 短剧列表（SyncSeriesList -> /aweme/v1/web/series/collections）—— 抖小云 DouyinSeriesListParams
SERIES_LIST_PARAMS = _with(
    version_code="170400", version_name="17.4.0",
    screen_width="1707", screen_height="1067",
    browser_name="Edge", browser_version="141.0.0.0", engine_version="141.0.0.0",
    cpu_core_num="32", support_h265="1", support_dash="1",
    round_trip_time="50", webid="7584476599598188067",
    cursor="0", count="15",
)

# 短剧作品（SyncSeriesViedosByMSeriesId -> /aweme/v1/web/series/aweme）—— 抖小云 DouyinSeriesVideosParams
SERIES_VIDEO_PARAMS = _with(
    version_code="170400", version_name="17.4.0",
    screen_width="1707", screen_height="1067",
    browser_name="Edge", browser_version="141.0.0.0", engine_version="141.0.0.0",
    cpu_core_num="32", support_h265="1", support_dash="1",
    round_trip_time="50", webid="7584476599598188067",
    pull_type="2", series_id="", cursor="0", count="20",
)


# 请求节流：照抄抖小云 job/DouyinBasicSyncJob.cs 的随机等待
#   await Task.Delay(_random.Next(2, 10) * 1000);   // 翻页之间
#   await Task.Delay(_random.Next(1, 4) * 1000);    // 每次下载前
# 抖小云长期稳定、无风控，这个随机间隔是关键之一（比固定间隔更像真人）。
_PAGE_DELAY_RANGE = (2.0, 10.0)
_BATCH_LIMIT = 30          # 抖小云 IsSyncLimitReached：单次同步最多 30 条


async def _pace() -> None:
    """翻页之间的随机等待（秒）。

    每次调用都重新读取模块级 _PAGE_DELAY_RANGE，这样测试可以把它调成 0 跳过等待。
    """
    import asyncio
    import random

    lo, hi = _PAGE_DELAY_RANGE
    if hi <= 0:
        return
    await asyncio.sleep(random.uniform(lo, hi))


def _ordered_urls(url_list: list) -> list[str]:
    """把一组 url_list 规整成"抖音官方 play 直链优先"的有序去重列表。"""
    raw = [u for u in (url_list or []) if isinstance(u, str) and u.strip()]
    preferred = [u for u in raw if u.startswith("https://www.douyin.com/aweme/v1/play")]
    out: list[str] = []
    for u in preferred + [u for u in raw if u not in preferred]:
        if u not in out:
            out.append(u)
    return out


def _best_bitrate(aw: dict) -> dict | None:
    """选最佳码率档（照抄抖小云 GetBestMatchedVideoUrl）。

    抖小云按配置优先 H.265，否则 H.264，同编码内取 ``bit_rate`` 最大的档。
    这里默认偏好 H.264（兼容性最好：H.265 在部分播放器/浏览器上不可播），
    同编码取最高码率；有 H.265 且没有 H.264 时才用它。
    """
    video = aw.get("video") or {}
    rates = [r for r in (video.get("bit_rate") or []) if isinstance(r, dict)]
    usable = [r for r in rates if _ordered_urls((r.get("play_addr") or {}).get("url_list"))]

    def bw(r: dict) -> int:
        try:
            return int(r.get("bit_rate") or 0)
        except (TypeError, ValueError):
            return 0

    h264 = [r for r in usable if int(r.get("is_h265") or 0) == 0]
    if h264:
        return max(h264, key=bw)
    return max(usable, key=bw) if usable else None


def _play_url_list(aw: dict) -> list[str]:
    """从 aweme 结构里取**全部**可直连下载的播放地址（去重、保序）。

    照抄抖小云：
    1. 先按 ``GetBestMatchedVideoUrl`` 选最佳码率档（H.264 优先、码率最高）；
    2. 该档的 play_addr 镜像排最前；
    3. 再依次附上**其它所有码率档**的全部镜像与 download_addr ——
       抖小云 ``SwitchOtherUrlAddressDown`` 就是在首选失败后遍历所有档的所有地址重试。

    只留第一条是不够的：抖音直链带 ``sign=`` 会过期、也可能被 CDN 限流，
    多档多镜像轮换才能把单条素材的失败率压下来。
    """
    video = aw.get("video") or {}
    best = _best_bitrate(aw)
    ordered: list[str] = []
    if best:
        ordered += _ordered_urls((best.get("play_addr") or {}).get("url_list"))
    # 首选档的顶层 play_addr（部分响应没有 bit_rate，只有顶层 play_addr）
    ordered += _ordered_urls((video.get("play_addr") or {}).get("url_list"))
    # 其它码率档的全部镜像
    for r in (video.get("bit_rate") or []):
        if not isinstance(r, dict) or r is best:
            continue
        ordered += _ordered_urls((r.get("play_addr") or {}).get("url_list"))
    # 最后兜底 download_addr
    ordered += _ordered_urls((video.get("download_addr") or {}).get("url_list"))
    out: list[str] = []
    for u in ordered:
        if u and u not in out:
            out.append(u)
    return out


def _pick_play_url(aw: dict) -> str:
    """取首选的直连下载地址（保持向后兼容；完整列表见 ``_play_url_list``）。"""
    urls = _play_url_list(aw)
    return urls[0] if urls else ""


def _client(cookie: str, referer: str) -> httpx.AsyncClient:
    from app.core import outbound

    return httpx.AsyncClient(
        base_url=_HOST,
        headers={"User-Agent": _UA, "Referer": referer, "Cookie": cookie},
        timeout=15,
        **outbound.httpx_proxy_kwargs("douyin"),
    )


@asynccontextmanager
async def _http(cookie: str, referer: str):
    """限速 + 配好的 httpx 客户端。所有抖音出站请求都应走这里。

    注意：限速是**逐请求**的（交给 _ThrottledClient），不是"进会话取一次"——
    否则会话内 for 循环翻页会连发，等于没限速。
    """
    from app.core import rate_limit

    async with rate_limit._ThrottledClient.wrap("douyin", _client(cookie, referer)) as client:
        yield client


async def _fetch_source_json(
    path: str,
    params: dict,
    cookie: str,
    *,
    method: str = "GET",
    referer: str = _HOST,
) -> dict | None:
    """取抖音接口 JSON；纯 HTTP 被 WAF 拦时回退到受管浏览器同源 fetch。

    背景：我的收藏/点赞/收藏短剧这几个接口在纯 HTTP 下会被 WAF 直接挡掉，
    响应体是字符串 ``blocked``（HTTP 仍是 200）。纯 Python 补 a_bogus/uifid 也无效
    ——缺的是页面安全 SDK 运行时生成的签名。改用同源 fetch 后平台 SDK 会自行补齐，
    实测返回正常 JSON。浏览器不可用时保留原行为（返回 None），不把同步整条打挂。
    """
    async with _http(cookie, referer) as client:
        resp = await (
            client.get(path, params=params) if method.upper() == "GET"
            else client.post(path, params=params)
        )
    blocked = resp.status_code == 200 and (resp.text or "").strip() == "blocked"
    if resp.status_code == 200 and not blocked:
        try:
            return resp.json()
        except ValueError:
            return None  # Cookie 失效时常回空体/HTML，交给上层当"无数据"
    if not blocked:
        return None  # 非 200 且不是 blocked：维持既有语义（无数据）
    # 被 WAF 拦 → 浏览器兜底
    from app.modules import douyin_browser_fetch

    try:
        return await douyin_browser_fetch.fetch_json(
            path, params, cookie, method=method, referer=referer
        )
    except Exception as exc:  # noqa: BLE001 兜底是增强项：失败只记日志，不打断整批同步
        logging.getLogger("platform.douyin").warning(
            "抖音浏览器兜底抓取失败 %s: %s", path, exc
        )
        return None


async def list_collection(cookie: str, cursor: str = "0", count: str = "10") -> dict | None:
    """收藏列表（dysync SyncCollectVideos）——纯 HTTP 被 WAF 拦时浏览器兜底。"""
    params = dict(COLLECT_PARAMS, cursor=cursor, count=count)
    return await _fetch_source_json(
        "/aweme/v1/web/aweme/listcollection", params, cookie, method="POST"
    )


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
    async with _http(cookie, referer) as client:
        resp = await client.get("/aweme/v1/web/user/following/list", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None


_COLLECT_REFERER = (
    "https://www.douyin.com/user/self?from_tab_name=main"
    "&showSubTab=favorite_folder&showTab=favorite_collection"
)


async def collect_folder_list(cookie: str, cursor: str = "0") -> dict | None:
    """自定义收藏夹列表（dysync SyncCollectFolderList -> /aweme/v1/web/collects/list）。"""
    params = dict(COLLECT_FOLDER_LIST_PARAMS, cursor=cursor, count="10")
    async with _http(cookie, _COLLECT_REFERER) as client:
        resp = await client.get("/aweme/v1/web/collects/list", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None


async def collect_folder_videos(
    cookie: str, collects_id: str, cursor: str = "0", count: str = "15"
) -> dict | None:
    """收藏夹内作品（dysync SyncCollectVideosByCollectId -> /aweme/v1/web/collects/video/list）。"""
    params = dict(COLLECT_FOLDER_VIDEO_PARAMS, collects_id=collects_id, cursor=cursor, count=count)
    async with _http(cookie, _COLLECT_REFERER) as client:
        resp = await client.get("/aweme/v1/web/collects/video/list", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None


async def mix_list(cookie: str, cursor: str = "0") -> dict | None:
    """收藏的合集列表（dysync SyncMixList -> /aweme/v1/web/mix/listcollection）。"""
    params = dict(MIX_LIST_PARAMS, cursor=cursor, count="10")
    async with _http(cookie, _HOST) as client:
        resp = await client.get("/aweme/v1/web/mix/listcollection", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None


async def mix_videos(cookie: str, mix_id: str, cursor: str = "0", count: str = "15") -> dict | None:
    """合集内作品（dysync SyncMixViedosByMixId -> /aweme/v1/web/mix/aweme）。"""
    params = dict(MIX_VIDEO_PARAMS, mix_id=mix_id, cursor=cursor, count=count)
    async with _http(cookie, _HOST) as client:
        resp = await client.get("/aweme/v1/web/mix/aweme", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None


async def series_list(cookie: str, cursor: str = "0") -> dict | None:
    """收藏的短剧列表（dysync SyncSeriesList -> /aweme/v1/web/series/collections）。"""
    params = dict(SERIES_LIST_PARAMS, cursor=cursor, count="15")
    async with _http(cookie, _HOST) as client:
        resp = await client.get("/aweme/v1/web/series/collections", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None


async def series_videos(
    cookie: str, series_id: str, cursor: str = "0", count: str = "20"
) -> dict | None:
    """短剧剧集（dysync SyncSeriesViedosByMSeriesId -> /aweme/v1/web/series/aweme）。"""
    params = dict(SERIES_VIDEO_PARAMS, series_id=series_id, cursor=cursor, count=count)
    return await _fetch_source_json(
        "/aweme/v1/web/series/aweme", params, cookie, method="GET"
    )


def _aweme_to_item(aw: dict, fallback_author_id: str = "") -> dict:
    """把抖音 aweme 结构规整成统一的 content_item 字典。

    照抄抖小云 DouyinBasicSyncJob.ProcessVideoList / SaveVideos 的取值：
    aweme_list[i] -> statistics(digg/comment/collect/share/play) + author + video.play_addr。
    """
    stats = aw.get("statistics") or {}
    author = aw.get("author") or {}
    aweme_id = aw.get("aweme_id", "")
    return {
        "external_id": str(aweme_id),
        "content_type": "video",
        "title": aw.get("desc", ""),
        "author_name": author.get("nickname", ""),
        "author_id": author.get("sec_uid", "") or fallback_author_id,
        "url": f"https://www.douyin.com/video/{aweme_id}",
        "cover_url": (((aw.get("video") or {}).get("cover") or {}).get("url_list") or [""])[0],
        # 直连播放地址：下载走它 + Cookie，不依赖 yt-dlp。
        # download_url 是首选；download_urls 留全部镜像，供失败重试时轮换。
        "extra": {"download_url": _pick_play_url(aw), "download_urls": _play_url_list(aw)},
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


class DouyinAdapter:
    platform = "douyin"
    label = "抖音"
    credential_kind = "cookie"
    supports_qr = True  # 经 browser_qr（Playwright），未装浏览器时 API 会返回明确报错
    # 账号级单例同步源：整个账号只有一份，可在「账号」页用开关直接打开（对齐抖小云）。
    singleton_sync_sources = ("favorite",)
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

        逐项对应抖小云 VideoTypeEnum 的同步入口：
        - favorite（我的收藏）：无列表接口，返回虚拟项，remote_id 用 sec_user_id 占位。
        - collection（收藏的合集）：/aweme/v1/web/mix/listcollection 真实列表。
        - collect_folder（自定义收藏夹）：/aweme/v1/web/collects/list 真实列表。
        - series（收藏的短剧）：/aweme/v1/web/series/collections 真实列表。
        - creator（关注的博主）：/aweme/v1/web/user/following/list 分页到 ~50 条。
        其它类型或缺少必需凭证字段时返回 []（不抛异常）。
        """
        cookie = credential.get("cookie", "")
        sec_user_id = (credential.get("sec_user_id") or "").strip()
        if source_type == "favorite":
            return [{"remote_id": sec_user_id or "self", "title": "我的收藏"}]
        if source_type == "creator":
            if not sec_user_id:
                return []
            return await self._list_followings(cookie, sec_user_id)
        if source_type == "collection":
            return await self._list_mixes(cookie)
        if source_type == "collect_folder":
            return await self._list_collect_folders(cookie)
        if source_type == "series":
            return await self._list_series(cookie)
        return []

    async def _list_mixes(self, cookie: str, limit: int = 100) -> list[dict]:
        """收藏的合集列表。防御式：请求失败只返回已拿到的部分。"""
        out: list[dict] = []
        cursor = "0"
        try:
            while len(out) < limit:
                data = await mix_list(cookie, cursor=cursor)
                if not data or data.get("status_code") != 0:
                    break
                infos = data.get("mix_infos") or []
                if not infos:
                    break
                for m in infos:
                    mix_id = str(m.get("mix_id") or "")
                    if not mix_id:
                        continue
                    out.append({"remote_id": mix_id, "title": m.get("mix_name") or mix_id})
                    if len(out) >= limit:
                        break
                if data.get("has_more") != 1:
                    break
                cursor = str(data.get("cursor", "0"))
                await _pace()
        except (httpx.HTTPError, ValueError):
            return out[:limit]
        return out[:limit]

    async def _list_collect_folders(self, cookie: str, limit: int = 100) -> list[dict]:
        """自定义收藏夹列表。"""
        out: list[dict] = []
        cursor = "0"
        try:
            while len(out) < limit:
                data = await collect_folder_list(cookie, cursor=cursor)
                if not data or data.get("status_code") != 0:
                    break
                folders = data.get("collects_list") or []
                if not folders:
                    break
                for f in folders:
                    fid = str(f.get("collects_id_str") or f.get("collects_id") or "")
                    if not fid:
                        continue
                    out.append({"remote_id": fid, "title": f.get("collects_name") or fid})
                    if len(out) >= limit:
                        break
                if not data.get("has_more"):
                    break
                cursor = str(data.get("cursor", "0"))
                await _pace()
        except (httpx.HTTPError, ValueError):
            return out[:limit]
        return out[:limit]

    async def _list_series(self, cookie: str, limit: int = 100) -> list[dict]:
        """收藏的短剧列表。"""
        out: list[dict] = []
        cursor = "0"
        try:
            while len(out) < limit:
                data = await series_list(cookie, cursor=cursor)
                if not data or data.get("status_code") != 0:
                    break
                infos = data.get("series_infos") or []
                if not infos:
                    break
                for s in infos:
                    sid = str(s.get("series_id") or "")
                    if not sid:
                        continue
                    out.append({"remote_id": sid, "title": s.get("series_name") or sid})
                    if len(out) >= limit:
                        break
                if data.get("has_more") != 1:
                    break
                cursor = str(data.get("cursor", "0"))
                await _pace()
        except (httpx.HTTPError, ValueError):
            return out[:limit]
        return out[:limit]

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
                await _pace()      # 翻页之间随机等待（照抄抖小云）
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
                await _pace()      # 翻页之间随机等待（照抄抖小云）
        except (httpx.HTTPError, ValueError):
            pass
        return out[:limit]

    async def fetch_source(self, credential: dict, source_type: str, remote_id: str, limit: int) -> list[dict]:
        """拉取订阅源内容（逐项对应抖小云 VideoTypeEnum）。

        - favorite：我的收藏（POST /aweme/v1/web/aweme/listcollection）
        - collect_folder：某收藏夹内作品（/aweme/v1/web/collects/video/list）
        - collection：某合集内作品（/aweme/v1/web/mix/aweme）
        - series：某短剧剧集（/aweme/v1/web/series/aweme）
        - creator：博主主页视频（/aweme/v1/web/aweme/post，无尾斜杠，无需签名）
        """
        cookie = credential.get("cookie", "")
        if source_type == "creator":
            return await self._fetch_creator_videos(cookie, remote_id, limit)
        if source_type == "collect_folder":
            return await self._fetch_paged(cookie, collect_folder_videos, limit, collects_id=remote_id)
        if source_type == "collection":
            return await self._fetch_paged(cookie, mix_videos, limit, mix_id=remote_id)
        if source_type == "series":
            return await self._fetch_paged(cookie, series_videos, limit, series_id=remote_id)
        if source_type != "favorite":
            return []
        return await self._fetch_paged(cookie, list_collection, limit)

    async def _fetch_paged(self, cookie, fn, limit: int, cursor_param: str = "cursor", **extra) -> list[dict]:
        """通用分页抓取：aweme_list 逐条映射成 content_item，按服务端 cursor 翻页。

        查表函数签名统一为 fn(cookie, <cursor_param>=cursor, count=..., **extra)。
        单次同步上限 30（照抄抖小云 IsSyncLimitReached），翻页之间随机等待。
        """
        limit = min(limit, _BATCH_LIMIT)
        items: list[dict] = []
        cursor = "0"
        kwargs = dict(extra)
        if "sec_user_id" in kwargs and not kwargs["sec_user_id"]:
            kwargs.pop("sec_user_id")
        while len(items) < limit:
            kw = {cursor_param: cursor, "count": "20", **kwargs}
            data = await fn(cookie, **kw)
            if not data or data.get("status_code") != 0:
                break
            for aw in data.get("aweme_list") or []:
                items.append(_aweme_to_item(aw, fallback_author_id=kwargs.get("sec_user_id", "")))
                if len(items) >= limit:
                    break
            if not data.get("has_more"):
                break
            cursor = str(data.get("cursor") or data.get("max_cursor") or "0")
            await _pace()
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
        # 单次同步上限 30：照抄抖小云 IsSyncLimitReached（避免一次拉太多触发风控，
        # 剩下的等下次任务继续，配合订阅周期自然消化）
        limit = min(limit, _BATCH_LIMIT)
        base = "/aweme/v1/web/aweme/post"
        referer = f"https://www.douyin.com/user/{sec_user_id}"
        items: list[dict] = []
        max_cursor = "0"
        async with _http(cookie, referer) as client:
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
                    # 被 Argus 拦时改由页面自身完成签名：在受管浏览器里同源请求。
                    from app.modules.douyin_browser_fetch import fetch_json

                    payload = await fetch_json(base, params, cookie, referer=referer)
                    for aw in payload.get("aweme_list") or []:
                        items.append(_aweme_to_item(aw, fallback_author_id=sec_user_id))
                        if len(items) >= limit:
                            break
                    if not payload.get("has_more"):
                        break
                    max_cursor = str(payload.get("max_cursor") or payload.get("cursor") or "0")
                    await _pace()
                    continue
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
                    items.append(_aweme_to_item(aw, fallback_author_id=sec_user_id))
                    if len(items) >= limit:
                        break
                if not data.get("has_more"):
                    break
                max_cursor = str(data.get("max_cursor", "0"))
                await _pace()      # 翻页之间随机等待（照抄抖小云）
        return items[:limit]


register(DouyinAdapter())
