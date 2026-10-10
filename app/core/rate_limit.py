"""平台出站请求限速（保守优先：宁可慢，绝不触发风控/封号）。

设计目标：**所有**对同一个平台的出站 API 请求都经过一个按平台分桶的漏桶限速器。
参考值来自 bili-sync（默认每 250ms 放行 4 次 ≈ 16 req/s），但那里是给"本地单人、
少量追番"设计的；我们是多账号批量同步，取**明显更慢**的保守档：

- 默认每 60 秒放行 12 次（≈ 5 秒/次），对应 ``PLATFORM_RATE_LIMIT_PER_MINUTE=12``。
- **容量固定为 1**（严格最小间隔）：不做"攒满一批再突发" —— 那种形态最像机器人。
  无论闲置多久，两次出站之间都至少隔 ``60/rate_limit_per_minute`` 秒。
- 桶空时 ``acquire()`` 按需等待，不做无界排队；同时长任务可随时取消。
- 不同平台互不影响（douyin / bilibili / xhs … 各自一个桶）。

为什么要限：抖音收藏/点赞接口在密集请求下会返回字面量 ``blocked``（WAF），
B站会直接 412。抖小云靠"整条 job 串行 + 每次随机延迟"规避；我们除了那些
（``download_min_gap_seconds`` / ``_PAGE_DELAY_RANGE``），再补一层统一的请求级限速，
把所有路径都罩住，避免"某条新路径忘了加延迟"。

实现选型：令牌桶（token bucket），按 ``refill_interval`` 均匀补充令牌。
不用滑动窗口是因为桶能自然地"允许一次突发后长期慢速"，更贴近人类操作节奏。

这里是**进程内**限速（与 worker 单进程架构一致）。若将来起多个 worker 进程，
需要换成 DB/Redis 级共享计数 —— 见 ``NOTE`` 注释。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from app.config import settings

# NOTE: 进程内令牌桶。架构红线是"one worker process"，因此单进程足够；
# 若未来并行多进程，这里需改为共享存储（PG 表或 Redis），否则限额会被乘以进程数。

# 关掉限速的开关：测试必须能跑得快，且测试本来就在 socket 层禁止联网。
# 生产/开发默认 False（限速生效）；tests/conftest.py 会置 True。
_disabled = False


def disable() -> None:
    """关闭限速（仅测试用）。"""
    global _disabled
    _disabled = True


def enable() -> None:
    """恢复限速。"""
    global _disabled
    _disabled = False


@dataclass
class _Bucket:
    """按平台分桶的令牌桶。

    capacity/refill_interval 由配置给出；桶满则不阻塞直接放行，桶空则 sleep 等令牌。
    """

    capacity: int
    refill_interval: float  # 每补充 1 个令牌的秒数
    tokens: float = 0.0
    updated_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.updated_at
        if elapsed <= 0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed / self.refill_interval)
        self.updated_at = now

    async def acquire(self, timeout: float | None = None) -> float:
        """取一个令牌，必要时等待。返回实际等待秒数（便于日志/测试）。"""
        waited = 0.0
        while True:
            async with self.lock:
                self._refill()
                if self.tokens >= 1:
                    self.tokens -= 1
                    return waited
                need = (1 - self.tokens) * self.refill_interval
            if timeout is not None and waited + need > timeout:
                raise TimeoutError(f"平台限速等待超过 {timeout:.0f}s")
            await asyncio.sleep(need)
            waited += need


_buckets: dict[str, _Bucket] = {}
_buckets_lock = asyncio.Lock()


def _bucket_for(platform: str) -> _Bucket:
    """取（或创建）某平台的桶。容量/间隔来自配置，可运行时调整（测试会改 settings）。

    注意 **capacity 设为 1**：我们不要"攒满 12 个令牌然后一次突发 12 连击" ——
    那正是最容易被 WAF 判成机器人的形态。容量 1 等价于"严格最小间隔"，
    无论之前闲置多久，两次出站之间都至少隔 ``refill_interval``。
    保守优先：宁可每次多等几秒，也不要任何形式的密集突发。
    """
    per_minute = max(1, int(getattr(settings, "rate_limit_per_minute", 12)))
    interval = 60.0 / per_minute
    bucket = _buckets.get(platform)
    if bucket is None or bucket.refill_interval != interval or bucket.capacity != 1:
        bucket = _Bucket(capacity=1, refill_interval=interval, tokens=1.0)
        _buckets[platform] = bucket
    return bucket


async def acquire(platform: str, *, timeout: float | None = 300.0) -> float:
    """在访问某平台前调用：拿到令牌才继续。

    ``timeout`` 是等待上限（默认 5 分钟），超时抛 ``TimeoutError`` —— 宁可让这一个
    任务失败并留下可读错误，也不要无限堆积或绕过限速。
    """
    if _disabled:
        return 0.0
    async with _buckets_lock:
        bucket = _bucket_for(platform)
    return await bucket.acquire(timeout=timeout)


def reset() -> None:
    """清空所有桶（仅测试用：避免用例间相互影响）。"""
    _buckets.clear()


class _ThrottledClient:
    """httpx.AsyncClient 的限速包装：**每次请求**都先取令牌。

    为什么不能在进入会话时取一次就行：一个会话里常常连发多个请求
    （如评论抓取 for 循环翻页）。只在会话开始限速，等于一进会话就把后面的
    请求全部放行 —— 实测就是 1.4 秒连发 4 个评论请求。这里改成每个
    ``get/post/head/stream`` 调用前都 ``acquire``，保证任意两个出站请求之间
    都有最小间隔。
    """

    def __init__(self, platform: str, client):
        self._platform = platform
        self._client = client

    async def _gate(self) -> None:
        await acquire(self._platform)

    async def get(self, *a, **kw):
        await self._gate()
        return await self._client.get(*a, **kw)

    async def post(self, *a, **kw):
        await self._gate()
        return await self._client.post(*a, **kw)

    async def head(self, *a, **kw):
        await self._gate()
        return await self._client.head(*a, **kw)

    def stream(self, *a, **kw):
        client = self._client
        gate = self._gate

        @contextlib.asynccontextmanager
        async def _cm():
            await gate()
            async with client.stream(*a, **kw) as resp:
                yield resp

        return _cm()

    def __getattr__(self, name):
        # 其余属性（如 cookies）透传给底层 client
        return getattr(self._client, name)

    @classmethod
    @contextlib.asynccontextmanager
    async def wrap(cls, platform: str, client):
        """包住一个**已存在**的 AsyncClient（适配器自建 client 时用），并负责关闭。

        关闭是 best-effort：测试替身 / 非标准 client 可能没有 ``aclose``，
        不能因此让业务失败。
        """
        try:
            yield cls(platform, client)
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001 关闭失败不影响业务
                    pass


@contextlib.asynccontextmanager
async def http_client(platform: str, *, headers: dict | None = None, timeout: float = 20.0, **kw):
    """限速 + httpx.AsyncClient 的便捷封装（**逐请求**限速）。

    各平台模块的**所有**出站请求都应经过它（或至少调用 ``acquire``），
    这样"新加一条请求路径忘了限速"不会重演。
    """
    import httpx

    # 进会话不取令牌，交给 _ThrottledClient 在每次请求前取 —— 否则会话内连发会绕过限速。
    async with httpx.AsyncClient(headers=headers, timeout=timeout, **kw) as client:
        yield _ThrottledClient(platform, client)
