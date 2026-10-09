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


@dataclass
class QrPoll:
    status: str  # pending / scanned / success / expired / error
    message: str = ""
    credential: dict | None = None  # success 时返回凭证明文 JSON
    external_id: str = ""
    display_name: str = ""


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
