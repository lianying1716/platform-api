import json
import platform as py_platform
import shutil
import sys
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core import security, storage
from app.core.process_settings import (
    _valid_dimension_list as _valid_common_dimensions,
    _valid_taxonomy,
    _valid_type_prompts,
)
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


@router.post("/settings/test-llm")
async def test_llm(db: Session = Depends(get_db), body: dict | None = None):
    """测试大模型连通性（"接入方法"的验证按钮）。

    用系统设置里的 Base URL / Key / 模型（未保存的字段也可经 body 临时覆盖），
    发一个最小 chat/completions 请求，返回是否通、耗时、回复片段。
    只做连通性探测，不写库、不产生费用之外的副作用。
    """
    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    body = body or {}
    base = (body.get("llm_base_url") or rows.get("llm_base_url") or settings.llm_base_url or "").rstrip("/")
    key = body.get("llm_api_key") or rows.get("llm_api_key") or settings.llm_api_key or ""
    model = body.get("llm_model") or rows.get("llm_model") or settings.llm_model or ""
    if not base:
        raise HTTPException(status_code=422, detail="未填 Base URL")
    if not model:
        raise HTTPException(status_code=422, detail="未填模型名")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"连接失败: {exc}", "elapsed_ms": int((time.time() - t0) * 1000)}
    ms = int((time.time() - t0) * 1000)
    if resp.status_code >= 400:
        return {
            "ok": False,
            "message": f"HTTP {resp.status_code}: {resp.text[:300]}",
            "elapsed_ms": ms,
        }
    try:
        data = resp.json()
        snippet = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except Exception:
        snippet = ""
    return {"ok": True, "message": "连接成功", "model": model, "reply": str(snippet)[:100], "elapsed_ms": ms}


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
    """写入爆款拆解配置。先全量校验再落库：非法值返回 422，不做部分写入。"""
    import math

    from app.core.breakdown_settings import BREAKDOWN_KEYS

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
        # 未知键忽略（与既有 /settings、/comment-settings 行为一致）

    for key, value in normalized.items():
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.add(row)
        row.value = value
    db.commit()
    return {"ok": True}


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


def _as_provider(value) -> str:
    if not isinstance(value, str) or value not in ("qwen", "gemini"):
        raise ValueError("provider 仅支持 qwen / gemini")
    return value


def _as_nonempty_str(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("需要非空字符串")
    return value.strip()


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
    "provider": _as_provider,
    "vision_model": _as_nonempty_str,
    "classify_model": _as_nonempty_str,
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
