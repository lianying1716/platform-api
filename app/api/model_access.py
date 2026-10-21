"""模型接入 API：供应商、模型、工作分配与调用审计。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import schemas
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
from app.services.legacy_llm_migration import legacy_migration_status
from app.services.model_access_service import (
    WORK_TYPES,
    capabilities_of,
    model_access_setup_state,
    model_can_serve,
    required_capabilities,
)

router = APIRouter(prefix="/api/model-access", tags=["model-access"])


@router.get("/legacy", response_model=schemas.LegacyMigrationOut)
def legacy_migration_overview(
    db: Session = Depends(get_db),
    actor=Depends(require_admin_or_scope("read", "operate", "admin")),
):
    """旧版 llm_* 认领状态；只返回状态，不暴露密钥或密文。"""
    return legacy_migration_status(db)


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


class ProviderImportBody(BaseModel):
    """一步式「填地址+密钥 → 勾模型 → 导入」。

    供应商按 base_url 认领（同地址视为同一个供应商，更新其密钥/协议），
    避免用户每点一次导入就多出一个重复供应商。
    """

    protocol: str = "openai"
    base_url: str
    api_key: str = ""
    name: str = ""
    preset_key: str = "custom"
    provider_id: int | None = None
    models: list[ModelImportItem]
    probe: ProbeSpec = Field(default_factory=ProbeSpec)


class PreviewModelsBody(BaseModel):
    protocol: str = "openai"
    base_url: str
    api_key: str = ""
    provider_id: int | None = None
    # 仅用于给临时探测对象起名/记预设，不落库。
    name: str = ""
    preset_key: str = "custom"


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
        # 余额与延迟：只做手动刷新，展示上次结果。null = 从未刷新过（前端显示「—」）。
        "balance_amount": row.balance_amount,
        "balance_error": row.balance_error,
        "balance_updated_at": row.balance_updated_at.isoformat() if row.balance_updated_at else None,
        "latency_ms": row.latency_ms,
        "latency_error": row.latency_error,
        "latency_updated_at": row.latency_updated_at.isoformat() if row.latency_updated_at else None,
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
        "probe_started_at": row.probe_started_at.isoformat() if row.probe_started_at else None,
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
            model.probe_started_at = datetime.utcnow()
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
        model.probe_started_at = datetime.utcnow()
        db.commit()
    return job


def _sensitive(scope: str):
    return require_admin_or_scope("admin") if scope == "admin" else require_admin_or_scope("operate", "admin")


def _provider_api_key(provider: AiProvider) -> str:
    """Decrypt once at the provider boundary; never return this value in a response."""
    return str(crypto.decrypt_credential(provider.api_key_encrypted or "").get("api_key") or "")


async def _fetch_model_names(provider: AiProvider, api_key: str, *, timeout: float = 30) -> list[str]:
    """The single model-list use case shared by preview and saved-provider discovery."""
    data = await model_protocols.send_request(
        model_protocols.list_models_request(provider, api_key=api_key), timeout=timeout
    )
    return model_protocols.parse_models(provider.protocol, data)


# ---------------- 供应商 ----------------
@router.get("/providers", response_model=list[schemas.ProviderOut])
def list_providers(
    db: Session = Depends(get_db), actor=Depends(require_admin_or_scope("read", "operate", "admin"))
):
    started = time.time()
    rows = db.scalars(select(AiProvider).order_by(AiProvider.id)).all()
    out = [_mask_provider(row) for row in rows]
    _api_log(db, actor, "model_access.list_providers", "ok", started)
    return out


@router.post("/providers", response_model=schemas.ProviderOut)
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


@router.patch("/providers/{provider_id}", response_model=schemas.ProviderOut)
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
        new_key = body.api_key.strip()
        old_key = ""
        if row.api_key_encrypted:
            try:
                old_key = str(crypto.decrypt_credential(row.api_key_encrypted).get("api_key") or "")
            except Exception:  # noqa: BLE001 无法解密也应按连接变更处理
                old_key = ""
        if new_key != old_key:
            row.api_key_encrypted = crypto.encrypt_credential({"api_key": new_key})
            changed_connection = True
    if body.enabled is not None:
        row.enabled = body.enabled
    if changed_connection:
        row.config_revision = int(row.config_revision or 1) + 1
        for model in db.scalars(select(AiModel).where(AiModel.provider_id == row.id)).all():
            # 保留旧能力记录，交由 capabilities_of() 按 config_revision 统一标记为 stale；
            # 直接清空会让“需重新检测”的安全提示在前端无迹可寻。
            model.probe_status = "untested"
            model.probe_started_at = None
            model.probed_at = None
    db.commit()
    _api_log(db, actor, "model_access.patch_provider", "ok", started)
    return _mask_provider(row)


@router.delete("/providers/{provider_id}", response_model=schemas.OkOut)
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


@router.post("/providers/{provider_id}/test", response_model=schemas.ProviderTestOut)
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
        key = _provider_api_key(row)
        await _fetch_model_names(row, key, timeout=20)
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


@router.post("/providers/{provider_id}/discover-models", response_model=schemas.ModelNamesOut)
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
        key = _provider_api_key(row)
        names = await _fetch_model_names(row, key)
        _api_log(db, actor, "model_access.discover_models", "ok", started)
        return {"models": names}
    except Exception as exc:  # noqa: BLE001 清单失败允许前端手输
        _api_log(db, actor, "model_access.discover_models", "error", started)
        safe = crypto.redact_text(str(exc), extra_secrets=(key,))
        raise HTTPException(status_code=502, detail=f"无法读取模型清单: {safe}") from exc


@router.post("/providers/{provider_id}/refresh-balance", response_model=schemas.ProviderOut)
async def refresh_balance(
    provider_id: int,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    """手动刷新供应商余额（星算等 OpenAI 兼容网关）。

    只读、只手动——前端点数字才触发一次，结果落库供下次直接展示。
    网关不提供这两个端点时如实回写错误，不猜数字。
    """
    started = time.time()
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    key = ""
    try:
        key = str(crypto.decrypt_credential(row.api_key_encrypted or "").get("api_key") or "")
    except Exception:  # noqa: BLE001 旧密文/换钥不可读时给出可读错误
        row.balance_error = "访问密钥无法解密，请重新填写"
        row.balance_updated_at = datetime.utcnow()
        db.commit()
        _api_log(db, actor, "model_access.refresh_balance", "error", started)
        return _mask_provider(row)

    requests = model_protocols.balance_requests(row, api_key=key)
    if not requests:
        row.balance_error = "该协议不支持查询余额"
        row.balance_updated_at = datetime.utcnow()
        db.commit()
        _api_log(db, actor, "model_access.refresh_balance", "error", started)
        return _mask_provider(row)

    try:
        payloads: dict[str, dict] = {}
        for kind, request in requests:
            payloads[kind] = await model_protocols.send_request(request, timeout=15)
        row.balance_amount = model_protocols.parse_balance(
            payloads.get("subscription", {}), payloads.get("usage", {})
        )
        row.balance_error = ""
    except Exception as exc:  # noqa: BLE001 查询失败要回写供 UI 展示
        row.balance_error = crypto.redact_text(str(exc), extra_secrets=(key,))[:200]
    row.balance_updated_at = datetime.utcnow()
    db.commit()
    _api_log(db, actor, "model_access.refresh_balance", "ok", started)
    return _mask_provider(row)


@router.post("/providers/{provider_id}/refresh-latency", response_model=schemas.ProviderOut)
async def refresh_latency(
    provider_id: int,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    """手动测一次供应商连通延迟（就是「拉模型清单」那一跳的耗时）。

    复用 list_models_request：它是最轻、最不产生计费的读取型调用，
    拿来量网络往返最合适；同时顺带验证密钥可用。
    """
    started = time.time()
    row = db.get(AiProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    key = ""
    try:
        key = str(crypto.decrypt_credential(row.api_key_encrypted or "").get("api_key") or "")
        request = model_protocols.list_models_request(row, api_key=key)
        t0 = time.perf_counter()
        await model_protocols.send_request(request, timeout=20)
        row.latency_ms = int((time.perf_counter() - t0) * 1000)
        row.latency_error = ""
    except Exception as exc:  # noqa: BLE001 测速失败要回写供 UI 展示
        row.latency_ms = None
        row.latency_error = crypto.redact_text(str(exc), extra_secrets=(key,))[:200]
    row.latency_updated_at = datetime.utcnow()
    db.commit()
    _api_log(db, actor, "model_access.refresh_latency", "ok", started)
    return _mask_provider(row)


@router.post("/providers/preview-models", response_model=schemas.ModelNamesOut)
async def preview_models(
    body: PreviewModelsBody,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    """按「地址 + 密钥 + 协议」临时拉一次模型清单——**不落库、不建供应商**。

    单页弹窗要先让用户看到并勾选模型，此时还没有供应商实体；用这个端点
    试连并把清单拿回来。密钥优先用请求里填的，没填则回退到已有供应商的密文
    （编辑场景：用户不改密钥就重新拉一次）。
    """
    started = time.time()
    key = body.api_key.strip()
    if not key and body.provider_id is not None:
        existing = db.get(AiProvider, body.provider_id)
        if existing is not None:
            try:
                key = str(crypto.decrypt_credential(existing.api_key_encrypted or "").get("api_key") or "")
            except Exception:  # noqa: BLE001 旧密文不可读时按「没填密钥」处理
                key = ""
    if not key:
        raise HTTPException(status_code=422, detail="请先填写访问密钥")
    protocol, base_url = _validate_provider_fields(body.protocol, body.base_url)
    probe_row = AiProvider(
        name=body.name.strip() or "preview",
        preset_key=body.preset_key.strip() or "custom",
        protocol=protocol,
        base_url=base_url,
        credential_source="api_key",
        api_key_encrypted="",
        config_revision=1,
        enabled=True,
    )
    try:
        names = await _fetch_model_names(probe_row, key)
    except Exception as exc:  # noqa: BLE001 清单失败允许前端手输
        _api_log(db, actor, "model_access.preview_models", "error", started)
        safe = crypto.redact_text(str(exc), extra_secrets=(key,))
        raise HTTPException(status_code=502, detail=f"无法读取模型清单: {safe}") from exc
    _api_log(db, actor, "model_access.preview_models", "ok", started)
    return {"models": names}


@router.post("/providers/import-models", response_model=schemas.ProviderImportOut)
def provider_import_models(
    body: ProviderImportBody,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    """一步式导入：认领/更新供应商，然后按勾选**覆盖**它的模型清单。

    与 ``/models/import`` 同一套覆盖语义（勾选之外的模型下线删除，仍被工作
    分配引用的改为停用）。区别只在于供应商可以在这里顺带创建/更新密钥。
    """
    started = time.time()
    protocol, base_url = _validate_provider_fields(body.protocol, body.base_url)

    provider = db.get(AiProvider, body.provider_id) if body.provider_id else None
    if provider is None:
        provider = db.scalar(select(AiProvider).where(AiProvider.base_url == base_url))
    if provider is None:
        if not body.api_key.strip():
            raise HTTPException(status_code=422, detail="访问密钥不能为空")
        provider = AiProvider(
            name=(body.name.strip() or base_url),
            preset_key=body.preset_key.strip() or "custom",
            protocol=protocol,
            base_url=base_url,
            credential_source="api_key",
            api_key_encrypted=crypto.encrypt_credential({"api_key": body.api_key.strip()}),
            config_revision=1,
            enabled=True,
        )
        db.add(provider)
        db.flush()
    else:
        changed = False
        if body.name.strip() and body.name.strip() != provider.name:
            provider.name = body.name.strip()
        if protocol != provider.protocol:
            provider.protocol = protocol
            changed = True
        if base_url != provider.base_url:
            provider.base_url = base_url
            changed = True
        if body.api_key.strip():
            provider.api_key_encrypted = crypto.encrypt_credential({"api_key": body.api_key.strip()})
            changed = True
        if changed:
            provider.config_revision = int(provider.config_revision or 1) + 1
    db.flush()

    result = import_models(
        ModelImportBody(provider_id=provider.id, models=body.models, probe=body.probe), db, actor
    )
    db.commit()
    _api_log(db, actor, "model_access.provider_import_models", "ok", started)
    return {"provider": _mask_provider(provider), **result}


# ---------------- 模型 ----------------
@router.get("/models", response_model=list[schemas.ModelOut])
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


class ProbeReconcileBody(BaseModel):
    # The UI polls for ~7 minutes.  Older probes with no live job are abandoned;
    # the janitor's 30-minute pass remains the conservative backstop.
    older_than_minutes: int = Field(default=7, ge=1, le=1440)


@router.post("/probes/reconcile", response_model=schemas.ReconcileProbesOut)
def reconcile_probes(
    body: ProbeReconcileBody | None = None,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    """Recover probes stuck in ``testing`` after the client's poll gave up.

    The client polling for seven minutes is itself the lease.  A queued probe
    that no worker ever claimed, or a running probe whose heartbeat has gone
    stale, is cancelled before reconciliation.  A live (freshly heartbeating)
    worker is left alone.  The list payload is returned for an immediate refresh.
    """
    from app.services.run_reconciler import reconcile_runs

    started = time.time()
    minutes = (body or ProbeReconcileBody()).older_than_minutes
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    job_ids = [
        row.id
        for row in db.scalars(
            select(JobQueue).where(
                JobQueue.job_type == "probe_model",
                JobQueue.status.in_(("queued", "running")),
            )
        ).all()
        if row.status == "queued"
        or not (
            (row.heartbeat_at or row.locked_at or row.updated_at or row.created_at) >= cutoff
        )
    ]
    if job_ids:
        db.execute(
            update(JobQueue)
            .where(JobQueue.id.in_(job_ids))
            .values(
                status="error",
                error="模型检测超时，已由前端终止；可重新检测",
                updated_at=datetime.utcnow(),
                heartbeat_at=None,
            )
        )
        db.commit()
    counts = reconcile_runs(db, older_than_minutes=minutes)
    rows = db.scalars(select(AiModel).order_by(AiModel.id)).all()
    _api_log(db, actor, "model_access.reconcile_probes", "ok", started)
    return {"recovered": counts.get("probe", 0), "models": [_model_payload(db, row) for row in rows]}


@router.post("/models/import", response_model=schemas.ModelImportOut)
def import_models(
    body: ModelImportBody,
    db: Session = Depends(get_db),
    actor=Depends(_sensitive("operate")),
):
    started = time.time()
    provider = db.get(AiProvider, body.provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    # 覆盖式导入：以本次勾选为准，该供应商名下**不在勾选内**的模型一律删除。
    # 场景是「供应商下线了某些模型」——旧行为只增不减，会把已下线的模型永远留在
    # 库里、还在工作分配里被选中，调用时才失败。
    wanted: list[str] = []
    for item in body.models:
        model_key = item.model_key.strip()
        if model_key and model_key not in wanted:
            wanted.append(model_key)
    wanted_set = set(wanted)

    existing_rows = db.scalars(
        select(AiModel).where(AiModel.provider_id == provider.id)
    ).all()
    by_key = {row.model_key: row for row in existing_rows}
    removed = [row for row in existing_rows if row.model_key not in wanted_set]

    # 仍被工作分配引用的模型不能直接删（外键 SET NULL 会静默清空分配，
    # 让用户在不知情的情况下失去主/备模型）。改为停用并保留，由用户在
    # 「已导入模型」里显式处理；其余（未被引用的）真删除。
    removed_ids = [row.id for row in removed]
    referenced: set[int] = set()
    if removed_ids:
        referenced = set(
            db.scalars(
                select(ModelAssignment.primary_model_id).where(
                    ModelAssignment.primary_model_id.in_(removed_ids)
                )
            ).all()
        ) | set(
            db.scalars(
                select(ModelAssignment.fallback_model_id).where(
                    ModelAssignment.fallback_model_id.in_(removed_ids)
                )
            ).all()
        )
        referenced.discard(None)

    created: list[AiModel] = []
    for model_key in wanted:
        row = by_key.get(model_key)
        if row is not None:
            created.append(row)
            continue
        row = AiModel(
            provider_id=provider.id,
            model_key=model_key,
            display_name=model_key,
            import_source="discovered",
            capabilities={},
        )
        db.add(row)
        db.flush()
        created.append(row)

    kept_in_use: list[str] = []
    for row in removed:
        if row.id in referenced:
            row.enabled = False
            kept_in_use.append(row.model_key)
        else:
            db.delete(row)
    db.commit()
    if any(body.probe.model_dump().values()):
        for row in created:
            _enqueue_probe(db, row.id, body.probe.model_dump(), model=row)
    _api_log(db, actor, "model_access.import_models", "ok", started)
    return {
        "models": [_model_payload(db, row) for row in created],
        "removed": [row.model_key for row in removed if row.id not in referenced],
        "kept_in_use": kept_in_use,
        "probe_requested": body.probe.model_dump(),
    }


@router.patch("/models/{model_id}", response_model=schemas.ModelOut)
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
        row.probe_started_at = None
    db.commit()
    _api_log(db, actor, "model_access.patch_model", "ok", started)
    return _model_payload(db, row)


@router.delete("/models/{model_id}", response_model=schemas.OkOut)
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


@router.post("/models/{model_id}/probe", response_model=schemas.ModelOut)
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


@router.post("/models/{model_id}/manual-capability", response_model=schemas.ModelOut)
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
@router.get("/assignments", response_model=schemas.AssignmentListOut)
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
                "configured": bool(row and (row.primary_model_id or row.fallback_model_id)),
                "primary_configured": bool(row and row.primary_model_id),
                "required_capabilities": list(required_capabilities(db, work_type)),
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
    return {"assignments": out, "models": out_models, "setup_state": model_access_setup_state(db)}


@router.put("/assignments/{work_type}", response_model=schemas.OkOut)
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
        required = required_capabilities(db, work_type)
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


@router.post("/assignments/recommend", response_model=schemas.OkOut)
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
        required = required_capabilities(db, work_type)
        chosen = None
        for model in models:
            provider = db.get(AiProvider, model.provider_id)
            if model_can_serve(db, model, provider, work_type, required=required):
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
@router.get(
    "/calls",
    response_class=JSONResponse,
    response_model=list[schemas.ModelCallAttemptOut],
    responses={200: {"headers": {"X-Total-Count": {"schema": {"type": "integer"}}}}},
)
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
    return JSONResponse(
        content=[_attempt_payload(db, row, asset_titles=asset_titles) for row in rows],
        headers={"X-Total-Count": str(int(total))},
    )


@router.get("/calls/{attempt_group_id}", response_model=list[schemas.ModelCallAttemptOut])
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
        .where(MediaAsset.id.in_(asset_ids), MediaAsset.deleted_at.is_(None))
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
