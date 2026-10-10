"""小红书适配器。

凭证 JSON: { cookie }   # 关键字段是 web_session / a1
体检：GET edith.xiaohongshu.com/api/sns/web/v2/user/me 带 Cookie，
success 且 guest=false 视为在线（该接口无需 x-s 签名）。
扫码：小红书无公开扫码 API，走浏览器方案（browser_qr）；手动粘 Cookie 兜底。
后续抓取所需的运行时签名（X-s 等）由 browser_runtime 模块负责，不在账号中心范围。
"""

import httpx

from app.core import rate_limit

from app.modules.base import CheckResult, QrPoll, QrStart, register

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_ME = "https://edith.xiaohongshu.com/api/sns/web/v2/user/me"


class XhsAdapter:
    platform = "xhs"
    label = "小红书"
    credential_kind = "browser"
    supports_qr = True
    # 小红书没有账号级「收藏/点赞」单例源；关注博主由用户手动添加。
    singleton_sync_sources: tuple[str, ...] = ()
    credential_fields = [
        {"key": "cookie", "label": "Cookie 字符串（含 web_session，整段粘贴）", "required": True},
    ]

    def parse_manual_input(self, raw: dict) -> dict:
        cookie = (raw.get("cookie") or "").strip()
        if not cookie:
            raise ValueError("小红书凭证需要整段 Cookie 字符串")
        if "web_session" not in cookie:
            raise ValueError("Cookie 中未找到 web_session 字段，请确认复制完整")
        return {"cookie": cookie}

    async def check(self, credential: dict) -> CheckResult:
        headers = {
            "User-Agent": _UA,
            "Cookie": credential.get("cookie", ""),
            "Referer": "https://www.xiaohongshu.com/",
            "Origin": "https://www.xiaohongshu.com",
        }
        try:
            async with rate_limit.http_client("xhs", headers=headers, timeout=15) as client:
                resp = await client.get(_ME)
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return CheckResult(ok=False, message=f"请求失败: {exc}")
        body = data.get("data", {})
        if data.get("success") and body and not body.get("guest", True):
            return CheckResult(
                ok=True,
                message="在线",
                external_id=str(body.get("user_id", "") or ""),
                display_name=body.get("nickname", ""),
            )
        return CheckResult(ok=False, message="登录态无效（游客状态或 Cookie 失效）")

    async def qr_start(self) -> QrStart:
        from app.modules.browser_qr import browser_qr_start

        return await browser_qr_start("xhs")

    async def qr_poll(self, session_data: dict) -> QrPoll:
        from app.modules.browser_qr import browser_qr_poll

        return await browser_qr_poll("xhs", session_data)

    async def fetch_source(self, credential: dict, source_type: str, remote_id: str, limit: int) -> list[dict]:
        """拉取订阅源。目前支持 ``creator``：按博主拉主页笔记。"""
        if source_type != "creator":
            return []
        from app.modules import creator_fetch

        return await creator_fetch.fetch_xhs_creator(
            user_id=remote_id,
            cookie=credential.get("cookie", ""),
            limit=limit,
        )


register(XhsAdapter())
