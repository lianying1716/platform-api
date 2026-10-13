"""存储目录可视化选择：只读的文件系统列目录。

用途：让「系统设置 → 存储目录」能像本机软件一样点选目录，而不是手打绝对路径。

红线（务必守住）：
- **只读**：只列目录项与可写位，绝不回文件内容、绝不创建/移动/删除。
- **白名单**：只能浏览 PLATFORM_BROWSE_ROOTS（或安全默认根）之内的路径；用
  realpath 解析后做包含判断，杜绝 `..` 与软链越权。
- **路径归属于 backend/worker 进程**：真正读写素材的是 worker，所以这里列的必须是
  本进程可见的文件系统。Docker 下宿主候选目录需先 bind mount 进容器，再列进白名单。

调用方：app/api/system.py 的 /api/system/fs/roots 与 /api/system/fs/list（仅管理员）。
"""

import os
from pathlib import PureWindowsPath


class BrowseError(ValueError):
    """列目录失败（路径不存在 / 不在白名单内 / 无权限）。"""


def _is_windows_env() -> bool:
    return os.name == "nt"


def _looks_windows_path(path: str) -> bool:
    return bool(PureWindowsPath(path).drive) or path.startswith("\\\\")


def _exists(path: str) -> bool:
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def _writable(path: str) -> bool:
    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False


def _norm(path: str) -> str:
    """规范化为绝对、无尾斜杠的形态（保留盘符/UNC 前缀）。"""
    p = (path or "").strip()
    if not p:
        return ""
    if _is_windows_env():
        return os.path.normpath(p)
    # POSIX：expanduser 便于 "~" 手输
    return os.path.normpath(os.path.expanduser(p))


def _is_within(child: str, parent: str) -> bool:
    """child 是否位于 parent 之内（按 realpath 判断，防软链/`..` 越权）。"""
    if not parent:
        return False
    try:
        rc = os.path.realpath(child)
        rp = os.path.realpath(parent)
    except OSError:
        return False
    if rc == rp:
        return True
    return rc.startswith(rp.rstrip("/\\") + os.sep)


def allowed_roots() -> list[dict]:
    """允许浏览的根清单。顺序即前端展示顺序。

    优先级：
    1) PLATFORM_BROWSE_ROOTS（`:` 分隔，Windows 下为 `;`）—— 显式白名单，Docker 推荐用。
    2) 未配置时：容器内（探测到 /.dockerenv）**只放行 /data**（安全默认）；
       裸机回退到平台默认根（POSIX：/、/Volumes、/mnt、/media、$HOME；Windows：盘符 + UNC）。
    """
    raw = os.environ.get("PLATFORM_BROWSE_ROOTS", "").strip()
    if raw:
        sep = ";" if _is_windows_env() else ":"
        candidates = [p for p in (s.strip() for s in raw.split(sep)) if p]
    elif os.path.exists("/.dockerenv"):
        # 容器内安全默认：只放行数据卷，绝不放整个容器文件系统。
        candidates = ["/data"]
    elif _is_windows_env():
        candidates = [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if _exists(f"{c}:\\")]
        candidates += [r"\\"]  # UNC 起点（Network）
    else:
        home = os.path.expanduser("~")
        candidates = [p for p in ("/", home, "/Volumes", "/mnt", "/media") if _exists(p)]

    out: list[dict] = []
    seen: set[str] = set()
    for c in candidates:
        norm = _norm(c)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append({"path": norm, "exists": _exists(norm), "writable": _writable(norm)})
    return out


def _root_containing(path: str) -> str | None:
    for r in allowed_roots():
        if _is_within(path, r["path"]):
            return r["path"]
    return None


def list_dir(path: str | None = None) -> dict:
    """列出某个目录下的**子目录**（存储根选的是一级目录，文件不列）。

    返回 {path, parent, sep, roots, entries}；entries 每项
    {name, path, is_dir, writable, is_link}。越界/不存在抛 BrowseError。
    """
    roots = allowed_roots()
    norm = _norm(path or "")
    if not norm:
        # 未给路径：回第一个存在的根（否则第一个根）
        first = next((r for r in roots if r["exists"]), roots[0] if roots else None)
        if first is None:
            raise BrowseError("没有可浏览的根目录（请配置 PLATFORM_BROWSE_ROOTS）")
        norm = first["path"]

    if _root_containing(norm) is None:
        raise BrowseError(f"路径不在允许浏览的范围内：{norm}")

    if not os.path.isdir(norm):
        # 区分「不存在」与「无权限」：macOS TCC 会抛 PermissionError
        raise BrowseError(f"目录不存在或不可访问：{norm}")

    entries: list[dict] = []
    try:
        with os.scandir(norm) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=True)
                except OSError:
                    continue
                if not is_dir:
                    continue
                sub = os.path.join(norm, entry.name)
                try:
                    is_link = entry.is_symlink()
                except OSError:
                    is_link = False
                entries.append(
                    {
                        "name": entry.name,
                        "path": sub,
                        "is_dir": True,
                        "writable": _writable(sub),
                        "is_link": is_link,
                    }
                )
    except PermissionError as exc:  # macOS TCC / POSIX 权限
        raise BrowseError(f"无权限访问该目录（系统可能未授权）：{norm}") from exc
    except OSError as exc:
        raise BrowseError(f"读取目录失败：{exc}") from exc

    entries.sort(key=lambda e: e["name"].lower())

    # 父目录：仅当仍在白名单内才提供，越界则不给「上一级」。
    parent_raw = os.path.dirname(norm.rstrip("/\\"))
    if _is_windows_env() and norm.rstrip("/\\").endswith(":"):
        parent_raw = ""  # 盘根没有上一级
    parent = parent_raw if parent_raw and _root_containing(parent_raw) is not None else None

    return {
        "path": norm,
        "parent": parent,
        "sep": os.sep,
        "roots": roots,
        "entries": entries,
    }
