"""迁移 0012 的真实 PostgreSQL 验收：历史数据分类回填 + 账号级唯一约束。

用临时数据库，不碰开发/生产库；无 PostgreSQL 时显式 skip，不伪绿。
"""

import uuid
from pathlib import Path

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


PG = dict(host="127.0.0.1", port=5433, user="platform", password="platform", dbname="postgres")


def _alembic_config(db_url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture()
def temp_pg_db():
    try:
        admin = psycopg2.connect(**PG)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"没有可用的 PostgreSQL，迁移未验证: {exc}")
    admin.autocommit = True
    name = "platform_mig_" + uuid.uuid4().hex[:12]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_0012_backfills_subscription_and_system_owner(temp_pg_db):
    """升级 0011→0012：订阅内容归真实账号，crawl/import 归系统账号，且可降级。"""
    cfg = _alembic_config(temp_pg_db)
    command.upgrade(cfg, "0011")

    engine = create_engine(temp_pg_db)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform_account (id, platform, display_name, external_id, "
                "credential_kind, status, status_message, created_at, updated_at) "
                "VALUES (1, 'bilibili', 'A', 'A', 'cookie', 'active', '', now(), now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO subscription (id, account_id, platform, source_type, remote_id, "
                "title, enabled, auto_download, interval_minutes, subdir_template, min_play, "
                "publish_after_days, max_items, only_new, download_quality, with_comments, created_at) "
                "VALUES (1, 1, 'bilibili', 'favorite', 'fav', '收藏夹', true, false, 360, "
                "'{platform}/{creator}/{date}', 0, 0, 50, true, 'best', false, now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO content_item (id, platform, external_id, content_type, title, "
                "author_name, author_id, url, cover_url, duration_sec, auto_category, "
                "category_confidence, source_kind, source_id, extra, created_at) VALUES "
                "(1, 'bilibili', 'BV1', 'video', '订阅内容', '', '', '', '', 0, '', 0, "
                "'subscription', 1, '{}', now()),"
                "(2, 'bilibili', 'BV2', 'video', '采集内容', '', '', '', '', 0, '', 0, "
                "'crawl', 1, '{}', now())"
            )
        )

    command.upgrade(cfg, "0012")
    with engine.connect() as conn:
        rows = dict(
            conn.execute(text("SELECT external_id, account_id FROM content_item ORDER BY id")).all()
        )
        assert rows["BV1"] == 1  # 订阅行可靠映射到真实账号
        assert rows["BV2"] != 1  # crawl_task.id=1 不能误配到 subscription.id=1
        system_id = conn.execute(
            text("SELECT id FROM platform_account WHERE platform='bilibili' AND is_system = true")
        ).scalar()
        assert rows["BV2"] == system_id

        # 账号级唯一约束存在：同账号同平台同 external_id 第二次插入必须失败
        with pytest.raises(Exception):
            conn.execute(
                text(
                    "INSERT INTO content_item (platform, external_id, account_id, content_type, title, "
                    "author_name, author_id, url, cover_url, duration_sec, auto_category, "
                    "category_confidence, source_kind, source_id, extra, created_at) VALUES "
                    "('bilibili', 'BV2', :aid, 'video', 'dup', '', '', '', '', 0, '', 0, "
                    "'crawl', 1, '{}', now())"
                ),
                {"aid": system_id},
            )
            conn.commit()

    command.downgrade(cfg, "0011")
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='content_item'"
                )
            )
        }
        assert "account_id" not in cols
