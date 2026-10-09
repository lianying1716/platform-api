"""存储路径管理。

红线：文件一律 storage_root + relative_path 保存，兼容 Linux/macOS 挂载、
Windows 盘符与 UNC。外部 API 不得提交绝对输出路径。
"""

import re
from datetime import datetime
from pathlib import PurePath, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MediaAsset, StorageRoot

_SAFE = re.compile(r"[^\w\-.一-鿿]+")


def get_default_root(db: Session) -> StorageRoot | None:
    root = db.scalar(select(StorageRoot).where(StorageRoot.is_default.is_(True)))
    if root is None:
        root = db.scalar(select(StorageRoot).order_by(StorageRoot.id))
    return root


def resolve_account_root(db: Session, account) -> StorageRoot | None:
    """账号绑定的存储根；账号未绑定或绑定失效时回退默认根。

    两个账户可指向同一个根（共用），也可各指一个（独立），两种都合法。
    """
    root_id = getattr(account, "storage_root_id", None) if account is not None else None
    if root_id is not None:
        root = db.get(StorageRoot, root_id)
        if root is not None:
            return root
    return get_default_root(db)


def sanitize_segment(value: str, fallback: str = "unknown") -> str:
    """把作者名/标题等清洗成安全的单层目录名，禁止路径穿越。"""
    value = (value or "").strip()
    value = value.replace("/", "_").replace("\\", "_")
    value = _SAFE.sub("_", value).strip("._")
    value = value[:80]
    return value or fallback


def render_subdir(
    template: str,
    *,
    platform: str,
    creator: str,
    date: datetime,
    content_id: str,
    account: str = "",
) -> str:
    """渲染子目录模板，每段都清洗，返回相对路径（POSIX 风格）。

    `account` 是账号段（账号名/ID），供 `{account}` token 使用；为空时该段被清掉
    （避免出现空目录层级）。
    """
    mapping = {
        "platform": sanitize_segment(platform, "platform"),
        # account 为空则映射成空串，让 {account} 段整体消失（不留空目录层）。
        "account": sanitize_segment(account, "account") if (account or "").strip() else "",
        "creator": sanitize_segment(creator, "creator"),
        "date": date.strftime("%Y-%m-%d"),
        "content_id": sanitize_segment(content_id, "id"),
    }
    rendered = template
    for key, val in mapping.items():
        rendered = rendered.replace("{" + key + "}", val)
    # 模板里未覆盖的占位符或多余分隔符清掉；{account} 为空时该段整体消失。
    parts = [sanitize_segment(p) for p in re.split(r"[/\\]+", rendered) if p and "{" not in p]
    return "/".join(parts)


def reject_absolute(path: str) -> None:
    """外部 API 提交的相对路径校验：拒绝绝对路径、盘符、UNC、穿越。"""
    if not path:
        return
    if path.startswith(("/", "\\")):
        raise ValueError("不允许提交绝对路径")
    if PureWindowsPath(path).drive or path.startswith("\\\\"):
        raise ValueError("不允许提交 Windows 盘符或 UNC 路径")
    if ".." in PurePath(path).parts:
        raise ValueError("路径不允许包含 ..")


def join_root(root_path: str, relative_path: str, *files: str) -> str:
    """拼出最终物理路径（按 root 风格）。仅后端内部使用。"""
    rel = relative_path.replace("\\", "/").strip("/")
    is_windows = bool(PureWindowsPath(root_path).drive) or root_path.startswith("\\\\")
    if is_windows:
        base = PureWindowsPath(root_path)
        for seg in rel.split("/"):
            if seg:
                base = base / seg
        for f in files:
            base = base / f
        return str(base)
    base = PurePath(root_path)
    for seg in rel.split("/"):
        if seg:
            base = base / seg
    for f in files:
        base = base / f
    return str(base)


def resolve_asset_dir(db: Session, asset: "MediaAsset") -> str:
    """拼出素材的物理目录（storage_root + relative_path）。仅后端内部使用。

    asset 未绑定 storage_root 时回退到默认根；都没有则只返回 relative_path。
    """
    root = None
    if asset.storage_root_id is not None:
        root = db.get(StorageRoot, asset.storage_root_id)
    if root is None:
        root = get_default_root(db)
    if root is None:
        return asset.relative_path or ""
    return join_root(root.path, asset.relative_path or "")


def probe_root(path: str) -> tuple[bool, str]:
    """检测存储根可用性：本机/挂载路径检查存在与可写；Windows 盘符/UNC 在非 Windows
    主机上无法校验，返回未知但不报错。返回 (available, detail)。"""
    import os

    if not path:
        return False, "未配置路径"
    is_windows_path = bool(PureWindowsPath(path).drive) or path.startswith("\\\\")
    if is_windows_path and os.name != "nt":
        return False, "Windows 盘符/UNC 路径，当前主机非 Windows，无法在线校验"
    try:
        exists = os.path.isdir(path)
        if not exists:
            return False, "目录不存在或未挂载"
        writable = os.access(path, os.W_OK)
        return writable, "可读写" if writable else "存在但不可写"
    except OSError as exc:
        return False, f"检测失败: {exc}"


def file_size(path: str) -> int:
    """单文件字节数；不存在/不可读返回 0。"""
    import os

    try:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        return 0


def dir_size(path: str, *, max_files: int = 20000) -> int:
    """目录总字节数（递归）。路径不存在返回 0，遍历文件数设上限避免卡死。"""
    import os

    if not path or not os.path.isdir(path):
        return 0
    total = 0
    seen = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
            seen += 1
            if seen >= max_files:
                return total
    return total


def resolve_asset_file(db: Session, asset: "MediaAsset") -> str:
    """素材本地视频文件的物理路径；无视频文件返回空串。

    仅后端内部使用（用于 Range 流式播放），不对外暴露绝对路径。
    """
    import os

    if not asset.video_file:
        return ""
    directory = resolve_asset_dir(db, asset)
    if not directory:
        return ""
    return os.path.join(directory, asset.video_file)
