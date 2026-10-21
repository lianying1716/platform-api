"""B 站适配器。协议来源：bili-sync crates/bili_sync/src/bilibili/credential.rs（MIT）。

凭证 JSON: { sessdata, bili_jct, buvid3, dedeuserid, ac_time_value }
注意：B 站把 refresh_token 叫 ac_time_value，不要改名。
扫码：官方 passport 接口，纯 HTTP，无需浏览器。
"""

import base64
import re
import time
from contextlib import asynccontextmanager

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.modules.base import CheckResult, QrPoll, QrStart, make_qr_png_b64, register
from app.modules.crawler import bili_sign


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

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_QR_GENERATE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
_QR_POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
_COOKIE_INFO = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
_GET_BUVID = "https://api.bilibili.com/x/web-frontend/getbuvid"
_NAV = "https://api.bilibili.com/x/web-interface/nav"
_FAV_LIST_ALL = "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
_SEASONS_SERIES = "https://api.bilibili.com/x/polymer/web-space/seasons_series_list"
_FOLLOWINGS = "https://api.bilibili.com/x/relation/followings"
_WATCH_LATER = "https://api.bilibili.com/x/v2/history/toview"
_CORRESPOND = "https://www.bilibili.com/correspond/1/"
_REFRESH = "https://passport.bilibili.com/x/passport-login/web/cookie/refresh"
_CONFIRM_REFRESH = "https://passport.bilibili.com/x/passport-login/web/confirm/refresh"
_VIEW = "https://api.bilibili.com/x/web-interface/view"
_PLAYURL = "https://api.bilibili.com/x/player/playurl"

# fnval 位掩码（照抄 bili-sync / B站文档）：
#   16=DASH 64=HDR 128=4K 256=DolbyAudio 512=DolbyVision 1024=8K 2048=AV1
# 全开才能拿到最高档 + AV1/HEVC 的 DASH 流。
_FNVAL_DASH = 4048

# bili-sync 内置公钥（MIT）：用于生成 cookie refresh 的 correspond path。
_REFRESH_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""

# 扫码 poll 的 data.code 含义
_CODE_SUCCESS = 0
_CODE_NOT_SCANNED = 86101
_CODE_SCANNED = 86090
_CODE_EXPIRED = 86038


def _client() -> httpx.AsyncClient:
    from app.core import outbound

    return httpx.AsyncClient(
        headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com"},
        timeout=15,
        **outbound.httpx_proxy_kwargs("bilibili"),
    )


# 翻页之间的随机等待（秒）。B站 412 风控很敏感，翻页必须拉开间隔；
# 抖音侧早有 _PACE，B站此前完全没有 —— 这里补齐（保守档）。
_PAGE_DELAY_RANGE = (2.0, 6.0)


async def _pace() -> None:
    """翻页/连续请求之间的随机等待（测试可把 _PAGE_DELAY_RANGE 调成 0 跳过）。"""
    import asyncio
    import random

    lo, hi = _PAGE_DELAY_RANGE
    if hi <= 0:
        return
    await asyncio.sleep(random.uniform(lo, hi))


@asynccontextmanager
async def _http():
    """限速 + 配好的 httpx 客户端。所有 B站出站请求都应走这里。

    两层保险：逐请求令牌桶（rate_limit，保守档 12 次/分钟）+ 调用处的 _pace 翻页间隔。
    限速必须逐请求（交 _ThrottledClient），否则会话内翻页会连发。
    """
    from app.core import rate_limit

    async with rate_limit._ThrottledClient.wrap("bilibili", _client()) as client:
        yield client


def _cookies(credential: dict) -> dict:
    return {
        "SESSDATA": credential.get("sessdata", ""),
        "bili_jct": credential.get("bili_jct", ""),
        "buvid3": credential.get("buvid3", ""),
        "DedeUserID": credential.get("dedeuserid", ""),
        "ac_time_value": credential.get("ac_time_value", ""),
    }


def _correspond_path() -> str:
    """RSA-OAEP(SHA256) 加密 refresh_{毫秒-20s}，hex 编码（照抄 bili-sync）。"""
    public_key = serialization.load_pem_public_key(_REFRESH_PUBLIC_KEY.encode())
    plaintext = f"refresh_{int(time.time() * 1000) - 20_000}".encode()
    encrypted = public_key.encrypt(
        plaintext,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return encrypted.hex()


def _set_cookie_map(response) -> dict:
    """兼容 httpx Cookies 与测试里的普通 dict。"""
    raw = response.cookies
    if hasattr(raw, "get"):
        return {k: raw.get(k, "") for k in ("SESSDATA", "bili_jct", "DedeUserID")}
    return {}


def _https(url) -> str:
    """B站头像/封面常以 `//i0.hdslb.com/...` 返回，补全协议。"""
    if not url:
        return ""
    url = str(url)
    return "https:" + url if url.startswith("//") else url


class BilibiliAdapter:
    platform = "bilibili"
    label = "B站"
    credential_kind = "cookie"
    supports_qr = True
    # B站收藏夹是「多个命名收藏夹」，不是账号级单例源；「稍后再看」是账号级单例。
    singleton_sync_sources: tuple[str, ...] = ("watch_later",)
    credential_fields = [
        {"key": "sessdata", "label": "SESSDATA", "required": True},
        {"key": "bili_jct", "label": "bili_jct", "required": True},
        {"key": "buvid3", "label": "buvid3", "required": False},
        {"key": "dedeuserid", "label": "DedeUserID", "required": False},
        {"key": "ac_time_value", "label": "ac_time_value (refresh_token)", "required": False},
    ]

    def parse_manual_input(self, raw: dict) -> dict:
        sessdata = (raw.get("sessdata") or "").strip()
        bili_jct = (raw.get("bili_jct") or "").strip()
        if not sessdata or not bili_jct:
            raise ValueError("B站凭证至少需要 SESSDATA 和 bili_jct")
        return {
            "sessdata": sessdata,
            "bili_jct": bili_jct,
            "buvid3": (raw.get("buvid3") or "").strip(),
            "dedeuserid": (raw.get("dedeuserid") or "").strip(),
            "ac_time_value": (raw.get("ac_time_value") or "").strip(),
        }

    async def check(self, credential: dict) -> CheckResult:
        async with _http() as client:
            resp = await client.get(_COOKIE_INFO, cookies=_cookies(credential))
            data = resp.json()
            if data.get("code") != 0:
                return CheckResult(ok=False, message=f"登录态无效: {data.get('message', data.get('code'))}")
            need_refresh = bool(data.get("data", {}).get("refresh"))
            # 顺带拿昵称
            nav = await client.get(_NAV, cookies=_cookies(credential))
            nav_data = nav.json().get("data", {})
            name = nav_data.get("uname", "")
            mid = str(nav_data.get("mid", "") or "")
            msg = "在线（平台建议刷新 Cookie）" if need_refresh else "在线"
            return CheckResult(ok=True, message=msg, external_id=mid, display_name=name, need_refresh=need_refresh)

    async def qr_start(self) -> QrStart:
        async with _http() as client:
            resp = await client.get(_QR_GENERATE)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"申请二维码失败: {data}")
            url = data["data"]["url"]
            qrcode_key = data["data"]["qrcode_key"]
            return QrStart(session_data={"qrcode_key": qrcode_key}, qr_image_b64=make_qr_png_b64(url))

    async def refresh(self, credential: dict) -> dict:
        """B站 Cookie 主动刷新（算法照抄 bili-sync，MIT）。

        步骤：correspond_path → refresh_csrf → cookie/refresh → confirm/refresh。
        新凭证保留旧 buvid3（设备指纹一致性）。
        """
        refresh_token = (credential.get("ac_time_value") or "").strip()
        if not refresh_token:
            raise ValueError("缺少 ac_time_value，无法自动刷新 B站 Cookie")
        async with _http() as client:
            correspond = await client.get(_CORRESPOND + _correspond_path(), cookies=_cookies(credential))
            match = re.search(r'<div id="1-name">(.+?)</div>', correspond.text)
            if not match:
                raise RuntimeError("获取 refresh_csrf 失败：响应中找不到 1-name")
            refresh_csrf = match.group(1)

            resp = await client.post(
                _REFRESH,
                cookies=_cookies(credential),
                data={
                    "csrf": credential.get("bili_jct", ""),
                    "refresh_csrf": refresh_csrf,
                    "refresh_token": refresh_token,
                    "source": "main_web",
                },
            )
            body = resp.json()
            if body.get("code") != 0:
                raise RuntimeError(f"刷新凭证失败: {body.get('message', body.get('code'))}")
            cookies = _set_cookie_map(resp)
            new_credential = {
                "sessdata": cookies.get("SESSDATA") or credential.get("sessdata", ""),
                "bili_jct": cookies.get("bili_jct") or credential.get("bili_jct", ""),
                "dedeuserid": cookies.get("DedeUserID") or credential.get("dedeuserid", ""),
                "ac_time_value": body.get("data", {}).get("refresh_token", ""),
                "buvid3": credential.get("buvid3", ""),
            }

            confirm = await client.post(
                _CONFIRM_REFRESH,
                cookies=_cookies(new_credential),
                data={
                    "csrf": new_credential["bili_jct"],
                    "refresh_token": refresh_token,  # 用旧 token 确认
                },
            )
            confirm_body = confirm.json()
            if confirm_body.get("code") != 0:
                raise RuntimeError(f"确认刷新失败: {confirm_body.get('message', confirm_body.get('code'))}")
            return new_credential

    async def qr_poll(self, session_data: dict) -> QrPoll:
        async with _http() as client:
            resp = await client.get(_QR_POLL, params={"qrcode_key": session_data["qrcode_key"]})
            body = resp.json()
            code = body.get("data", {}).get("code")
            if code == _CODE_NOT_SCANNED:
                return QrPoll(status="pending", message="等待扫码")
            if code == _CODE_SCANNED:
                return QrPoll(status="scanned", message="已扫码，请在手机上确认")
            if code == _CODE_EXPIRED:
                return QrPoll(status="expired", message="二维码已过期")
            if code != _CODE_SUCCESS:
                return QrPoll(status="error", message=f"未知状态: {body}")

            # 成功：凭证在 Set-Cookie 里，refresh_token 在 body 里
            jar = resp.cookies
            credential = {
                "sessdata": jar.get("SESSDATA", ""),
                "bili_jct": jar.get("bili_jct", ""),
                "dedeuserid": jar.get("DedeUserID", ""),
                "ac_time_value": body.get("data", {}).get("refresh_token", ""),
                "buvid3": "",
            }
            buvid_resp = await client.get(_GET_BUVID)
            credential["buvid3"] = buvid_resp.json().get("data", {}).get("buvid", "")
            result = await self.check(credential)
            return QrPoll(
                status="success",
                message="登录成功",
                credential=credential,
                external_id=result.external_id or credential["dedeuserid"],
                display_name=result.display_name,
            )

    async def list_sources(self, credential: dict, source_type: str) -> list[dict]:
        """列出可订阅的源，返回 [{remote_id, title}]。

        source_type：
        - favorite：自己创建的收藏夹（需 dedeuserid）
        - collection：自己创建的合集/系列（需 dedeuserid）
        - creator：自己关注的 UP 主（需 dedeuserid）
        - watch_later：稍后再看（账号级单例，无需额外标识）
        缺少必需凭证字段或类型不支持时返回 []（不抛异常）。
        """
        if source_type == "watch_later":
            return [{"remote_id": "watch_later", "title": "稍后再看"}]
        mid = str(credential.get("dedeuserid") or "").strip()
        if not mid:
            return []
        try:
            if source_type == "favorite":
                return await self._list_favorites(credential, mid)
            if source_type == "collection":
                return await self._list_collections(credential, mid)
            if source_type == "creator":
                return await self._list_followings(credential, mid)
        except (httpx.HTTPError, ValueError):
            # 网络/解析异常一律降级为空列表，由调用方决定如何提示
            return []
        return []

    async def _list_favorites(self, credential: dict, mid: str) -> list[dict]:
        async with _http() as client:
            resp = await client.get(_FAV_LIST_ALL, params={"up_mid": mid}, cookies=_cookies(credential))
            data = resp.json().get("data") or {}
        return [
            {"remote_id": str(f["id"]), "title": f.get("title", "")}
            for f in (data.get("list") or [])
            if f.get("id") is not None
        ]

    async def _list_collections(self, credential: dict, mid: str) -> list[dict]:
        """合集/系列列表。返回结构不稳定，解析防御式，拿不到就返回 []。"""
        async with _http() as client:
            resp = await client.get(
                _SEASONS_SERIES,
                params={"mid": mid, "page_num": "1", "page_size": "20"},
                cookies=_cookies(credential),
            )
            data = resp.json().get("data") or {}
        items_lists = data.get("items_lists") or {}
        out: list[dict] = []
        for id_key, list_key in (("season_id", "seasons_list"), ("series_id", "series_list")):
            for item in items_lists.get(list_key) or []:
                meta = item.get("meta") or item
                rid = meta.get(id_key) or item.get(id_key)
                if rid is None:
                    continue
                out.append({"remote_id": str(rid), "title": meta.get("name", "")})
        return out

    async def _list_followings(self, credential: dict, mid: str) -> list[dict]:
        async with _http() as client:
            resp = await client.get(
                _FOLLOWINGS, params={"mid": mid, "pn": "1", "ps": "20"}, cookies=_cookies(credential)
            )
            data = resp.json().get("data") or {}
        return [
            {"remote_id": str(f["mid"]), "title": f.get("uname", "")}
            for f in (data.get("list") or [])
            if f.get("mid") is not None
        ]

    async def list_followings(self, credential: dict, limit: int = 200) -> list[dict]:
        """关注的 UP 主**富列表**（供「关注博主」页）：真头像/昵称/签名/主页 + 粉丝数。

        - 列表：``/x/relation/followings`` 分页（pn/ps，每页 50）。
        - 粉丝：由 API 层带 24h 缓存补全（此处不逐条请求）。
        缺少 dedeuserid 返回 []；**认证失效/风控抛可读异常**（便于上层进入冷却），
        其余网络异常返回已拿到的部分。
        """
        mid = str(credential.get("dedeuserid") or "").strip()
        if not mid:
            return []
        out: list[dict] = []
        page_size = 50
        page = 1
        try:
            async with _http() as client:
                while len(out) < limit:
                    resp = await client.get(
                        _FOLLOWINGS,
                        params={"mid": mid, "pn": str(page), "ps": str(page_size)},
                        cookies=_cookies(credential),
                    )
                    if resp.status_code in (403, 412):
                        raise RuntimeError(f"B站风控校验失败（HTTP {resp.status_code}），请稍后再试")
                    payload = resp.json()
                    code = payload.get("code")
                    if code in (-352, -412, -509):
                        raise RuntimeError(f"B站风控校验失败（{payload.get('message', code)}），请稍后再试")
                    if code == -101:
                        raise RuntimeError("B站账号未登录或登录已过期，请重新登录该账号")
                    data = payload.get("data") or {}
                    rows = data.get("list") or []
                    if not rows:
                        break
                    for f in rows:
                        fm = f.get("mid")
                        if fm is None:
                            continue
                        out.append(
                            {
                                "author_id": str(fm),
                                "author_name": f.get("uname", ""),
                                "avatar": _https(f.get("face", "")),
                                "home_url": f"https://space.bilibili.com/{fm}",
                                "signature": f.get("sign", ""),
                                "verified": False,  # 关注列表接口不返回认证；由 prof 补充时再填
                                "follower_count": 0,
                                "like_count": 0,
                                "raw": f,
                            }
                        )
                        if len(out) >= limit:
                            break
                    total = int(data.get("total") or 0)
                    # 以 total 为权威停止条件；缺 total 时靠「空页」兜底（下方 if not rows）。
                    if total and len(out) >= total:
                        break
                    await _pace()      # 翻页间隔（B站 412 风控敏感）
                    page += 1
        except (httpx.HTTPError, ValueError):
            pass
        # 粉丝数不在此处逐条请求（无缓存会重复打平台）：由 API 层带 24h 缓存补全。
        return out[:limit]

    async def fetch_follower_count(self, credential: dict, vmid: str) -> int:
        """单个 UP 主的粉丝数（供 API 层带缓存调用）。失败返回 0。"""
        return await self._follower_count(str(vmid), credential)

    async def _follower_count(self, vmid: str, credential: dict) -> int:
        try:
            async with _http() as client:
                resp = await client.get(
                    "https://api.bilibili.com/x/relation/stat",
                    params={"vmid": vmid},
                    cookies=_cookies(credential),
                )
                data = resp.json().get("data") or {}
            return int(data.get("follower") or 0)
        except (httpx.HTTPError, ValueError, TypeError):
            return 0

    async def fetch_source(self, credential: dict, source_type: str, remote_id: str, limit: int) -> list[dict]:
        """拉取订阅源内容列表，规整为统一 item。

        - favorite：收藏夹（/x/v3/fav/resource/list）
        - creator：UP 主投稿（/x/space/wbi/arc/search，WBI 签名 + 登录 Cookie）
        """
        if source_type == "creator":
            return await self._fetch_creator_videos(credential, remote_id, limit)
        if source_type == "watch_later":
            return await self._fetch_watch_later(credential, limit)
        if source_type != "favorite":
            return []
        return await self._fetch_favorite_videos(credential, remote_id, limit)

    async def _fetch_watch_later(self, credential: dict, limit: int) -> list[dict]:
        """稍后再看（/x/v2/history/toview）。接口一次性返回全部，无分页参数。"""
        async with _http() as client:
            resp = await client.get(_WATCH_LATER, cookies=_cookies(credential))
            payload = resp.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"稍后再看拉取失败: {payload.get('message', payload.get('code'))}")
        out: list[dict] = []
        for v in ((payload.get("data") or {}).get("list") or []):
            bvid = str(v.get("bvid") or "")
            if not bvid:
                continue
            owner = v.get("owner") or {}
            stat = v.get("stat") or {}
            out.append(
                {
                    "external_id": bvid,
                    "content_type": "video",
                    "title": v.get("title", ""),
                    "author_name": owner.get("name", ""),
                    "author_id": str(owner.get("mid", "")),
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "cover_url": _https(v.get("pic", "")),
                    "published_at": _ts_to_iso(v.get("pubdate")),
                    "duration_sec": int(v.get("duration") or 0),
                    "metrics": {
                        "play": stat.get("view"),
                        "favorite": stat.get("favorite"),
                        "comment": stat.get("danmaku"),
                    },
                }
            )
            if len(out) >= limit:
                break
        return out

    async def _fetch_favorite_videos(self, credential: dict, remote_id: str, limit: int) -> list[dict]:
        items: list[dict] = []
        page = 1
        async with _http() as client:
            while len(items) < limit:
                resp = await client.get(
                    "https://api.bilibili.com/x/v3/fav/resource/list",
                    params={"media_id": remote_id, "pn": str(page), "ps": "20", "platform": "web"},
                    cookies=_cookies(credential),
                )
                data = resp.json().get("data") or {}
                medias = data.get("medias") or []
                if not medias:
                    break
                for m in medias:
                    items.append(
                        {
                            "external_id": str(m.get("bvid") or m.get("id")),
                            "content_type": "video",
                            "title": m.get("title", ""),
                            "author_name": (m.get("upper") or {}).get("name", ""),
                            "author_id": str((m.get("upper") or {}).get("mid", "")),
                            "url": f"https://www.bilibili.com/video/{m.get('bvid', '')}",
                            "cover_url": m.get("cover", ""),
                            # pubtime 为 Unix 秒；缺失留 None（同步侧 publish_after_days 依赖它）
                            "published_at": _ts_to_iso(m.get("pubtime")),
                            "metrics": {
                                "play": (m.get("cnt_info") or {}).get("play"),
                                "favorite": (m.get("cnt_info") or {}).get("collect"),
                                "comment": (m.get("cnt_info") or {}).get("danmaku"),
                            },
                        }
                    )
                if not data.get("has_more"):
                    break
                await _pace()          # 翻页间隔
                page += 1
        return items[:limit]

    async def _fetch_creator_videos(self, credential: dict, mid: str, limit: int) -> list[dict]:
        """UP 主投稿列表（WBI 签名）。命中风控（-352/-412/412）抛可读异常。"""
        from urllib.parse import urlencode

        mid = str(mid or "").strip()
        if not mid:
            return []
        cookies = _cookies(credential)
        img_key, sub_key = await bili_sign.fetch_wbi_keys(cookies)
        items: list[dict] = []
        page = 1
        async with _http() as client:
            while len(items) < limit:
                params = bili_sign.sign(
                    {
                        "mid": mid,
                        "pn": page,
                        "ps": 30,
                        "order": "pubdate",
                        "platform": "web",
                        "web_location": "1550101",
                    },
                    img_key,
                    sub_key,
                )
                url = f"https://api.bilibili.com/x/space/wbi/arc/search?{urlencode(params)}"
                resp = await client.get(url, cookies=cookies)
                if resp.status_code in (412, 403):
                    raise RuntimeError("B站风控校验失败（HTTP %s），请稍后再试" % resp.status_code)
                data = resp.json()
                code = data.get("code")
                if code in (-352, -412, -509):
                    raise RuntimeError(f"B站风控校验失败（{data.get('message', code)}），请稍后再试")
                if code != 0:
                    raise RuntimeError(f"B站投稿拉取失败: {data.get('message', code)}")
                vlist = ((data.get("data") or {}).get("list") or {}).get("vlist") or []
                if not vlist:
                    break
                for v in vlist:
                    items.append(
                        {
                            "external_id": str(v.get("bvid") or ""),
                            "content_type": "video",
                            "title": v.get("title", ""),
                            "author_name": v.get("author", ""),
                            "author_id": str(v.get("mid") or mid),
                            "url": f"https://www.bilibili.com/video/{v.get('bvid', '')}",
                            "cover_url": _https(v.get("pic", "")),
                            "published_at": _ts_to_iso(v.get("created")),
                            "metrics": {
                                "play": v.get("play"),
                                "comment": v.get("video_review") or v.get("comment"),
                                "favorite": v.get("favorites"),
                            },
                        }
                    )
                if len(items) >= limit:
                    break
                page_count = ((data.get("data") or {}).get("page") or {}).get("count") or 0
                if page_count and page >= int(page_count):
                    break
                await _pace()          # 翻页间隔
                page += 1
        return items[:limit]


def _pick_best_video_stream(streams: list[dict], max_height: int | None = None) -> dict | None:
    """按「清晰度优先、其次码率」选最高档视频流（照抄 bili-sync best_stream）。

    bili-sync 先比 quality（id），相同再按 codecs 偏好；我们用 id→bandwidth 近似，
    效果等价于"取最高画质"。
    """
    if max_height is not None:
        bounded = [v for v in streams if int(v.get("height") or 0) <= max_height]
        if bounded:
            streams = bounded
    if not streams:
        return None
    return max(streams, key=lambda v: (int(v.get("id") or 0), int(v.get("bandwidth") or 0)))


def _pick_best_audio_stream(streams: list[dict]) -> dict | None:
    if not streams:
        return None
    return max(streams, key=lambda a: (int(a.get("id") or 0), int(a.get("bandwidth") or 0)))


def _stream_urls(stream: dict | None) -> list[str]:
    """取一条 DASH 流的全部候选地址（baseUrl + backupUrl），照抄 bili-sync Stream::urls。

    B站为每个流给多个 CDN 备份地址；主地址失败要能轮换备份，否则一条素材直接失败。
    """
    if not stream:
        return []
    out: list[str] = []
    for u in [stream.get("baseUrl"), *(stream.get("backupUrl") or [])]:
        if isinstance(u, str) and u and u not in out:
            out.append(u)
    return out


async def resolve_play_urls(
    credential: dict,
    bvid: str,
    *,
    max_height: int | None = None,
) -> dict | None:
    """用 view + playurl 把 BV 号解析成可下载的 DASH 地址。

    这是 B站下载的正确入口（照抄 bili-sync：先 /x/web-interface/view 拿 cid，
    再 /x/player/playurl 拿 dash 流）。**不要**用 yt-dlp：B站对 yt-dlp 的元数据请求
    直接回 412 风控，实测连不带 Cookie 的公开视频都下不动。

    返回 ``{"video": [url...], "audio": [url...], "cid": int, "pages": [...]}``；
    多P视频的每个分P有独立 cid 与流地址，``pages`` 逐个给出，``video``/``audio``
    始终等于第一个分P（向后兼容单P调用方）。拿不到返回 None。
    """
    bvid = str(bvid or "").strip()
    if not bvid:
        return None
    cookies = _cookies(credential)
    async with _http() as client:
        try:
            view = await client.get(_VIEW, params={"bvid": bvid}, cookies=cookies)
            if view.status_code in (412, 403):
                raise RuntimeError(f"B站风控校验失败（HTTP {view.status_code}），请稍后再试")
            vdata = view.json()
        except (httpx.HTTPError, ValueError):
            return None
        if vdata.get("code") != 0:
            return None
        info = vdata.get("data") or {}
        cid = info.get("cid")
        if not cid:
            return None
        page_infos = info.get("pages") or [{"cid": cid, "page": 1, "part": info.get("title", "")}]

        async def _resolve_page(page_cid: int) -> dict | None:
            try:
                play = await client.get(
                    _PLAYURL,
                    params={
                        "bvid": bvid, "cid": page_cid,
                        "qn": 127 if max_height is None else {1080: 80, 720: 64}.get(max_height, 127),
                        "fnval": _FNVAL_DASH, "fourk": 1,
                    },
                    cookies=cookies,
                )
                if play.status_code in (412, 403):
                    raise RuntimeError(f"B站风控校验失败（HTTP {play.status_code}），请稍后再试")
                pdata = play.json()
            except (httpx.HTTPError, ValueError):
                return None
            if pdata.get("code") != 0:
                return None
            p = pdata.get("data") or {}
            dash = p.get("dash") or {}
            v_urls = _stream_urls(
                _pick_best_video_stream(dash.get("video") or [], max_height=max_height)
            )
            if not v_urls:
                durl = p.get("durl") or []
                mixed: list[str] = []
                for seg in durl:
                    for u in [seg.get("url"), *(seg.get("backup_url") or [])]:
                        if isinstance(u, str) and u and u not in mixed:
                            mixed.append(u)
                if not mixed:
                    return None
                return {"video": mixed, "audio": []}
            return {
                "video": v_urls,
                "audio": _stream_urls(_pick_best_audio_stream(dash.get("audio") or [])),
            }

        pages: list[dict] = []
        for page in page_infos:
            page_cid = page.get("cid")
            if not page_cid:
                continue
            streams = await _resolve_page(int(page_cid))
            if streams is None:
                continue
            pages.append({
                "cid": int(page_cid),
                "page": int(page.get("page") or len(pages) + 1),
                "part": page.get("part", ""),
                "duration": int(page.get("duration") or 0),
                **streams,
            })
        if not pages:
            return None
        first = pages[0]
        return {
            "video": first["video"],
            "audio": first["audio"],
            "cid": first["cid"],
            "pages": pages,
        }


register(BilibiliAdapter())
