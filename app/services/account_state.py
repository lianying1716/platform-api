"""账号可用性判定（含「沉默期」总开关）。

一个账号要被判定为「可调用」，必须同时满足：
1. ``status == "active"``（体检通过）；
2. ``is_system`` 为 False；
3. ``sync_enabled`` 为 True —— 账号级同步总开关。关闭即「沉默期」：
   该账号所有同步/采集/体检调用一律禁止（源头拦截，不靠每个调用点各自记得判）。

在此之上再叠加订阅级的 enabled/sync_videos 各自开关（与门关系）。

只读判断放这里，写侧（API 报错文案）复用它，避免多处条件漂移。
"""

from app.models import PlatformAccount

# 沉默期统一提示：前端直接展示，用户一眼知道要回账号中心打开开关。
SILENT_MESSAGE = "账号处于沉默期（同步总开关已关闭），已禁止所有同步与调用"


def is_silent(account: PlatformAccount | None) -> bool:
    """账号是否处于沉默期（不存在也算不可用）。"""
    return account is None or not bool(account.sync_enabled)


def is_callable(account: PlatformAccount | None) -> bool:
    """账号是否允许发起平台调用：非系统账号 + 在线 + 未处于沉默期。"""
    if account is None or account.is_system:
        return False
    if not bool(account.sync_enabled):
        return False
    return account.status == "active"
