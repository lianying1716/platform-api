"""抖音适配器。协议来源：dysync.net service/DouyinHttpClientService.cs +
utils/DouyinRequestParamManager.cs（MIT，纯 HTTP + Cookie，无签名依赖）。

凭证 JSON: { cookie, sec_user_id, my_user_id }
体检逻辑与 dysync CheckCookie 一致：
- 有 sec_user_id：调关注列表接口，StatusCode==0 即在线
- 无 sec_user_id：调收藏列表接口判断
扫码：抖音无公开扫码 API，走浏览器方案（browser_qr，需安装 playwright），
手动粘 Cookie 是兜底。
"""

import httpx

from app.modules.base import CheckResult, QrPoll, QrStart, register
from app.modules.crawler import douyin_sign


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


def _client(cookie: str, referer: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_HOST,
        headers={"User-Agent": _UA, "Referer": referer, "Cookie": cookie},
        timeout=15,
    )


async def list_collection(cookie: str, cursor: str = "0", count: str = "10") -> dict | None:
    """收藏列表（dysync SyncCollectVideos）。"""
    params = dict(BASE_PARAMS, version_code="290100", version_name="29.1.0", cursor=cursor, count=count)
    async with _client(cookie, _HOST) as client:
        resp = await client.post("/aweme/v1/web/aweme/listcollection", params=params)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            # Cookie 无效时抖音常返回空体或 HTML
            return None


async def following_list(cookie: str, sec_user_id: str, offset: str = "1", count: str = "10") -> dict | None:
    """关注列表（dysync SyncMyFollows）。"""
    params = dict(
        BASE_PARAMS,
        user_id="",
        sec_user_id=sec_user_id,
        offset=offset,
        count=count,
        source_type="1",
        gps_access="0",
        address_book_access="0",
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
        offset = 1
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
                offset += count
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
        offset = 1
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
                offset += count
        except (httpx.HTTPError, ValueError):
            pass
        return out[:limit]

    async def fetch_source(self, credential: dict, source_type: str, remote_id: str, limit: int) -> list[dict]:
        """拉取订阅源内容。

        - favorite/collection：我的收藏（/aweme/v1/web/aweme/listcollection）
        - creator：博主主页视频（/aweme/v1/web/aweme/post/，a_bogus 签名 + 登录 Cookie）
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
        """博主主页视频列表（a_bogus 签名）。签名失败/风控抛可读异常。"""
        from urllib.parse import urlencode

        sec_user_id = str(sec_user_id or "").strip()
        if not sec_user_id:
            return []
        base = "/aweme/v1/web/aweme/post/"
        referer = f"https://www.douyin.com/user/{sec_user_id}"
        items: list[dict] = []
        max_cursor = "0"
        async with _client(cookie, referer) as client:
            while len(items) < limit:
                params = dict(
                    BASE_PARAMS,
                    sec_user_id=sec_user_id,
                    count="18",
                    max_cursor=max_cursor,
                    locate_query="false",
                    publish_video_strategy_type="2",
                )
                qs = urlencode(params)
                try:
                    a_bogus = douyin_sign.get_a_bogus(base, qs, _UA)
                except Exception as exc:  # noqa: BLE001 签名不可用时给可读错误
                    raise RuntimeError(f"抖音签名失败（{exc}），无法拉取博主视频") from exc
                resp = await client.get(f"{base}?{qs}&a_bogus={a_bogus}")
                if resp.status_code in (403, 412):
                    raise RuntimeError("抖音风控拦截（HTTP %s），请稍后再试" % resp.status_code)
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
