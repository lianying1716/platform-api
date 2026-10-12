"""启动双向版本门禁（契约 I3）。

在迁移之后、对外服务之前运行：确保"代码版本"与"数据库 revision"匹配，
否则拒绝启动，避免坏代码配坏库对外提供读写。

四种结论（`GuardResult.action`）：
- ok              已同步，放行
- migrated        版本落后且允许自动迁移，迁移后复核通过
- allowed_unknown 命中逃生开关，带未知 revision 放行（仅人工排障）
- reject          未知 revision / 迁移失败 / 迁移后仍不同步，拒绝启动

设计见 docs/plans/2026-10-11-versioning-and-upgrade-design.md 3.2/3.9。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.core.version import _VERSIONS_DIR, db_head, db_heads

logger = logging.getLogger("platform.version_guard")


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    action: str
    message: str


def _read_revision(db_url: str) -> str | None:
    """读取数据库当前 alembic revision；未迁移或表不存在返回 None。"""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 无表/连不上都按"未知/未迁移"处理
        return None
    finally:
        engine.dispose()


def _known_revisions() -> set[str]:
    """本镜像内置的迁移链全部 revision（静态解析脚本目录）。"""
    revisions: set[str] = set()
    rev_re = re.compile(r"^revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)['\"]", re.M)
    try:
        for path in sorted(_VERSIONS_DIR.glob("*.py")):
            match = rev_re.search(path.read_text(encoding="utf-8"))
            if match:
                revisions.add(match.group(1))
    except OSError:
        pass
    return revisions


def _run_migration(db_url: str) -> None:
    """执行 alembic upgrade head（复用项目 alembic.ini 配置）。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def check(
    *, db_url: str, auto_migrate: bool, allow_unknown: bool, enforce: bool = True
) -> GuardResult:
    """enforce=True（生产）严格；enforce=False（开发 create_all 模式）宽松只告警。"""
    heads = db_heads()
    head = heads[0] if len(heads) == 1 else ""
    revision = _read_revision(db_url)

    if not head:
        # head 不可解析有两种情形，都说明迁移链本身有问题，严格模式下必须 fail-closed：
        #   1) 没有任何迁移脚本（打包损坏）
        #   2) 出现多个 head（合并冲突未解决，alembic 会拒绝执行）
        # 安全闸门绝不能因为"自己看不懂状态"就放行。
        if len(heads) > 1:
            detail = f"检测到多个 head：{', '.join(heads)}；请先 `alembic merge` 合并分支"
        else:
            detail = "未找到内置迁移脚本（打包可能损坏）"
        if enforce:
            return GuardResult(False, "reject", f"迁移链异常，拒绝启动：{detail}")
        logger.warning("开发模式：迁移链异常（%s），仅告警放行", detail)
        return GuardResult(True, "dev_warn", f"开发模式放行：{detail}")

    if revision is None:
        # 全新库（无 alembic_version 表）。
        if not enforce:
            # 开发模式：create_all 会兜底建表，放行。
            return GuardResult(True, "dev_warn", "开发模式：库尚无版本记录，由 create_all 兜底")
        if not auto_migrate:
            return GuardResult(
                False,
                "reject",
                "数据库尚无版本记录且已禁用自动迁移（PLATFORM_AUTO_MIGRATE=false）；"
                "请先执行 alembic upgrade head。",
            )
        # 生产严格模式：空库必须由本次启动迁移建成（不能带着空库对外服务）。
        try:
            logger.info("数据库尚无版本记录，开始初始迁移到 head %s", head)
            _run_migration(db_url)
        except Exception as exc:  # noqa: BLE001
            return GuardResult(False, "reject", f"初始迁移失败，拒绝启动：{exc}")
        after = _read_revision(db_url)
        if after != head:
            return GuardResult(False, "reject", f"初始迁移后 revision 仍为 {after}（期望 {head}），拒绝启动")
        return GuardResult(True, "migrated", f"已初始迁移到 {head}")

    if revision == head:
        return GuardResult(True, "ok", f"版本同步：{revision}")

    known = _known_revisions()
    if not enforce:
        # 开发模式（PLATFORM_AUTO_CREATE_TABLES=true）：schema 由 create_all 兜底，
        # 版本不一致只告警，不阻断本地开发。生产不会走到这里。
        logger.warning("开发模式：DB revision=%s 与 head=%s 不一致，仅告警不阻断", revision, head)
        return GuardResult(True, "dev_warn", f"开发模式放行（DB={revision}，head={head}）")

    if revision not in known:
        if allow_unknown:
            logger.warning("数据库 revision %s 不在已知链中，逃生开关放行", revision)
            return GuardResult(True, "allowed_unknown", f"未知 revision {revision}（逃生开关放行）")
        return GuardResult(
            False,
            "reject",
            f"数据库 revision {revision} 不在本镜像迁移链（已知 head={head}）；"
            f"疑似用旧版本代码启动新库。请改用匹配版本，或设 PLATFORM_ALLOW_UNKNOWN_DB=1 仅作排障。",
        )

    # revision 已知且落后于 head —— 需要升级。
    if not auto_migrate:
        return GuardResult(
            False,
            "reject",
            f"数据库 revision {revision} 落后于 head {head}，且已禁用自动迁移"
            f"（PLATFORM_AUTO_MIGRATE=false）。请手动执行 alembic upgrade head。",
        )

    try:
        logger.info("数据库 revision %s 落后于 head %s，开始自动迁移", revision, head)
        _run_migration(db_url)
    except Exception as exc:  # noqa: BLE001 迁移失败必须拒绝启动
        return GuardResult(False, "reject", f"自动迁移失败，拒绝启动：{exc}")

    after = _read_revision(db_url)
    if after != head:
        return GuardResult(False, "reject", f"迁移后 revision 仍为 {after}（期望 {head}），拒绝启动")
    return GuardResult(True, "migrated", f"已自动迁移 {revision} -> {head}")
