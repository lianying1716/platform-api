"""版本单一来源。

发布事实是 git tag `vX.Y.Z`，构建期由 CI 注入三个环境变量：
    PLATFORM_BUILD_VERSION / PLATFORM_BUILD_COMMIT / PLATFORM_BUILD_TIME
本地开发未注入时回退到 pyproject 里的版本 + 当前短 commit，并标注 `-dev`。

数据库版本（Alembic revision）也在这里统一读取，避免各处以不同方式推断。
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent  # platform-api/


@lru_cache(maxsize=1)
def _pyproject_version() -> str:
    try:
        with (_ROOT / "pyproject.toml").open("rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except Exception:  # noqa: BLE001 读不到就用兜底，版本展示不应导致启动失败
        return "0.0.0"


@lru_cache(maxsize=1)
def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 git 不可用不是错误
        pass
    return "unknown"


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str
    build_time: str


def load_build_info() -> BuildInfo:
    """返回构建版本信息。已按 `lru_cache` 缓存，但测试需要可重载，故不缓存本函数。"""
    injected_version = os.environ.get("PLATFORM_BUILD_VERSION", "").strip()
    commit = os.environ.get("PLATFORM_BUILD_COMMIT", "").strip() or _git_commit()
    build_time = os.environ.get("PLATFORM_BUILD_TIME", "").strip() or ""
    if injected_version:
        return BuildInfo(version=injected_version, commit=commit, build_time=build_time)
    # 本地开发回退：明确标注 dev，避免被误当成正式发布版本
    return BuildInfo(version=f"{_pyproject_version()}-dev+{commit}", commit=commit, build_time=build_time)


def build_provenance() -> dict[str, str]:
    """Return how this process learned its version, without exposing secrets.

    A release image and a source checkout can both report ``v1.0.9`` while
    running different commits.  The diagnostic surface needs to expose that
    distinction so operators can audit which artifact is actually live.
    """
    info = load_build_info()
    return {
        "version": info.version,
        "commit": info.commit,
        "source": "release_image" if os.environ.get("PLATFORM_BUILD_VERSION", "").strip() else "source_checkout",
    }


_VERSIONS_DIR = _ROOT / "alembic" / "versions"


@lru_cache(maxsize=1)
def db_heads() -> tuple[str, ...]:
    """内置迁移脚本的全部 head revision（纯静态解析，不连数据库）。

    通过找出"从未作为任何 down_revision 出现"的 revision 得到链头。
    正常应恰有一个 head；返回多个 head 表示迁移链自相矛盾（合并冲突未解决），
    调用方（版本门禁）必须视为错误，不能 fail-open 放行。
    """
    import re

    revisions: set[str] = set()
    referenced: set[str] = set()
    rev_re = re.compile(r"^revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)['\"]", re.M)
    down_re = re.compile(r"^down_revision\s*(?::[^=]+)?=\s*(.+)$", re.M)
    try:
        for path in sorted(_VERSIONS_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            rev_match = rev_re.search(text)
            if rev_match:
                revisions.add(rev_match.group(1))
            for down_match in down_re.finditer(text):
                for token in re.findall(r"['\"]([^'\"]+)['\"]", down_match.group(1)):
                    referenced.add(token)
    except OSError:
        return ()
    return tuple(sorted(revisions - referenced))


def db_head() -> str:
    """单一 head；多 head 或解析失败返回空串（调用方须按错误处理，不得放行）。"""
    heads = db_heads()
    return heads[0] if len(heads) == 1 else ""
