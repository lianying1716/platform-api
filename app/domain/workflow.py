"""Declarative content-processing workflow graph.

The graph is deliberately data-only: it does not import SQLAlchemy or read
configuration.  API routes, the workflow engine, worker handlers and tests all
share this definition, so a new processing stage has one place to declare its
prerequisites and one place to make it reachable from a request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StepState(StrEnum):
    WAITING_PREREQUISITE = "waiting_prerequisite"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


class RunState(StrEnum):
    WAITING = "waiting"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class StepDefinition:
    name: str
    job_type: str
    depends_on: tuple[str, ...] = ()


# One graph for every local content-processing operation.  ``download`` stays
# outside the graph because a run is only created after a usable local file is
# present; that keeps the pre-download state machine unchanged.
STEPS: dict[str, StepDefinition] = {
    "transcribe": StepDefinition("transcribe", "transcribe"),
    "analyze": StepDefinition("analyze", "analyze", ("transcribe",)),
    "understand_video": StepDefinition(
        "understand_video", "understand_video", ("transcribe",)
    ),
    "breakdown": StepDefinition(
        "breakdown", "breakdown", ("transcribe", "understand_video")
    ),
}

STEP_LABELS: dict[str, str] = {
    "transcribe": "语音转文字",
    "analyze": "分析速览",
    "understand_video": "视频理解",
    "breakdown": "爆款拆解",
}


def require_step(name: str) -> StepDefinition:
    try:
        return STEPS[name]
    except KeyError as exc:
        raise ValueError(f"未知加工步骤: {name!r}") from exc


def step_closure(requested: list[str] | tuple[str, ...] | set[str]) -> set[str]:
    """Return ``requested`` plus every transitive prerequisite."""
    out: set[str] = set()

    def visit(name: str) -> None:
        definition = require_step(name)
        if name in out:
            return
        out.add(name)
        for dependency in definition.depends_on:
            visit(dependency)

    for name in requested:
        visit(name)
    return out


def downstream_of(name: str) -> set[str]:
    """Return steps that directly or transitively depend on ``name``."""
    out: set[str] = set()
    changed = True
    while changed:
        changed = False
        for candidate, definition in STEPS.items():
            if candidate in out:
                continue
            if name in definition.depends_on or any(dep in out for dep in definition.depends_on):
                out.add(candidate)
                changed = True
    return out


def prerequisite_names(name: str) -> tuple[str, ...]:
    return require_step(name).depends_on
