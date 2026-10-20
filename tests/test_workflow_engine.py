"""Content workflow DAG: durable dependencies, not handler-side chaining."""

import asyncio

import pytest
from sqlalchemy import select

from app.models import (
    AnalysisResult,
    ContentItem,
    JobQueue,
    MediaAsset,
    PipelineRun,
    PipelineStep,
    Transcript,
    VideoUnderstanding,
)


def _asset(db, *, status="done"):
    content = ContentItem(
        account_id=1,
        platform="douyin",
        external_id=f"wf-{id(db)}-{status}",
        content_type="video",
        title="工作流测试",
        url="https://example.test/v",
    )
    db.add(content)
    db.flush()
    asset = MediaAsset(
        content_id=content.id,
        relative_path=f"douyin/{content.id}",
        video_file="v.mp4",
        download_status=status,
    )
    db.add(asset)
    db.commit()
    return content, asset


def _steps(db, asset_id):
    run = db.scalar(
        select(PipelineRun).where(PipelineRun.asset_id == asset_id).order_by(PipelineRun.id.desc())
    )
    assert run is not None
    return run, {row.step: row for row in db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id))}


def test_breakdown_waits_for_transcript_and_understanding(session):
    from app.services.workflow_engine import register_workflow

    _content, asset = _asset(session)
    result = register_workflow(session, asset.id, ["breakdown"], commit=True)
    run, steps = _steps(session, asset.id)

    # The only immediately runnable step is transcribe; downstream steps stay
    # blocked and have no queue job.
    assert result.queued_jobs
    assert steps["transcribe"].status == "queued"
    assert steps["understand_video"].status == "waiting_prerequisite"
    assert steps["understand_video"].job_id is None
    assert steps["breakdown"].status == "waiting_prerequisite"
    assert steps["breakdown"].job_id is None
    assert run.status == "queued"


def test_breakdown_service_refuses_without_understanding(monkeypatch, session):
    from app.services import breakdown_service

    content, asset = _asset(session)
    session.add(Transcript(asset_id=asset.id, full_text="只有转录", language="zh", model="base"))
    session.commit()
    called = {"llm": False}

    async def fake_llm(*args, **kwargs):
        called["llm"] = True
        return {"data": {}, "usage": {}, "model": "m"}

    monkeypatch.setattr(breakdown_service.breakdown, "run_llm", fake_llm)
    with pytest.raises(breakdown_service.BreakdownInputError, match="视频理解"):
        asyncio.run(breakdown_service.run_breakdown(session, asset.id))
    assert called["llm"] is False


def test_transcribe_success_releases_understand(session):
    from app.services import workflow_engine

    _content, asset = _asset(session)
    workflow_engine.register_workflow(session, asset.id, ["breakdown"], commit=True)
    transcribe_job = session.scalar(select(JobQueue).where(JobQueue.job_type == "transcribe"))
    transcribe_job.status = "running"
    session.commit()
    workflow_engine.start_step(session, transcribe_job)
    session.commit()

    workflow_engine.complete_step(session, job=transcribe_job, failed=False)
    session.commit()
    _run, steps = _steps(session, asset.id)
    assert steps["transcribe"].status == "done"
    assert steps["understand_video"].status == "queued"
    assert steps["understand_video"].job_id is not None
    assert steps["breakdown"].status == "waiting_prerequisite"


def test_transcribe_failure_does_not_release_downstream(session):
    from app.services import workflow_engine

    _content, asset = _asset(session)
    workflow_engine.register_workflow(session, asset.id, ["breakdown"], commit=True)
    job = session.scalar(select(JobQueue).where(JobQueue.job_type == "transcribe"))
    workflow_engine.complete_step(session, job=job, failed=True, error="whisper crashed")
    session.commit()

    _run, steps = _steps(session, asset.id)
    assert steps["transcribe"].status == "error"
    assert steps["understand_video"].status == "waiting_prerequisite"
    assert steps["breakdown"].status == "waiting_prerequisite"
    assert session.query(JobQueue).filter_by(job_type="understand_video").count() == 0


def test_understand_success_releases_breakdown(session):
    from app.services import workflow_engine

    _content, asset = _asset(session)
    session.add(Transcript(asset_id=asset.id, full_text="转录", language="zh", model="base"))
    session.commit()
    workflow_engine.register_workflow(session, asset.id, ["breakdown"], commit=True)
    # Existing transcript is seeded as done, so the first queued job is
    # understand_video.
    understand_job = session.scalar(select(JobQueue).where(JobQueue.job_type == "understand_video"))
    assert understand_job is not None
    workflow_engine.complete_step(session, job=understand_job, failed=False)
    session.commit()

    _run, steps = _steps(session, asset.id)
    assert steps["breakdown"].status == "queued"
    assert steps["breakdown"].job_id is not None


def test_force_rerun_keeps_prerequisites(session):
    from app.services.workflow_engine import register_workflow

    _content, asset = _asset(session)
    register_workflow(session, asset.id, ["breakdown"], commit=True)
    register_workflow(
        session,
        asset.id,
        ["breakdown"],
        options={"force": True},
        force=["breakdown"],
        commit=True,
    )
    _run, steps = _steps(session, asset.id)
    assert steps["transcribe"].status in {"queued", "running", "done"}
    assert steps["breakdown"].status == "waiting_prerequisite"
    assert steps["breakdown"].job_id is None


def test_duplicate_request_does_not_duplicate_jobs(session):
    from app.services.workflow_engine import register_workflow

    _content, asset = _asset(session)
    register_workflow(session, asset.id, ["breakdown"], commit=True)
    register_workflow(session, asset.id, ["breakdown"], commit=True)
    assert session.query(JobQueue).filter_by(job_type="transcribe").count() == 1
    assert session.query(JobQueue).filter_by(job_type="breakdown").count() == 0


def test_existing_results_are_reused_for_prerequisites(session):
    from app.services.workflow_engine import register_workflow

    content, asset = _asset(session)
    session.add(Transcript(asset_id=asset.id, full_text="已有转录", language="zh", model="base"))
    session.add(
        VideoUnderstanding(
            asset_id=asset.id,
            category="other",
            summary="已有理解",
            dimensions={"visual": "画面"},
            timeline=[{"t": 1, "event": "开场"}],
            status="done",
        )
    )
    session.commit()
    result = register_workflow(session, asset.id, ["breakdown"], commit=True)
    _run, steps = _steps(session, asset.id)
    assert steps["transcribe"].status == "done"
    assert steps["understand_video"].status == "done"
    assert steps["breakdown"].status == "queued"
    assert result.queued_jobs


def test_existing_target_is_not_rebilled_without_force(session):
    from app.services.workflow_engine import register_workflow

    _content, asset = _asset(session)
    session.add(Transcript(asset_id=asset.id, full_text="已有转录", language="zh", model="base"))
    session.add(
        VideoUnderstanding(
            asset_id=asset.id,
            category="other",
            summary="已有理解",
            dimensions={"visual": "画面"},
            timeline=[{"t": 1, "event": "开场"}],
            status="done",
        )
    )
    session.commit()
    register_workflow(session, asset.id, ["breakdown"], commit=True)
    first = session.scalar(select(JobQueue).where(JobQueue.job_type == "breakdown"))
    assert first is not None
    first.status = "done"
    step = session.scalar(select(PipelineStep).where(PipelineStep.step == "breakdown"))
    step.status = "done"
    run = session.get(PipelineRun, step.run_id)
    run.status = "done"
    session.commit()

    register_workflow(session, asset.id, ["breakdown"], commit=True)
    assert session.query(JobQueue).filter_by(job_type="breakdown").count() == 1


def test_failed_understand_does_not_set_shared_asset_error(session):
    from app.services import workflow_engine
    from app.services.asset_state import refresh_asset_after_step

    _content, asset = _asset(session)
    workflow_engine.register_workflow(session, asset.id, ["understand_video"], commit=True)
    job = session.scalar(select(JobQueue).where(JobQueue.job_type == "transcribe"))
    job.status = "done"
    workflow_engine.complete_step(session, job=job, failed=False)
    session.commit()
    understand_job = session.scalar(select(JobQueue).where(JobQueue.job_type == "understand_video"))
    workflow_engine.complete_step(session, job=understand_job, failed=True, error="vision failed")
    refresh_asset_after_step(session, "understand_video", {"asset_id": asset.id}, failed=True, error="vision failed")
    session.refresh(asset)
    assert asset.understand_status == "error"
    assert asset.error == ""


def test_public_status_explains_blockers(session):
    from app.services.workflow_engine import public_status, register_workflow

    _content, asset = _asset(session)
    register_workflow(session, asset.id, ["breakdown"], commit=True)
    status = public_status(session, asset.id)
    by_name = {row["step"]: row for row in status["steps"]}
    assert by_name["transcribe"]["status"] == "queued"
    assert by_name["understand_video"]["blocked_by"] == ["transcribe"]
    assert by_name["breakdown"]["blocked_by"] == ["transcribe", "understand_video"]


def test_unfinished_download_cannot_start_processing(session):
    from app.services.workflow_engine import WorkflowInputError, register_workflow

    _content, asset = _asset(session, status="pending")
    with pytest.raises(WorkflowInputError, match="尚未下载"):
        register_workflow(session, asset.id, ["transcribe"])


def test_force_transcribe_creates_new_job_after_done(session):
    from app.services.workflow_engine import register_workflow

    _content, asset = _asset(session)
    register_workflow(session, asset.id, ["transcribe"], commit=True)
    first = session.scalar(select(JobQueue).where(JobQueue.job_type == "transcribe"))
    first.status = "done"
    workflow_row = session.scalar(
        select(PipelineStep).where(PipelineStep.step == "transcribe")
    )
    workflow_row.status = "done"
    run = session.get(PipelineRun, workflow_row.run_id)
    run.status = "done"
    session.commit()

    register_workflow(
        session, asset.id, ["transcribe"], force=["transcribe"], commit=True
    )
    jobs = session.scalars(select(JobQueue).where(JobQueue.job_type == "transcribe")).all()
    assert len(jobs) == 2
    assert jobs[-1].status == "queued"


def test_force_understand_does_not_hide_unfinished_transcript(session):
    from app.services.workflow_engine import register_workflow

    _content, asset = _asset(session)
    session.add(Transcript(asset_id=asset.id, full_text="转录", language="zh", model="base"))
    session.commit()
    register_workflow(session, asset.id, ["understand_video"], commit=True)
    # Mark legacy transcript-derived step done, but transaction is still in
    # flight; a forced understand rerun must remain queued for the step but
    # cannot bypass its declared prerequisite.
    register_workflow(
        session,
        asset.id,
        ["understand_video"],
        force=["understand_video"],
        commit=True,
    )
    _run, steps = _steps(session, asset.id)
    assert steps["transcribe"].status == "done"
    assert steps["understand_video"].status == "queued"


def test_workflow_api_exposes_waiting_prerequisite(client, session):
    from app.services.workflow_engine import register_workflow

    content, asset = _asset(session)
    register_workflow(session, asset.id, ["breakdown"], commit=True)
    body = client.get(f"/api/pipeline/assets/{asset.id}").json()
    by_name = {row["step"]: row for row in body["steps"]}
    assert by_name["breakdown"]["status"] == "waiting_prerequisite"
    assert by_name["breakdown"]["job_id"] is None


def test_workflow_api_returns_empty_contract_for_legacy_asset(client, session):
    _content, asset = _asset(session)
    response = client.get(f"/api/pipeline/assets/{asset.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"] == asset.id
    assert body["run_id"] is None
    assert body["status"] == "not_started"
    assert body["steps"] == []


def test_runner_releases_next_step_only_after_success(_engine, monkeypatch):
    """Real runner path: transcribe success advances understanding exactly once."""
    import asyncio

    from sqlalchemy.orm import sessionmaker

    import app.workers.runner as runner
    from app.services.workflow_engine import register_workflow

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    content, asset = _asset(db)
    register_workflow(db, asset.id, ["breakdown"], commit=True)
    db.close()

    calls: list[str] = []

    async def fake_transcribe(db, payload, job=None):
        calls.append("transcribe")
        db.add(Transcript(asset_id=payload["asset_id"], full_text="真实转录", language="zh", model="base"))
        db.commit()

    monkeypatch.setattr(runner, "SessionLocal", Session)
    monkeypatch.setattr(
        runner,
        "HANDLERS",
        {"transcribe": fake_transcribe, "understand_video": lambda *a, **k: _unexpected()},
    )
    assert asyncio.run(runner._run_one()) is True

    db = Session()
    _run, steps = _steps(db, asset.id)
    assert calls == ["transcribe"]
    assert steps["transcribe"].status == "done"
    assert steps["understand_video"].status == "queued"
    assert db.query(JobQueue).filter_by(job_type="understand_video").count() == 1
    db.close()


def _unexpected():
    raise AssertionError("downstream handler must not run in the same runner turn")
