"""Dependency-aware content-processing workflow.

``job_queue`` remains the technical execution plane.  This module owns the
business workflow: which steps exist, whether prerequisites succeeded, and
which queue job is currently advancing a step.

The implementation is deliberately conservative:

- a request expands to all transitive prerequisites;
- a step is enqueued only after every prerequisite is ``done``;
- failures never release downstream steps;
- a force rerun resets only the requested step and its downstream steps, and
  still waits for prerequisites;
- concurrent requests reuse the active run and the queue's dedup behaviour.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import queue
from app.core.events import publish
from app.domain.workflow import (
    STEPS,
    RunState,
    StepState,
    downstream_of,
    prerequisite_names,
    require_step,
    step_closure,
)
from app.models import (
    AnalysisResult,
    JobQueue,
    MediaAsset,
    PipelineRun,
    PipelineStep,
    Transcript,
    VideoUnderstanding,
    ViralBreakdown,
)

logger = logging.getLogger("platform.workflow")

PROCESS_PRIORITY = 120
_ACTIVE_RUN_STATES = (RunState.WAITING.value, RunState.QUEUED.value, RunState.RUNNING.value)
_ACTIVE_STEP_STATES = (
    StepState.WAITING_PREREQUISITE.value,
    StepState.READY.value,
    StepState.QUEUED.value,
    StepState.RUNNING.value,
)


class WorkflowInputError(ValueError):
    """The requested workflow cannot be registered for this asset."""


@dataclass(frozen=True)
class WorkflowRegistration:
    run: PipelineRun
    created_steps: tuple[str, ...]
    queued_jobs: tuple[int, ...]


def active_run(db: Session, asset_id: int, *, kind: str = "content_processing") -> PipelineRun | None:
    return db.scalar(
        select(PipelineRun)
        .where(
            PipelineRun.asset_id == asset_id,
            PipelineRun.kind == kind,
            PipelineRun.status.in_(_ACTIVE_RUN_STATES),
        )
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )


def get_or_create_run(db: Session, asset_id: int, *, kind: str = "content_processing") -> PipelineRun:
    if db.get(MediaAsset, asset_id) is None:
        raise WorkflowInputError(f"素材不存在: {asset_id}")
    run = active_run(db, asset_id, kind=kind)
    if run is not None:
        return run
    run = PipelineRun(asset_id=asset_id, kind=kind, status=RunState.WAITING.value)
    db.add(run)
    db.flush()
    return run


def latest_run(db: Session, asset_id: int, *, kind: str = "content_processing") -> PipelineRun | None:
    return db.scalar(
        select(PipelineRun)
        .where(PipelineRun.asset_id == asset_id, PipelineRun.kind == kind)
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )


def _step_payload(step: str, asset_id: int, options: dict | None) -> dict:
    """Build the queue payload for a step from request-level options."""
    given = options or {}
    payload: dict = {"asset_id": asset_id}
    if step == "transcribe":
        if isinstance(given.get("transcribe_options"), dict):
            payload["options"] = dict(given["transcribe_options"])
    elif step == "understand_video":
        if given.get("force_category"):
            payload["force_category"] = str(given["force_category"])
        if given.get("use_cover") is not None:
            payload["use_cover"] = bool(given["use_cover"])
    elif step == "breakdown":
        payload["force"] = bool(given.get("force", False))
    return payload


def _merge_step_options(row: PipelineStep, step: str, options: dict | None) -> None:
    """Refresh only explicitly supplied target options, not inherited defaults."""
    given = options or {}
    updates: dict = {}
    if step == "transcribe" and isinstance(given.get("transcribe_options"), dict):
        updates["options"] = dict(given["transcribe_options"])
    elif step == "understand_video":
        if given.get("force_category"):
            updates["force_category"] = str(given["force_category"])
        if given.get("use_cover") is not None:
            updates["use_cover"] = bool(given["use_cover"])
    elif step == "breakdown" and "force" in given:
        updates["force"] = bool(given.get("force"))
    if updates:
        row.payload = {**(row.payload or {}), **updates}


def _result_exists(db: Session, asset: MediaAsset, step: str) -> bool:
    if step == "transcribe":
        return bool(db.scalar(select(Transcript.id).where(Transcript.asset_id == asset.id)))
    if step == "analyze":
        return bool(db.scalar(select(AnalysisResult.id).where(AnalysisResult.asset_id == asset.id)))
    if step == "understand_video":
        row = db.scalar(
            select(VideoUnderstanding).where(VideoUnderstanding.asset_id == asset.id)
        )
        return bool(row is not None and row.status == "done")
    if step == "breakdown":
        row = db.scalar(select(ViralBreakdown).where(ViralBreakdown.asset_id == asset.id))
        return bool(row is not None and row.status == "done")
    return False


def _is_request_satisfied(db: Session, run: PipelineRun, requested: set[str]) -> bool:
    """True when every requested target already succeeded and needs no rerun."""
    rows = {
        row.step: row
        for row in db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all()
    }
    return bool(requested) and all(
        rows.get(step) is not None
        and rows[step].status == StepState.DONE.value
        and rows[step].job_id is not None
        for step in requested
    )


def _import_existing_results(
    db: Session,
    run: PipelineRun,
    asset: MediaAsset,
) -> None:
    """Seed a new run from results produced before workflows existed.

    Only waiting steps are touched.  A queued/running step is never marked done
    from a stale asset flag.
    """
    rows = db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all()
    for row in rows:
        if row.status not in (
            StepState.WAITING_PREREQUISITE.value,
            StepState.READY.value,
        ) or row.job_id is not None:
            continue
        if _result_exists(db, asset, row.step):
            row.status = StepState.DONE.value
            row.finished_at = datetime.utcnow()
    db.flush()


def _refresh_waiting_states(db: Session, run: PipelineRun) -> None:
    rows = db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all()
    by_name = {row.step: row for row in rows}
    for row in rows:
        if row.status in (
            StepState.QUEUED.value,
            StepState.RUNNING.value,
            StepState.DONE.value,
            StepState.SKIPPED.value,
            StepState.ERROR.value,
        ):
            continue
        dependencies = row.depends_on or list(prerequisite_names(row.step))
        ready = all(
            by_name.get(dep) is not None and by_name[dep].status == StepState.DONE.value
            for dep in dependencies
        )
        row.status = StepState.READY.value if ready else StepState.WAITING_PREREQUISITE.value
    db.flush()


def _refresh_run_status(db: Session, run: PipelineRun) -> None:
    rows = db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all()
    states = {row.status for row in rows}
    if not rows:
        run.status = RunState.WAITING.value
    elif StepState.ERROR.value in states:
        run.status = RunState.ERROR.value
    elif states <= {StepState.DONE.value, StepState.SKIPPED.value}:
        run.status = RunState.DONE.value
        run.finished_at = run.finished_at or datetime.utcnow()
    elif StepState.RUNNING.value in states:
        run.status = RunState.RUNNING.value
    elif StepState.QUEUED.value in states or StepState.READY.value in states:
        run.status = RunState.QUEUED.value
    else:
        run.status = RunState.WAITING.value
    if run.status != RunState.ERROR.value:
        run.error = ""
    db.flush()


def _queue_ready_steps(db: Session, run: PipelineRun) -> list[PipelineStep]:
    rows = db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all()
    queued: list[PipelineStep] = []
    for row in rows:
        if row.status != StepState.READY.value:
            continue
        spec = require_step(row.step)
        job = queue.enqueue(
            db,
            spec.job_type,
            dict(row.payload or {"asset_id": run.asset_id}),
            priority=PROCESS_PRIORITY,
            commit=False,
        )
        row.job_id = job.id
        row.status = StepState.QUEUED.value
        row.error = ""
        queued.append(row)
    if queued:
        db.flush()
    return queued


def _reset_for_force(db: Session, run: PipelineRun, targets: set[str]) -> None:
    affected = set(targets)
    for target in targets:
        affected |= downstream_of(target)
    rows = db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all()
    for row in rows:
        if row.step not in affected:
            continue
        # Never create a second concurrent job for a step already executing.
        if row.status in (StepState.QUEUED.value, StepState.RUNNING.value):
            continue
        row.status = StepState.WAITING_PREREQUISITE.value
        row.job_id = None
        row.error = ""
        row.started_at = None
        row.finished_at = None


def register_workflow(
    db: Session,
    asset_id: int,
    steps: list[str] | tuple[str, ...] | set[str],
    *,
    options: dict | None = None,
    force: list[str] | tuple[str, ...] | set[str] = (),
    require_downloaded: bool = True,
    commit: bool = False,
) -> WorkflowRegistration:
    """Register requested steps and enqueue only currently-ready work."""
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise WorkflowInputError(f"素材不存在: {asset_id}")
    if require_downloaded and asset.download_status != "done":
        raise WorkflowInputError("素材尚未下载完成，无法开始加工")
    if asset.deleted_at is not None:
        raise WorkflowInputError("素材已删除")

    requested = set(steps)
    expanded = step_closure(requested)
    run = active_run(db, asset_id)
    if run is not None and not force and _is_request_satisfied(db, run, requested):
        # A completed target is a completed request (and a completed model call).
        # Reusing it prevents automatic flows from billing again on every sync.
        return WorkflowRegistration(run, (), ())
    if run is None:
        prior = latest_run(db, asset_id)
        if prior is not None and not force and _is_request_satisfied(db, prior, requested):
            return WorkflowRegistration(prior, (), ())
        run = get_or_create_run(db, asset_id)
    existing = {
        row.step: row
        for row in db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all()
    }
    created: list[str] = []
    for name in expanded:
        if name in existing:
            if name in requested:
                _merge_step_options(existing[name], name, options)
            continue
        spec = STEPS[name]
        row = PipelineStep(
            run_id=run.id,
            step=name,
            status=StepState.WAITING_PREREQUISITE.value,
            depends_on=list(spec.depends_on),
            payload=_step_payload(name, asset_id, options),
        )
        db.add(row)
        existing[name] = row
        created.append(name)
    db.flush()

    # Existing results are reusable for both automatic and manual requests.  A
    # force request resets its target below, so it still performs the rerun.
    _import_existing_results(db, run, asset)
    if force:
        _reset_for_force(db, run, set(force))
    _refresh_waiting_states(db, run)
    queued = _queue_ready_steps(db, run)
    _refresh_run_status(db, run)
    if queued:
        publish(
            "pipeline_status",
            {"asset_id": asset_id, "run_id": run.id, "status": run.status},
            db=db,
        )
    if commit:
        db.commit()
        db.refresh(run)
    return WorkflowRegistration(run, tuple(sorted(created)), tuple(job.id for job in queued))


def start_step(db: Session, job: JobQueue) -> bool:
    """Mark the workflow step owned by ``job`` as running."""
    row = db.scalar(select(PipelineStep).where(PipelineStep.job_id == job.id))
    if row is None:
        return False
    row.status = StepState.RUNNING.value
    row.started_at = datetime.utcnow()
    row.attempts = int(row.attempts or 0) + 1
    run = db.get(PipelineRun, row.run_id)
    if run is not None:
        _refresh_run_status(db, run)
    db.flush()
    return True


def complete_step(
    db: Session,
    *,
    job: JobQueue,
    failed: bool = False,
    error: str = "",
) -> bool:
    """Close the step owned by a terminal job and release downstream steps."""
    row = db.scalar(select(PipelineStep).where(PipelineStep.job_id == job.id))
    if row is None:
        return False
    row.status = StepState.ERROR.value if failed else StepState.DONE.value
    row.error = error[:512] if failed else ""
    row.finished_at = datetime.utcnow()
    run = db.get(PipelineRun, row.run_id)
    if run is not None:
        if failed:
            _refresh_run_status(db, run)
            if run.status == RunState.ERROR.value:
                run.error = error[:512]
        else:
            _refresh_waiting_states(db, run)
            _queue_ready_steps(db, run)
            _refresh_run_status(db, run)
        publish(
            "pipeline_status",
            {"asset_id": run.asset_id, "run_id": run.id, "status": run.status},
            db=db,
        )
    db.flush()
    return True


def retry_step(db: Session, job: JobQueue) -> bool:
    """After a manual queue retry, return the owning step to queued."""
    row = db.scalar(select(PipelineStep).where(PipelineStep.job_id == job.id))
    if row is None:
        return False
    row.status = StepState.QUEUED.value
    row.error = ""
    row.finished_at = None
    run = db.get(PipelineRun, row.run_id)
    if run is not None:
        _refresh_run_status(db, run)
    db.flush()
    return True


def breakdown_prerequisites_done(db: Session, asset_id: int) -> bool:
    return step_prerequisites_done(db, asset_id, "breakdown")


def step_prerequisites_done(db: Session, asset_id: int, step: str) -> bool:
    """Whether a step's persisted prerequisites are all done."""
    run = active_run(db, asset_id)
    if run is None:
        return False
    rows = {
        row.step: row
        for row in db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all()
    }
    return all(
        rows.get(name) is not None and rows[name].status == StepState.DONE.value
        for name in prerequisite_names(step)
    )


def ensure_breakdown_prerequisites(db: Session, asset_id: int, *, force: bool = False) -> PipelineRun:
    """Make a direct service call obey the same dependency graph as the API."""
    return register_workflow(
        db,
        asset_id,
        ["breakdown"],
        options={"force": force},
        force=["breakdown"] if force else (),
    ).run


def public_status(db: Session, asset_id: int) -> dict | None:
    run = db.scalar(
        select(PipelineRun)
        .where(PipelineRun.asset_id == asset_id)
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    if run is None:
        return None
    order = {"transcribe": 0, "analyze": 1, "understand_video": 2, "breakdown": 3}
    rows = sorted(
        db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all(),
        key=lambda row: (order.get(row.step, 99), row.id or 0),
    )
    by_name = {row.step: row for row in rows}
    steps = []
    for row in rows:
        blocked_by = [
            name
            for name in (row.depends_on or [])
            if by_name.get(name) is None or by_name[name].status != StepState.DONE.value
        ]
        steps.append(
            {
                "step": row.step,
                "status": row.status,
                "depends_on": row.depends_on or [],
                "blocked_by": blocked_by,
                "job_id": row.job_id,
                "attempts": row.attempts,
                "error": row.error or "",
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }
        )
    return {
        "run_id": run.id,
        "asset_id": run.asset_id,
        "kind": run.kind,
        "status": run.status,
        "error": run.error or "",
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "steps": steps,
    }


def active_understand_reservations(db: Session) -> set[int]:
    """Assets whose advanced step is already reserved by the workflow."""
    rows = db.execute(
        select(PipelineRun.asset_id)
        .join(PipelineStep, PipelineStep.run_id == PipelineRun.id)
        .where(
            PipelineRun.status.in_(_ACTIVE_RUN_STATES),
            PipelineStep.step == "understand_video",
            PipelineStep.status.in_(_ACTIVE_STEP_STATES),
        )
    ).all()
    return {int(asset_id) for (asset_id,) in rows}
