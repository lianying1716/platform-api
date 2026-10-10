"""平台出站限速（保守档）：宁可慢，绝不触发风控/封号。

设计取舍：
- 参考 bili-sync 默认 4 次/250ms(≈16/s)，但那是单人少量场景；我们多账号批量同步，
  取**明显更慢**的 12 次/分钟（≈5 秒一次）。
- 所有平台出站 HTTP 都经过按平台分桶的令牌桶，避免"某条新路径忘了加延迟"。
- 测试默认关闭限速（conftest），这里临时打开来验证行为本身。
"""

import asyncio
import time

import pytest

from app.core import rate_limit
from app.core.rate_limit import _Bucket


@pytest.fixture(autouse=True)
def _clean_buckets():
    """每个用例独立桶，避免相互影响；结束恢复 enable（conftest 的默认态由它自己管理）。"""
    rate_limit.reset()
    rate_limit.enable()
    yield
    rate_limit.reset()


def test_default_is_conservative_not_upstream_aggressive():
    """默认限额必须比 bili-sync 的 4 次/250ms 保守得多。"""
    from app.config import settings

    per_minute = settings.rate_limit_per_minute
    assert per_minute <= 20, f"默认 {per_minute}/分钟 太激进，保守档应 <=20"
    # bili-sync 默认约 16/s = 960/min，我们应远低于它
    assert per_minute < 960 / 10


@pytest.mark.asyncio
async def test_bucket_enforces_strict_min_interval():
    """容量=1：连续两次 acquire 之间必须至少隔一个补充间隔（不允许突发）。"""
    bucket = _Bucket(capacity=1, refill_interval=0.3, tokens=1.0)
    t0 = time.monotonic()
    await bucket.acquire()          # 桶里那颗，立刻
    burst = time.monotonic() - t0
    assert burst < 0.1, f"第一次应立刻放行，实际 {burst:.3f}s"

    t1 = time.monotonic()
    await bucket.acquire()          # 第二次必须等满一个间隔
    waited = time.monotonic() - t1
    assert waited >= 0.25, f"第二次应等待补充，实际只等 {waited:.3f}s"


@pytest.mark.asyncio
async def test_acquire_returns_wait_time():
    bucket = _Bucket(capacity=1, refill_interval=0.2, tokens=0.0)
    waited = await bucket.acquire()
    assert waited >= 0.15


@pytest.mark.asyncio
async def test_acquire_timeout_raises_instead_of_waiting_forever():
    """等待超过上限要抛错（宁可单任务失败，也不要无限堆积）。"""
    bucket = _Bucket(capacity=1, refill_interval=60.0, tokens=0.0)
    with pytest.raises(TimeoutError):
        await bucket.acquire(timeout=0.2)


@pytest.mark.asyncio
async def test_disable_skips_waiting():
    """测试/紧急开关：disable 后 acquire 立即返回，不 sleep。"""
    rate_limit.disable()
    bucket = _Bucket(capacity=1, refill_interval=60.0, tokens=0.0)
    t0 = time.monotonic()
    import app.core.rate_limit as rl
    # 直接走模块级 acquire（用真实桶）
    await rl.acquire("douyin")
    await rl.acquire("douyin")
    assert time.monotonic() - t0 < 0.05, "disable 后不得有任何等待"


@pytest.mark.asyncio
async def test_platforms_are_independent_buckets():
    """不同平台互不占用对方的配额（一个平台限速不该拖慢另一个）。"""
    rate_limit.reset()
    await rate_limit.acquire("douyin")   # 消耗 douyin 那颗
    # bilibili 仍应立刻可用（各自独立的桶）
    t0 = time.monotonic()
    await rate_limit.acquire("bilibili")
    assert time.monotonic() - t0 < 0.05, "不同平台不应共享桶"


@pytest.mark.asyncio
async def test_default_bucket_has_no_burst(monkeypatch):
    """默认配置下第二次出站必须等约 5 秒 —— 不允许 12 连击突发。"""
    from app.config import settings

    monkeypatch.setattr(settings, "rate_limit_per_minute", 12, raising=False)
    rate_limit.reset()
    await rate_limit.acquire("douyin")
    t0 = time.monotonic()
    task = asyncio.ensure_future(rate_limit.acquire("douyin"))
    await asyncio.sleep(0.05)
    assert not task.done(), "第二次不该立刻放行（说明桶容量 >1，允许了突发）"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_real_adapters_share_the_same_limiter(monkeypatch):
    """抖音/B站适配器出站都要经过对应平台的桶（防止漏加限速）。"""
    calls: list[str] = []

    async def fake_acquire(platform, *, timeout=300.0):
        calls.append(platform)
        return 0.0

    monkeypatch.setattr(rate_limit, "acquire", fake_acquire)

    from app.modules import douyin, bilibili

    # 限速是**逐请求**的：进入会话本身不取令牌（否则会话内连发会绕过限速）。
    async with douyin._http("c=1", "https://www.douyin.com/"):
        pass
    async with bilibili._http():
        pass
    assert calls == [], "进会话不该取令牌，应按请求取"

    # 真正发请求前会 acquire（socket 被 conftest 拦，但 acquire 已经发生）
    async with douyin._http("c=1", "https://www.douyin.com/") as c:
        try:
            await c.get("/ping")
        except Exception:
            pass
    assert calls == ["douyin"], "每次请求前都必须 acquire"


@pytest.mark.asyncio
async def test_crawler_clients_are_throttled(monkeypatch):
    """爬取/评论等出站也必须过限速（曾是漏网之鱼：1.4 秒连发 4 个评论请求）。"""
    calls: list[str] = []

    async def fake_acquire(platform, *, timeout=300.0):
        calls.append(platform)
        return 0.0

    monkeypatch.setattr(rate_limit, "acquire", fake_acquire)

    from app.modules.crawler import client as crawler_client, bili_sign

    # 爬虫 client 的 _http 逐请求过桶：会话开始不取，发请求才取
    async with crawler_client._http("douyin", {}) as c:
        try:
            await c.get("https://www.douyin.com/aweme/v1/web/comment/list/")
        except Exception:
            pass
    assert calls == ["douyin"], "评论抓取必须逐请求限速（此前 1.4s 连发 4 个）"

    calls.clear()
    # bili_sign 的 WBI key 请求必须过 bilibili 桶（它也要打 B站 API）
    import inspect

    src = inspect.getsource(bili_sign.fetch_wbi_keys)
    assert "rate_limit.http_client" in src, "fetch_wbi_keys 必须走限速"


@pytest.mark.asyncio
async def test_no_unthrottled_platform_client_in_douyin_or_bilibili():
    """抖音/B站适配器里不允许出现裸 httpx.AsyncClient（除各自的 _client() 工厂）。"""
    import inspect

    from app.modules import douyin, bilibili

    for mod in (douyin, bilibili):
        src = inspect.getsource(mod)
        # _client() 工厂内部那一处是允许的（_http 会调用它并限速）
        raw = src.count("httpx.AsyncClient(")
        assert raw <= 1, f"{mod.__name__} 有 {raw} 处裸 AsyncClient，应统一走 _http"


@pytest.mark.asyncio
async def test_throttled_client_gates_every_request_not_just_session(monkeypatch):
    """回归：限速必须逐请求 —— 会话内 for 循环翻页曾绕过限速（1.4s 连发 4 个评论请求）。"""
    gates: list[str] = []

    async def fake_acquire(platform, *, timeout=300.0):
        gates.append(platform)
        return 0.0

    monkeypatch.setattr(rate_limit, "acquire", fake_acquire)

    class _FakeClient:
        async def get(self, *a, **kw):
            return "ok"

        async def aclose(self):
            return None

    async with rate_limit._ThrottledClient.wrap("douyin", _FakeClient()) as c:
        for _ in range(4):
            await c.get("/x")
    assert len(gates) == 4, f"4 个请求应取 4 次令牌，实际 {len(gates)}"


@pytest.mark.asyncio
async def test_throttled_client_wrap_closes_underlying(monkeypatch):
    closed = {"n": 0}

    class _FakeClient:
        async def aclose(self):
            closed["n"] += 1

    async with rate_limit._ThrottledClient.wrap("xhs", _FakeClient()):
        pass
    assert closed["n"] == 1
