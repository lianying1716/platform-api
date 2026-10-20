"""微信公众号适配器。协议来源：wechat-article-exporter server/api/web/login/*（MIT）。

凭证 JSON: { token, cookie }   # cookie 为整段字符串
扫码流程（纯 HTTP）：
1. POST bizlogin?action=startlogin       -> 种下会话 cookie
2. GET  scanloginqrcode?action=getqrcode -> 返回二维码 PNG
3. GET  scanloginqrcode?action=ask       -> 轮询 status: 0未扫 / 4已扫待确认 / 1成功
4. POST bizlogin?action=login            -> Set-Cookie + redirect_url 里带 token
体检：调 searchbiz 接口，base_resp.ret==200003 表示登录态失效。
原项目 isExpired 是 TODO 恒返回 false，这里真正实现。
登录态有效期约 4 天（原项目经验值），expires_at 由 API 层写入。
"""

import base64
import re
from urllib.parse import parse_qs, urlparse

import httpx

from app.modules.base import CheckResult, QrPoll, QrStart, register

_MP = "https://mp.weixin.qq.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

_LOGIN_PAYLOAD = {
    "userlang": "zh_CN",
    "redirect_url": "",
    "cookie_forbidden": "0",
    "cookie_cleaned": "0",
    "plugin_used": "0",
    "login_type": "3",
    "token": "",
    "lang": "zh_CN",
    "f": "json",
    "ajax": "1",
}


def _headers(cookie: str = "") -> dict:
    h = {"User-Agent": _UA, "Referer": f"{_MP}/", "Origin": _MP}
    if cookie:
        h["Cookie"] = cookie
    return h


def _jar_to_cookie_str(jar: httpx.Cookies) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


class WechatMpAdapter:
    platform = "wechat_mp"
    label = "微信公众号"
    credential_kind = "cookie"
    supports_qr = True
    credential_fields = [
        {"key": "token", "label": "token（后台 URL 里的 token= 参数）", "required": True},
        {"key": "cookie", "label": "Cookie 字符串（整段粘贴）", "required": True},
    ]

    def parse_manual_input(self, raw: dict) -> dict:
        token = (raw.get("token") or "").strip()
        cookie = (raw.get("cookie") or "").strip()
        if not token or not cookie:
            raise ValueError("公众号凭证需要 token 和整段 Cookie")
        return {"token": token, "cookie": cookie}

    async def check(self, credential: dict) -> CheckResult:
        params = {
            "action": "search_biz",
            "begin": "0",
            "count": "1",
            "query": "新华社",
            "token": credential.get("token", ""),
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }
        from app.core.egress import client_kwargs

        async with httpx.AsyncClient(
            headers=_headers(credential.get("cookie", "")), timeout=15, **client_kwargs("wechat_mp")
        ) as client:
            resp = await client.get(f"{_MP}/cgi-bin/searchbiz", params=params)
            data = resp.json()
            ret = data.get("base_resp", {}).get("ret")
            if ret == 0:
                return CheckResult(ok=True, message="在线")
            if ret == 200003:
                return CheckResult(ok=False, message="登录态已失效（ret=200003）")
            return CheckResult(ok=False, message=f"异常返回 ret={ret}: {data.get('base_resp', {}).get('err_msg', '')}")

    async def qr_start(self) -> QrStart:
        async with httpx.AsyncClient(
            headers=_headers(), timeout=15, follow_redirects=False, **client_kwargs("wechat_mp")
        ) as client:
            start = await client.post(
                f"{_MP}/cgi-bin/bizlogin", params={"action": "startlogin"}, data=_LOGIN_PAYLOAD
            )
            session_cookie = _jar_to_cookie_str(client.cookies)
            if not session_cookie:
                raise RuntimeError(f"startlogin 未返回会话 Cookie: HTTP {start.status_code}")
            qr = await client.get(
                f"{_MP}/cgi-bin/scanloginqrcode",
                params={"action": "getqrcode", "random": "1"},
                headers=_headers(session_cookie),
            )
            if "image" not in qr.headers.get("content-type", ""):
                raise RuntimeError("获取二维码失败，返回的不是图片")
            return QrStart(
                session_data={"cookie": session_cookie},
                qr_image_b64=base64.b64encode(qr.content).decode(),
            )

    async def qr_poll(self, session_data: dict) -> QrPoll:
        session_cookie = session_data["cookie"]
        async with httpx.AsyncClient(
            headers=_headers(session_cookie), timeout=15, **client_kwargs("wechat_mp")
        ) as client:
            ask = await client.get(
                f"{_MP}/cgi-bin/scanloginqrcode",
                params={"action": "ask", "token": "", "lang": "zh_CN", "f": "json", "ajax": "1"},
            )
            status = ask.json().get("status")
            if status == 0:
                return QrPoll(status="pending", message="等待扫码")
            if status == 4:
                return QrPoll(status="scanned", message="已扫码，请在手机上确认")
            if status == 2 or status == 3:
                return QrPoll(status="expired", message="二维码已过期或被取消")
            if status != 1:
                return QrPoll(status="error", message=f"未知状态: {status}")

            # 已确认 -> 正式登录拿 token
            login = await client.post(
                f"{_MP}/cgi-bin/bizlogin", params={"action": "login"}, data=_LOGIN_PAYLOAD
            )
            body = login.json()
            redirect = body.get("redirect_url", "")
            token_vals = parse_qs(urlparse(redirect).query).get("token", [])
            token = token_vals[0] if token_vals else ""
            if not token:
                m = re.search(r"token=(\d+)", redirect)
                token = m.group(1) if m else ""
            if not token:
                return QrPoll(status="error", message=f"登录响应中未找到 token: {body}")
            full_cookie = _jar_to_cookie_str(client.cookies)
            credential = {"token": token, "cookie": full_cookie}
            result = await self.check(credential)
            if not result.ok:
                return QrPoll(status="error", message=f"登录后校验失败: {result.message}")
            return QrPoll(status="success", message="登录成功", credential=credential)


register(WechatMpAdapter())
