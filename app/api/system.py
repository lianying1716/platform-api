import json
import platform as py_platform
import shutil
import sys

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core import fs_browse, security, storage
from app.core.process_settings import (
    _valid_dimension_list as _valid_common_dimensions,
    _valid_taxonomy,
    _valid_type_prompts,
)
from app.core.scheduler import scheduler
from app.db import get_db
from app.models import StorageRoot, SystemSetting

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(security.require_admin)])

# 公开只读：版本信息不敏感，用于"我装的是哪版 / DB 是否同步"，无需登录。
public_router = APIRouter(prefix="/api/system", tags=["system"])


@public_router.get("/version")
def get_version(db: Session = Depends(get_db)):
    """返回应用版本与数据库 revision（升级排障用）。"""
    from app.core import version as version_mod

    info = version_mod.load_build_info()
    head = version_mod.db_head()
    try:
        db_revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 未迁移/无该表时应报告未知而非 500
        db_revision = None
    db_revision = db_revision or ""
    return {
        "app_version": info.version,
        "git_commit": info.commit,
        "build_time": info.build_time,
        "db_revision": db_revision,
        "db_head": head,
        "db_in_sync": bool(head) and db_revision == head,
    }


@router.get("/update/check")
async def check_update():
    """检查是否有新版本（只读查询 GitHub Releases）。"""
    from app.core import version_upgrade

    current = version_upgrade.load_current_version()
    return await version_upgrade.check_latest(current=current, repo=settings.update_repo)


@router.get("/update/status")
def update_status():
    """一键升级是否可用（是否配置了 updater 边车）。"""
    from app.core.version import load_build_info

    return {
        "enabled": bool(settings.updater_url and settings.updater_token),
        "current": load_build_info().version,
    }


class UpgradeIn(BaseModel):
    version: str


@router.get("/upgrade/status")
async def upgrade_status():
    """代理 updater 的升级进度（前端轮询用）。

    主 API 在升级中会被重建，期间前端可能拿到 502；前端据此提示"服务重启中"，
    待新版本起来后继续轮询。
    """
    if not settings.updater_url:
        return {"enabled": False, "busy": False, "job": None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.updater_url.rstrip('/')}/upgrade/status")
        if resp.status_code >= 400:
            return {"enabled": True, "busy": False, "job": None, "error": f"updater {resp.status_code}"}
        data = resp.json()
        data["enabled"] = True
        return data
    except httpx.HTTPError:
        # 升级期间主 API 自身可能不可达；返回可解析结构让前端继续轮询
        return {"enabled": True, "busy": True, "job": None, "restarting": True}


@router.post("/upgrade")
async def trigger_upgrade(body: UpgradeIn):
    """触发一键升级（调 updater 边车）。

    安全约束：仅在配置 PLATFORM_UPDATER_URL 时可用；版本号必须过白名单校验，
    防止把任意字符串当成镜像 tag / shell 片段传给持 docker socket 的边车。
    """
    from app.core import version_upgrade

    if not settings.updater_url:
        raise HTTPException(status_code=409, detail="一键升级未启用（未配置 PLATFORM_UPDATER_URL）")
    if not settings.updater_token:
        raise HTTPException(status_code=409, detail="一键升级未启用（未配置 PLATFORM_UPDATER_TOKEN）")
    if not version_upgrade.is_valid_version(body.version):
        raise HTTPException(status_code=422, detail=f"非法版本号：{body.version!r}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.updater_url.rstrip('/')}/upgrade",
                json={"version": body.version},
                headers={"X-Updater-Token": settings.updater_token},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"连接 updater 失败：{exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"updater 返回 {resp.status_code}: {resp.text[:300]}")
    return resp.json()


# ---------- 存储根目录 ----------
class StorageRootIn(BaseModel):
    name: str
    path: str
    is_default: bool = False


@router.get("/storage-roots")
def list_storage_roots(db: Session = Depends(get_db)):
    roots = db.scalars(select(StorageRoot).order_by(StorageRoot.id)).all()
    out = []
    for r in roots:
        ok, detail = storage.probe_root(r.path)
        out.append(
            {"id": r.id, "name": r.name, "path": r.path, "is_default": r.is_default, "available": ok, "detail": detail}
        )
    return out


@router.post("/storage-roots")
def create_storage_root(body: StorageRootIn, db: Session = Depends(get_db)):
    try:
        path = storage.validate_root_path(body.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    wanted = storage.normalize_root_key(path)
    if any(storage.normalize_root_key(r.path) == wanted for r in db.scalars(select(StorageRoot)).all()):
        raise HTTPException(status_code=409, detail="该存储根路径已存在")
    if body.is_default:
        for r in db.scalars(select(StorageRoot).where(StorageRoot.is_default.is_(True))).all():
            r.is_default = False
    root = StorageRoot(name=(body.name or "").strip(), path=path, is_default=body.is_default)
    db.add(root)
    db.commit()
    db.refresh(root)
    # 回带可用性供前端软提示：路径对 backend/worker 进程是否真实可写。
    # 不阻塞保存——非 Windows 主机配置 Windows 路径仍合法（只是"存了待校验"）。
    ok, detail = storage.probe_root(root.path)
    return {"id": root.id, "available": ok, "detail": detail}


@router.delete("/storage-roots/{root_id}", status_code=200)
def delete_storage_root(root_id: int, db: Session = Depends(get_db)):
    from app.services.storage_lifecycle import StorageRootInUse, delete_root

    try:
        delete_root(db, root_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="存储根不存在") from exc
    except StorageRootInUse as exc:
        raise HTTPException(status_code=409, detail=f"存储根仍被引用，无法删除：{exc}") from exc
    return {"ok": True}


@router.get("/storage-roots/orphans")
def storage_orphans(root_id: int | None = None, db: Session = Depends(get_db)):
    """只读孤儿文件报告；管理员据此决定是否人工清理。"""
    from app.services.storage_lifecycle import scan_orphan_files

    return scan_orphan_files(db, root_id=root_id)


# ---------- 目录浏览（存储根可视化选择；只读） ----------
@router.get("/fs/roots")
def list_fs_roots():
    """允许浏览的根清单（白名单）。前端「选择目录」用它做根快捷入口。

    只读接口，仅管理员可调；真正的落盘红线不受影响（仍走 storage_root + relative_path）。
    """
    return {"roots": fs_browse.allowed_roots()}


@router.get("/fs/list")
def list_fs_dir(path: str = ""):
    """列出某目录下的子目录（仅目录，不含文件）。越界/不存在返回 400。

    path 为空时回第一个存在的白名单根。前端据此画面包屑 + 目录列表。
    """
    try:
        return fs_browse.list_dir(path)
    except fs_browse.BrowseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------- 通用设置（非秘密运行配置，存 system_setting 键值） ----------
# Generic settings are read-only. Credentials and model routing belong to the
# model-access surface; the old llm_* writer is kept rejected so clients get an
# actionable error instead of silently storing a second source of truth.
_PUBLIC_SETTINGS = ()
_REJECTED_SECRET_SETTINGS = {"llm_api_key", "llm_base_url", "llm_model"}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    from app.core import crypto

    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    out = {k: rows.get(k, "") for k in _PUBLIC_SETTINGS}
    out["credential_key_configured"] = bool(crypto.key_is_configured())
    return out


@router.put("/settings")
def update_settings(body: dict, db: Session = Depends(get_db)):
    rejected = _REJECTED_SECRET_SETTINGS & set(body)
    if rejected:
        raise HTTPException(
            status_code=422,
            detail="凭证不能写入普通系统设置，请在「模型接入」中配置供应商密钥",
        )
    allowed = set(_PUBLIC_SETTINGS)
    if body:
        raise HTTPException(
            status_code=422,
            detail="通用系统设置中没有可写字段；代理请在部署环境配置，模型请在「模型接入」配置",
        )
    for key, value in body.items():
        if key not in allowed:
            continue
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.add(row)
        row.value = str(value)
    db.commit()
    return {"ok": True}


# ---------- AI 任务运行时段 ----------
@router.get("/ai-schedule")
def get_ai_schedule_api(db: Session = Depends(get_db)):
    """AI 任务可运行的时段、当前是否开放以及下一次开放时间。"""
    from app.core.ai_schedule import ai_schedule_status, get_ai_schedule

    return ai_schedule_status(get_ai_schedule(db))


@router.put("/ai-schedule")
def update_ai_schedule_api(body: dict, db: Session = Depends(get_db)):
    """保存 AI 时段。先完整校验再写入，非法配置返回 422。"""
    from app.core.ai_schedule import (
        SETTING_KEY,
        ai_schedule_status,
        normalize_ai_schedule,
    )

    try:
        normalized = normalize_ai_schedule(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"非法 AI 时段配置：{exc}") from exc

    row = db.get(SystemSetting, SETTING_KEY)
    if row is None:
        row = SystemSetting(key=SETTING_KEY)
        db.add(row)
    row.value = json.dumps(normalized, ensure_ascii=False)
    db.commit()
    return {"ok": True, "schedule": ai_schedule_status(normalized)}


# ---------- 评论抓取配置（抖音评论富集门控/抓取参数） ----------
def _as_int(value, *, lo: int) -> int:
    """数字 -> int；拒绝 bool / 非整数 / < lo。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("需要数字")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("需要整数")
    iv = int(value)
    if iv < lo:
        raise ValueError(f"需 >= {lo}")
    return iv


def _as_float(value, *, lo: float, hi: float | None = None) -> float:
    """数字 -> float；拒绝 bool / NaN / Inf / 越界。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("需要数字")
    fv = float(value)
    if fv != fv or fv in (float("inf"), float("-inf")):
        raise ValueError("需要有限数")
    if fv < lo:
        raise ValueError(f"需 >= {lo}")
    if hi is not None and fv > hi:
        raise ValueError(f"需 <= {hi}")
    return fv


# 短名 -> 校验器（越界即 422）。范围来源：C-Task 3/4 review 的 Task 8 carry-forward。
# 不校验会让门控退化：budget=-1 时 take=min(n-1, -1) 变成 n-1（off-by-one）。
_COMMENT_SPECS = {
    "top_pct": lambda v: _as_float(v, lo=0.0, hi=1.0),
    "budget": lambda v: _as_int(v, lo=0),
    "min_comments": lambda v: _as_int(v, lo=0),
    "min_likes": lambda v: _as_int(v, lo=0),
    "weight": lambda v: _as_float(v, lo=0.0),
    "pages": lambda v: _as_int(v, lo=1),
    "per_page": lambda v: _as_int(v, lo=1),
    "top_n": lambda v: _as_int(v, lo=1),
    "interval": lambda v: _as_float(v, lo=0.0),
    "with_sub_comments": lambda v: bool(v) if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on"),
}


@router.get("/comment-settings")
def get_comment_settings_api(db: Session = Depends(get_db)):
    from app.core.comment_settings import get_comment_settings

    return get_comment_settings(db)


@router.put("/comment-settings")
def update_comment_settings_api(body: dict, db: Session = Depends(get_db)):
    """写入评论抓取配置。先全量校验再落库：任一越界值返回 422，不做部分写入。"""
    from app.core.comment_settings import COMMENT_KEYS

    normalized: dict[str, str] = {}
    for short, value in (body or {}).items():
        spec = _COMMENT_SPECS.get(short)
        if spec is None:
            continue  # 未知键忽略（与既有 /settings 行为一致）
        try:
            normalized[COMMENT_KEYS[short]] = str(spec(value))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"非法评论配置 {short}={value!r}: {exc}") from exc

    for key, value in normalized.items():
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.add(row)
        row.value = value
    db.commit()
    return {"ok": True}


# ---------- 爆款拆解配置（提示词 + 爆款判定阈值） ----------
@router.get("/breakdown-settings")
def get_breakdown_settings_api(db: Session = Depends(get_db)):
    from app.core.breakdown_settings import get_breakdown_settings

    return get_breakdown_settings(db)


@router.put("/breakdown-settings")
def update_breakdown_settings_api(body: dict, db: Session = Depends(get_db)):
    """写入爆款拆解配置（含自动拆解阀门）。先全量校验再落库：非法值返回 422，不做部分写入。"""
    import math

    from app.core.breakdown_settings import (
        BREAKDOWN_KEYS,
        resolve_breakdown_settings_from_rows,
    )

    _int_bounds = {
        "min_play": (0, 10 ** 12),
        "min_like": (0, 10 ** 12),
        "publish_within_days": (0, 3650),
    }
    normalized: dict[str, str] = {}
    for short, value in (body or {}).items():
        if short == "prompt":
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(status_code=422, detail="breakdown_prompt 必须是非空字符串")
            normalized[BREAKDOWN_KEYS["prompt"]] = value
        elif short == "viral_multiple":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(status_code=422, detail="viral_multiple 必须是数字")
            fv = float(value)
            if not math.isfinite(fv) or fv <= 0:
                raise HTTPException(status_code=422, detail="viral_multiple 必须是有限正数")
            normalized[BREAKDOWN_KEYS["viral_multiple"]] = str(fv)
        elif short == "min_engagement_rate":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(status_code=422, detail="min_engagement_rate 必须是数字")
            fv = float(value)
            if not math.isfinite(fv) or fv < 0 or fv > 1:
                raise HTTPException(status_code=422, detail="min_engagement_rate 需在 [0, 1] 之间（互动率）")
            normalized[BREAKDOWN_KEYS["min_engagement_rate"]] = str(fv)
        elif short in _int_bounds:
            if isinstance(value, bool) or not isinstance(value, int):
                raise HTTPException(status_code=422, detail=f"{short} 必须是整数")
            lo, hi = _int_bounds[short]
            if value < lo or value > hi:
                raise HTTPException(status_code=422, detail=f"{short} 需在 [{lo}, {hi}] 之间")
            normalized[BREAKDOWN_KEYS[short]] = str(value)
        elif short == "match_rule":
            if value not in ("any", "all"):
                raise HTTPException(status_code=422, detail="match_rule 只能是 any 或 all")
            normalized[BREAKDOWN_KEYS["match_rule"]] = str(value)
        elif short in ("auto_transcribe", "auto_understand", "auto_breakdown"):
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail=f"{short} 必须是布尔")
            normalized[BREAKDOWN_KEYS[short]] = "true" if value else "false"
        # 未知键忽略（与既有 /settings、/comment-settings 行为一致）

    for key, value in normalized.items():
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.add(row)
        row.value = value
    db.commit()
    return {"ok": True, "settings": resolve_breakdown_settings_from_rows(
        {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    )}


# ---------- 同步频率 / 批量配置（照抄抖小云：随机时间 + 每次数量范围） ----------
@router.get("/sync-settings")
def get_sync_settings_api(db: Session = Depends(get_db)):
    from app.core.sync_settings import get_sync_settings

    return get_sync_settings(db)


@router.put("/sync-settings")
def update_sync_settings_api(body: dict, db: Session = Depends(get_db)):
    """写入同步频率配置。先全量收敛到合法区间再落库（min>max 自动交换、越界钳制）。"""
    from app.core.sync_settings import (
        BATCH_CEIL,
        BATCH_FLOOR,
        DOWNLOAD_GAP_CEIL,
        INTERVAL_MINUTES_CEIL,
        INTERVAL_MINUTES_FLOOR,
        SYNC_KEYS,
        resolve_sync_settings_from_rows,
    )

    # 先把传入值并入现有配置，再用读取侧同一套归一逻辑纠正区间，避免部分写入留下非法组合。
    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    merged = dict(rows)
    bounds = {
        "interval_min_minutes": (INTERVAL_MINUTES_FLOOR, INTERVAL_MINUTES_CEIL, "int"),
        "interval_max_minutes": (INTERVAL_MINUTES_FLOOR, INTERVAL_MINUTES_CEIL, "int"),
        "batch_min": (BATCH_FLOOR, BATCH_CEIL, "int"),
        "batch_max": (BATCH_FLOOR, BATCH_CEIL, "int"),
        "download_gap_min_seconds": (0.0, DOWNLOAD_GAP_CEIL, "float"),
        "download_gap_max_seconds": (0.0, DOWNLOAD_GAP_CEIL, "float"),
    }
    for short, value in (body or {}).items():
        if short not in SYNC_KEYS:
            continue  # 未知键忽略（与既有 /settings 行为一致）
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(status_code=422, detail=f"{short} 必须是数字")
        lo, hi, kind = bounds[short]
        fv = float(value)
        if fv != fv or fv in (float("inf"), float("-inf")):
            raise HTTPException(status_code=422, detail=f"{short} 必须是有限数字")
        if fv < lo or fv > hi:
            raise HTTPException(status_code=422, detail=f"{short} 需在 [{lo}, {hi}] 之间")
        merged[SYNC_KEYS[short]] = str(int(value) if kind == "int" else fv)

    resolved = resolve_sync_settings_from_rows(merged)
    for short, val in resolved.items():
        key = SYNC_KEYS[short]
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.add(row)
        row.value = str(val)
    db.commit()
    return {"ok": True, "settings": resolved}


# ---------- 素材分档加工配置（普通/高级门控 + 类型目录 + 类型提示词） ----------
def _as_bool(value) -> bool:
    """布尔归一：接受 true/false、1/0、"true"/"1" 等；其余非法。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    raise ValueError("需要布尔值")


# 短名 -> 校验器。范围/枚举写死在这里（配置值本身不写死，可在线改）。
_PROCESS_SPECS = {
    "min_heat": lambda v: _as_int(v, lo=0),
    "min_likes_floor": lambda v: _as_int(v, lo=0),
    "budget": lambda v: _as_int(v, lo=0),
    "top_pct": lambda v: _as_float(v, lo=0.0, hi=1.0),
    "weight_like": lambda v: _as_float(v, lo=0.0),
    "weight_comment": lambda v: _as_float(v, lo=0.0),
    "weight_play": lambda v: _as_float(v, lo=0.0),
    "video_max_mb": lambda v: _as_float(v, lo=0.0),
    "cover_max_mb": lambda v: _as_float(v, lo=0.0),
    "low_confidence_threshold": lambda v: _as_float(v, lo=0.0, hi=1.0),
    "classify_enabled": _as_bool,
    "use_cover": _as_bool,
    # 全局自动加工默认（新素材下载完成后是否自动转录/分析）。
    "auto_transcribe": _as_bool,
    "auto_analyze": _as_bool,
}


# 结构化配置键 -> 校验器。落库键见 _PROCESS_STRUCT_STORE。
# 校验器复用 app.core.process_settings 的实现（读取侧同一套），避免两处定义漂移。
_PROCESS_STRUCT_KEYS = {
    "taxonomy": _valid_taxonomy,
    "type_prompts": _valid_type_prompts,
    "common_dimensions": _valid_common_dimensions,
}
_PROCESS_STRUCT_STORE = {
    "taxonomy": "process_type_taxonomy",
    "type_prompts": "process_type_prompts",
    "common_dimensions": "process_prompt_common",
}


@router.get("/process-settings")
def get_process_settings_api(db: Session = Depends(get_db)):
    """返回门控参数 + 类型目录 + 类型提示词 + 通用维度（设置页展示用）。"""
    from app.core.process_settings import (
        get_common_dimensions,
        get_process_settings,
        get_taxonomy,
        get_type_prompts,
    )

    out = get_process_settings(db)
    out["taxonomy"] = get_taxonomy(db)
    out["type_prompts"] = get_type_prompts(db)
    out["common_dimensions"] = get_common_dimensions(db)
    return out


@router.put("/process-settings")
def update_process_settings_api(body: dict, db: Session = Depends(get_db)):
    """写入分档加工配置。先全量校验再落库：任一非法值返回 422，不做部分写入。"""
    from app.core.process_settings import PROCESS_KEYS

    normalized: dict[str, str] = {}
    for short, value in (body or {}).items():
        if short in _PROCESS_STRUCT_KEYS:
            if not _PROCESS_STRUCT_KEYS[short](value):
                raise HTTPException(status_code=422, detail=f"非法配置 {short}：结构不符合预期")
            normalized[_PROCESS_STRUCT_STORE[short]] = json.dumps(value, ensure_ascii=False)
            continue
        spec = _PROCESS_SPECS.get(short)
        if spec is None:
            continue  # 未知键忽略（与既有 /settings 行为一致）
        try:
            normalized[PROCESS_KEYS[short]] = str(spec(value))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"非法加工配置 {short}={value!r}: {exc}") from exc

    for key, value in normalized.items():
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.add(row)
        row.value = value
    db.commit()
    return {"ok": True}


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db)):
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("Python", True, f"{sys.version.split()[0]} / {py_platform.system()}")
    try:
        db.execute(text("SELECT 1"))
        add("数据库", True, settings.resolved_database_url.split("://")[0])
    except Exception as exc:
        add("数据库", False, str(exc))
    add("数据目录", settings.data_dir.exists(), str(settings.data_dir))
    from app.core import crypto

    key_ok = crypto.key_is_configured()
    key_source = (
        "环境变量 PLATFORM_CREDENTIAL_ENC_KEY"
        if settings.credential_enc_key
        else "credential_key_file"
        if settings.credential_key_file is not None
        else "data/secret.key"
    )
    add("加密密钥", key_ok, f"来源：{key_source}（迁移机器必须随行备份）" if key_ok else "未配置或无法加载")
    ffmpeg = shutil.which("ffmpeg")
    add("FFmpeg", ffmpeg is not None, ffmpeg or "未找到，P2 视频下载/转码需要")
    try:
        import playwright  # noqa: F401

        from app.modules import browser_runtime

        st = browser_runtime.status()
        if st["playwright"] and st["chromium"]:
            add("浏览器运行时", True, "Playwright + Chromium 就绪，抖音/小红书扫码登录可用")
        elif st["playwright"]:
            add("浏览器运行时", False, "Playwright 已装但缺 Chromium：uv run playwright install chromium")
        else:
            add("浏览器运行时", False, "未安装。抖音/小红书扫码不可用，可用手动粘 Cookie 兜底")
        add("Node", st["node_available"], "受管浏览器/JS 运行时检查")
    except ImportError:
        add("浏览器运行时", False, "未安装。抖音/小红书扫码不可用，可用手动粘 Cookie 兜底")
    add("定时体检", scheduler.running, f"间隔 {settings.account_check_interval} 分钟")
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}
