"""UI-07 / EXEC-06: a timed-out probe must be recoverable from the UI path."""

from datetime import datetime, timedelta

from app.models import AiModel, AiProvider, JobQueue


def _model(session, status="testing"):
    provider = AiProvider(name="p", base_url="https://example.invalid", enabled=True)
    session.add(provider)
    session.flush()
    model = AiModel(
        provider_id=provider.id,
        model_key="m",
        enabled=True,
        probe_status=status,
        probe_started_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(model)
    session.commit()
    return model


def test_reconcile_endpoint_recovers_stuck_testing(client, session):
    model = _model(session, "testing")
    r = client.post("/api/model-access/probes/reconcile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recovered"] == 1
    row = next(item for item in body["models"] if item["id"] == model.id)
    assert row["probe_status"] == "failed"
    assert "超时" in row["probe_error"]


def test_reconcile_does_not_steal_a_live_probe(client, session):
    model = _model(session, "testing")
    session.add(
        JobQueue(
            job_type="probe_model",
            payload={"model_id": model.id},
            status="running",
            heartbeat_at=datetime.utcnow(),
        )
    )
    session.commit()
    body = client.post("/api/model-access/probes/reconcile").json()
    assert body["recovered"] == 0
    row = next(item for item in body["models"] if item["id"] == model.id)
    assert row["probe_status"] == "testing"
