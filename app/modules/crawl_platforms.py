"""采集向平台的账号适配器（快手 / 知乎 / 微博 / 贴吧）。

这四家只有「关键词采集」能力（无订阅同步），但账号中心仍需能录入/体检登录态——
否则 `crawl_service._platform_cookie` 永远取不到 Cookie，采集任务必然失败。

凭证 JSON: { cookie }（整段 Cookie 字符串）。
体检接口：
- 快手：POST www.kuaishou.com/graphql visionProfileUserList（result==1 视为在线）
- 知乎：GET www.zhihu.com/api/v4/me（返回 id/name 视为在线）
- 微博：GET m.weibo.cn/api/config（data.login 为 true 视为在线）
- 贴吧：GET tieba.baidu.com/f/user/json_userinfo（no==0 且有用户名视为在线）

接口来源：MediaCrawler media_platform/{kuaishou,zhihu,weibo,tieba}/client.py:pong
（NON-COMMERCIAL LEARNING LICENSE 1.1, Copyright (c) 2024 relakkes@gmail.com，仅自用学习）
快手 / 知乎支持浏览器扫码登录（browser_qr，与抖音/小红书同款）；微博 / 贴吧仅手动粘贴 Cookie。
"""

import functools
from pathlib import Path

import httpx

from app.modules.base import CheckResult, QrPoll, QrStart, register

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

_KS_GRAPHQL = "https://www.kuaishou.com/graphql"
_KS_PROFILE_QUERY = (
    Path(__file__).parent / "crawler" / "graphql" / "kuaishou" / "vision_profile_user_list.graphql"
)


@functools.lru_cache(maxsize=1)
def _ks_profile_query() -> str:
    """读取并缓存打包的 visionProfileUserList GraphQL 查询。"""
    return _KS_PROFILE_QUERY.read_text(encoding="utf-8")


class _CookieAdapter:
    """四家采集平台的公共骨架：整段 Cookie 录入、可选手动粘贴、体检各自实现。

    子类置 ``supports_qr = True`` 并覆写 qr_start/qr_poll 即可接入扫码。
    """

    platform = ""
    label = ""
    credential_kind = "cookie"
    supports_qr = False
    credential_fields = [
        {"key": "cookie", "label": "Cookie 字符串（整段粘贴）", "required": True},
    ]
    # 建议存在的关键字段（不强制，体检接口才是权威判定）
    hint_cookie_keys: tuple[str, ...] = ()

    def parse_manual_input(self, raw: dict) -> dict:
        cookie = (raw.get("cookie") or "").strip()
        if not cookie:
            raise ValueError(f"{self.label}凭证需要整段 Cookie 字符串")
        return {"cookie": cookie}

    async def qr_start(self) -> QrStart:  # pragma: no cover - 平台无扫码
        raise ValueError(f"{self.label}暂不支持扫码登录，请手动粘贴 Cookie")

    async def qr_poll(self, session_data: dict) -> QrPoll:  # pragma: no cover - 平台无扫码
        raise ValueError(f"{self.label}暂不支持扫码登录，请手动粘贴 Cookie")


class KuaishouAdapter(_CookieAdapter):
    platform = "kuaishou"
    label = "快手"
    supports_qr = True

    async def qr_start(self) -> QrStart:
        from app.modules.browser_qr import browser_qr_start

        return await browser_qr_start("kuaishou")

    async def qr_poll(self, session_data: dict) -> QrPoll:
        from app.modules.browser_qr import browser_qr_poll

        return await browser_qr_poll("kuaishou", session_data)

    async def check(self, credential: dict) -> CheckResult:
        headers = {
            "User-Agent": _UA,
            "Cookie": credential.get("cookie", ""),
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://www.kuaishou.com",
            "Referer": "https://www.kuaishou.com",
        }
        body = {
            "operationName": "visionProfileUserList",
            "variables": {"ftype": 1},
            "query": _ks_profile_query(),
        }
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                resp = await client.post(_KS_GRAPHQL, json=body)
                data = resp.json() or {}
        except (httpx.HTTPError, ValueError) as exc:
            return CheckResult(ok=False, message=f"请求失败: {exc}")
        result = ((data.get("data") or {}).get("visionProfileUserList") or {})
        if result.get("result") == 1:
            host = result.get("hostName") or ""
            return CheckResult(ok=True, message="在线", display_name=host)
        return CheckResult(ok=False, message="登录态无效（Cookie 失效或被风控）")


class ZhihuAdapter(_CookieAdapter):
    platform = "zhihu"
    label = "知乎"
    hint_cookie_keys = ("z_c0",)
    supports_qr = True

    async def qr_start(self) -> QrStart:
        from app.modules.browser_qr import browser_qr_start

        return await browser_qr_start("zhihu")

    async def qr_poll(self, session_data: dict) -> QrPoll:
        from app.modules.browser_qr import browser_qr_poll

        return await browser_qr_poll("zhihu", session_data)

    async def check(self, credential: dict) -> CheckResult:
        headers = {
            "User-Agent": _UA,
            "Cookie": credential.get("cookie", ""),
            "Referer": "https://www.zhihu.com/",
            "x-zse-93": "101_3_3.0",
            "x-requested-with": "fetch",
        }
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                resp = await client.get("https://www.zhihu.com/api/v4/me")
                data = resp.json() if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            return CheckResult(ok=False, message=f"请求失败: {exc}")
        if data.get("id") and data.get("name"):
            return CheckResult(
                ok=True, message="在线", external_id=str(data["id"]), display_name=data.get("name", "")
            )
        return CheckResult(ok=False, message="登录态无效（Cookie 失效或缺少 z_c0）")


class WeiboAdapter(_CookieAdapter):
    platform = "weibo"
    label = "微博"
    hint_cookie_keys = ("SUB", "SUBP")

    async def check(self, credential: dict) -> CheckResult:
        headers = {
            "User-Agent": _UA,
            "Cookie": credential.get("cookie", ""),
            "Referer": "https://m.weibo.cn/",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                resp = await client.get("https://m.weibo.cn/api/config")
                payload = resp.json() or {}
        except (httpx.HTTPError, ValueError) as exc:
            return CheckResult(ok=False, message=f"请求失败: {exc}")
        data = payload.get("data") or {}
        if data.get("login"):
            return CheckResult(
                ok=True, message="在线", external_id=str(data.get("uid") or "")
            )
        return CheckResult(ok=False, message="登录态无效（Cookie 失效或未登录）")


class TiebaAdapter(_CookieAdapter):
    platform = "tieba"
    label = "贴吧"
    hint_cookie_keys = ("BDUSS",)

    async def check(self, credential: dict) -> CheckResult:
        headers = {
            "User-Agent": _UA,
            "Cookie": credential.get("cookie", ""),
            "Referer": "https://tieba.baidu.com/",
        }
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                resp = await client.get("https://tieba.baidu.com/f/user/json_userinfo")
                data = resp.json() or {}
        except (httpx.HTTPError, ValueError) as exc:
            return CheckResult(ok=False, message=f"请求失败: {exc}")
        user = data.get("data") or {}
        if str(data.get("no", 1)) == "0" and user:
            return CheckResult(
                ok=True,
                message="在线",
                external_id=str(user.get("user_id") or ""),
                display_name=user.get("user_name_show") or user.get("user_name") or "",
            )
        return CheckResult(ok=False, message="登录态无效（Cookie 失效或缺少 BDUSS）")


register(KuaishouAdapter())
register(ZhihuAdapter())
register(WeiboAdapter())
register(TiebaAdapter())
