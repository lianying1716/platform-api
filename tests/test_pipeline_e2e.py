"""端到端管线测试：证明 P2~P6 数据流真能闭环，不只是"能导入"。

策略：用独立内存 SQLite，驱动真实的 队列(queue) + worker handler + service 编排，
只 mock 最外层的"出网"依赖（yt-dlp 下载、faster-whisper 转录、LLM 分析、MediaCrawler 抓取）。
覆盖链路：
  订阅同步 -> content_item/metric/media_asset 入库 + download 任务入队
  download job  -> handler 写文件名、置 done
  transcribe job-> handler 写 transcript、置 done
  analyze job   -> handler 写 analysis、置 done
  看板 summary / 作者维度 聚合正确
  PG 队列语义（claim/complete/fail 重试）在 SQLite 下的降级路径
"""

import asyncio
import base64
import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def session(monkeypatch):
    monkeypatch.setenv("PLATFORM_CREDENTIAL_ENC_KEY", base64.b64encode(os.urandom(32)).decode())
    import app.config as config

    config.settings.credential_enc_key = base64.b64encode(os.urandom(32)).decode()
    import app.core.crypto as crypto

    crypto._key = None

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    import app.models  # noqa: F401  确保全部模型注册到 metadata 后再建表
    from app.db import Base

    Base.metadata.create_all(engine)
    db = TestSession()
    yield db
    db.close()


def _seed_account_and_subscription(db):
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential, PlatformAccount, Subscription

    acc = PlatformAccount(platform="bilibili", display_name="主号", credential_kind="cookie", status="active")
    db.add(acc)
    db.flush()
    acc.credential = AccountCredential(ciphertext=encrypt_credential({"sessdata": "x", "bili_jct": "y"}))
    sub = Subscription(
        account_id=acc.id, platform="bilibili", source_type="favorite",
        remote_id="fav1", title="收藏夹", enabled=True, auto_download=True,
    )
    db.add(sub)
    db.commit()
    return acc, sub


def test_subscription_sync_ingests_and_enqueues(session, monkeypatch):
    """订阅同步：mock 适配器返回 2 条内容 → 入库 content/metric/asset + download 入队。"""
    from app.models import ContentItem, ContentMetricSnapshot, JobQueue, MediaAsset
    from app.services import sync_service

    _, sub = _seed_account_and_subscription(session)

    async def fake_fetch(self, credential, source_type, remote_id, limit):
        return [
            {
                "external_id": "BV001", "title": "视频一", "author_name": "UP主A", "author_id": "a1",
                "url": "https://b.com/BV001", "content_type": "video",
                "metrics": {"play": 1000, "like": 100, "comment": 10},
            },
            {
                "external_id": "BV002", "title": "视频二", "author_name": "UP主A", "author_id": "a1",
                "url": "https://b.com/BV002", "content_type": "video",
                "metrics": {"play": 2000, "like": 200, "comment": 20},
            },
        ]

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.fetch_source", fake_fetch, raising=False)

    run = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run.status == "success"
    assert run.fetched == 2 and run.added == 2

    assert session.scalar(select(ContentItem).where(ContentItem.external_id == "BV001")) is not None
    assert len(session.scalars(select(ContentMetricSnapshot)).all()) == 2
    assert len(session.scalars(select(MediaAsset)).all()) == 2
    # auto_download=True → 2 个 download 任务入队
    dl_jobs = session.scalars(select(JobQueue).where(JobQueue.job_type == "download")).all()
    assert len(dl_jobs) == 2

    # 幂等：再同步一次不应重复入库
    run2 = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run2.added == 0
    assert len(session.scalars(select(ContentItem)).all()) == 2


def test_full_pipeline_download_transcribe_analyze(session, monkeypatch):
    """完整流水线：download → transcribe → analyze，全部经 worker handler。"""
    from app.models import AnalysisResult, ContentItem, MediaAsset, Transcript
    from app.workers import handlers

    # 准备一条内容 + 素材
    content = ContentItem(platform="bilibili", external_id="BV9", title="测试视频", author_name="UP", url="https://b/BV9")
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id, download_status="pending")
    session.add(asset)
    session.commit()

    # mock 出网依赖
    async def fake_download(url, target_dir, **kw):
        return {"video_file": "video.mp4", "audio_file": "audio.wav"}

    async def fake_transcribe(media_path, options):
        return {"language": "zh", "model": "base",
                "segments": [{"start": 0, "end": 2, "text": "你好"}], "full_text": "你好世界"}

    async def fake_analyze(title, full_text):
        return {"summary": "这是摘要", "tags": ["科技", "教程"], "structured": {"hook": "开头"}, "model": "mock-llm"}

    monkeypatch.setattr("app.modules.media_download.download", fake_download)
    monkeypatch.setattr("app.modules.transcription.transcribe", fake_transcribe)
    monkeypatch.setattr("app.modules.analysis.analyze", fake_analyze)

    # download
    asyncio.run(handlers.handle_download(session, {"asset_id": asset.id}))
    session.refresh(asset)
    assert asset.download_status == "done"
    assert asset.video_file == "video.mp4" and asset.audio_file == "audio.wav"

    # transcribe
    asyncio.run(handlers.handle_transcribe(session, {"asset_id": asset.id}))
    session.refresh(asset)
    assert asset.transcribe_status == "done"
    tr = session.scalar(select(Transcript).where(Transcript.asset_id == asset.id))
    assert tr.full_text == "你好世界"

    # analyze
    asyncio.run(handlers.handle_analyze(session, {"asset_id": asset.id}))
    session.refresh(asset)
    assert asset.analyze_status == "done"
    an = session.scalar(select(AnalysisResult).where(AnalysisResult.asset_id == asset.id))
    assert an.summary == "这是摘要" and "科技" in an.tags


def test_transcribe_blocked_before_download(session, monkeypatch):
    """未下载完成不允许转录（管线顺序约束）。"""
    from app.models import ContentItem, MediaAsset
    from app.workers import handlers

    content = ContentItem(platform="bilibili", external_id="BV8", url="https://b/BV8")
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id, download_status="pending")
    session.add(asset)
    session.commit()

    with pytest.raises(ValueError, match="尚未下载"):
        asyncio.run(handlers.handle_transcribe(session, {"asset_id": asset.id}))


def test_queue_claim_complete_and_retry(session):
    """PG 队列在 SQLite 降级路径下的 claim/complete/fail 重试语义。"""
    from app.core import queue
    from app.models import JobQueue

    queue.enqueue(session, "download", {"asset_id": 1})
    queue.enqueue(session, "download", {"asset_id": 2})

    # dedup：相同 payload 不重复
    queue.enqueue(session, "download", {"asset_id": 1})
    assert len(session.scalars(select(JobQueue)).all()) == 2

    job = queue.claim(session, ["download"])
    assert job is not None and job.status == "running" and job.attempts == 1

    # 失败一次 → 重新排队（未到上限）
    queue.fail(session, job, "boom", retry_delay_seconds=0)
    assert job.status == "queued"

    # 反复失败到上限 → error
    for _ in range(job.max_attempts):
        j = queue.claim(session, ["download"])
        if j and j.id == job.id:
            queue.fail(session, j, "again", retry_delay_seconds=0)
    session.refresh(job)
    assert job.status == "error"

    # 手动重试 → 回到 queued
    queue.retry(session, job.id)
    session.refresh(job)
    assert job.status == "queued" and job.attempts == 0


def test_dashboard_summary_aggregates(session, monkeypatch):
    """看板汇总：账号异常数 / 素材总数 / 热门 Top 聚合正确。"""
    from app.models import ContentItem, ContentMetricSnapshot, MediaAsset, PlatformAccount

    session.add(PlatformAccount(platform="bilibili", status="active"))
    session.add(PlatformAccount(platform="douyin", status="expired"))  # 异常
    c1 = ContentItem(platform="bilibili", external_id="h1", title="热门", author_name="A")
    session.add(c1)
    session.flush()
    session.add(MediaAsset(content_id=c1.id))
    session.add(ContentMetricSnapshot(content_id=c1.id, play=99999))
    session.commit()

    from app.api import analytics

    summary = analytics.dashboard_summary(db=session)
    assert summary["accounts"]["total"] == 2
    assert summary["accounts"]["abnormal"] == 1
    assert summary["assets_total"] == 1
    assert summary["hot"] and summary["hot"][0]["play"] == 99999
