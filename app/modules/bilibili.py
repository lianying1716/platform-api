"""B 站适配器。协议来源：bili-sync crates/bili_sync/src/bilibili/credential.rs（MIT）。

凭证 JSON: { sessdata, bili_jct, buvid3, dedeuserid, ac_time_value }
注意：B 站把 refresh_token 叫 ac_time_value，不要改名。
扫码：官方 passport 接口，纯 HTTP，无需浏览器。
"""

import httpx

from app.modules.base import CheckResult, QrPoll, QrStart, make_qr_png_b64, register


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

# 扫码 poll 的 data.code 含义
_CODE_SUCCESS = 0
_CODE_NOT_SCANNED = 86101
_CODE_SCANNED = 86090
_CODE_EXPIRED = 86038


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com"}, timeout=15)


def _cookies(credential: dict) -> dict:
    return {
        "SESSDATA": credential.get("sessdata", ""),
        "bili_jct": credential.get("bili_jct", ""),
        "buvid3": credential.get("buvid3", ""),
        "DedeUserID": credential.get("dedeuserid", ""),
    }


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
        async with _client() as client:
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
            return CheckResult(ok=True, message=msg, external_id=mid, display_name=name)

    async def qr_start(self) -> QrStart:
        async with _client() as client:
            resp = await client.get(_QR_GENERATE)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"申请二维码失败: {data}")
            url = data["data"]["url"]
            qrcode_key = data["data"]["qrcode_key"]
            return QrStart(session_data={"qrcode_key": qrcode_key}, qr_image_b64=make_qr_png_b64(url))

    async def qr_poll(self, session_data: dict) -> QrPoll:
        async with _client() as client:
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
        缺少必需凭证字段或类型不支持时返回 []（不抛异常）。
        """
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
        async with _client() as client:
            resp = await client.get(_FAV_LIST_ALL, params={"up_mid": mid}, cookies=_cookies(credential))
            data = resp.json().get("data") or {}
        return [
            {"remote_id": str(f["id"]), "title": f.get("title", "")}
            for f in (data.get("list") or [])
            if f.get("id") is not None
        ]

    async def _list_collections(self, credential: dict, mid: str) -> list[dict]:
        """合集/系列列表。返回结构不稳定，解析防御式，拿不到就返回 []。"""
        async with _client() as client:
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
        async with _client() as client:
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
        - 粉丝：``/x/relation/stat?vmid=``（免登录，best-effort，失败留 0）。
        缺少 dedeuserid 返回 []；网络异常返回已拿到的部分（不抛）。
        """
        mid = str(credential.get("dedeuserid") or "").strip()
        if not mid:
            return []
        out: list[dict] = []
        page_size = 50
        page = 1
        try:
            async with _client() as client:
                while len(out) < limit:
                    resp = await client.get(
                        _FOLLOWINGS,
                        params={"mid": mid, "pn": str(page), "ps": str(page_size)},
                        cookies=_cookies(credential),
                    )
                    data = resp.json().get("data") or {}
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
                    page += 1
        except (httpx.HTTPError, ValueError):
            pass
        # 粉丝数 best-effort（逐条；失败不影响整表）
        for c in out:
            c["follower_count"] = await self._follower_count(c["author_id"], credential)
        return out[:limit]

    async def _follower_count(self, vmid: str, credential: dict) -> int:
        try:
            async with _client() as client:
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
        """拉取一个收藏夹里的视频列表，规整为统一 item。"""
        if source_type != "favorite":
            return []
        items: list[dict] = []
        page = 1
        async with _client() as client:
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
                                "play": (m.get("cnt_info") or {}).get("play", 0),
                                "favorite": (m.get("cnt_info") or {}).get("collect", 0),
                                "comment": (m.get("cnt_info") or {}).get("danmaku", 0),
                            },
                        }
                    )
                if not data.get("has_more"):
                    break
                page += 1
        return items[:limit]


register(BilibiliAdapter())
