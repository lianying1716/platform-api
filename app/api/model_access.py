"""模型接入 API：供应商、模型、工作分配与调用审计。"""

from __future__ import annotations

import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.api_auth import require_admin_or_scope
from app.db import get_db
from app.models import (
    AiModel,
    AiProvider,
    ApiCallLog,
    ApiKey,
    ContentItem,
    JobQueue,
    MediaAsset,
    ModelAssignment,
    ModelCallAttempt,
)
from app.modules import model_protocols
from app.services import model_probe
from app.services.model_access_service import WORK_TYPES, capabilities_of

router = APIRouter(prefix="/api/model-access", tags=["model-access"])


class ProviderIn(BaseModel):
    name: str
    preset_key: str = "custom"
    protocol: str = "openai"
    base_url: str
    api_key: str = ""
    credential_source: str = "api_key"
    enabled: bool = True


class ProviderPatch(BaseModel):
    name: str | None = None
    protocol: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None


class ModelImportItem(BaseModel):
    model_key: str
    display_name: str = ""


class ProbeSpec(BaseModel):
    text: bool = True
    image: bool = True
    video: bool = True


class ModelImportBody(BaseModel):
    provider_id: int
    models: list[ModelImportItem]
    probe: ProbeSpec = Field(default_factory=ProbeSpec)


class ModelPatch(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    # 模型级协议覆盖：null 表示恢复「跟随供应商」；指定值时写 protocol_source=manual。
    # 传 "inherit" 时清空覆盖、回到继承（等价于 protocol=null）。
    protocol: str | None = None
    protocol_source: str | None = None


class ManualCapabilityBody(BaseModel):
    video: bool
    transport: str | None = None
    reason: str = ""


class AssignmentBody(BaseModel):
    primary_model_id: int | None = None
    fallback_model_id: int | None = None


def _api_log(db: Session, actor, tool: str, status: str, started: float) -> None:
    if isinstance(actor, ApiKey):
        db.add(
            ApiCallLog(
                key_id=actor.id,
                tool=tool[:64],
                params_summary="",
                status=status,
                duration_ms=int((time.time() - started) * 1000),
            )
        )
        db.commit()


def _mask_provider(row: AiProvider) -> dict:
    masked = ""
    if row.api_key_encrypted:
        try:
            plain = str(crypto.decrypt_credential(row.api_key_encrypted).get("api_key") or "")
            masked = crypto.mask(plain)
        except Exception:  # noqa: BLE001 旧密文/换钥不可读时仍只返回“已配置”
            masked = "****"
    return {
        "id": row.id,
        "name": row.name,
        "preset_key": row.preset_key,
        "protocol": row.protocol,
        "base_url": row.base_url,
        "credential_source": row.credential_source,
        "has_api_key": bool(row.api_key_encrypted),
        "api_key_masked": masked,
        "config_revision": row.config_revision,
        "enabled": row.enabled,
        "last_test_status": row.last_test_status,
        "last_test_at": row.last_test_at.isoformat() if row.last_test_at else None,
        "last_test_error": row.last_test_error,
    }


def _model_payload(db: Session, row: AiModel) -> dict:
    provider = db.get(AiProvider, row.provider_id)
    assigned = db.scalars(
        select(ModelAssignment.work_type).where(
            (ModelAssignment.primary_model_id == row.id)
            | (ModelAssignment.fallback_model_id == row.id)
        )
    ).all()
    effective = model_protocols.resolve_protocol(row, provider) if provider else ""
    return {
        "id": row.id,
        "provider_id": row.provider_id,
        "provider_name": provider.name if provider else "",
        "provider_protocol": provider.protocol if provider else "",
        # 生效协议（模型级覆盖优先）与来源；protocol_override 为 null 表示跟随供应商。
        "protocol": effective,
        "protocol_source": row.protocol_source or "inherit",
        "protocol_override": row.protocol,
        "provider_base_url": provider.base_url if provider else "",
        "provider_enabled": bool(provider.enabled) if provider else False,
        "model_key": row.model_key,
        "display_name": row.display_name or row.model_key,
        "import_source": row.import_source,
        "enabled": row.enabled,
        "capabilities": capabilities_of(row, provider),
        "probe_status": row.probe_status,
        "probe_error": row.probe_error,
        "probed_at": row.probed_at.isoformat() if row.probed_at else None,
        "assigned_work_types": list(assigned),
    }


def _validate_provider_fields(protocol: str, base_url: str) -> tuple[str, str]:
    protocol = (protocol or "").strip().lower()
    base_url = (base_url or "").strip().rstrip("/")
    if protocol not in ("openai", "anthropic", "gemini"):
        raise HTTPException(status_code=422, detail="协议仅支持 openai / anthropic / gemini")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Base URL 必须以 http:// 或 https:// 开头")
    return protocol, base_url


def _enqueue_probe(db: Session, model_id: int, spec: dict, *, model: AiModel | None = None) -> JobQueue:
    """入队探测；同一模型已有排队/运行任务时合并探测范围，避免并发覆盖。"""
    requested = {
        "text": bool(spec.get("text", True)),
        "image": bool(spec.get("image", True)),
        "video": bool(spec.get("video", True)),
    }
    existing_rows = db.scalars(
        select(JobQueue).where(
            JobQueue.job_type == "probe_model",
            JobQueue.status.in_(("queued", "running")),
        )
    ).all()
    for row in existing_rows:
        payload = dict(row.payload or {})
        if payload.get("model_id") != model_id:
            continue
        current = payload.get("probe") or {}
        payload["probe"] = {
            key: bool(current.get(key)) or requested[key] for key in ("text", "image", "video")
        }
        row.payload = payload
        db.commit()
        if model is not None:
            model.probe_status = "testing"
            db.commit()
        return row
    from app.core.queue import enqueue

    job = enqueue(
        db,
        "probe_model",
        {"model_id": model_id, "probe": requested},
        priority=80,
        dedup=False,
    )
    if model is not None:
        model.probe_status = "testing"
        db.commit()
    return job


def _sensitive(scope: str):
    return require_admin_or_scope("admin") if scope == "admin" else require_admin_or_scope("operate", "admin")


# ---------------- 供应商 ----------------
@router.get("/providers")
def list_providers(
    db: Session = Depends(get_db), actor=Depends(require_admin_or_scope("read", "operate", "admin"))
):
    started = time.time()
    rows = db.scalars(select(AiProvider).order_by(AiProvider.id)).all()
    out = [_mask_provider(row) for row in rows]
    _api_log(db, actor, "model_access.list_providers", "ok", started)
    return out


@router.post("/providers")
def create_provider(
    body: ProviderIn,
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("admin")),
):
    started = time.time()
    protocol, base_url = _validate_provider_fields(body.protocol, body.base_url)
    if body.credential_source != "api_key":
        raise HTTPException(status_code=422, detail="首版仅支持 api_key 凭证来源")
    if not body.api_key.strip():
        raise HTTPException(status_code=422, detail="访问密钥不能为空")
    row = AiProvider(
        name=body.name.strip(),
        preset_key=body.preset_key.strip() or "custom",
        protocol=protocol,
        base_url=base_url,
        credential_source="api_key",
        api_key_encrypted=crypto.encrypt_credential({"api_key": body.api_key.strip()}),
        config_revision=1,
        enabled=body.enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _api_log(db, actor, "model_access.create_provider", "ok", started)
    return _mask_provider(row)


@router.patch("/providers/{provider_id}")
def patch_provider(
    provider_id: int,
    body: ProviderPatch,
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("admin")),
):
    started = time.time()
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    changed_connection = False
    if body.name is not None:
        row.name = body.name.strip()
    if body.protocol is not None:
        protocol, _ = _validate_provider_fields(body.protocol, row.base_url)
        if protocol != row.protocol:
            row.protocol = protocol
            changed_connection = True
    if body.base_url is not None:
        _, base_url = _validate_provider_fields(row.protocol, body.base_url)
        if base_url != row.base_url:
            row.base_url = base_url
            changed_connection = True
    if body.api_key is not None and body.api_key.strip():
        row.api_key_encrypted = crypto.encrypt_credential({"api_key": body.api_key.strip()})
    if body.enabled is not None:
        row.enabled = body.enabled
    if changed_connection:
        row.config_revision = int(row.config_revision or 1) + 1
        for model in db.scalars(select(AiModel).where(AiModel.provider_id == row.id)).all():
            # 保留旧能力记录，交由 capabilities_of() 按 config_revision 统一标记为 stale；
            # 直接清空会让“需重新检测”的安全提示在前端无迹可寻。
            model.probe_status = "untested"
            model.probed_at = None
        for assignment in db.scalars(select(ModelAssignment)).all():
            assignment.legacy_pending_validation = False
    db.commit()
    _api_log(db, actor, "model_access.patch_provider", "ok", started)
    return _mask_provider(row)


@router.delete("/providers/{provider_id}")
def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("admin")),
):
    started = time.time()
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if db.scalar(select(AiModel.id).where(AiModel.provider_id == provider_id).limit(1)):
        raise HTTPException(status_code=409, detail="供应商下还有模型，请先删除模型")
    db.delete(row)
    db.commit()
    _api_log(db, actor, "model_access.delete_provider", "ok", started)
    return {"ok": True}


@router.post("/providers/{provider_id}/test")
async def test_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    key = ""
    try:
        key = str(crypto.decrypt_credential(row.api_key_encrypted or "").get("api_key") or "")
        req = model_protocols.list_models_request(row, api_key=key)
        data = await model_protocols.send_request(req, timeout=20)
        model_protocols.parse_models(row.protocol, data)
        row.last_test_status, row.last_test_error, row.last_test_at = "ok", "", datetime.utcnow()
        db.commit()
        _api_log(db, actor, "model_access.test_provider", "ok", started)
        return {"ok": True, "message": "连接成功"}
    except Exception as exc:  # noqa: BLE001 连接测试失败要回写到供应商，供 UI 展示
        row.last_test_status, row.last_test_error, row.last_test_at = (
            "error",
            crypto.redact_text(str(exc), extra_secrets=(key,))[:1000],
            datetime.utcnow(),
        )
        db.commit()
        _api_log(db, actor, "model_access.test_provider", "error", started)
        return {"ok": False, "message": crypto.redact_text(str(exc), extra_secrets=(key,))[:1000]}


@router.post("/providers/{provider_id}/discover-models")
async def discover_models(
    provider_id: int,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    key = ""
    try:
        key = str(crypto.decrypt_credential(row.api_key_encrypted or "").get("api_key") or "")
        data = await model_protocols.send_request(
            model_protocols.list_models_request(row, api_key=key), timeout=30
        )
        names = model_protocols.parse_models(row.protocol, data)
        _api_log(db, actor, "model_access.discover_models", "ok", started)
        return {"models": names}
    except Exception as exc:  # noqa: BLE001 清单失败允许前端手输
        _api_log(db, actor, "model_access.discover_models", "error", started)
        safe = crypto.redact_text(str(exc), extra_secrets=(key,))
        raise HTTPException(status_code=502, detail=f"无法读取模型清单: {safe}") from exc


# ---------------- 模型 ----------------
@router.get("/models")
def list_models(
    provider_id: int | None = None,
    work_type: str | None = None,
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("read", "operate", "admin")),
):
    started = time.time()
    stmt = select(AiModel).order_by(AiModel.id)
    if provider_id is not None:
        stmt = stmt.where(AiModel.provider_id == provider_id)
    rows = db.scalars(stmt).all()
    out = [_model_payload(db, row) for row in rows]
    if work_type:
        out = [row for row in out if work_type in row["assigned_work_types"]]
    _api_log(db, actor, "model_access.list_models", "ok", started)
    return out


@router.post("/models/import")
def import_models(
    body: ModelImportBody,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    provider = db.get(AiProvider, body.provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    created: list[AiModel] = []
    for item in body.models:
        model_key = item.model_key.strip()
        if not model_key:
            continue
        existing = db.scalar(
            select(AiModel).where(
                AiModel.provider_id == provider.id, AiModel.model_key == model_key
            )
        )
        if existing is not None:
            created.append(existing)
            continue
        row = AiModel(
            provider_id=provider.id,
            model_key=model_key,
            display_name=item.display_name.strip() or model_key,
            import_source="discovered",
            capabilities={},
        )
        db.add(row)
        db.flush()
        created.append(row)
    db.commit()
    if any(body.probe.model_dump().values()):
        for row in created:
            _enqueue_probe(db, row.id, body.probe.model_dump(), model=row)
    _api_log(db, actor, "model_access.import_models", "ok", started)
    return {
        "models": [_model_payload(db, row) for row in created],
        "probe_requested": body.probe.model_dump(),
    }


@router.patch("/models/{model_id}")
def patch_model(
    model_id: int,
    body: ModelPatch,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    row = db.get(AiModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    if body.display_name is not None:
        row.display_name = body.display_name.strip()
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.protocol is not None or body.protocol_source is not None:
        # 恢复继承：protocol="inherit" 或显式 null。
        if body.protocol_source == "inherit" or body.protocol == "inherit":
            row.protocol = None
            row.protocol_source = "inherit"
        elif body.protocol is not None:
            try:
                row.protocol = model_protocols.validate_protocol(body.protocol)
            except model_protocols.ModelProtocolError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            # 用户显式指定即视为 manual：后续自动检测不再覆盖。
            row.protocol_source = body.protocol_source or "manual"
        # 协议变化会让既有能力结论失效；capabilities_of() 会按协议比对判 stale，
        # 这里只把探测状态复位，提示用户重新检测。
        row.probe_status = "untested"
    db.commit()
    _api_log(db, actor, "model_access.patch_model", "ok", started)
    return _model_payload(db, row)


@router.delete("/models/{model_id}")
def delete_model(
    model_id: int,
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("admin")),
):
    started = time.time()
    row = db.get(AiModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    assigned = db.scalar(
        select(ModelAssignment.id).where(
            (ModelAssignment.primary_model_id == model_id)
            | (ModelAssignment.fallback_model_id == model_id)
        ).limit(1)
    )
    if assigned:
        raise HTTPException(status_code=409, detail="模型仍在工作分配中，请先解除分配")
    db.delete(row)
    db.commit()
    _api_log(db, actor, "model_access.delete_model", "ok", started)
    return {"ok": True}


@router.post("/models/{model_id}/probe")
async def probe(
    model_id: int,
    body: ProbeSpec | None = None,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    spec = body or ProbeSpec()
    row = db.get(AiModel, model_id)
    if row is None:
        _api_log(db, actor, "model_access.probe", "error", started)
        raise HTTPException(status_code=404, detail="模型不存在")
    _enqueue_probe(db, model_id, spec.model_dump(), model=row)
    _api_log(db, actor, "model_access.probe", "ok", started)
    return _model_payload(db, row)


@router.post("/models/{model_id}/manual-capability")
async def manual_capability(
    model_id: int,
    body: ManualCapabilityBody,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    try:
        row = await model_probe.set_manual_video_capability(
            db, model_id, value=body.video, transport=body.transport, reason=body.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _api_log(db, actor, "model_access.manual_capability", "ok", started)
    return _model_payload(db, row)


# ---------------- 工作分配 ----------------
@router.get("/assignments")
def list_assignments(
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("read", "operate", "admin")),
):
    started = time.time()
    out = []
    for work_type in WORK_TYPES:
        row = db.scalar(select(ModelAssignment).where(ModelAssignment.work_type == work_type))
        primary = db.get(AiModel, row.primary_model_id) if row and row.primary_model_id else None
        fallback = db.get(AiModel, row.fallback_model_id) if row and row.fallback_model_id else None
        out.append(
            {
                "work_type": work_type,
                "primary_model_id": row.primary_model_id if row else None,
                "primary_model": _model_payload(db, primary) if primary else None,
                "fallback_model_id": row.fallback_model_id if row else None,
                "fallback_model": _model_payload(db, fallback) if fallback else None,
                "auto_configured": bool(row.auto_configured) if row else False,
                "legacy_pending_validation": bool(row.legacy_pending_validation) if row else False,
                "configured": bool(row and row.primary_model_id),
            }
        )
    candidates = db.scalars(select(AiModel).where(AiModel.enabled.is_(True))).all()
    out_models = []
    for row in candidates:
        provider = db.get(AiProvider, row.provider_id)
        if provider is None or not provider.enabled:
            continue
        out_models.append(_model_payload(db, row))
    _api_log(db, actor, "model_access.list_assignments", "ok", started)
    return {"assignments": out, "models": out_models}


@router.put("/assignments/{work_type}")
def update_assignment(
    work_type: str,
    body: AssignmentBody,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    if work_type not in WORK_TYPES:
        raise HTTPException(status_code=422, detail="未知工作类型")
    if body.primary_model_id is not None and body.primary_model_id == body.fallback_model_id:
        raise HTTPException(status_code=422, detail="主模型和备用模型不能相同")
    models: dict[int, AiModel] = {}
    for model_id in (body.primary_model_id, body.fallback_model_id):
        if model_id is None:
            continue
        model = db.get(AiModel, model_id)
        provider = db.get(AiProvider, model.provider_id) if model else None
        if model is None or provider is None or not model.enabled or not provider.enabled:
            raise HTTPException(status_code=422, detail=f"模型 {model_id} 不存在或已停用")
        caps = capabilities_of(model, provider)
        required = ("text", "json") if work_type in ("text_analysis", "cover_classify") else ("json", "video")
        if not all((caps.get(key) or {}).get("value") for key in required):
            raise HTTPException(status_code=422, detail=f"模型 {model_id} 不满足该工作的能力要求")
        models[model_id] = model
    assignment = db.scalar(select(ModelAssignment).where(ModelAssignment.work_type == work_type))
    if assignment is None:
        assignment = ModelAssignment(work_type=work_type)
        db.add(assignment)
    assignment.primary_model_id = body.primary_model_id
    assignment.fallback_model_id = body.fallback_model_id
    assignment.auto_configured = False
    assignment.legacy_pending_validation = False
    db.commit()
    _api_log(db, actor, "model_access.update_assignment", "ok", started)
    return {"ok": True}


@router.post("/assignments/recommend")
def recommend_assignments(
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    models = db.scalars(select(AiModel).where(AiModel.enabled.is_(True)).order_by(AiModel.id)).all()
    for work_type in WORK_TYPES:
        assignment = db.scalar(select(ModelAssignment).where(ModelAssignment.work_type == work_type))
        if assignment is not None and assignment.primary_model_id is not None:
            continue
        required = ("text", "json") if work_type in ("text_analysis", "cover_classify") else ("json", "video")
        chosen = None
        for model in models:
            provider = db.get(AiProvider, model.provider_id)
            if provider is None or not provider.enabled:
                continue
            caps = capabilities_of(model, provider)
            if all((caps.get(key) or {}).get("value") for key in required):
                chosen = model
                break
        if chosen is None:
            continue
        if assignment is None:
            assignment = ModelAssignment(work_type=work_type)
            db.add(assignment)
        assignment.primary_model_id = chosen.id
        assignment.auto_configured = True
    db.commit()
    _api_log(db, actor, "model_access.recommend_assignments", "ok", started)
    return {"ok": True}


# ---------------- 调用审计 ----------------
@router.get("/calls")
def list_calls(
    work_type: str | None = None,
    model_id: int | None = None,
    asset_id: int | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("read", "operate", "admin")),
):
    started = time.time()
    stmt = select(ModelCallAttempt).order_by(ModelCallAttempt.id.desc())
    if work_type:
        stmt = stmt.where(ModelCallAttempt.work_type == work_type)
    if model_id is not None:
        stmt = stmt.where(ModelCallAttempt.model_id == model_id)
    if asset_id is not None:
        stmt = stmt.where(ModelCallAttempt.asset_id == asset_id)
    if status:
        stmt = stmt.where(ModelCallAttempt.status == status)
    count_stmt = select(func.count(ModelCallAttempt.id))
    if work_type:
        count_stmt = count_stmt.where(ModelCallAttempt.work_type == work_type)
    if model_id is not None:
        count_stmt = count_stmt.where(ModelCallAttempt.model_id == model_id)
    if asset_id is not None:
        count_stmt = count_stmt.where(ModelCallAttempt.asset_id == asset_id)
    if status:
        count_stmt = count_stmt.where(ModelCallAttempt.status == status)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    _api_log(db, actor, "model_access.list_calls", "ok", started)
    asset_titles = _asset_titles(db, rows)
    content = json.dumps(
        [_attempt_payload(db, row, asset_titles=asset_titles) for row in rows], ensure_ascii=False
    ).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"X-Total-Count": str(int(total)), "Content-Length": str(len(content))},
    )


@router.get("/calls/{attempt_group_id}")
def get_call_group(
    attempt_group_id: str,
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("read", "operate", "admin")),
):
    started = time.time()
    rows = db.scalars(
        select(ModelCallAttempt)
        .where(ModelCallAttempt.attempt_group_id == attempt_group_id)
        .order_by(ModelCallAttempt.id)
    ).all()
    _api_log(db, actor, "model_access.get_call_group", "ok", started)
    asset_titles = _asset_titles(db, rows)
    return [_attempt_payload(db, row, asset_titles=asset_titles) for row in rows]


def _asset_titles(db: Session, rows) -> dict[int, str]:
    """批量取本页尝试关联的素材标题，避免逐行查询。"""
    asset_ids = {int(row.asset_id) for row in rows if row.asset_id is not None}
    if not asset_ids:
        return {}
    pairs = db.execute(
        select(MediaAsset.id, ContentItem.title)
        .join(ContentItem, MediaAsset.content_id == ContentItem.id)
        .where(MediaAsset.id.in_(asset_ids))
    ).all()
    return {int(asset_id): title or "" for asset_id, title in pairs}


def _attempt_payload(db: Session, row: ModelCallAttempt, *, asset_titles: dict[int, str] | None = None) -> dict:
    if asset_titles is None:
        asset_titles = _asset_titles(db, [row])
    asset_title = asset_titles.get(int(row.asset_id), "") if row.asset_id is not None else ""
    return {
        "id": row.id,
        "attempt_group_id": row.attempt_group_id,
        "work_type": row.work_type,
        "operation": row.operation,
        "asset_id": row.asset_id,
        "asset_title": asset_title,
        "job_id": row.job_id,
        "role": row.role,
        "provider_id": row.provider_id,
        "provider_name": row.provider_name,
        "model_id": row.model_id,
        "model_key": row.model_key,
        "status": row.status,
        "error_class": row.error_class,
        # 读取侧再脱敏一次，兜住旧版本遗留的未脱敏历史数据。
        "error_message": crypto.redact_text(row.error_message or ""),
        "http_status": row.http_status,
        "duration_ms": row.duration_ms,
        "usage": json.loads(crypto.redact_text(json.dumps(row.usage or {}, ensure_ascii=False))),
        "request_meta": json.loads(
            crypto.redact_text(json.dumps(row.request_meta or {}, ensure_ascii=False))
        ),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }
