"""账号体检与按需刷新：API 手动体检和定时巡检共用同一套逻辑。"""

from datetime import datetime

from app.core.crypto import decrypt_credential, encrypt_credential
from app.models import PlatformAccount
from app.modules import get_adapter


async def check_account(account: PlatformAccount) -> None:
    """体检一个账号，必要时按需刷新凭证。"""
    adapter = get_adapter(account.platform)
    if account.credential is None:
        account.status, account.status_message = "error", "未录入凭证"
        account.last_checked_at = datetime.utcnow()
        return

    plain = decrypt_credential(account.credential.ciphertext)
    try:
        result = await adapter.check(plain)
    except Exception as exc:  # noqa: BLE001 网络异常等不应把账号判死
        account.status, account.status_message = "error", f"体检失败: {exc}"
        account.last_checked_at = datetime.utcnow()
        return

    account.status = "active" if result.ok else "expired"
    account.status_message = result.message
    if result.ok and result.need_refresh:
        refresh = getattr(adapter, "refresh", None)
        if refresh is not None:
            try:
                refreshed = await refresh(plain)
                account.credential.ciphertext = encrypt_credential(refreshed)
                account.status_message = "在线（已自动刷新 Cookie）"
            except Exception as exc:  # noqa: BLE001 刷新失败不判死，留待人工重登
                account.status_message = f"在线，但 Cookie 自动刷新失败: {exc}"
    if result.external_id:
        account.external_id = result.external_id
    if result.display_name and not account.display_name:
        account.display_name = result.display_name
    account.last_checked_at = datetime.utcnow()
