"""版本升级辅助：检查远端最新版、校验版本号、触发 updater 边车。

设计红线（见 docs/plans/2026-10-11-versioning-and-upgrade-design.md）：
- 检查更新只读，比对 GitHub Releases。
- 版本号必须过白名单正则（拒绝 shell 注入 / 路径穿越），才允许传给 updater。
- 触发升级仅在显式配置 PLATFORM_UPDATER_URL 时可用（默认关闭）；
  updater 是独立边车，持有 docker socket，不暴露到宿主端口。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

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
    from app.config import settings

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}
    if settings.update_github_token:
        headers["Authorization"] = f"Bearer {settings.update_github_token}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return {
        "latest": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "url": data.get("html_url", ""),
    }


async def fetch_latest_redirect(*, repo: str, timeout: float = 10.0) -> dict:
    """Fallback to GitHub's public release redirect when the API quota is spent.

    ``/releases/latest`` is a public web redirect, not the rate-limited REST API.
    It only works for public repositories, which is the supported install mode;
    private repos still need API auth via ``PLATFORM_UPDATE_GITHUB_TOKEN``.
    """
    url = f"https://github.com/{repo}/releases/latest"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        resp = await client.get(url)
    location = resp.headers.get("location", "")
    match = re.search(r"/releases/tag/([^/?#]+)", location)
    if resp.status_code not in (301, 302, 303, 307, 308) or not match:
        raise httpx.HTTPStatusError(
            f"GitHub release redirect failed: HTTP {resp.status_code}",
            request=resp.request,
            response=resp,
        )
    tag = match.group(1)
    return {
        "latest": tag,
        "name": tag,
        "url": location if location.startswith("https://") else f"https://github.com/{repo}/releases/tag/{tag}",
    }


async def check_latest(*, current: str, repo: str = DEFAULT_REPO) -> dict:
    try:
        info = await fetch_latest(repo=repo)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        retry_at = _rate_limit_reset(exc.response)
        if status in (403, 429):
            try:
                info = await fetch_latest_redirect(repo=repo)
            except httpx.HTTPError:
                retry = f"，约 {retry_at} 后自动恢复" if retry_at else ""
                message = f"GitHub 接口限流{retry}；可在部署环境配置 PLATFORM_UPDATE_GITHUB_TOKEN"
                return {
                    "ok": False,
                    "current": current,
                    "message": message,
                    "retry_at": retry_at,
                }
            latest = info["latest"]
            return {
                "ok": True,
                "current": current,
                "latest": latest,
                "available": bool(latest) and is_newer(latest, current),
                "url": info["url"],
                "name": info["name"],
                "source": "public_redirect",
            }
        elif status == 404:
            message = "未找到正式 Release，请确认仓库已创建 GitHub Release"
        else:
            message = f"检查更新失败：HTTP {status}"
        return {
            "ok": False,
            "current": current,
            "message": message,
            "retry_at": retry_at,
        }
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


def _rate_limit_reset(response: httpx.Response) -> str:
    """Convert GitHub's reset epoch to a short local-time hint when available."""
    raw = response.headers.get("x-ratelimit-reset", "")
    try:
        reset = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return reset.astimezone().strftime("%H:%M")
