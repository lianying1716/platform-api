"""Keyword crawl must apply off/gate/force exactly, before worker execution."""

import asyncio

from sqlalchemy import select

from app.core.crypto import encrypt_credential
from app.models import AccountCredential, CrawlTask, JobQueue, PlatformAccount


def _account(session):
    account = PlatformAccount(platform="douyin", display_name="crawl-mode", status="active")
    session.add(account)
    session.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential({"cookie": "c=1"}))
    session.commit()
    return account


def _patch_search(monkeypatch):
    from app.modules import crawler

    async def fake_search(**kwargs):
        return [{
            "external_id": "aw-mode-1",
            "content_type": "video",
            "title": "mode",
            "url": "https://example.com/v",
            "metrics": {"play": 1, "like": 0, "comment": 0},
        }]

    monkeypatch.setattr(crawler, "search", fake_search)


def _run(session, monkeypatch, mode):
    from app.services import crawl_service

    _account(session)
    task = CrawlTask(platform="douyin", keyword="k", comment_mode=mode)
    session.add(task)
    session.commit()
    _patch_search(monkeypatch)
    run = asyncio.run(crawl_service.run_crawl_task(session, task.id))
    assert run.status == "success", run.error
    return session.scalars(select(JobQueue).where(JobQueue.job_type == "fetch_comments")).all()


def test_crawl_off_never_enqueues_comments(session, monkeypatch):
    assert _run(session, monkeypatch, "off") == []


def test_crawl_gate_keeps_heat_gate(session, monkeypatch):
    assert _run(session, monkeypatch, "gate") == []


def test_crawl_force_bypasses_heat_gate(session, monkeypatch):
    jobs = _run(session, monkeypatch, "force")
    assert len(jobs) == 1
    assert jobs[0].payload["content_id"] is not None
