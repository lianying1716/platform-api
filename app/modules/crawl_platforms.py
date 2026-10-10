"""采集向平台的账号适配器（快手 / 知乎）。

这两家只有「关键词采集」能力（无订阅同步），但账号中心仍需能录入/体检登录态——
否则 `crawl_service._platform_cookie` 永远取不到 Cookie，采集任务必然失败。

凭证 JSON: { cookie }（整段 Cookie 字符串）。
体检通过各平台公开的登录态接口完成；查询文档在本模块内自行定义。
"""

import httpx

from app.core import rate_limit

from app.modules.base import CheckResult, QrPoll, QrStart, register

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

_KS_GRAPHQL = "https://www.kuaishou.com/graphql"
_KS_PROFILE_QUERY = """
query visionProfileUserList($pcursor: String, $ftype: Int) {
  visionProfileUserList(pcursor: $pcursor, ftype: $ftype) {
    result
    hostName
  }
}
"""


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
            "query": _KS_PROFILE_QUERY,
        }
        try:
            async with rate_limit.http_client("kuaishou", headers=headers, timeout=15) as client:
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
            async with rate_limit.http_client("zhihu", headers=headers, timeout=15) as client:
                resp = await client.get("https://www.zhihu.com/api/v4/me")
                data = resp.json() if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            return CheckResult(ok=False, message=f"请求失败: {exc}")
        if data.get("id") and data.get("name"):
            return CheckResult(
                ok=True, message="在线", external_id=str(data["id"]), display_name=data.get("name", "")
            )
        return CheckResult(ok=False, message="登录态无效（Cookie 失效或缺少 z_c0）")


register(KuaishouAdapter())
register(ZhihuAdapter())
