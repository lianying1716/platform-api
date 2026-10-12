"""版本持久化：把网页升级的目标版本写回 .env，确保重启不回退。

为什么必须有这个模块：
升级时 updater 用临时环境变量 `PLATFORM_VERSION=vX docker compose pull/up` 重建服务，
但**不从盘上记住**这个版本。于是下一次 `docker compose up -d`（或宿主机重启、watchdog
拉起）会按 .env 里的默认值解析镜像 tag，把服务静默切回旧版本——网页升级名存实亡。

修法：升级成功后把 `PLATFORM_VERSION=<版本>` 原子写回 compose 同目录的 .env，
让"当前版本"成为持久事实。写入必须：
- 保留 .env 中其它配置与注释（那是用户的生产配置）
- 替换而非追加已存在的 PLATFORM_VERSION 行
- 原子替换（临时文件 + rename），避免中途崩溃留下半截文件
- 拒绝非法版本串（复用白名单校验）
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_KEY = "PLATFORM_VERSION"


def read_pinned_version(env_path: str | Path) -> str | None:
    """读取 .env 中固定的版本；文件不存在或无该键返回 None。"""
    path = Path(env_path)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{_KEY}="):
            value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def write_pinned_version(env_path: str | Path, version: str) -> None:
    """把 `PLATFORM_VERSION=version` 原子写回 .env，保留其余内容。"""
    from app.core.version_upgrade import is_valid_version

    if not is_valid_version(version):
        raise ValueError(f"非法版本号，拒绝写入 .env：{version!r}")

    path = Path(env_path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines()

    newline = f"{_KEY}={version}"
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{_KEY}="):
            if not replaced:
                out.append(newline)
                replaced = True
            # 重复的旧行丢弃，只保留一行
            continue
        out.append(line)
    if not replaced:
        out.append(newline)

    content = "\n".join(out)
    if not content.endswith("\n"):
        content += "\n"

    # 原子替换：同目录临时文件写入后 rename，避免半截 .env。
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
