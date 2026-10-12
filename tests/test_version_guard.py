"""启动双向版本门禁：同步放行 / 落后自动迁移 / 未知 revision 拒启 / 逃生开关。

对应设计 3.2。核心契约 I3：不让坏代码配坏库对外服务。
"""

import pytest

from app.core import version_guard


class _FakeConn:
    """最小 DB 桩：返回预设的 alembic_version，并执行升级动作。"""

    def __init__(self, revision):
        self.revision = revision
        self.upgraded = False
        self.closed = False

    def close(self):
        self.closed = True

    def execute(self, *a, **k):
        class _R:
            def __init__(self, rev):
                self._rev = rev

            def scalar(self):
                return self._rev

        return _R(self.revision)


def test_guard_ok_when_in_sync(monkeypatch):
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0019")
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: pytest.fail("不应触发迁移"))
    result = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False)
    assert result.action == "ok"
    assert result.ok is True


def test_guard_auto_migrates_when_behind(monkeypatch):
    """新代码配旧库：自动迁移后复核通过。"""
    state = {"rev": "0018", "migrated": False}

    def _migrate(url):
        state["migrated"] = True
        state["rev"] = "0019"

    monkeypatch.setattr(version_guard, "_read_revision", lambda url: state["rev"])
    monkeypatch.setattr(version_guard, "_known_revisions", lambda: {"0001", "0018", "0019"})
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", _migrate)
    result = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False)
    assert result.action == "migrated"
    assert result.ok is True
    assert state["migrated"] is True


def test_guard_rejects_unknown_revision(monkeypatch):
    """旧代码配新库：revision 不在本镜像迁移链里，拒绝启动。"""
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0099")
    monkeypatch.setattr(version_guard, "_known_revisions", lambda: {"0001", "0019"})
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: pytest.fail("不应迁移"))
    result = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False)
    assert result.ok is False
    assert result.action == "reject"
    assert "0099" in result.message


def test_guard_allow_unknown_escape_hatch(monkeypatch):
    """逃生开关：人工排障时允许带未知 revision 启动。"""
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0099")
    monkeypatch.setattr(version_guard, "_known_revisions", lambda: {"0001", "0019"})
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: pytest.fail("不应迁移"))
    result = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=True)
    assert result.ok is True
    assert result.action == "allowed_unknown"


def test_guard_fails_when_migration_does_not_sync(monkeypatch):
    """迁移后仍不同步：拒绝启动，避免半迁移对外服务。"""
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0018")
    monkeypatch.setattr(version_guard, "_known_revisions", lambda: {"0001", "0018", "0019"})
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: None)  # 没改状态
    result = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False)
    assert result.ok is False
    assert result.action == "reject"


def test_guard_fails_on_migration_error(monkeypatch):
    """迁移抛异常：拒绝启动且带出错误信息。"""
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0018")
    monkeypatch.setattr(version_guard, "_known_revisions", lambda: {"0001", "0018", "0019"})
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))

    def _boom(url):
        raise RuntimeError("boom")

    monkeypatch.setattr(version_guard, "_run_migration", _boom)
    result = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False)
    assert result.ok is False
    assert result.action == "reject"


def test_guard_auto_migrate_disabled(monkeypatch):
    """只读/演示实例关掉自动迁移：版本落后直接拒绝，不擅自改库。"""
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0018")
    monkeypatch.setattr(version_guard, "_known_revisions", lambda: {"0001", "0018", "0019"})
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: pytest.fail("不应迁移"))
    result = version_guard.check(db_url="sqlite://", auto_migrate=False, allow_unknown=False)
    assert result.ok is False
    assert result.action == "reject"


def test_guard_dev_mode_only_warns(monkeypatch):
    """开发模式（enforce=False）：版本不一致只告警放行，不阻断本地开发。"""
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0017")
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: pytest.fail("开发模式不应迁移"))
    result = version_guard.check(
        db_url="sqlite://", auto_migrate=True, allow_unknown=False, enforce=False
    )
    assert result.ok is True
    assert result.action == "dev_warn"


def test_guard_empty_db_strict_rejects_when_migrate_disabled(monkeypatch):
    """严格模式 + 空库 + 禁用自动迁移：拒绝启动（不能带空库对外服务）。"""
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: None)
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: pytest.fail("不应迁移"))
    result = version_guard.check(db_url="sqlite://", auto_migrate=False, allow_unknown=False, enforce=True)
    assert result.ok is False
    assert result.action == "reject"


def test_guard_empty_db_strict_auto_migrates(monkeypatch):
    """严格模式 + 空库 + 允许自动迁移：执行初始迁移并复核通过。"""
    state = {"rev": None}

    def _migrate(url):
        state["rev"] = "0019"

    monkeypatch.setattr(version_guard, "_read_revision", lambda url: state["rev"])
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019",))
    monkeypatch.setattr(version_guard, "_run_migration", _migrate)
    result = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False, enforce=True)
    assert result.ok is True
    assert result.action == "migrated"


def test_guard_multiple_heads_rejects(monkeypatch):
    """迁移链出现多个 head（合并冲突未解决）：严格模式必须 fail-closed 拒启。"""
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019", "0900"))
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0019")
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: pytest.fail("不应迁移"))
    r = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False, enforce=True)
    assert r.ok is False
    assert r.action == "reject"
    assert "多个 head" in r.message


def test_guard_no_scripts_rejects(monkeypatch):
    """找不到任何迁移脚本（打包损坏）：严格模式 fail-closed 拒启。"""
    monkeypatch.setattr(version_guard, "db_heads", lambda: ())
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: None)
    monkeypatch.setattr(version_guard, "_run_migration", lambda url: pytest.fail("不应迁移"))
    r = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False, enforce=True)
    assert r.ok is False
    assert r.action == "reject"


def test_guard_multiple_heads_dev_mode_warns(monkeypatch):
    """开发模式遇多 head：只告警放行（不阻断本地开发）。"""
    monkeypatch.setattr(version_guard, "db_heads", lambda: ("0019", "0900"))
    monkeypatch.setattr(version_guard, "_read_revision", lambda url: "0019")
    r = version_guard.check(db_url="sqlite://", auto_migrate=True, allow_unknown=False, enforce=False)
    assert r.ok is True
    assert r.action == "dev_warn"
