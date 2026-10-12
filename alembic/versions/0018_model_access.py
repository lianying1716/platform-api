"""model access: providers / models / assignments / attempts + result routing

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-10 20:00:00.000000
"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WORK_TYPES = ("text_analysis", "cover_classify", "video_understanding")
_RESULT_TABLES = ("analysis_result", "video_understanding", "viral_breakdown")


def _dialect() -> str:
    return op.get_bind().dialect.name


def _upsert_setting(bind, key: str, value: str) -> None:
    result = bind.execute(
        sa.text("UPDATE system_setting SET value = :v WHERE key = :k"),
        {"k": key, "v": value},
    )
    if result.rowcount == 0:
        bind.execute(
            sa.text(
                "INSERT INTO system_setting (key, value, updated_at) "
                "VALUES (:k, :v, CURRENT_TIMESTAMP)"
            ),
            {"k": key, "v": value},
        )


def _read_settings(bind) -> dict[str, str]:
    rows = bind.execute(sa.text("SELECT key, value FROM system_setting")).all()
    return {str(k): ("" if v is None else str(v)) for k, v in rows}


def _create_tables() -> None:
    op.create_table(
        "ai_provider",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("preset_key", sa.String(length=64), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("credential_source", sa.String(length=16), nullable=False, server_default="api_key"),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("config_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_status", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("last_test_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "ai_model",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("model_key", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("import_source", sa.String(length=16), nullable=False, server_default="discovered"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("probe_status", sa.String(length=16), nullable=False, server_default="untested"),
        sa.Column("probe_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("probed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_provider.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_key", name="uq_ai_model_provider_key"),
    )
    op.create_index("ix_ai_model_provider_id", "ai_model", ["provider_id"])
    op.create_table(
        "model_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_type", sa.String(length=48), nullable=False),
        sa.Column("primary_model_id", sa.Integer(), nullable=True),
        sa.Column("fallback_model_id", sa.Integer(), nullable=True),
        sa.Column("auto_configured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("legacy_pending_validation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["primary_model_id"], ["ai_model.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["fallback_model_id"], ["ai_model.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_type", name="uq_model_assignment_work_type"),
    )
    op.create_table(
        "model_call_attempt",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attempt_group_id", sa.String(length=64), nullable=False),
        sa.Column("work_type", sa.String(length=48), nullable=False),
        sa.Column("operation", sa.String(length=48), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("provider_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("model_key", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("error_class", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("request_meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_provider.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_id"], ["ai_model.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_call_attempt_group", "model_call_attempt", ["attempt_group_id"])
    op.create_index("ix_model_call_attempt_work_created", "model_call_attempt", ["work_type", "created_at"])
    op.create_index("ix_model_call_attempt_asset_created", "model_call_attempt", ["asset_id", "created_at"])
    op.create_index("ix_model_call_attempt_model_created", "model_call_attempt", ["model_id", "created_at"])
    op.create_index(
        "ix_model_call_attempt_recovery",
        "model_call_attempt",
        ["status", "finished_at", "created_at"],
    )


def _drop_tables() -> None:
    for name in (
        "ix_model_call_attempt_recovery",
        "ix_model_call_attempt_model_created",
        "ix_model_call_attempt_asset_created",
        "ix_model_call_attempt_work_created",
        "ix_model_call_attempt_group",
    ):
        op.drop_index(name, table_name="model_call_attempt")
    op.drop_table("model_call_attempt")
    op.drop_table("model_assignment")
    op.drop_index("ix_ai_model_provider_id", table_name="ai_model")
    op.drop_table("ai_model")
    op.drop_table("ai_provider")


def _alter_result_columns() -> None:
    if _dialect() == "sqlite":
        for table in _RESULT_TABLES:
            with op.batch_alter_table(table) as batch:
                batch.alter_column("model", existing_type=sa.String(length=64), type_=sa.String(length=200))
                batch.add_column(sa.Column("routing", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    else:
        for table in _RESULT_TABLES:
            op.alter_column(table, "model", existing_type=sa.String(length=64), type_=sa.String(length=200))
            op.add_column(table, sa.Column("routing", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def _restore_result_columns() -> None:
    if _dialect() == "sqlite":
        for table in _RESULT_TABLES:
            with op.batch_alter_table(table) as batch:
                batch.drop_column("routing")
                batch.alter_column("model", existing_type=sa.String(length=200), type_=sa.String(length=64))
    else:
        for table in _RESULT_TABLES:
            op.drop_column(table, "routing")
            op.alter_column(table, "model", existing_type=sa.String(length=200), type_=sa.String(length=64))


def _key_ready() -> bool:
    from app.core.crypto import key_is_configured

    return key_is_configured()


def _find_or_create_provider(bind, *, name: str, base_url: str, protocol: str):
    row = bind.execute(
        sa.text(
            "SELECT id, config_revision FROM ai_provider "
            "WHERE name = :name AND preset_key = 'custom' AND base_url = :base_url"
        ),
        {"name": name, "base_url": base_url},
    ).first()
    if row:
        return row[0], "existing"
    sql = (
        "INSERT INTO ai_provider "
        "(name, preset_key, protocol, base_url, credential_source, config_revision, enabled, "
        " last_test_status, last_test_error, created_at, updated_at) "
        "VALUES (:name, 'custom', :protocol, :base_url, 'api_key', 1, :enabled, '', '', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    params = {"name": name, "base_url": base_url, "protocol": protocol, "enabled": True}
    if bind.dialect.name == "sqlite":
        return bind.execute(sa.text(sql), params).lastrowid, "created"
    return bind.execute(sa.text(sql + " RETURNING id"), params).scalar_one(), "created"


def _find_or_create_model(bind, provider_id: int, model_key: str):
    row = bind.execute(
        sa.text("SELECT id FROM ai_model WHERE provider_id = :pid AND model_key = :mk"),
        {"pid": provider_id, "mk": model_key},
    ).first()
    if row:
        return row[0]
    sql = (
        "INSERT INTO ai_model "
        "(provider_id, model_key, display_name, import_source, enabled, capabilities, probe_status, "
        " probe_error, created_at, updated_at) "
        "VALUES (:pid, :mk, :mk, 'manual', :enabled, :caps, 'untested', '', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    params = {"pid": provider_id, "mk": model_key, "enabled": True, "caps": "{}"}
    if bind.dialect.name == "sqlite":
        return bind.execute(sa.text(sql), params).lastrowid
    return bind.execute(sa.text(sql + " RETURNING id"), params).scalar_one()


def _upsert_assignment(bind, work_type: str, model_id: int) -> None:
    updated = bind.execute(
        sa.text(
            "UPDATE model_assignment SET primary_model_id = :mid, auto_configured = :auto, "
            "legacy_pending_validation = :legacy, updated_at = CURRENT_TIMESTAMP WHERE work_type = :wt"
        ),
        {"mid": model_id, "auto": True, "legacy": True, "wt": work_type},
    )
    if updated.rowcount == 0:
        bind.execute(
            sa.text(
                "INSERT INTO model_assignment "
                "(work_type, primary_model_id, fallback_model_id, auto_configured, legacy_pending_validation, updated_at) "
                "VALUES (:wt, :mid, NULL, :auto, :legacy, CURRENT_TIMESTAMP)"
            ),
            {"wt": work_type, "mid": model_id, "auto": True, "legacy": True},
        )


def _infer_protocol(base_url: str) -> str:
    low = (base_url or "").lower()
    # OpenAI 兼容路径最常见；只有明确是 Gemini 站点根时才用原生协议。
    if "generativelanguage.googleapis.com" in low or "/v1beta" in low:
        return "gemini"
    return "openai"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "model_access_legacy_imported" in _read_settings(bind):
        # 手工重放守卫：只做结构存在性检查，不做生产级半完成修复。
        assert {"ai_provider", "ai_model", "model_assignment", "model_call_attempt"} <= tables
        for table in _RESULT_TABLES:
            cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
            assert "routing" in cols
        return

    # 加密子系统必须先就绪，且不得触发自动生成 secret.key。
    if not _key_ready():
        raise RuntimeError("模型接入迁移需要 PLATFORM_CREDENTIAL_ENC_KEY 或已存在的 credential key file；拒绝自动生成新密钥")

    _create_tables()
    _alter_result_columns()

    from app.core.process_settings import resolve_process_settings_from_rows

    rows = _read_settings(bind)
    effective = resolve_process_settings_from_rows(rows)
    base_url = rows.get("llm_base_url", "").strip()
    model_key = rows.get("llm_model", "").strip()
    plain_key = rows.get("llm_api_key", "").strip()

    # 迁移输入允许 DB Key；环境变量 Key 在导入阶段也更优先于空值。
    if not plain_key:
        try:
            from app.config import settings as app_settings

            plain_key = (app_settings.llm_api_key or "").strip()
        except Exception:
            plain_key = ""

    if base_url and model_key and plain_key:
        from app.core.crypto import encrypt_credential, decrypt_credential

        protocol = _infer_protocol(base_url)
        provider_id, _ = _find_or_create_provider(
            bind, name="系统迁移（旧配置）", base_url=base_url, protocol=protocol
        )
        ciphertext = encrypt_credential({"api_key": plain_key})
        assert decrypt_credential(ciphertext)["api_key"] == plain_key
        bind.execute(
            sa.text("UPDATE ai_provider SET api_key_encrypted = :c WHERE id = :pid"),
            {"c": ciphertext, "pid": provider_id},
        )

        text_model_id = _find_or_create_model(bind, provider_id, model_key)
        _upsert_assignment(bind, "text_analysis", text_model_id)

        classify_model = str(effective.get("classify_model") or "").strip()
        if classify_model:
            classify_model_id = _find_or_create_model(bind, provider_id, classify_model)
            _upsert_assignment(bind, "cover_classify", classify_model_id)

        vision_model = str(effective.get("vision_model") or "").strip()
        if vision_model:
            vision_model_id = _find_or_create_model(bind, provider_id, vision_model)
            transport = "gemini_inline" if str(effective.get("provider") or "qwen") == "gemini" else "openai_video_url"
            caps = json.dumps(
                {"video": {"value": False, "source": "legacy", "transport": transport}},
                ensure_ascii=False,
            )
            bind.execute(
                sa.text("UPDATE ai_model SET capabilities = :caps WHERE id = :mid"),
                {"caps": caps, "mid": vision_model_id},
            )
            _upsert_assignment(bind, "video_understanding", vision_model_id)

        _upsert_setting(bind, "model_access_legacy_imported", "1")
        bind.execute(sa.text("DELETE FROM system_setting WHERE key = 'llm_api_key'"))


def downgrade() -> None:
    _restore_result_columns()
    _drop_tables()
