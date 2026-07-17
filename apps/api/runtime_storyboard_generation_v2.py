from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agentflow_studio.model_gateway.errors import ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest, load_provider_registry
from apps.api.runtime_errors import RuntimeApiError
from apps.api.runtime_llm_enhancement_dispatch import dispatch_llm_with_fallback
from apps.api.runtime_models import PromptOptimizationRequest, StoryboardBreakdownRequest
from apps.api.runtime_storyboard_contract_v2 import (
    StoryboardContractError,
    validate_storyboard_set,
    validated_generation,
    validated_resolution,
    validated_verifier_result,
)
from apps.api.runtime_storyboard_json_v2 import StoryboardJsonError, json_object_from_provider_text
from apps.api.runtime_storyboard_entity_resolver import materialize_verified_shots
from apps.api.runtime_storyboard_knowledge import storyboard_knowledge_context, storyboard_llm_request
from apps.api.runtime_storyboard_verification_prompt import (
    entity_resolution_prompt,
    generation_prompt,
    shot_verification_prompt,
)


PIPELINE_ID = "provider_verified_v2"


class ProviderCallSession:
    def __init__(
        self,
        registry: Any,
        llm_request: PromptOptimizationRequest,
        output_dir: Path,
        *,
        max_calls: int,
    ) -> None:
        self.registry = registry
        self.llm_request = llm_request
        self.output_dir = output_dir
        self.max_calls = max_calls
        self.call_count = 0
        self.cache_hits = 0
        self._cache: dict[str, str] = {}

    def call(self, prompt: str, *, stage: str, timeout_sec: float) -> str:
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if cache_key in self._cache:
            self.cache_hits += 1
            return self._cache[cache_key]
        if self.call_count >= self.max_calls:
            raise _pipeline_error(
                "provider_output_invalid",
                stage="call_budget",
                message="分镜验证所需调用超过本次安全预算，请减少镜头数量后重试。",
                details={"max_calls": self.max_calls},
            )
        self.call_count += 1
        request = ProviderDispatchRequest(
            prompt=prompt,
            output_dir=self.output_dir / stage,
            task_type=f"storyboard_{stage}",
            timeout_sec=timeout_sec,
        )
        try:
            result = dispatch_llm_with_fallback(self.registry, self.llm_request, request)
        except ModelGatewayError as exc:
            raise _pipeline_error(
                "provider_unavailable",
                stage=stage,
                message="LLM 服务暂时不可用，未生成本地替代分镜。请检查服务后重试。",
                status_code=503,
                retryable=True,
                details={"attempted_calls": self.call_count},
            ) from exc
        text = str(result.get("text") or "").strip() if isinstance(result, dict) else ""
        if not text:
            raise _pipeline_error(
                "provider_output_invalid",
                stage=stage,
                message="LLM 返回了空结果，未生成本地替代分镜。",
                details={"attempted_calls": self.call_count},
            )
        self._cache[cache_key] = text
        return text

    def summary(self) -> dict[str, Any]:
        return {
            "call_count": self.call_count,
            "cache_hits": self.cache_hits,
            "max_calls": self.max_calls,
            "max_concurrency": 1,
            "raw_provider_response_stored": False,
        }


def build_provider_verified_storyboard_v2(
    request: StoryboardBreakdownRequest,
    output_dir: Path,
    *,
    gate: dict[str, str],
) -> dict[str, Any]:
    if str(gate.get("status") or "") == "blocked":
        raise _pipeline_error(
            "provider_gate_closed",
            stage="generation",
            message="远程 LLM 能力未启用，严格分镜流程不会生成本地替代结果。",
            status_code=409,
            user_action="启用 LLM gate 后重新拆分分镜。",
        )
    try:
        registry = load_provider_registry()
    except ModelGatewayError as exc:
        raise _pipeline_error(
            "provider_unavailable",
            stage="provider_setup",
            message="LLM 服务配置不可用，未生成本地替代分镜。",
            status_code=503,
            retryable=True,
        ) from exc
    params = request.node_parameters or {}
    max_calls = _bounded_int(params.get("storyboard_v2_max_calls"), default=34, minimum=4, maximum=82)
    session = ProviderCallSession(registry, storyboard_llm_request(request), output_dir, max_calls=max_calls)
    knowledge = storyboard_knowledge_context(request)
    prompt = generation_prompt(
        script_text=request.script_text,
        target_platform=request.target_platform,
        style=request.style,
        shot_count_hint=request.shot_count_hint,
        knowledge_lines=_knowledge_lines(knowledge),
    )
    try:
        generation_text = session.call(prompt, stage="generation", timeout_sec=90.0)
        generated_shots = validated_generation(json_object_from_provider_text(generation_text), request.script_text)
    except (StoryboardContractError, StoryboardJsonError) as exc:
        raise _contract_pipeline_error("provider_output_invalid", "generation_contract", exc) from exc
    if session.call_count + len(generated_shots) + 1 > session.max_calls:
        raise _pipeline_error(
            "provider_output_invalid",
            stage="call_budget",
            message="LLM 返回的镜头数量超过逐镜核查预算，未接受该结果。",
            details={"shot_count": len(generated_shots), "max_calls": session.max_calls},
        )

    verified_shots: list[dict[str, Any]] = []
    verification_records: list[dict[str, Any]] = []
    for shot in generated_shots:
        try:
            verification_text = session.call(
                shot_verification_prompt(script_text=request.script_text, shot=shot),
                stage=f"verify_{int(shot['index']):02d}",
                timeout_sec=45.0,
            )
            status, verified_shot, reasons = validated_verifier_result(
                json_object_from_provider_text(verification_text),
                shot,
                request.script_text,
            )
        except (StoryboardContractError, StoryboardJsonError) as exc:
            raise _contract_pipeline_error("verification_failed", "shot_verification_contract", exc) from exc
        record = {"shot_id": shot["shot_id"], "status": status, "reason_codes": reasons}
        verification_records.append(record)
        if status == "requires_review":
            raise _pipeline_error(
                "manual_review_required",
                stage="shot_verification",
                message="逐镜核查发现无法自动确定的内容，本次不创建分镜节点。",
                status_code=409,
                details={"shot_id": shot["shot_id"], "reason_codes": reasons},
            )
        if status == "rejected":
            raise _pipeline_error(
                "verification_failed",
                stage="shot_verification",
                message="逐镜核查拒绝了生成结果，本次不创建分镜节点。",
                details={"shot_id": shot["shot_id"], "reason_codes": reasons},
            )
        if verified_shot.get("unsupported_additions"):
            raise _pipeline_error(
                "verification_failed",
                stage="unsupported_additions",
                message="核查后的分镜仍包含无剧本证据的新增内容，本次不接受该结果。",
                details={"shot_id": shot["shot_id"]},
            )
        verified_shots.append(verified_shot)

    try:
        validate_storyboard_set(verified_shots)
    except StoryboardContractError as exc:
        raise _contract_pipeline_error("verification_failed", "verified_storyboard_contract", exc) from exc

    mentions = [mention for shot in verified_shots for mention in shot.get("asset_mentions", [])]
    entities: list[dict[str, Any]] = []
    if mentions:
        try:
            resolution_text = session.call(
                entity_resolution_prompt(script_text=request.script_text, shots=verified_shots),
                stage="entity_resolution",
                timeout_sec=60.0,
            )
            entities, review_items = validated_resolution(
                json_object_from_provider_text(resolution_text),
                verified_shots,
                request.script_text,
            )
        except (StoryboardContractError, StoryboardJsonError) as exc:
            raise _contract_pipeline_error("verification_failed", "entity_resolution_contract", exc) from exc
        if review_items:
            raise _pipeline_error(
                "manual_review_required",
                stage="entity_resolution",
                message="全局实体解析存在歧义，本次不创建分镜节点。",
                status_code=409,
                details={"review_items": review_items},
            )

    shots = materialize_verified_shots(verified_shots, entities, verification_records)
    return {
        "shots": shots,
        "provider_calls_started": session.call_count > 0,
        "pipeline": PIPELINE_ID,
        "verification_summary": {
            "status": "verified",
            "shot_count": len(shots),
            "accepted_count": sum(item["status"] == "accepted" for item in verification_records),
            "corrected_count": sum(item["status"] == "corrected" for item in verification_records),
            "entity_count": len(entities),
            "unresolved_count": 0,
            "semantic_fallback_used": False,
            "provider_call_summary": session.summary(),
            "idempotency_key": _idempotency_key(request),
        },
    }


def _knowledge_lines(context: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for rule in context.get("knowledge_rules", []) if isinstance(context.get("knowledge_rules"), list) else []:
        if not isinstance(rule, dict):
            continue
        transform = rule.get("prompt_transform")
        guidance = str(transform.get("guidance") or "").strip() if isinstance(transform, dict) else ""
        if guidance:
            result.append(f"- {guidance}")
        if len(result) >= 8:
            break
    return result


def _contract_pipeline_error(state: str, stage: str, exc: ValueError) -> RuntimeApiError:
    return _pipeline_error(
        state,
        stage=stage,
        message="LLM 返回结果未通过结构与证据合同，未生成本地替代分镜。",
        details={
            "reason": str(getattr(exc, "reason", str(exc))),
            **dict(getattr(exc, "details", {}) or {}),
        },
    )


def _pipeline_error(
    state: str,
    *,
    stage: str,
    message: str,
    status_code: int = 422,
    user_action: str = "修正输入或服务状态后重新拆分分镜。",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> RuntimeApiError:
    return RuntimeApiError(
        state,
        message,
        stage=stage,
        status_code=status_code,
        user_action=user_action,
        retryable=retryable,
        details={"pipeline": PIPELINE_ID, "pipeline_state": state, **(details or {})},
    )


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _idempotency_key(request: StoryboardBreakdownRequest) -> str:
    material = "\x1f".join(
        [request.script_text, request.target_platform, request.style, str(request.shot_count_hint or "")]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


__all__ = ("PIPELINE_ID", "ProviderCallSession", "build_provider_verified_storyboard_v2")
