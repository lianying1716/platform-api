"""扫码登录会话存储（内存 + TTL）。

一次扫码 = 一个 session：前端/AI 先 POST 创建拿二维码，再轮询状态。
适配器自己的上下文放在 session.data 里（B站是 qrcode_key，浏览器方案是 browser_session）。
"""

import time
import uuid
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class QrSession:
    platform: str
    data: dict
    display_name: str = ""
    created: float = field(default_factory=time.time)
    # 会话结束后的**终态**（status/message）。会话本身会被移除，但终态要留一小会儿：
    # 否则任何迟到的轮询（同一 tick 的第二发请求、用户手快再点一次）都会撞上
    # "会话不存在或已过期"，把「已确认但拿不到登录态」「风控拦截」这类真实原因盖掉，
    # 用户看到的就是一句和事实无关的"过期"。真机排查时这一点最误导人。
    ended_status: str = ""
    ended_message: str = ""
    ended_at: float = 0.0
    ended_account_id: int | None = None

    @property
    def expired(self) -> bool:
        return time.time() - self.created > settings.qr_session_ttl

    @property
    def ended(self) -> bool:
        return bool(self.ended_status)


_sessions: dict[str, QrSession] = {}

# 终态保留时长：够覆盖同一次操作里所有在途/重复的轮询，又不会无限堆积。
_ENDED_TTL = 120


def _gc_ended() -> None:
    now = time.time()
    for sid in [s for s, v in _sessions.items() if v.ended and now - v.ended_at > _ENDED_TTL]:
        _sessions.pop(sid, None)


def create(platform: str, data: dict, display_name: str = "") -> str:
    _gc()
    _gc_ended()
    session_id = uuid.uuid4().hex
    _sessions[session_id] = QrSession(platform=platform, data=data, display_name=display_name)
    return session_id


def get(session_id: str) -> QrSession | None:
    sess = _sessions.get(session_id)
    # 已结束的会话不再返回给适配器（浏览器上下文早就关了），但终态仍在库里可查。
    if sess and sess.ended:
        return None
    if sess and sess.expired:
        _sessions.pop(session_id, None)
        return None
    return sess


def remove(session_id: str) -> None:
    """结束会话并保留终态（默认当作正常收尾，不覆盖已记的原因）。"""
    _sessions.pop(session_id, None)


def finish(session_id: str, status: str, message: str = "") -> None:
    """把会话标记为结束，并记住真实原因；之后的轮询会如实回放这个原因。"""
    sess = _sessions.get(session_id)
    if sess is None:
        return
    sess.ended_status = status
    sess.ended_message = message
    sess.ended_at = time.time()
    sess.data = {}


def finish_success(session_id: str, account_id: int | None, message: str = "") -> None:
    """成功也是终态：重复轮询要回放"成功"，而不是回头说"会话不存在"。"""
    finish(session_id, "success", message)
    sess = _sessions.get(session_id)
    if sess is not None:
        sess.ended_account_id = account_id


def claim(session_id: str, status: str, message: str = "") -> bool:
    """占位：把会话标成终态，但允许后续 ``finish_success`` 补上 account_id。

    返回 True 表示本次调用抢到了处理权（此前未结束）；False 表示已被别人处理——
    调用方应放弃，避免并发轮询重复落库（一次扫码建两个账号）。
    """
    sess = _sessions.get(session_id)
    if sess is None or sess.ended:
        return False
    sess.ended_status = status
    sess.ended_message = message
    sess.ended_at = time.time()
    return True


def terminal_status(session_id: str) -> tuple[str, str, int | None] | None:
    """取已结束会话的终态 (status, message, account_id)；没有则 None。"""
    sess = _sessions.get(session_id)
    if sess is not None and sess.ended:
        return sess.ended_status, sess.ended_message, sess.ended_account_id
    return None


def _gc() -> None:
    # 已结束的记录由 _gc_ended 按 _ENDED_TTL 回收，别在这里按会话 TTL 提前删掉。
    for sid in [sid for sid, s in _sessions.items() if s.expired and not s.ended]:
        _sessions.pop(sid, None)
