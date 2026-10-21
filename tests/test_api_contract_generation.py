"""ARC-07: generated transport types must track the FastAPI schema."""

from __future__ import annotations

import subprocess
import sys
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_generated_api_types_are_current():
    result = subprocess.run(
        [sys.executable, "scripts/generate_api_types.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "up to date" in result.stdout


def test_frontend_consumes_generated_platform_and_account_types():
    api = (ROOT / "web" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "from './api.generated'" in api
    assert "GeneratedPlatformInfo" in api
    assert "AccountOut" in api
    generated = (ROOT / "web" / "src" / "api.generated.ts").read_text(encoding="utf-8")
    assert "export interface PlatformInfo" in generated
    assert '"metrics"?: string[]' in generated
    assert "export interface AccountOut" in generated


def test_backend_schema_removal_changes_generated_contract():
    """A failing proof: removing a field from OpenAPI must alter the TS surface."""
    from app.main import app

    original = app.openapi()
    mutated = dict(original)
    schemas = dict(original.get("components", {}).get("schemas", {}))
    platform = dict(schemas["PlatformInfo"])
    props = dict(platform.get("properties") or {})
    props.pop("metrics", None)
    platform["properties"] = props
    schemas["PlatformInfo"] = platform
    mutated["components"] = {**original["components"], "schemas": schemas}

    from scripts.generate_api_types import render

    baseline = render(original)
    altered = render(mutated)
    assert '"metrics"?: string[]' in baseline
    assert '"metrics"?: string[]' not in altered


def test_response_models_are_exported_as_typed_aliases():
    """Success responses must be typed, not just request bodies."""
    from app.main import app

    from scripts.generate_api_types import render

    text = render(app.openapi())
    assert "export type list_assets_api_assets_getResponse = AssetCard[]" in text
    assert "export type dashboard_summary_api_dashboard_summary_getResponse = DashboardSummary" in text
    assert "export type list_jobs_api_pipeline_jobs_getResponse = QueueJob[]" in text


def test_generator_exports_operation_routes_and_response_map():
    """Pages can call by operationId instead of duplicating URL strings."""
    from app.main import app

    from scripts.generate_api_types import render

    text = render(app.openapi())
    assert '"list_assets_api_assets_get": { method: "GET", path: "/api/assets" }' in text
    assert '"get_asset_api_assets__asset_id__get"' in text
    assert 'export interface ApiResponses {' in text
    assert '"list_assets_api_assets_get": list_assets_api_assets_getResponse' in text
    assert "export interface ApiTotalHeaderOperations {" in text
    assert '"list_assets_api_assets_get": true' in text
    assert '"list_calls_api_model_access_calls_get": true' in text
    assert "export type list_calls_api_model_access_calls_getResponse = ModelCallAttemptOut[]" in text


def test_total_header_operations_are_consumed_through_one_helper():
    """Every list operation with X-Total-Count must use apiCallWithMeta.

    Hand-rolled api.get() calls previously fabricated the total from the
    current page length when the header was absent, so page N could claim the
    dataset had only N rows.  Keep the generated header contract and the
    frontend consumer locked together.
    """
    generated = (ROOT / "web" / "src" / "api.generated.ts").read_text(encoding="utf-8")
    block = generated.split("export interface ApiTotalHeaderOperations {", 1)[1].split("}", 1)[0]
    operations = re.findall(r'^\s+"([^"]+)": true', block, re.M)
    assert operations == [
        "list_assets_api_assets_get",
        "list_calls_api_model_access_calls_get",
    ]
    for source in (ROOT / "web" / "src").rglob("*"):
        if source.suffix not in {".ts", ".vue"}:
            continue
        if source.name == "api.generated.ts":
            continue
        text = source.read_text(encoding="utf-8")
        for operation in operations:
            if operation not in text:
                continue
            assert "apiCallWithMeta" in text, f"{source} bypasses apiCallWithMeta for {operation}"
            assert f"apiCallWithMeta('{operation}'" in text or f'apiCallWithMeta("{operation}"' in text, (
                f"{source} references {operation} without calling apiCallWithMeta"
            )
    api_source = (ROOT / "web" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "?? 0" not in api_source.split("apiCallWithMeta", 1)[1].split("if (DEMO_MODE)", 1)[0]


def test_removing_asset_response_field_changes_frontend_contract():
    from app.main import app

    from scripts.generate_api_types import render

    original = app.openapi()
    mutated = dict(original)
    schemas = dict(original.get("components", {}).get("schemas", {}))
    card = dict(schemas["AssetCard"])
    props = dict(card.get("properties") or {})
    props.pop("playable", None)
    card["properties"] = props
    schemas["AssetCard"] = card
    mutated["components"] = {**original["components"], "schemas": schemas}
    # Pydantic defaults are optional in OpenAPI. Assert the field itself rather
    # than a particular optional marker so this remains a drift test.
    assert '"playable"' in render(original)
    assert '"playable"' not in render(mutated)


def test_frontend_api_literals_resolve_to_registered_routes():
    """Catch page code calling a path the backend no longer registers."""
    from app.main import app

    def normalize(path: str) -> str:
        path = path.split("?", 1)[0].rstrip("/") or "/"
        # FastAPI path parameters: /api/assets/{asset_id}
        path = re.sub(r"\{[^}]+\}", "{}", path)
        # Frontend path parameters: /api/assets/${id}; a trailing
        # interpolation appending a query string, e.g. jobs${q}, is not one.
        path = re.sub(r"\$\{(?!/)[^}]*\}", "{}", path)
        path = re.sub(r"\$\{[^}]+\}", "", path)
        return path

    registered_paths = list((app.openapi().get("paths") or {}))
    # Normalize both sides by stripping the parameter name, not replacing it
    # with a second placeholder syntax.
    registered = {re.sub(r"\{[^}]+\}", "{}", path) for path in registered_paths}
    # SSE is intentionally hidden from OpenAPI but is still a real route.
    registered.update(
        re.sub(r"\{[^}]+\}", "{}", route.path.rstrip("/") or "/")
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/")
    )
    missing: list[str] = []
    for source in (ROOT / "web" / "src").rglob("*"):
        if source.suffix not in {".ts", ".vue"}:
            continue
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(r"['\"`](/api/[^'\"`]+)['\"`]", text):
            literal = match.group(1)
            normalized = normalize(literal)
            if normalized not in registered:
                # A literal with an interpolated suffix (``jobs${q}``) may
                # resolve to the route before that suffix is appended.
                suffix = re.sub(r"[A-Za-z0-9_]+\$\{[^}]+\}\$$", "", literal)
                if normalize(suffix) in registered:
                    continue
                # Dynamic action segments (``/${kind}``) are validated at the
                # route family level; the caller still has a compile-time
                # union elsewhere. This check exists to catch stale families.
                if re.search(r"/\$\{[^}]+\}", literal):
                    family = normalize(literal.rsplit("/", 1)[0])
                    if any(route.startswith(family + "/") for route in registered):
                        continue
                if normalize(re.sub(r"\$\{[^}]+\}$", "", literal)) in registered:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                missing.append(f"{source.relative_to(ROOT)}:{line} {match.group(1)}")
    assert not missing, "unregistered frontend API paths:\n" + "\n".join(missing)


def test_breakdown_page_prefers_terminal_domain_state_over_workflow_step():
    """A failed breakdown must remain visible even if its step is queued/waiting."""
    page = (ROOT / "web" / "src" / "pages" / "Assets.vue").read_text(encoding="utf-8")
    assert "['done', 'error'].includes(data.status)" in page


def test_every_ui_api_call_uses_a_generated_typed_response():
    """No page may call an operation that OpenAPI leaves as ``unknown``.

    Migrating URL strings alone is not enough: a route without a response model
    generates an empty contract and quietly restores the old blind spot.
    """
    from app.main import app

    from scripts.generate_api_types import render

    text = render(app.openapi())
    response_block = text.split("export interface ApiResponses {", 1)[1].split("}", 1)[0]
    typed = set(re.findall(r'^\s+"([^"]+)":', response_block, re.M))
    used: set[str] = set()
    pattern = re.compile(r"api(?:Call|CallWithMeta|Download)<[^>]*>\(|api(?:Call|CallWithMeta|Download)\(\s*['\"]([^'\"]+)['\"]")
    call_pattern = re.compile(r"api(?:Call|CallWithMeta|Download)\(\s*['\"]([^'\"]+)['\"]")
    for source in (ROOT / "web" / "src").rglob("*"):
        if source.suffix not in {".ts", ".vue"} or source.name == "api.generated.ts":
            continue
        used.update(call_pattern.findall(source.read_text(encoding="utf-8")))
    assert used, "the frontend should call generated operations"
    missing = sorted(used - typed)
    assert not missing, "UI operations without a generated response contract:\n" + "\n".join(missing)
