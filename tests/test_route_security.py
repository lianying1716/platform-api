"""Security regression tests for transport-level boundaries."""

import re

from app.main import app


def test_events_stream_requires_admin(anon_client):
    response = anon_client.get("/api/events/stream")
    assert response.status_code == 401


def test_every_non_public_api_route_rejects_anonymous_requests(anon_client):
    """A newly added router must not become public by forgetting a dependency.

    GET probes are enough to prove the authentication dependency runs before the
    handler; mutating methods use an empty body because authentication precedes
    body validation on these routes.  The explicit public allowlist is tiny and
    reviewed here rather than duplicated in every router.
    """
    public = {
        "/api/auth/status",
        "/api/auth/login",
        "/api/auth/setup",
        "/api/auth/logout",
        "/api/system/version",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
    failures = []
    seen = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = {m for m in getattr(route, "methods", set()) if m not in ("HEAD", "OPTIONS")}
        if not path.startswith("/api") or path in public or path in seen:
            continue
        seen.add(path)
        probe_path = re.sub(r"\{[^}]+\}", "1", path)
        for method in methods:
            response = anon_client.request(
                method,
                probe_path,
                json={} if method in ("POST", "PUT", "PATCH") else None,
            )
            if response.status_code not in (401, 403):
                failures.append(f"{method} {probe_path} -> {response.status_code}")
    assert not failures, "未鉴权即可到达的业务路由: " + ", ".join(failures)
