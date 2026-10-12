"""版本升级辅助：检查远端最新版、校验版本号、触发 updater 边车。

设计红线（见 docs/plans/2026-10-11-versioning-and-upgrade-design.md）：
- 检查更新只读，比对 GitHub Releases。
- 版本号必须过白名单正则（拒绝 shell 注入 / 路径穿越），才允许传给 updater。
- 触发升级仅在显式配置 PLATFORM_UPDATER_URL 时可用（默认关闭）；
  updater 是独立边车，持有 docker socket，不暴露到宿主端口。
"""

from __future__ import annotations

import re

import httpx

# 仅允许语义化版本：可选 v 前缀 + 数字段 + 可选预发布/构建元数据。
# 显式拒绝空格、分号、&、|、$、/ 等一切可能被 shell 解释或路径穿越的字符。
_VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?(?:\+[0-9A-Za-z.]+)?$")

DEFAULT_REPO = "lianying1716/platform-api"


def load_current_version() -> str:
    from app.core.version import load_build_info

    return load_build_info().version


def is_valid_version(value: str | None) -> bool:
    return bool(value) and bool(_VERSION_RE.match(value))


def _parse(version: str) -> tuple[int, ...]:
    """取核心数字段做比较；预发布信息这里不参与（够用即可）。"""
    core = version.lstrip("v").split("-")[0].split("+")[0]
    return tuple(int(p) for p in core.split("."))


def is_newer(candidate: str, current: str) -> bool:
    try:
        return _parse(candidate) > _parse(current)
    except (ValueError, AttributeError):
        return False


async def fetch_latest(*, repo: str, timeout: float = 10.0) -> dict:
    """读 GitHub 最新 release（不含 pre-release）。失败抛 httpx 异常，由路由翻译。"""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    return {
        "latest": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "url": data.get("html_url", ""),
    }


async def check_latest(*, current: str, repo: str = DEFAULT_REPO) -> dict:
    try:
        info = await fetch_latest(repo=repo)
    except httpx.HTTPError as exc:
        return {"ok": False, "current": current, "message": f"检查更新失败：{exc}"}
    latest = info["latest"]
    return {
        "ok": True,
        "current": current,
        "latest": latest,
        "available": bool(latest) and is_newer(latest, current),
        "url": info["url"],
        "name": info["name"],
    }
