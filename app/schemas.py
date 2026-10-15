"""API 请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


# ---------- auth ----------
class AuthStatus(BaseModel):
    initialized: bool


class SetupRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


# ---------- accounts ----------
class PlatformInfo(BaseModel):
    platform: str
    label: str
    credential_kind: str
    supports_qr: bool
    credential_fields: list[dict]


class AccountOut(BaseModel):
    id: int
    platform: str
    platform_label: str = ""
    display_name: str
    external_id: str
    credential_kind: str
    status: str
    status_message: str
    last_checked_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    storage_root_id: int | None = None  # NULL = 使用默认根目录
    storage_root_name: str = ""  # 绑定的存储根名称（展示用）
    storage_root_path: str = ""  # 绑定的存储根路径（展示用）
    # 账号级同步总开关：False = 沉默期（禁止该账号所有同步/调用）。
    sync_enabled: bool = True
    credential_preview: dict[str, str] = {}  # 脱敏后的凭证字段

    model_config = {"from_attributes": True}


class AccountStorageUpdate(BaseModel):
    """给账号指定/清除存储根。storage_root_id=None 表示用默认根。"""

    storage_root_id: int | None = None


class AccountSyncToggle(BaseModel):
    """账号级同步总开关：False = 沉默期（禁止该账号所有同步/调用）。"""

    enabled: bool


class AccountCreate(BaseModel):
    platform: str
    display_name: str = ""
    credential: dict  # 明文字段，按平台 credential_fields 约定
    skip_check: bool = False  # True 时不立即探活（例如平台暂时风控）


class CredentialUpdate(BaseModel):
    credential: dict
    skip_check: bool = False


class CheckResponse(BaseModel):
    ok: bool
    status: str
    message: str


# ---------- QR login ----------
class QrLoginCreate(BaseModel):
    platform: str
    display_name: str = ""


class QrLoginSession(BaseModel):
    session_id: str
    platform: str
    qr_image_base64: str
    expires_in: int
    # 屏幕上这张码**本身**的寿命（秒）。抖音单张码只活 ~59s 就会被登录页换新，
    # 与会话 TTL（expires_in，300s）不是一回事；前端倒计时应优先用它。
    qr_image_expires_in: int | None = None


class QrLoginStatus(BaseModel):
    # pending / scanned / confirmed / captcha（需电脑端人工过滑块）/ success / expired / error
    status: str
    message: str = ""
    account_id: int | None = None
    # 轮询遇到平台二维码自然过期时回传的新二维码（抖音约 60s 一换），前端直接换图继续。
    qr_image_base64: str | None = None
    # 随新图一起回传的"这张码还能活多久"，前端据此重置倒计时（抖音 ~59s）。
    qr_image_expires_in: int | None = None
