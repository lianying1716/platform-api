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
