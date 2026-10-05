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

    @property
    def expired(self) -> bool:
        return time.time() - self.created > settings.qr_session_ttl


_sessions: dict[str, QrSession] = {}


def create(platform: str, data: dict, display_name: str = "") -> str:
    _gc()
    session_id = uuid.uuid4().hex
    _sessions[session_id] = QrSession(platform=platform, data=data, display_name=display_name)
    return session_id


def get(session_id: str) -> QrSession | None:
    sess = _sessions.get(session_id)
    if sess and sess.expired:
        _sessions.pop(session_id, None)
        return None
    return sess


def remove(session_id: str) -> None:
    _sessions.pop(session_id, None)


def _gc() -> None:
    for sid in [sid for sid, s in _sessions.items() if s.expired]:
        _sessions.pop(sid, None)
