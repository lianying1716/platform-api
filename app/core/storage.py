"""存储路径管理。

红线：文件一律 storage_root + relative_path 保存，兼容 Linux/macOS 挂载、
Windows 盘符与 UNC。外部 API 不得提交绝对输出路径。
"""

import os
import re
from datetime import datetime
from pathlib import Path, PurePath, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MediaAsset, StorageRoot

_SAFE = re.compile(r"[^\w\-.一-鿿]+")


class StorageUnavailable(RuntimeError):
    """No usable storage root (or an unsafe root) for a filesystem operation."""


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


def safe_asset_filename(name: str, *, label: str = "文件名") -> str:
    """Validate a single stored filename (not a path).

    Download adapters normally return ``Path.name``, but the database is also
    reachable by archive import and history.  Treating ``video_file='../x'`` as
    a trusted name would let playback, deletion and inference escape the asset
    directory.  The contract is deliberately stricter than ``join_root``.
    """
    value = (name or "").strip()
    if not value:
        return ""
    if "/" in value or "\\" in value or ".." in PurePath(value).parts:
        raise ValueError(f"{label}必须是单层文件名，不能包含路径分隔符或 ..")
    if value in (".", ".."):
        raise ValueError(f"{label}非法")
    return value


def ensure_within_directory(directory: str, candidate: str) -> str:
    """Resolve both sides and fail closed when ``candidate`` escapes ``directory``.

    ``resolve(strict=False)`` catches symlinked ancestors while still allowing a
    missing leaf; callers decide whether the leaf should exist.
    """
    base = Path(directory).resolve()
    target = Path(candidate).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"路径越出素材目录: {candidate}") from exc
    return str(target)


def resolve_asset_file_path(directory: str, filename: str, *, label: str = "文件名") -> str:
    name = safe_asset_filename(filename, label=label)
    if not name:
        return ""
    return ensure_within_directory(directory, str(Path(directory) / name))


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
            name = safe_asset_filename(f)
            if name:
                base = base / name
        return str(base)
    base = PurePath(root_path)
    for seg in rel.split("/"):
        if seg:
            base = base / seg
    for f in files:
        name = safe_asset_filename(f)
        if name:
            base = base / name
    return str(base)


def resolve_asset_root(db: Session, asset: "MediaAsset") -> StorageRoot:
    """Return the storage root that owns an asset, without guessing a fallback path."""
    root = None
    if asset.storage_root_id is not None:
        root = db.get(StorageRoot, asset.storage_root_id)
        if root is None:
            # 显式绑定已经失效时绝不能回退到默认根：那会把文件写到另一个盘，
            # 更糟的是删除素材时可能删错目录。绑定失效是数据损坏，必须显式失败。
            raise StorageUnavailable(
                f"素材 #{asset.id} 绑定的存储根 #{asset.storage_root_id} 不存在"
            )
    if root is None:
        root = get_default_root(db)
    if root is None:
        raise StorageUnavailable("未配置存储根目录，无法定位素材文件")
    if not (root.path or "").strip():
        raise StorageUnavailable(f"存储根 #{root.id} 路径为空")
    return root


def resolve_asset_dir(db: Session, asset: "MediaAsset") -> str:
    """拼出素材的物理目录（storage_root + relative_path）。仅后端内部使用。

    asset 未绑定 storage_root 时回退到默认根；都没有时抛 ``StorageUnavailable``，
    绝不退回进程 CWD（那会静默把文件写到部署目录）。最终目录必须仍在存储根内，
    这样即使历史数据或存储根内的符号链接指向外部，后续破坏性操作也会拒绝。
    """
    root = resolve_asset_root(db, asset)
    try:
        # 存储根在写入 API 上已校验；这里再校验一次，兜住早期版本/手工写库留下的
        # 相对路径。否则进程 CWD 会在解析时被当成根目录，静默落错位置。
        validate_root_path(root.path)
        relative_path = (asset.relative_path or "").strip()
        reject_absolute(relative_path)
    except ValueError as exc:
        raise StorageUnavailable(f"存储路径不安全：{exc}") from exc
    directory = join_root(root.path, relative_path)
    try:
        return ensure_within_directory(root.path, directory)
    except ValueError as exc:
        # Windows paths configured on a non-Windows host cannot be resolved by
        # pathlib meaningfully; there is no local file to delete in that case.
        is_windows_root = bool(PureWindowsPath(root.path).drive) or root.path.startswith("\\\\")
        if is_windows_root and os.name != "nt":
            return directory
        raise StorageUnavailable(f"存储路径不安全：{exc}") from exc


def validate_root_path(path: str, *, name: str = "存储根") -> str:
    """Validate a storage root supplied by an administrator.

    Absolute POSIX paths, Windows drive paths and UNC paths are all accepted;
    relative paths, empty values and path traversal are rejected.  The returned
    value is stripped without normalising case or separators (a Windows mount can
    be configured from a Linux host).
    """
    value = (path or "").strip()
    if not value:
        raise ValueError(f"{name}路径不能为空")
    is_posix_abs = value.startswith("/")
    is_windows_abs = bool(PureWindowsPath(value).drive) and not value.startswith("\\")
    is_unc = value.startswith("\\\\")
    if not (is_posix_abs or is_windows_abs or is_unc):
        raise ValueError(f"{name}必须是绝对路径、Windows 盘符或 UNC 路径")
    if ".." in PurePath(value.replace("\\", "/")).parts:
        raise ValueError(f"{name}不能包含 ..")
    return value


def normalize_root_key(path: str) -> str:
    """Comparison key for duplicate roots (case-insensitive on Windows paths)."""
    value = (path or "").strip().replace("\\", "/").rstrip("/")
    is_windows = bool(PureWindowsPath(path or "").drive) or (path or "").startswith("\\\\")
    return value.lower() if is_windows else value


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
    try:
        return resolve_asset_file_path(directory, asset.video_file, label="视频文件名")
    except ValueError:
        return ""


def root_file_sizes(path: str, *, max_files: int = 200_000) -> dict[str, int]:
    """一次遍历返回 {相对路径(POSIX) -> 文件字节数}。

    素材列表/总览要按 storage_root + relative_path 统计体积，但逐个素材 stat 是
    O(素材数) 次 syscall，且素材可能上万条（我们导入了 9k+ 归档素材），单次请求会
    拖到十几秒甚至更久。这里改成「每个存储根遍历一次目录、缓存到内存」，之后按
    相对路径查表即 O(1)。原生的 per-asset 路径只作为兜底。
    """
    import os

    out: dict[str, int] = {}
    if not path or not os.path.isdir(path):
        return out
    seen = 0
    base = os.path.normpath(path)
    for root, _dirs, files in os.walk(base):
        rel_dir = os.path.relpath(root, base)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for name in files:
            try:
                out[f"{rel_dir}/{name}" if rel_dir else name] = os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
            seen += 1
            if seen >= max_files:
                return out
    return out


def sizes_for_assets(db: Session, assets) -> dict[int, int]:
    """批量算素材体积：每个存储根只遍历一次目录，避免逐素材 stat。

    返回 {asset_id: size_bytes}。找不到文件的素材记 0（与旧行为一致）。
    """
    import os

    from app.models import MediaAsset  # 局部导入，避免循环依赖

    assets = list(assets)
    if not assets:
        return {}
    # 1) 收集用到的存储根（asset.storage_root_id，缺省回退默认根）
    default_root = get_default_root(db)
    root_ids = {a.storage_root_id for a in assets if a.storage_root_id is not None}
    roots: dict[int, str] = {}
    for rid in root_ids:
        r = db.get(StorageRoot, rid)
        if r is not None:
            roots[rid] = r.path
    cache: dict[str | None, dict[str, int]] = {}
    result: dict[int, int] = {}
    for a in assets:
        rid = a.storage_root_id
        rpath = roots.get(rid) if rid is not None else None
        if rpath is None:
            rpath = default_root.path if default_root is not None else None
        if rpath is None:
            result[a.id] = 0
            continue
        if rpath not in cache:
            cache[rpath] = cached_root_file_sizes(rpath)
        files = cache[rpath]
        rel = (a.relative_path or "").strip("/")
        size = 0
        if a.video_file:
            size = files.get(f"{rel}/{a.video_file}" if rel else a.video_file, 0)
        if not size and rel:
            # 视频缺失/文件名对不上时，退回该素材目录内已缓存文件求和（不再二次遍历磁盘）
            prefix = f"{rel}/"
            size = sum(v for k, v in files.items() if k.startswith(prefix))
        result[a.id] = size
    return result


def sizes_for_asset_rows(db: Session, rows) -> dict[int, int]:
    """Same calculation as ``sizes_for_assets`` for lightweight row tuples.

    Dashboard aggregation needs only ``(asset_id, root_id, relative_path,
    video_file)`` and must not materialise every MediaAsset/ContentItem ORM
    object.  Keep the file-index lookup shared so both paths have identical
    fallback semantics.
    """
    from app.models import MediaAsset  # noqa: F401  (kept for type symmetry)

    rows = list(rows)
    if not rows:
        return {}
    default_root = get_default_root(db)
    root_ids = {root_id for _aid, root_id, _rel, _video in rows if root_id is not None}
    roots: dict[int, str] = {}
    for rid in root_ids:
        r = db.get(StorageRoot, rid)
        if r is not None:
            roots[rid] = r.path
    cache: dict[str, dict[str, int]] = {}
    result: dict[int, int] = {}
    for asset_id, root_id, relative_path, video_file in rows:
        root_path = roots.get(root_id) if root_id is not None else None
        if root_path is None:
            root_path = default_root.path if default_root is not None else None
        if root_path is None:
            result[asset_id] = 0
            continue
        if root_path not in cache:
            cache[root_path] = cached_root_file_sizes(root_path)
        files = cache[root_path]
        rel = (relative_path or "").strip("/")
        size = 0
        if video_file:
            size = files.get(f"{rel}/{video_file}" if rel else video_file, 0)
        if not size and rel:
            prefix = f"{rel}/"
            size = sum(value for key, value in files.items() if key.startswith(prefix))
        result[asset_id] = size
    return result


# ---------------------------------------------------------------------------
# 存储根体积：整树 stat 在 NAS 大库上要数秒，做成进程级后台刷新缓存。
# ---------------------------------------------------------------------------
_dir_size_cache: dict[str, tuple[float, int]] = {}
_DIR_SIZE_TTL = 300.0  # 秒


def cached_dir_size(path: str, *, ttl: float = _DIR_SIZE_TTL) -> int:
    """返回目录体积；命中缓存直接返回，miss 时后台线程刷新、本次先给旧值（无则 0）。

    这样单次请求永远不会被整树遍历阻塞（原先每次总览请求都同步 dir_size，
    246G 的库能卡到十几秒）。
    """
    import threading
    import time

    now = time.time()
    hit = _dir_size_cache.get(path)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]

    def _refresh() -> None:
        try:
            _dir_size_cache[path] = (time.time(), dir_size(path))
        except Exception:  # noqa: BLE001 后台刷新失败不影响接口
            _dir_size_cache[path] = (time.time(), hit[1] if hit else 0)

    threading.Thread(target=_refresh, name="dir-size-refresh", daemon=True).start()
    return hit[1] if hit else 0


# 存储根文件索引缓存：整树遍历 71k 文件约 2-4s，总览每次请求都走一遍太久。
_file_index_cache: dict[str, tuple[float, dict[str, int]]] = {}
_FILE_INDEX_TTL = 300.0


def cached_root_file_sizes(path: str, *, ttl: float = _FILE_INDEX_TTL) -> dict[str, int]:
    """带缓存的文件索引（相对路径 -> 字节数）。

    内存里保留最近一次遍历结果；过期后由后台线程刷新，本次先返回旧值，
    保证 compute 侧永远不会被整树遍历阻塞。首次调用会同步遍历一次。
    """
    import threading
    import time

    now = time.time()
    hit = _file_index_cache.get(path)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]

    if hit is None:
        # 冷启动先读磁盘索引（上次进程落盘的），避免重启后再整树遍历一次。
        disk = _load_index_from_disk(path)
        if disk is not None:
            _file_index_cache[path] = (time.time(), disk)
            hit = _file_index_cache[path]

    def _refresh() -> None:
        try:
            idx = root_file_sizes(path)
            _file_index_cache[path] = (time.time(), idx)
            _save_index_to_disk(path, idx)
        except Exception:  # noqa: BLE001 后台刷新失败保留旧值
            pass

    if hit is None:
        # 连磁盘缓存都没有：同步建一次，否则体积会全 0（导入/首次部署时才会走到）
        idx = root_file_sizes(path)
        _file_index_cache[path] = (time.time(), idx)
        _save_index_to_disk(path, idx)
        return idx

    # 有（内存或磁盘）旧索引：后台刷新，本次先返回旧值
    threading.Thread(target=_refresh, name="file-index-refresh", daemon=True).start()
    return hit[1]


def _index_cache_path(path: str) -> Path:
    import hashlib

    from app.config import settings

    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    return Path(settings.data_dir) / "storage_index" / f"{digest}.json"


def _load_index_from_disk(path: str) -> dict[str, int] | None:
    import json

    try:
        f = _index_cache_path(path)
        if not f.is_file():
            return None
        raw = json.loads(f.read_text("utf-8"))
        return {str(k): int(v) for k, v in raw.items()}
    except (OSError, ValueError):
        return None


def _save_index_to_disk(path: str, index: dict[str, int]) -> None:
    import json

    try:
        f = _index_cache_path(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(index), "utf-8")
        tmp.replace(f)
    except OSError:
        pass
