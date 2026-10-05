import platform as py_platform
import shutil
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core import security, storage
from app.core.scheduler import scheduler
from app.db import get_db
from app.models import StorageRoot, SystemSetting

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(security.require_admin)])


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
    if body.is_default:
        for r in db.scalars(select(StorageRoot).where(StorageRoot.is_default.is_(True))).all():
            r.is_default = False
    root = StorageRoot(name=body.name, path=body.path, is_default=body.is_default)
    db.add(root)
    db.commit()
    db.refresh(root)
    return {"id": root.id}


@router.delete("/storage-roots/{root_id}")
def delete_storage_root(root_id: int, db: Session = Depends(get_db)):
    root = db.get(StorageRoot, root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="存储根不存在")
    db.delete(root)
    db.commit()
    return {"ok": True}


# ---------- 通用设置（大模型/代理等，存 system_setting 键值） ----------
_PUBLIC_SETTINGS = ("llm_base_url", "llm_model", "proxy_url")  # llm_api_key 写入但不回显


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    out = {k: rows.get(k, "") for k in _PUBLIC_SETTINGS}
    out["llm_api_key_set"] = bool(rows.get("llm_api_key"))
    return out


@router.put("/settings")
def update_settings(body: dict, db: Session = Depends(get_db)):
    allowed = set(_PUBLIC_SETTINGS) | {"llm_api_key"}
    for key, value in body.items():
        if key not in allowed:
            continue
        if key == "llm_api_key" and not value:
            continue  # 空值不覆盖已存的密钥
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.add(row)
        row.value = str(value)
    db.commit()
    return {"ok": True}


# ---------- 评论抓取配置（抖音评论富集门控/抓取参数） ----------
_COMMENT_KEYS = {
    "top_pct": "comment_gate_top_pct",
    "budget": "comment_gate_budget",
    "min_comments": "comment_gate_min_comments",
    "min_likes": "comment_gate_min_likes",
    "weight": "comment_gate_weight",
    "pages": "comment_fetch_pages",
    "per_page": "comment_fetch_per_page",
    "top_n": "comment_fetch_top_n",
    "interval": "comment_fetch_interval_sec",
}


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
}


@router.get("/comment-settings")
def get_comment_settings_api(db: Session = Depends(get_db)):
    from app.core.comment_settings import get_comment_settings

    return get_comment_settings(db)


@router.put("/comment-settings")
def update_comment_settings_api(body: dict, db: Session = Depends(get_db)):
    """写入评论抓取配置。先全量校验再落库：任一越界值返回 422，不做部分写入。"""
    normalized: dict[str, str] = {}
    for short, value in (body or {}).items():
        spec = _COMMENT_SPECS.get(short)
        if spec is None:
            continue  # 未知键忽略（与既有 /settings 行为一致）
        try:
            normalized[_COMMENT_KEYS[short]] = str(spec(value))
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
    add("密钥文件", (settings.data_dir / "secret.key").exists(), "凭证加密密钥（迁移机器必须随行备份）")
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
        add("Node(execjs)", st["node_for_execjs"], "抖音 a_bogus 签名需要，缺失则抖音抓取不可用")
    except ImportError:
        add("浏览器运行时", False, "未安装。抖音/小红书扫码不可用，可用手动粘 Cookie 兜底")
    add("定时体检", scheduler.running, f"间隔 {settings.account_check_interval} 分钟")
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}
