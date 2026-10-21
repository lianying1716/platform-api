"""ARC-07 follow-up: the API migration backlog is measurable and stable."""

from scripts.audit_frontend_api_calls import audit, summarize


# Migration ceiling.  Lower this number when a page moves to generated
# operation IDs; never raise it.  New pages must use apiCall(operationId).
MAX_HAND_WRITTEN_CALLS = 0
MAX_HAND_WRITTEN_WRITES = 0


def test_frontend_api_audit_classifies_writes_and_reads():
    report = summarize(audit())
    assert report["total"] == 0
    assert report["write_calls"] + report["read_calls"] == report["total"]
    assert report["highest_risk"] == []


def test_hand_written_api_calls_do_not_grow():
    report = summarize(audit())
    assert report["total"] <= MAX_HAND_WRITTEN_CALLS, (
        "New hand-written API calls were added. Use apiCall(operationId) from "
        "api.generated.ts instead, or lower the ceiling when migrating."
    )
    assert report["write_calls"] <= MAX_HAND_WRITTEN_WRITES, (
        "New hand-written write calls were added; these bypass request/response "
        "type checking and are the highest-risk migration surface."
    )
