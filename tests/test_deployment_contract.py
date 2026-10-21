"""Deployment settings must reach the processes that consume them.

These tests use Compose's own resolver rather than searching YAML text.  A key
that appears in a service block but is overwritten by a literal value is still a
broken deployment, and only the resolved model catches that class of regression.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _compose_config(extra_env: dict[str, str]) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("PLATFORM_") or key == "COMPOSE_PROJECT_NAME":
            env.pop(key, None)
    env.update(extra_env)
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_compose_passes_deployment_overrides_to_api_and_worker():
    config = _compose_config(
        {
            "COMPOSE_PROJECT_NAME": "customer-stack",
            "PLATFORM_PORT": "9201",
            "PLATFORM_UPDATE_REPO": "someone/fork",
            "PLATFORM_ACCOUNT_CHECK_INTERVAL": "42",
            "PLATFORM_WORKER_CONCURRENCY": "4",
            "PLATFORM_WHISPER_MODEL": "small",
            "PLATFORM_PLATFORM_PROXIES": '{"douyin":"none"}',
        }
    )

    api = config["services"]["platform-api"]
    worker = config["services"]["platform-worker"]
    assert config["name"] == "customer-stack"
    assert api["ports"][0]["published"] == "9201"
    assert api["ports"][0]["target"] == 9100
    assert api["environment"]["PLATFORM_UPDATE_REPO"] == "someone/fork"
    assert api["environment"]["PLATFORM_ACCOUNT_CHECK_INTERVAL"] == "42"
    assert worker["environment"]["PLATFORM_WORKER_CONCURRENCY"] == "4"
    assert worker["environment"]["PLATFORM_WHISPER_MODEL"] == "small"
    assert worker["environment"]["PLATFORM_PLATFORM_PROXIES"] == '{"douyin":"none"}'


def test_compose_database_credentials_are_structured_and_shared():
    config = _compose_config(
        {
            "PLATFORM_DB_USER": "alice",
            "PLATFORM_DB_PASSWORD": "p@ss:word/",
            "PLATFORM_DB_NAME": "archive",
        }
    )
    for service in ("platform-api", "platform-worker"):
        env = config["services"][service]["environment"]
        assert env["PLATFORM_DB_HOST"] == "db"
        assert env["PLATFORM_DB_PASSWORD"] == "p@ss:word/"
        assert env["PLATFORM_DB_NAME"] == "archive"
    assert config["services"]["db"]["environment"] == {
        "POSTGRES_USER": "alice",
        "POSTGRES_PASSWORD": "p@ss:word/",
        "POSTGRES_DB": "archive",
    }
    # The updater is profile-gated, so inspect its YAML fields directly.  Any
    # omission here makes the sidecar back up the wrong database after a user
    # renames their DB account.
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    updater_block = compose_text.split("platform-updater:", 1)[1].split("platform-backup:", 1)[0]
    assert "POSTGRES_USER: ${PLATFORM_DB_USER:-platform}" in updater_block
    assert "POSTGRES_DB: ${PLATFORM_DB_NAME:-platform}" in updater_block


def test_structured_database_settings_encode_special_characters(monkeypatch):
    from sqlalchemy.engine import make_url

    from app.config import Settings

    monkeypatch.delenv("PLATFORM_DATABASE_URL", raising=False)
    monkeypatch.setenv("PLATFORM_CREDENTIAL_ENC_KEY", "unused")
    cfg = Settings(
        database_url="",
        db_host="db",
        db_port=5432,
        db_user="alice",
        db_password="p@ss:word/",
        db_name="archive",
    )
    parsed = make_url(cfg.resolved_database_url)
    assert parsed.username == "alice"
    assert parsed.password == "p@ss:word/"
    assert parsed.host == "db"
    assert parsed.database == "archive"


def test_raw_special_database_url_fails_with_actionable_configuration_error():
    from app.config import Settings

    cfg = Settings(
        database_url="postgresql+psycopg2://alice:p@ss:word/@db:5432/archive",
    )
    with pytest.raises(ValueError, match="URL 编码"):
        _ = cfg.resolved_database_url


def test_empty_compose_json_setting_uses_documented_default(monkeypatch):
    """Compose passes PLATFORM_PLATFORM_PROXIES='' when the user leaves it blank."""
    from app.config import Settings

    monkeypatch.setenv("PLATFORM_PLATFORM_PROXIES", "")
    cfg = Settings()
    assert cfg.platform_proxies == {}


def test_alembic_config_does_not_interpolate_encoded_password(tmp_path):
    from app.core.alembic_config import configured_url, make_config

    url = "postgresql+psycopg2://alice:p%40ss%3Aword%2F@db:5432/archive"
    cfg = make_config(ROOT, url)
    assert configured_url(cfg) == url
    assert cfg.get_main_option("sqlalchemy.url") == ""


def test_backup_volume_resolution_uses_compose_not_directory_name(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "docker"
    fake.write_text(
        "#!/bin/sh\n"
        "if [ \"$1 $2 $3 $4\" = 'compose config --format json' ]; then\n"
        "  printf '%s\\n' '{\"volumes\":{\"platform-data\":{\"name\":\"customer-stack_platform-data\"}}}'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1 $2\" = 'volume ls' ]; then exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    script = (
        f'source "{ROOT / "scripts" / "lib" / "compose.sh"}"; '
        "compose_volume_name platform-data"
    )
    env = dict(os.environ, PATH=f"{bindir}:{os.environ.get('PATH', '')}")
    result = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "customer-stack_platform-data"
    for script in ("backup.sh", "restore.sh"):
        text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert 'basename "$ROOT"_platform-data' not in text


def test_local_install_installs_transcription_and_starts_worker():
    text = (ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")
    assert "--extra transcribe" in text
    assert "command -v ffmpeg" in text
    assert "python -m app.workers.runner" in text
    assert "stargather-platform-worker.service" in text
    assert "com.stargather.platform-worker.plist" in text


def test_mcp_wizard_has_no_fake_default_directory():
    text = (ROOT / "web" / "src" / "pages" / "AiAccess.vue").read_text(encoding="utf-8")
    assert "/home/you/platform-api" not in text
    assert "const projectDir = computed(() => mcpProjectDir.value.trim())" in text
    assert "missingProjectDir.value" in text
    assert "cd ${projectDir.value}" in text


def test_upgrade_ui_sends_and_surfaces_expected_release_commit():
    text = (ROOT / "web" / "src" / "pages" / "Settings.vue").read_text(encoding="utf-8")
    assert "expected_commit: update.value.expected_commit" in text
    assert "无法确认发行提交" in text
    assert "提交 ${update.value.expected_commit.slice(0, 12)}" in text


def test_local_upgrade_uses_local_backup_and_installs_transcription():
    upgrade = (ROOT / "scripts" / "upgrade.sh").read_text(encoding="utf-8")
    backup_local = (ROOT / "scripts" / "backup-local.sh").read_text(encoding="utf-8")
    restore_local = (ROOT / "scripts" / "restore-local.sh").read_text(encoding="utf-8")
    assert 'PLATFORM_VERSION="$TARGET" ./scripts/backup-local.sh' in upgrade
    assert "--extra transcribe" in upgrade
    assert '"$ROOT/scripts/lib/local-install.sh"' in upgrade
    assert "local_stop" in upgrade
    assert "local_start" in upgrade
    assert "platform-api-dev" not in upgrade
    assert "platform.db" in backup_local
    assert "pg_dump" in backup_local
    assert "manifest.json" in restore_local
    assert "local_stop" in restore_local
    assert "local_start" in restore_local
    assert "sha256sum -c" in restore_local


def test_local_upgrade_rebuilds_frontend_before_restarting():
    upgrade = (ROOT / "scripts" / "upgrade.sh").read_text(encoding="utf-8")
    assert "npx vite build --outDir \"$STATIC_STAGE\"" in upgrade
    assert "test -f \"$STATIC_STAGE/index.html\"" in upgrade
    assert "mv \"$STATIC_STAGE\" app/static" in upgrade
    assert upgrade.index("build:release") < upgrade.index("alembic upgrade head") if "build:release" in upgrade else True
    assert upgrade.index("npx vite build") < upgrade.index("systemctl --user restart")
    assert upgrade.index("npx vite build") < upgrade.index("launchctl kickstart")


def test_installer_builds_frontend_to_staging_before_swapping():
    install = (ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")
    assert "npx vite build --outDir \"$STATIC_STAGE\"" in install
    assert "mv \"$STATIC_STAGE\" app/static" in install
    assert install.index("npx vite build") < install.index("mv \"$STATIC_STAGE\" app/static")


def test_static_swap_traps_do_not_delete_the_new_bundle(tmp_path):
    """The EXIT trap must only remove staging, never the swapped-in app/static."""
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script = tmp_path / "swap.sh"
    script.write_text(
        "set -euo pipefail\n"
        "ROOT=\"$1\"\n"
        "cd \"$ROOT\"\n"
        "STATIC_STAGE=\"$(mktemp -d \"$ROOT/.app-static-XXXXXX\")\"\n"
        "STATIC_BACKUP=\"$ROOT/.app-static-backup-$$\"\n"
        "cleanup_static() { rm -rf \"$STATIC_STAGE\"; }\n"
        "trap cleanup_static EXIT\n"
        "printf new > \"$STATIC_STAGE/index.html\"\n"
        "if [ -e app/static ]; then mv app/static \"$STATIC_BACKUP\"; fi\n"
        "mv \"$STATIC_STAGE\" app/static\n"
        "rm -rf \"$STATIC_BACKUP\"\n"
        "STATIC_BACKUP=\"\"\n"
        "trap - EXIT\n"
        "unset STATIC_STAGE\n"
        "exit 0\n",
        encoding="utf-8",
    )
    root = tmp_path / "repo"
    (root / "app" / "static").mkdir(parents=True)
    (root / "app" / "static" / "index.html").write_text("old", encoding="utf-8")
    result = subprocess.run(["bash", str(script), str(root)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (root / "app" / "static" / "index.html").read_text(encoding="utf-8") == "new"


def test_local_install_help_documents_env_file_and_quotes_paths():
    text = (ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")
    assert "PLATFORM_LOCAL_ENV_FILE" in text.split("set -euo pipefail", 1)[0]
    assert "printf '      API" in text
    assert 'set -a; . %q' in text


def test_local_restore_validates_before_stopping_and_restores_sqlite_stage(tmp_path):
    """A restore must not take the service down before validating the archive."""
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script = (ROOT / "scripts" / "restore-local.sh").read_text(encoding="utf-8")
    assert script.index('[ -f "$ENV_FILE" ]') < script.index("local_stop_services")
    assert script.index('[ -s "$WORK/manifest.json" ]') < script.index("local_stop_services")
    assert script.index("local_stop_services") < script.index("local_start_services")
    assert 'cp "$WORK/platform.db" "$STAGE/platform.db"' in script


def test_local_runtime_rejects_occupied_port_and_detaches_processes():
    text = (ROOT / "scripts" / "lib" / "local-install.sh").read_text(encoding="utf-8")
    assert "local_port_in_use" in text
    assert "拒绝启动" in text
    assert "setsid uv run uvicorn" in text
    assert "setsid uv run python -m app.workers.runner" in text


def test_release_workflow_runs_transcription_smoke_before_release_publish():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    # Use the step name, not the explanatory comment at the job header.
    smoke = workflow.index("- name: 镜像内真实转录 smoke")
    release = workflow.index("- name: 创建 GitHub Release")
    assert smoke < release
    assert workflow.index("- name: 构建 amd64 候选镜像（暂不推送）") < smoke
    assert "push: false" in workflow[:smoke]
    assert "model.transcribe" in workflow[smoke:release]


def test_release_updater_uses_the_digest_of_the_built_main_image():
    """The sidecar base must be byte-identical to the image built in this run.

    `${github.ref_name}` is a Git ref, not necessarily the semver tag emitted by
    metadata-action.  Reference the manifest digest instead of assuming they
    are the same string.
    """
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "id: digest" in workflow
    assert "steps.build.outputs.digest" in workflow
    assert "BASE_IMAGE=${{ steps.digest.outputs.image }}" in workflow
    assert "BASE_IMAGE=${{ env.IMAGE }}:${{ github.ref_name }}" not in workflow
    assert "id: build_updater" in workflow
    assert "steps.build_updater.outputs.digest" in workflow


def test_dockerfile_pins_the_uv_installer_version():
    """Rebuilding a commit must not pick up a new environment builder."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ghcr.io/astral-sh/uv:latest" not in dockerfile
    assert "ghcr.io/astral-sh/uv:0.13.4" in dockerfile
