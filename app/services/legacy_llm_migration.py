"""一次性认领旧版 ``llm_*`` 配置，之后业务只走模型网关。

历史版本允许在 ``system_setting`` 或环境变量里配置 Base URL / Key / 模型，并由
分析、判类、视频理解、拆解各自读取。两套读取路径会让网页配置、环境变量和模型
接入页互相覆盖，也让凭证散落在普通 KV 中。

这里的边界是明确的：

- 启动时**最多认领一次**旧配置，把密钥加密进 ``AiProvider``，把模型和三个工作槽位
  建成显式的 ``AiModel`` / ``ModelAssignment``；
- 认领后删除旧 ``llm_*`` 与加工页里的 ``process_*_model`` 选择键；
- 运行期业务代码只通过 ``ModelAccessService`` 读取供应商、模型和路由；
- 认领是幂等的，并且只填空槽位，不覆盖用户已经手动配置的工作分配。

存量 0018 迁移可能只建了 ``AiProvider/AiModel``，却把能力留空（或把视频标成不可用）。
本模块会把这些“已分配但不可用”的模型补成 ``legacy`` 能力：允许继续运行以保持升级
可用性，同时在模型接入页明确标为“未检测”，供应商连接或协议变化后立即转为 stale。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.crypto import decrypt_credential, encrypt_credential
from app.models import AiModel, AiProvider, ModelAssignment, SystemSetting
from app.modules import model_protocols
from app.services.model_access_service import WORK_TYPES

logger = logging.getLogger("platform.legacy_llm")

CLAIM_MARKER = "model_access_legacy_claimed"
IMPORT_MARKER = "model_access_legacy_imported"
_LEGACY_SELECTION_KEYS = ("process_provider", "process_vision_model", "process_classify_model")
_LEGACY_LLM_KEYS = ("llm_base_url", "llm_api_key", "llm_model")
_LOCK_NAME = "platform:legacy-llm-claim"


@dataclass(frozen=True)
class LegacyClaimResult:
    status: str
    message: str
    provider_id: int | None = None
    model_ids: tuple[int, ...] = ()
    assignments: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["model_ids"] = list(self.model_ids)
        data["assignments"] = list(self.assignments)
        return data


def _rows(db: Session) -> dict[str, str]:
    return {row.key: row.value for row in db.scalars(select(SystemSetting)).all()}


def _put(db: Session, key: str, value: str) -> None:
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key)
        db.add(row)
    row.value = value


def _drop(db: Session, keys: tuple[str, ...]) -> None:
    for key in keys:
        row = db.get(SystemSetting, key)
        if row is not None:
            db.delete(row)


def _lock(db: Session) -> None:
    """串行化 API/worker 同时启动时的认领；SQLite 单进程测试无需锁。"""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": _LOCK_NAME})


def _legacy_fingerprint(rows: dict[str, str]) -> dict:
    base_url = (rows.get("llm_base_url") or settings.llm_base_url or "").strip().rstrip("/")
    model_key = (rows.get("llm_model") or settings.llm_model or "").strip()
    return {
        "base_url": base_url,
        "model": model_key,
        "classify_model": (rows.get("process_classify_model") or "").strip(),
        "vision_model": (rows.get("process_vision_model") or "").strip(),
        "provider": (rows.get("process_provider") or "qwen").strip(),
        "use_cover": str(rows.get("process_use_cover") or "").strip().lower() in ("1", "true", "yes", "on"),
    }


def _legacy_key(rows: dict[str, str], provider: AiProvider | None) -> str:
    key = (rows.get("llm_api_key") or settings.llm_api_key or "").strip()
    if key:
        return key
    if provider is not None and provider.api_key_encrypted:
        try:
            return str(decrypt_credential(provider.api_key_encrypted).get("api_key") or "").strip()
        except Exception:  # noqa: BLE001 旧密文不可读时按无密钥处理
            return ""
    return ""


def _protocol_for(base_url: str, legacy_provider: str) -> str:
    low = (base_url or "").lower()
    if "generativelanguage.googleapis.com" in low or "/v1beta" in low:
        return "gemini"
    if legacy_provider.strip().lower() in ("gemini", "google"):
        return "gemini"
    return "openai"


def _find_provider(db: Session, base_url: str) -> AiProvider | None:
    return db.scalar(select(AiProvider).where(AiProvider.base_url == base_url).order_by(AiProvider.id))


def _provider_for_claim(db: Session, *, base_url: str, protocol: str, key: str) -> AiProvider:
    provider = _find_provider(db, base_url)
    if provider is None:
        provider = AiProvider(
            name="旧配置自动认领",
            preset_key="custom",
            protocol=protocol,
            base_url=base_url,
            credential_source="api_key",
            api_key_encrypted=encrypt_credential({"api_key": key}),
            config_revision=1,
            enabled=True,
        )
        db.add(provider)
        db.flush()
        return provider

    changed = False
    if provider.protocol != protocol:
        provider.protocol = protocol
        changed = True
    if not provider.api_key_encrypted:
        provider.api_key_encrypted = encrypt_credential({"api_key": key})
        changed = True
    else:
        # Re-running with an updated PLATFORM_LLM_API_KEY must not silently keep
        # the old credential while the LLM base URL still matches.
        try:
            current_key = str(decrypt_credential(provider.api_key_encrypted).get("api_key") or "")
        except Exception:  # noqa: BLE001 不可读密文按失效处理，重新写入新密钥
            current_key = ""
        if current_key != key:
            provider.api_key_encrypted = encrypt_credential({"api_key": key})
            changed = True
    if changed:
        provider.config_revision = int(provider.config_revision or 1) + 1
    provider.enabled = True
    db.flush()
    return provider


def _model_for(db: Session, provider: AiProvider, model_key: str) -> AiModel | None:
    if not model_key:
        return None
    row = db.scalar(
        select(AiModel).where(
            AiModel.provider_id == provider.id,
            AiModel.model_key == model_key,
        )
    )
    if row is None:
        row = AiModel(
            provider_id=provider.id,
            model_key=model_key,
            display_name=model_key,
            import_source="legacy",
            enabled=True,
            capabilities={},
            probe_status="untested",
        )
        db.add(row)
        db.flush()
    return row


def _legacy_capability(value: bool, *, revision: int, protocol: str, detail: str, extra: dict | None = None) -> dict:
    item = {
        "value": bool(value),
        "source": "legacy",
        "config_revision": revision,
        "protocol": protocol,
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "detail": detail,
    }
    if extra:
        item.update(extra)
    return item


def _ensure_capabilities(
    model: AiModel,
    provider: AiProvider,
    work_types: set[str],
    *,
    image: bool,
) -> None:
    """只补缺失/旧的 legacy 能力，绝不覆盖 probe/manual_override 结论。"""
    revision = int(provider.config_revision or 1)
    protocol = model_protocols.resolve_protocol(model, provider)
    caps = dict(model.capabilities or {})
    text_work = bool(work_types & {"text_analysis", "cover_classify"})
    if text_work:
        for name in ("text", "json"):
            current = caps.get(name) if isinstance(caps.get(name), dict) else {}
            if current.get("source") not in ("probe", "manual_override", "legacy"):
                caps[name] = _legacy_capability(
                    True,
                    revision=revision,
                    protocol=protocol,
                    detail="由旧版 llm_* 配置认领，尚未做能力检测",
                )
        if image:
            current = caps.get("image") if isinstance(caps.get("image"), dict) else {}
            if current.get("source") not in ("probe", "manual_override", "legacy"):
                caps["image"] = _legacy_capability(
                    True,
                    revision=revision,
                    protocol=protocol,
                    detail="旧配置允许封面判类，尚未做看图能力检测",
                )
    if "video_understanding" in work_types:
        transport = "gemini_inline" if protocol == "gemini" else "openai_video_url"
        current = caps.get("video") if isinstance(caps.get("video"), dict) else {}
        if current.get("source") not in ("probe", "manual_override", "legacy"):
            caps["video"] = _legacy_capability(
                True,
                revision=revision,
                protocol=protocol,
                detail="由旧版视频理解配置认领，尚未做真实视频检测",
                extra={"transport": transport},
            )
        current_json = caps.get("json") if isinstance(caps.get("json"), dict) else {}
        if current_json.get("source") not in ("probe", "manual_override", "legacy"):
            caps["json"] = _legacy_capability(
                True,
                revision=revision,
                protocol=protocol,
                detail="由旧版视频理解配置认领，尚未做能力检测",
            )
    model.capabilities = caps
    model.probe_status = "untested" if any(
        (cap or {}).get("source") == "legacy" for cap in caps.values() if isinstance(cap, dict)
    ) else model.probe_status


def _fill_assignment(db: Session, work_type: str, model: AiModel, *, replace_empty_only: bool = True) -> bool:
    assignment = db.scalar(select(ModelAssignment).where(ModelAssignment.work_type == work_type))
    if assignment is None:
        assignment = ModelAssignment(work_type=work_type)
        db.add(assignment)
    if replace_empty_only and assignment.primary_model_id is not None:
        return False
    assignment.primary_model_id = model.id
    assignment.auto_configured = False
    assignment.legacy_pending_validation = True
    db.flush()
    return True


def _repair_existing_assignments(db: Session, *, image: bool) -> tuple[int, ...]:
    """修复 0018 留下的“有分配、无有效能力”模型，返回被修复的模型 id。"""
    repaired: set[int] = set()
    assignments = db.scalars(select(ModelAssignment)).all()
    by_model: dict[int, set[str]] = {}
    for assignment in assignments:
        if assignment.work_type in WORK_TYPES and assignment.primary_model_id is not None:
            by_model.setdefault(assignment.primary_model_id, set()).add(assignment.work_type)
    for model_id, work_types in by_model.items():
        model = db.get(AiModel, model_id)
        provider = db.get(AiProvider, model.provider_id) if model else None
        if model is None or provider is None:
            continue
        before = {
            key: value for key, value in (model.capabilities or {}).items() if isinstance(value, dict)
        }
        _ensure_capabilities(model, provider, work_types, image=image)
        after = {
            key: value for key, value in (model.capabilities or {}).items() if isinstance(value, dict)
        }
        changed = before != after
        if changed:
            repaired.add(model.id)
    for assignment in assignments:
        model = db.get(AiModel, assignment.primary_model_id) if assignment.primary_model_id else None
        if model is None:
            continue
        if assignment.legacy_pending_validation is False and any(
            (cap or {}).get("source") == "legacy"
            for cap in (model.capabilities or {}).values()
            if isinstance(cap, dict)
        ):
            assignment.legacy_pending_validation = True
    return tuple(sorted(repaired))


def claim_legacy_llm_settings(db: Session) -> LegacyClaimResult:
    """幂等认领旧配置；返回可直接展示、且不含密钥的状态。"""
    _lock(db)
    rows = _rows(db)
    fingerprint = _legacy_fingerprint(rows)
    already_claimed = CLAIM_MARKER in rows or IMPORT_MARKER in rows
    has_legacy = any(rows.get(key) for key in _LEGACY_LLM_KEYS)

    repaired = _repair_existing_assignments(db, image=bool(fingerprint["use_cover"]))
    if repaired:
        logger.info("修复旧模型能力记录 %s 个", len(repaired))
        db.commit()

    if already_claimed and not any(rows.get(key) for key in _LEGACY_LLM_KEYS):
        if repaired:
            return LegacyClaimResult("repaired", f"已补齐 {len(repaired)} 个旧模型的能力记录", model_ids=repaired)
        return LegacyClaimResult("already", "旧配置已认领，业务统一走模型接入")

    base_url = fingerprint["base_url"]
    if not base_url:
        return LegacyClaimResult("no_legacy", "未发现旧版 llm_* 配置")

    provider = _find_provider(db, base_url)
    key = _legacy_key(rows, provider)
    if not key:
        # A previous startup may already have repaired capabilities.  Keep that
        # repair, but leave the old KV rows intact so a missing key can be
        # supplied later instead of silently dropping the only address.
        db.commit()
        return LegacyClaimResult(
            "needs_api_key",
            "检测到旧版模型地址，但没有可用的访问密钥；请在「模型接入」中添加供应商并分配模型",
        )

    model_key = fingerprint["model"]
    if not model_key:
        db.commit()
        return LegacyClaimResult("needs_model", "检测到旧版模型地址，但缺少模型名；请在「模型接入」中导入模型")

    protocol = _protocol_for(base_url, fingerprint["provider"])
    provider = _provider_for_claim(db, base_url=base_url, protocol=protocol, key=key)
    text_model = _model_for(db, provider, model_key)
    # The old settings had implicit qwen3.8-flash defaults for these two slots,
    # but only the text analysis model is known to be configured.  Do not invent
    # bindings for slots the operator never supplied.
    classify_model_key = fingerprint["classify_model"]
    vision_model_key = fingerprint["vision_model"]
    classify_model = _model_for(db, provider, classify_model_key)
    vision_model = _model_for(db, provider, vision_model_key)

    use_cover = bool(fingerprint["use_cover"])
    _ensure_capabilities(text_model, provider, {"text_analysis", "cover_classify"}, image=use_cover)
    if classify_model is not None:
        _ensure_capabilities(classify_model, provider, {"cover_classify"}, image=use_cover)
    if vision_model is not None:
        _ensure_capabilities(vision_model, provider, {"video_understanding"}, image=use_cover)

    filled: list[str] = []
    if _fill_assignment(db, "text_analysis", text_model):
        filled.append("text_analysis")
    if classify_model is not None and _fill_assignment(db, "cover_classify", classify_model):
        filled.append("cover_classify")
    if vision_model is not None and _fill_assignment(db, "video_understanding", vision_model):
        filled.append("video_understanding")

    _put(
        db,
        CLAIM_MARKER,
        json.dumps(
            {
                "provider_id": provider.id,
                "models": [m.model_key for m in (text_model, classify_model, vision_model) if m is not None],
                "claimed_at": datetime.utcnow().isoformat() + "Z",
            },
            ensure_ascii=False,
        ),
    )
    _drop(db, _LEGACY_LLM_KEYS + _LEGACY_SELECTION_KEYS)
    db.commit()
    logger.info("旧版 LLM 配置已认领：provider=%s models=%s", provider.id, sorted({m.model_key for m in (text_model, classify_model, vision_model) if m is not None}))
    return LegacyClaimResult(
        "claimed",
        "旧版模型配置已导入「模型接入」；请在模型接入页完成能力检测",
        provider_id=provider.id,
        model_ids=tuple(sorted({m.id for m in (text_model, classify_model, vision_model) if m is not None})),
        assignments=tuple(filled),
    )


def legacy_migration_status(db: Session) -> dict:
    """供模型接入页展示认领状态；绝不返回密钥或密文。"""
    rows = _rows(db)
    pending: list[str] = []
    for row in db.scalars(
        select(ModelAssignment).where(ModelAssignment.legacy_pending_validation.is_(True))
    ).all():
        work_model = db.get(AiModel, row.primary_model_id or row.fallback_model_id or 0)
        still_pending = work_model is not None and any(
            isinstance(cap, dict) and cap.get("source") == "legacy"
            for cap in (work_model.capabilities or {}).values()
        )
        if still_pending:
            pending.append(row.work_type)
    marker = rows.get(CLAIM_MARKER) or rows.get(IMPORT_MARKER)
    has_legacy = any(rows.get(key) for key in _LEGACY_LLM_KEYS)
    needs_key = bool(rows.get("llm_base_url")) and not rows.get("llm_api_key") and not settings.llm_api_key
    if needs_key:
        message = "检测到旧版模型配置，但缺少访问密钥；请重新添加供应商并分配模型"
    elif pending:
        message = "旧版配置已认领，仍有模型待检测"
    elif marker:
        message = "旧版配置已认领"
    elif has_legacy:
        message = "检测到尚未认领的旧版模型配置；若缺少密钥，请重新添加供应商"
    else:
        message = "未发现旧版 llm_* 配置"
    return {
        "detected": has_legacy,
        "claimed": bool(marker),
        "needs_api_key": needs_key,
        "pending_validation": pending,
        "message": message,
    }


def bootstrap_legacy_llm() -> LegacyClaimResult | None:
    """启动时执行一次认领；失败只记录，不阻止服务启动。

    ``needs_api_key``/``needs_model`` 会保留旧 KV 供用户补齐后再试；真正的业务调用
    仍会因为没有 assignment 而失败并给出“去模型接入配置”的可操作错误。
    """
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        return claim_legacy_llm_settings(db)
    except Exception:  # noqa: BLE001 认领是兼容迁移，不应让服务无法启动
        db.rollback()
        logger.exception("旧版 LLM 配置认领失败")
        return None
    finally:
        db.close()
