"""SSE 事件流：前端实时刷新任务/账号状态，避免轮询。

进程内发布订阅。worker 在另一进程，故事件先落 asyncio.Queue 的仅限 API 进程内来源
（账号体检、API 内触发的状态变更）。跨进程的 worker 进度通过前端轮询 /api/pipeline/jobs
兜底，第一版不做跨进程事件总线（单机够用，避免引入 Redis pub/sub）。
"""

import asyncio
import json
from collections.abc import AsyncGenerator

_subscribers: set[asyncio.Queue] = set()


def publish(event: str, data: dict) -> None:
    """向所有订阅者推送一条事件。非阻塞，队列满则丢弃最旧。"""
    payload = {"event": event, "data": data}
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


async def subscribe() -> AsyncGenerator[str, None]:
    """SSE 端点用：yield 出 text/event-stream 格式的字符串。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    try:
        # 连接建立先发一条 hello，前端据此确认通道可用
        yield _format("hello", {"ok": True})
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=25)
                yield _format(payload["event"], payload["data"])
            except asyncio.TimeoutError:
                # 心跳保活，防止反向代理掐断空闲连接
                yield ": keepalive\n\n"
    finally:
        _subscribers.discard(q)


def _format(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
