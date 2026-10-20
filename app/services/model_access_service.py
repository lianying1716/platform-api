"""统一模型调用网关：工作分配解析、能力校验、主备切换与调用审计。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_credential, redact_text
from app.db import SessionLocal
from app.models import AiModel, AiProvider, ModelAssignment, ModelCallAttempt
from app.modules import model_protocols


WORK_TYPES = ("text_analysis", "cover_classify", "video_understanding")
_WORK_TYPE_LABELS = {
    "text_analysis": "文字分析",
    "cover_classify": "封面判类",
    "video_understanding": "视频理解",
}
_CAPABILITY_REQUIREMENTS = {
    "text_analysis": ("text", "json"),
    "cover_classify": ("text", "json"),
    "video_understanding": ("json", "video"),
}

class RoutingStage(TypedDict):
    used_model_id: int | None
    used_model_key: str
    used_role: str
    fallback_used: bool
    primary_model_id: int | None
    primary_model_key: str
    fallback_model_id: int | None
    fallback_model_key: str
    primary_error_class: str
    primary_error_summary: str
    fallback_error_class: str
    fallback_error_summary: str
    attempt_group_id: str


@dataclass
class ModelCallResult:
    data: dict | str
    used_model_id: int
    used_model_key: str
    used_role: str
    fallback_used: bool
    primary_model_id: int | None
    primary_model_key: str
    fallback_model_id: int | None
    fallback_model_key: str
    primary_error_class: str
    primary_error_message: str
    fallback_error_class: str
    fallback_error_message: str
    attempt_group_id: str
    usage: dict

    def to_routing(self) -> RoutingStage:
        return RoutingStage(
            used_model_id=self.used_model_id,
            used_model_key=self.used_model_key,
            used_role=self.used_role,
            fallback_used=self.fallback_used,
            primary_model_id=self.primary_model_id,
            primary_model_key=self.primary_model_key,
            fallback_model_id=self.fallback_model_id,
            fallback_model_key=self.fallback_model_key,
            primary_error_class=self.primary_error_class,
            primary_error_summary=_summary(self.primary_error_message),
            fallback_error_class=self.fallback_error_class,
            fallback_error_summary=_summary(self.fallback_error_message),
            attempt_group_id=self.attempt_group_id,
        )


class ModelCallError(Exception):
    """主备都拿不到合格结果时的结构化错误。"""

    def __init__(
        self,
        message: str,
        *,
        attempt_group_id: str,
        primary_model_id: int | None,
        primary_model_key: str,
        fallback_model_id: int | None,
        fallback_model_key: str,
        primary_error_class: str,
        primary_error_message: str,
        fallback_error_class: str,
        fallback_error_message: str,
    ):
        super().__init__(message)
        self.attempt_group_id = attempt_group_id
        self.primary_model_id = primary_model_id
        self.primary_model_key = primary_model_key
        self.fallback_model_id = fallback_model_id
        self.fallback_model_key = fallback_model_key
        self.primary_error_class = primary_error_class
        self.primary_error_message = primary_error_message
        self.fallback_error_class = fallback_error_class
        self.fallback_error_message = fallback_error_message

    def to_routing(self) -> RoutingStage:
        return RoutingStage(
            used_model_id=None,
            used_model_key="",
            used_role="",
            fallback_used=False,
            primary_model_id=self.primary_model_id,
            primary_model_key=self.primary_model_key,
            fallback_model_id=self.fallback_model_id,
            fallback_model_key=self.fallback_model_key,
            primary_error_class=self.primary_error_class,
            primary_error_summary=_summary(self.primary_error_message),
            fallback_error_class=self.fallback_error_class,
            fallback_error_summary=_summary(self.fallback_error_message),
            attempt_group_id=self.attempt_group_id,
        )


def _summary(message: str, limit: int = 512) -> str:
    raw = redact_text(message or "")
    if len(raw) <= limit:
        return raw
    return raw[:limit]


def _provider_api_key(provider: AiProvider | None) -> str:
    if provider is None or not provider.api_key_encrypted:
        return ""
    try:
        return str(decrypt_credential(provider.api_key_encrypted).get("api_key") or "")
    except Exception:  # noqa: BLE001 旧密文不可读时仍按通用规则脱敏
        return ""


def _redact_secrets(text: str, *, secret: str = "") -> str:
    return redact_text(text or "", extra_secrets=(secret,) if secret else ())


def capabilities_of(model: AiModel, provider: AiProvider | None = None) -> dict:
    """按 config_revision 与生效协议判定能力是否有效；stale/缺版本一律视为不可用。

    协议也是能力的前提：模型级协议被改动后，旧探测结论不再成立，必须重新检测。
    ``legacy`` 表示旧版 llm_* 配置已被认领但尚未实测：它可以让存量实例继续运行，
    但在供应商连接或协议变化后必须转为 stale，绝不能用旧结论静默继续调用。
    """
    if model is None or provider is None:
        return {}
    revision = int(provider.config_revision or 1)
    protocol = model_protocols.resolve_protocol(model, provider)
    raw = model.capabilities if isinstance(model.capabilities, dict) else {}
    out: dict[str, dict] = {}
    trusted_sources = ("probe", "manual_override", "legacy")
    for name, value in raw.items():
        if not isinstance(value, dict):
            continue
        item = dict(value)
        source = str(item.get("source") or "")
        same_revision = int(item.get("config_revision") or 0) == revision
        # 旧记录可能没写 protocol：缺省视为与当前协议一致，避免升级即全量失效。
        same_protocol = str(item.get("protocol") or protocol) == protocol
        if source not in trusted_sources or not (same_revision and same_protocol):
            item["value"] = False
            item["source"] = "stale" if source in trusted_sources else source
            if source in trusted_sources:
                item["detail"] = (
                    "模型协议已变更，需重新检测"
                    if same_revision and not same_protocol
                    else "供应商连接配置已变更，需重新检测"
                )
        out[name] = item
    return out


def _model_usable(model: AiModel | None, provider: AiProvider | None, work_type: str, *, image_required: bool = False) -> bool:
    if model is None or provider is None or not model.enabled or not provider.enabled:
        return False
    caps = capabilities_of(model, provider)
    needed = list(_CAPABILITY_REQUIREMENTS.get(work_type, ()))
    if image_required and "image" not in needed:
        needed.append("image")
    return all(bool((caps.get(name) or {}).get("value")) for name in needed)


def resolve_assignment(db: Session, work_type: str) -> ModelAssignment | None:
    if work_type not in WORK_TYPES:
        raise ValueError(f"未知工作类型: {work_type}")
    return db.scalar(select(ModelAssignment).where(ModelAssignment.work_type == work_type))


def assignment_configured(db: Session, work_type: str) -> bool:
    """该工作是否已有主模型或备用模型分配。"""
    row = resolve_assignment(db, work_type)
    return bool(row and (row.primary_model_id is not None or row.fallback_model_id is not None))


def model_access_setup_state(db: Session) -> dict:
    """A provider-agnostic next-step state shared by API, tests and the UI."""
    providers = list(db.scalars(select(AiProvider).order_by(AiProvider.id)).all())
    enabled_providers = [row for row in providers if row.enabled]
    models = list(db.scalars(select(AiModel).order_by(AiModel.id)).all())
    providers_by_id = {row.id: row for row in providers}
    enabled_models = [
        row
        for row in models
        if row.enabled and (providers_by_id.get(row.provider_id) is not None) and providers_by_id[row.provider_id].enabled
    ]
    configured_work = [work for work in WORK_TYPES if assignment_configured(db, work)]

    if not providers:
        stage, action, title = "no_provider", "add_provider", "添加模型供应商"
    elif not enabled_providers:
        stage, action, title = "provider_disabled", "enable_provider", "启用模型供应商"
    elif not enabled_models:
        stage, action, title = "no_models", "import_models", "导入并检测模型"
    elif not configured_work:
        stage, action, title = "no_assignment", "assign_work", "分配工作模型"
    else:
        stage, action, title = "ready", "verify", "模型接入已配置"

    return {
        "stage": stage,
        "action": action,
        "title": title,
        "ready": stage == "ready",
        "provider_count": len(providers),
        "enabled_provider_count": len(enabled_providers),
        "model_count": len(models),
        "enabled_model_count": len(enabled_models),
        "configured_work_types": configured_work,
        "missing_work_types": [work for work in WORK_TYPES if work not in configured_work],
    }


def resolved_transport(db: Session, work_type: str) -> str:
    """返回当前分配的默认视频传输方式，供调用方明确记录来源。

    真正的请求构造仍由 ``ModelAccessService._invoke`` 按生效协议决定；这里只用于
    视频理解入参中的兼容标签，避免再从 ``process_provider`` 猜供应商。
    """
    row = resolve_assignment(db, work_type)
    model_id = (row.primary_model_id or row.fallback_model_id) if row else None
    model = db.get(AiModel, model_id) if model_id else None
    provider = db.get(AiProvider, model.provider_id) if model is not None else None
    if model is None or provider is None:
        return "openai_video_url"
    return "gemini_inline" if model_protocols.resolve_protocol(model, provider) == "gemini" else "openai_video_url"


class ModelAccessService:
    """模型网关。attempt 审计永远使用独立 session，不参与业务事务提交边界。"""

    def __init__(self, *, attempt_session_factory: Callable[[], Session] | None = None):
        self._attempt_session_factory = attempt_session_factory or SessionLocal

    async def call_with_fallback(
        self,
        db: Session,
        *,
        work_type: str,
        operation: str,
        request: dict,
        parse_result: Callable[[dict | str], dict | str],
        validate_result: Callable[[dict | str], None] | None = None,
        asset_id: int | None = None,
        job_id: int | None = None,
        image_required: bool = False,
        video_request: bool = False,
    ) -> ModelCallResult:
        if work_type not in WORK_TYPES:
            raise ValueError(f"未知工作类型: {work_type}")
        assignment = resolve_assignment(db, work_type)
        primary = db.get(AiModel, assignment.primary_model_id) if assignment and assignment.primary_model_id else None
        fallback = db.get(AiModel, assignment.fallback_model_id) if assignment and assignment.fallback_model_id else None
        group_id = f"mg_{uuid.uuid4().hex[:20]}"

        primary_provider = db.get(AiProvider, primary.provider_id) if primary else None
        fallback_provider = db.get(AiProvider, fallback.provider_id) if fallback else None
        primary_ok = _model_usable(primary, primary_provider, work_type, image_required=image_required)
        fallback_ok = _model_usable(fallback, fallback_provider, work_type, image_required=image_required)

        primary_class = ""
        primary_message = ""
        if primary is not None and not primary.enabled:
            primary_class, primary_message = "disabled", "主模型已停用"
        elif primary_provider is not None and not primary_provider.enabled:
            primary_class, primary_message = "disabled", "主模型供应商已停用"
        elif primary is not None and not primary_ok:
            primary_class, primary_message = "unsupported_capability", "主模型能力不满足当前工作"
        fallback_class = ""
        fallback_message = ""
        if fallback is not None and not fallback.enabled:
            fallback_class, fallback_message = "disabled", "备用模型已停用"
        elif fallback_provider is not None and not fallback_provider.enabled:
            fallback_class, fallback_message = "disabled", "备用模型供应商已停用"
        elif fallback is not None and not fallback_ok:
            fallback_class, fallback_message = "unsupported_capability", "备用模型能力不满足当前工作"

        if not primary_ok and not fallback_ok:
            if primary is None and fallback is None:
                raise ModelCallError(
                    f"{_WORK_TYPE_LABELS.get(work_type, work_type)}没有配置可用模型",
                    attempt_group_id=group_id,
                    primary_model_id=None,
                    primary_model_key="",
                    fallback_model_id=None,
                    fallback_model_key="",
                    primary_error_class="not_configured",
                    primary_error_message="该工作尚未配置模型",
                    fallback_error_class="",
                    fallback_error_message="",
                )
            raise ModelCallError(
                f"{_WORK_TYPE_LABELS.get(work_type, work_type)}没有可用模型",
                attempt_group_id=group_id,
                primary_model_id=primary.id if primary else None,
                primary_model_key=primary.model_key if primary else "",
                fallback_model_id=fallback.id if fallback else None,
                fallback_model_key=fallback.model_key if fallback else "",
                primary_error_class=primary_class or "unsupported_capability",
                primary_error_message=primary_message or "主模型不可用",
                fallback_error_class=fallback_class,
                fallback_error_message=fallback_message,
            )

        first_model, first_provider, first_role = (
            (primary, primary_provider, "primary") if primary_ok else (fallback, fallback_provider, "fallback")
        )
        if first_role == "fallback" and primary is None:
            primary_class = primary_class or "unsupported_capability"
            primary_message = primary_message or "主模型未配置或已删除"
        second_model, second_provider = (
            (fallback, fallback_provider)
            if first_role == "primary" and fallback_ok
            else (None, None)
        )

        first_attempt_id = self._start_attempt(
            group_id=group_id,
            work_type=work_type,
            operation=operation,
            role=first_role,
            model=first_model,
            provider=first_provider,
            asset_id=asset_id,
            job_id=job_id,
        )
        try:
            parsed, usage = await self._invoke(
                first_provider,
                first_model,
                request,
                parse_result,
                validate_result,
                video_request=video_request,
            )
            self._finish_attempt(first_attempt_id, status="ok", usage=usage)
            return ModelCallResult(
                data=parsed,
                used_model_id=first_model.id,
                used_model_key=first_model.model_key,
                used_role=first_role,
                fallback_used=first_role == "fallback",
                primary_model_id=primary.id if primary else None,
                primary_model_key=primary.model_key if primary else "",
                fallback_model_id=fallback.id if fallback else None,
                fallback_model_key=fallback.model_key if fallback else "",
                primary_error_class=primary_class,
                primary_error_message=primary_message,
                fallback_error_class="",
                fallback_error_message="",
                attempt_group_id=group_id,
                usage=usage,
            )
        except Exception as first_error:  # noqa: BLE001 任何拿不到合格结果的失败都切一次
            first_class = model_protocols.classify_error(first_error)
            first_message = _redact_secrets(
                str(first_error), secret=_provider_api_key(first_provider)
            )[:8000]
            self._finish_attempt(
                first_attempt_id,
                status="error",
                error_class=first_class,
                error_message=first_message,
                http_status=model_protocols.http_status_of(first_error),
            )
            if first_role == "primary":
                primary_class, primary_message = first_class, first_message
            else:
                fallback_class, fallback_message = first_class, first_message

        if second_model is None or second_provider is None:
            raise ModelCallError(
                f"{_WORK_TYPE_LABELS.get(work_type, work_type)}调用失败，且没有可用备用模型",
                attempt_group_id=group_id,
                primary_model_id=primary.id if primary else None,
                primary_model_key=primary.model_key if primary else "",
                fallback_model_id=fallback.id if fallback else None,
                fallback_model_key=fallback.model_key if fallback else "",
                primary_error_class=primary_class,
                primary_error_message=primary_message,
                fallback_error_class=fallback_class,
                fallback_error_message=fallback_message,
            )

        second_attempt_id = self._start_attempt(
            group_id=group_id,
            work_type=work_type,
            operation=operation,
            role="fallback",
            model=second_model,
            provider=second_provider,
            asset_id=asset_id,
            job_id=job_id,
        )
        try:
            parsed, usage = await self._invoke(
                second_provider,
                second_model,
                request,
                parse_result,
                validate_result,
                video_request=video_request,
            )
            self._finish_attempt(second_attempt_id, status="ok", usage=usage)
            return ModelCallResult(
                data=parsed,
                used_model_id=second_model.id,
                used_model_key=second_model.model_key,
                used_role="fallback",
                fallback_used=True,
                primary_model_id=primary.id if primary else None,
                primary_model_key=primary.model_key if primary else "",
                fallback_model_id=fallback.id if fallback else None,
                fallback_model_key=fallback.model_key if fallback else "",
                primary_error_class=primary_class,
                primary_error_message=primary_message,
                fallback_error_class="",
                fallback_error_message="",
                attempt_group_id=group_id,
                usage=usage,
            )
        except Exception as second_error:  # noqa: BLE001
            fallback_class = model_protocols.classify_error(second_error)
            fallback_message = _redact_secrets(
                str(second_error), secret=_provider_api_key(second_provider)
            )[:8000]
            self._finish_attempt(
                second_attempt_id,
                status="error",
                error_class=fallback_class,
                error_message=fallback_message,
                http_status=model_protocols.http_status_of(second_error),
            )
            raise ModelCallError(
                f"{_WORK_TYPE_LABELS.get(work_type, work_type)}主模型和备用模型都失败",
                attempt_group_id=group_id,
                primary_model_id=primary.id if primary else None,
                primary_model_key=primary.model_key if primary else "",
                fallback_model_id=fallback.id if fallback else None,
                fallback_model_key=fallback.model_key if fallback else "",
                primary_error_class=primary_class,
                primary_error_message=primary_message,
                fallback_error_class=fallback_class,
                fallback_error_message=fallback_message,
            ) from second_error

    async def _invoke(
        self,
        provider: AiProvider,
        model: AiModel,
        request: dict,
        parse_result: Callable[[dict | str], dict | str],
        validate_result: Callable[[dict | str], None] | None,
        *,
        video_request: bool = False,
    ) -> tuple[dict | str, dict]:
        api_key = ""
        if provider.api_key_encrypted:
            api_key = str(decrypt_credential(provider.api_key_encrypted).get("api_key") or "")
        # 生效协议：模型级覆盖优先，未设置则继承供应商。
        effective_protocol = model_protocols.resolve_protocol(model, provider)
        if video_request:
            built = model_protocols.video_request(
                provider,
                model.model_key,
                request["video_b64"],
                request["messages"][-1]["content"],
                transport=request.get("transport")
                or model_protocols.normalize_transport(effective_protocol, None),
                api_key=api_key,
                mime=request.get("mime") or "video/mp4",
                protocol=effective_protocol,
            )
        else:
            built = model_protocols.chat_request(
                provider, model.model_key, request, api_key=api_key, protocol=effective_protocol
            )
        raw = await model_protocols.send_request(built, timeout=float(request.get("timeout", 90)))
        response_protocol = (
            "gemini"
            if video_request and (request.get("transport") or "") == "gemini_inline"
            else effective_protocol
        )
        parsed_response = model_protocols.parse_chat_response(response_protocol, raw)
        candidate = parse_result(parsed_response["text"])
        if validate_result is not None:
            validate_result(candidate)
        usage = parsed_response.get("usage") if isinstance(parsed_response.get("usage"), dict) else {}
        return candidate, usage

    def _start_attempt(
        self,
        *,
        group_id: str,
        work_type: str,
        operation: str,
        role: str,
        model: AiModel,
        provider: AiProvider,
        asset_id: int | None,
        job_id: int | None,
    ) -> int:
        session = self._attempt_session_factory()
        try:
            row = ModelCallAttempt(
                attempt_group_id=group_id,
                work_type=work_type,
                operation=operation,
                asset_id=asset_id,
                job_id=job_id,
                role=role,
                provider_id=provider.id,
                provider_name=provider.name,
                model_id=model.id,
                model_key=model.model_key,
                status="running",
                request_meta={
                    "protocol": model_protocols.resolve_protocol(model, provider),
                    "role": role,
                },
            )
            session.add(row)
            session.commit()
            return int(row.id)
        finally:
            session.close()

    def _finish_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        error_class: str = "",
        error_message: str = "",
        http_status: int | None = None,
        usage: dict | None = None,
    ) -> None:
        session = self._attempt_session_factory()
        try:
            row = session.get(ModelCallAttempt, attempt_id)
            if row is None:
                return
            row.status = status
            row.error_class = error_class
            row.error_message = _redact_secrets(error_message)[:8000]
            row.http_status = http_status
            row.usage = usage or {}
            row.finished_at = datetime.utcnow()
            session.commit()
        finally:
            session.close()
