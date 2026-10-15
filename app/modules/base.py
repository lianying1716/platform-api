"""平台适配器统一接口。

每个平台模块提供一个 PlatformAdapter 实例并注册进 REGISTRY。
凭证明文 JSON 结构按平台约定（见各模块 docstring），加密后入库。
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CheckResult:
    ok: bool
    message: str = ""
    external_id: str = ""  # 体检顺带拿到的平台侧用户 ID（有则回填账号）
    display_name: str = ""  # 顺带拿到的昵称
    need_refresh: bool = False  # 平台明确建议刷新凭证（目前 B站使用）


@dataclass
class QrStart:
    """扫码会话启动结果。qr_image_b64 是 PNG base64，前端/AI 直接渲染。"""

    session_data: dict  # 模块自用的会话上下文（qrcode_key、cookie 等）
    qr_image_b64: str
    # 屏幕上这张码**本身**的有效期（秒）；None = 该平台不区分，前端沿用会话 TTL。
    # 与 qr_session_ttl 是两件事：抖音单张码只活 59s，会话却有 300s。
    qr_image_expires_in: int | None = None


@dataclass
class QrPoll:
    status: str  # pending / scanned / success / expired / error
    message: str = ""
    credential: dict | None = None  # success 时返回凭证明文 JSON
    external_id: str = ""
    display_name: str = ""
    # 平台自带的二维码会过期（抖音约 60s）并由登录页自动换新；轮询遇到这种自然过期时，
    # 若能从页面抓到新码就随本轮结果回传，前端直接换图继续扫——避免用户稍慢就扫到死码。
    qr_image_b64: str | None = None
    # 这批二维码**本身**的有效期（秒），与「登录会话存活多久」是两件事。
    # 抖音实测单张码只活 59s（check_qrconnect 换码响应里 expire_time 恒为 now+59），
    # 而会话 TTL 是 300s。前端倒计时必须按前者展示，否则用户看到"还剩 4 分多钟"，
    # 屏幕上那张码其实早已作废。换码时随新图一起回传，前端据此重置倒计时。
    qr_image_expires_in: int | None = None


class PlatformAdapter(Protocol):
    platform: str
    label: str
    credential_kind: str  # cookie / browser
    supports_qr: bool
    credential_fields: list[dict]  # 手动录入时前端要展示的字段说明

    def parse_manual_input(self, raw: dict) -> dict:
        """把用户手动粘贴的内容规整成凭证 JSON，校验必填字段。"""
        ...

    async def check(self, credential: dict) -> CheckResult:
        """探活：调平台真实接口判断登录态是否有效。"""
        ...

    async def qr_start(self) -> QrStart: ...

    async def qr_poll(self, session_data: dict) -> QrPoll: ...


REGISTRY: dict[str, PlatformAdapter] = {}


def register(adapter: PlatformAdapter) -> PlatformAdapter:
    REGISTRY[adapter.platform] = adapter
    return adapter


def get_adapter(platform: str) -> PlatformAdapter:
    if platform not in REGISTRY:
        raise ValueError(f"不支持的平台: {platform}")
    return REGISTRY[platform]


def make_qr_png_b64(content: str) -> str:
    """把字符串（登录 URL）转成二维码 PNG base64。"""
    import base64
    import io

    import qrcode

    img = qrcode.make(content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
