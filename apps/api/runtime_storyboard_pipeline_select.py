from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agentflow_studio.model_gateway.errors import ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest
from apps.api.runtime_llm_enhancement_dispatch import dispatch_llm_with_fallback
from apps.api.runtime_errors import RuntimeApiError
from apps.api.runtime_models import StoryboardBreakdownRequest
from apps.api.runtime_storyboard_generation_v2 import PIPELINE_ID, build_provider_verified_storyboard_v2
from apps.api.runtime_storyboard_knowledge import storyboard_instruction, storyboard_llm_request
from apps.api.runtime_storyboard_local import local_storyboard_shots


def select_storyboard_pipeline(
    request: StoryboardBreakdownRequest,
    output_dir: Path,
    *,
    gate: dict[str, str],
    storyboard_knowledge: dict[str, Any],
    registry_loader: Callable[[], Any],
    provider_parser: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    if _provider_verified_v2_requested(request):
        verified = build_provider_verified_storyboard_v2(request, output_dir, gate=gate)
        shots = verified["shots"]
        if not shots:
            raise RuntimeApiError(
                "verification_failed",
                "严格分镜流程没有生成可接受镜头，本次不创建本地替代结果。",
                stage="verified_output",
                details={"pipeline": PIPELINE_ID, "pipeline_state": "verification_failed"},
            )
        return {
            "shots": shots,
            "status": "provider_verified_v2",
            "pipeline": verified["pipeline"],
            "verification_summary": verified["verification_summary"],
            "provider_calls_started": bool(verified["provider_calls_started"]),
            "fallback_reason": None,
            "discard_reason": None,
        }

    provider_calls_started = False
    shots: list[dict[str, Any]] | None = None
    status = "local_fallback"
    discard_reason = None
    fallback_reason = "llm_gate_blocked" if gate["status"] == "blocked" else None
    if gate["status"] != "blocked":
        try:
            registry = registry_loader()
            dispatch_request = ProviderDispatchRequest(
                prompt=storyboard_instruction(request, storyboard_knowledge),
                output_dir=output_dir,
                task_type="storyboard_breakdown",
            )
            provider_result = dispatch_llm_with_fallback(registry, storyboard_llm_request(request), dispatch_request)
            provider_calls_started = bool(provider_result.get("provider_calls_started", True))
            shots = provider_parser(str(provider_result.get("text") or ""), source_script_text=request.script_text)
            status = "provider_structured"
        except ValueError as exc:
            discard_reason = _safe_reason(str(exc))
            shots = None
            fallback_reason = "provider_output_discarded" if provider_calls_started else "provider_output_unavailable"
        except ModelGatewayError as exc:
            discard_reason = _safe_reason(str(exc))
            shots = None
            provider_calls_started = False
            fallback_reason = "provider_call_failed"
    if not shots:
        shots = local_storyboard_shots(request.script_text, request.shot_count_hint)
    return {
        "shots": shots,
        "status": status,
        "pipeline": "legacy_storyboard_v1",
        "verification_summary": {
            "status": "not_run",
            "semantic_fallback_used": status == "local_fallback",
        },
        "provider_calls_started": provider_calls_started,
        "fallback_reason": fallback_reason,
        "discard_reason": discard_reason,
    }


def _provider_verified_v2_requested(request: StoryboardBreakdownRequest) -> bool:
    params = request.node_parameters or {}
    return str(params.get("storyboard_pipeline") or "").strip() == PIPELINE_ID


def _safe_reason(value: str) -> str:
    lowered = value.lower()
    if any(term in lowered for term in ("api", "key", "secret", "token", "authorization", "cookie")):
        return "llm provider configuration is not ready"
    return " ".join(value.split())[:160] or "llm provider is not ready"


__all__ = ("select_storyboard_pipeline",)
