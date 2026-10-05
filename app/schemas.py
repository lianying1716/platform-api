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
    credential_preview: dict[str, str] = {}  # 脱敏后的凭证字段

    model_config = {"from_attributes": True}


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


class QrLoginStatus(BaseModel):
    status: str  # pending / scanned / success / expired / error
    message: str = ""
    account_id: int | None = None
