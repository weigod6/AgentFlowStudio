from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agentflow_studio.model_gateway.errors import ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest, load_provider_registry
from apps.api.runtime_file_logging import runtime_file_event
from apps.api.runtime_llm_enhancement_constants import PROMPT_OPTIMIZER_PROVIDER
from apps.api.runtime_llm_enhancement_dispatch import dispatch_llm_with_fallback
from apps.api.runtime_llm_enhancement_fallback import deterministic_chinese_fallback_prompt, salvage_prompt_from_llm_article
from apps.api.runtime_llm_enhancement_gate import (
    llm_provider_gate,
    prompt_optimization_mode,
    provider_name,
    provider_text_requested,
)
from apps.api.runtime_llm_enhancement_instructions import enhancement_instruction, strict_format_retry_instruction
from apps.api.runtime_llm_enhancement_safety import (
    provider_output_diagnostics,
    safe_reason,
    sanitize_enhanced_prompt,
    sections_from_canonical,
    validate_enhanced_prompt_specificity,
)
from apps.api.runtime_models import PromptOptimizationRequest
from apps.api.runtime_prompt_text import plain_prompt_from_sections, strip_user_prompt_section_headers
from apps.api.runtime_script_generation_body import (
    is_script_generation_request,
    is_script_surface_request,
    script_body_from_candidate,
    script_surface_body_from_candidate,
)


def maybe_enhance_prompt_with_llm(
    request: PromptOptimizationRequest,
    assembly: dict[str, Any],
    *,
    log_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    requested = provider_text_requested(request)
    optimization_mode = prompt_optimization_mode(request)
    log_base = _llm_log_context(request, log_context)
    _log_prompt_llm_step(
        "llm_decision",
        started,
        log_base,
        requested=requested,
        optimization_mode=optimization_mode,
        provider=provider_name(request) if requested else "not_requested",
    )
    base = {
        "requested": requested,
        "provider": provider_name(request) if requested else "not_requested",
        "model": "provider_configured" if requested else "not_requested",
        "optimization_mode": optimization_mode,
        "status": "not_requested",
        "provider_calls_started": False,
        "discard_reason": None,
        "timings_ms": {},
    }
    if not requested:
        _log_prompt_llm_step("llm_not_requested", started, log_base, optimization_mode=optimization_mode)
        return _with_total_elapsed(base, started)
    if llm_provider_gate()["status"] == "blocked":
        _log_prompt_llm_step(
            "llm_gate_blocked",
            started,
            log_base,
            level="WARNING",
            provider=provider_name(request),
            optimization_mode=optimization_mode,
            reason="remote_llm_gate_closed",
        )
        return _with_total_elapsed({**base, "status": "blocked", "discard_reason": "remote_llm_gate_closed"}, started)

    try:
        provider_started = time.perf_counter()
        _log_prompt_llm_step(
            "provider_call_start",
            provider_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
        )
        registry = load_provider_registry()
        dispatch_request = ProviderDispatchRequest(
            prompt=enhancement_instruction(request, assembly),
            output_dir=Path("."),
            task_type="prompt_enhancement",
        )
        result = dispatch_llm_with_fallback(registry, request, dispatch_request)
        enhanced = str(result.get("text") or "")
        base.update(provider_output_diagnostics(enhanced))
        base["timings_ms"]["provider_dispatch"] = _elapsed_ms(provider_started)
        _log_prompt_llm_step(
            "provider_call_done",
            provider_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
            provider_calls_started=True,
            provider_elapsed_ms=base["timings_ms"]["provider_dispatch"],
            provider_output_length=base.get("provider_output_length"),
            provider_error_markers=base.get("provider_error_markers"),
            missing_sections=base.get("missing_sections"),
        )
    except ModelGatewayError as exc:
        _log_prompt_llm_step(
            "provider_call_failed",
            provider_started if "provider_started" in locals() else started,
            log_base,
            level="ERROR",
            provider=provider_name(request),
            optimization_mode=optimization_mode,
            error=type(exc).__name__,
            reason=safe_reason(str(exc)),
        )
        return _with_total_elapsed({**base, "status": "discarded", "discard_reason": safe_reason(str(exc))}, started)

    retry_count = 0
    if is_script_generation_request(request):
        script_validate_started = time.perf_counter()
        _log_prompt_llm_step(
            "script_body_validate_start",
            script_validate_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
        )
        try:
            script_body = script_body_from_candidate(enhanced, request)
        except ValueError as exc:
            _log_prompt_llm_step(
                "script_body_validate_failed",
                script_validate_started,
                log_base,
                level="WARNING",
                provider=provider_name(request),
                optimization_mode=optimization_mode,
                reason=safe_reason(str(exc)),
            )
            return _with_total_elapsed({**base, "status": "discarded", "discard_reason": safe_reason(str(exc))}, started)
        _log_prompt_llm_step(
            "script_body_validate_done",
            script_validate_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
            script_body_status=script_body.get("status"),
            discard_reason=script_body.get("discard_reason"),
            fallback_used=script_body.get("fallback_used"),
        )
        payload = {
            **base,
            "status": "applied",
            "provider_calls_started": True,
            "guardrail_fallback_used": bool(script_body.get("fallback_used")),
            "discard_reason": script_body.get("discard_reason"),
            **_prompt_payload(str(script_body["script_body"])),
        }
        return _with_total_elapsed(payload, started)

    if is_script_surface_request(request):
        script_validate_started = time.perf_counter()
        _log_prompt_llm_step(
            "script_surface_validate_start",
            script_validate_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
        )
        try:
            script_body = script_surface_body_from_candidate(enhanced, request)
        except ValueError as exc:
            _log_prompt_llm_step(
                "script_surface_validate_failed",
                script_validate_started,
                log_base,
                level="WARNING",
                provider=provider_name(request),
                optimization_mode=optimization_mode,
                reason=safe_reason(str(exc)),
            )
            return _with_total_elapsed({**base, "status": "discarded", "discard_reason": safe_reason(str(exc))}, started)
        _log_prompt_llm_step(
            "script_surface_validate_done",
            script_validate_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
            script_body_status=script_body.get("status"),
            discard_reason=script_body.get("discard_reason"),
            fallback_used=script_body.get("fallback_used"),
        )
        payload = {
            **base,
            "status": "applied",
            "provider_calls_started": True,
            "guardrail_fallback_used": bool(script_body.get("fallback_used")),
            "discard_reason": script_body.get("discard_reason"),
            **_prompt_payload(str(script_body["script_body"])),
        }
        return _with_total_elapsed(payload, started)

    try:
        sanitize_started = time.perf_counter()
        _log_prompt_llm_step(
            "response_validate_start",
            sanitize_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
        )
        prompt = sanitize_enhanced_prompt(enhanced)
        base["timings_ms"]["sanitize"] = _elapsed_ms(sanitize_started)
        _log_prompt_llm_step(
            "response_validate_done",
            sanitize_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
            provider_output_length=base.get("provider_output_length"),
            missing_sections=base.get("missing_sections"),
        )
    except ValueError as exc:
        if str(exc) == "enhancement missing required sections":
            _log_prompt_llm_step(
                "response_validate_failed",
                sanitize_started,
                log_base,
                level="WARNING",
                provider=provider_name(request),
                optimization_mode=optimization_mode,
                reason=safe_reason(str(exc)),
                provider_output_length=base.get("provider_output_length"),
                missing_sections=base.get("missing_sections"),
            )
            if bool((request.node_parameters or {}).get("disable_provider_retry")):
                return _with_total_elapsed(
                    {
                        **base,
                        "status": "discarded",
                        "provider_calls_started": True,
                        "discard_reason": "enhancement_missing_required_sections_retry_disabled",
                        "format_retry_count": 0,
                    },
                    started,
                )
            retry_started = time.perf_counter()
            _log_prompt_llm_step(
                "retry_or_salvage_start",
                retry_started,
                log_base,
                provider=provider_name(request),
                optimization_mode=optimization_mode,
            )
            retry_result = _retry_or_salvage_enhancement(base, registry, request, assembly, enhanced, log_context=log_base)
            retry_result.setdefault("timings_ms", {}).update(base.get("timings_ms") or {})
            retry_result["timings_ms"]["retry_or_salvage"] = _elapsed_ms(retry_started)
            if retry_result.get("status") in {"applied", "continue"}:
                base["timings_ms"].update(retry_result.get("timings_ms") or {})
                prompt = str(retry_result["prompt"])
                retry_count = int(retry_result.get("format_retry_count") or 1)
                base["format_salvage_used"] = bool(retry_result.get("format_salvage_used"))
                _log_prompt_llm_step(
                    "retry_or_salvage_done",
                    retry_started,
                    log_base,
                    provider=provider_name(request),
                    optimization_mode=optimization_mode,
                    retry_count=retry_count,
                    format_salvage_used=bool(base.get("format_salvage_used")),
                    retry_or_salvage_ms=retry_result["timings_ms"]["retry_or_salvage"],
                )
            else:
                _log_prompt_llm_step(
                    "retry_or_salvage_failed",
                    retry_started,
                    log_base,
                    level="WARNING",
                    provider=provider_name(request),
                    optimization_mode=optimization_mode,
                    llm_status=retry_result.get("status"),
                    discard_reason=retry_result.get("discard_reason"),
                    retry_or_salvage_ms=retry_result.get("timings_ms", {}).get("retry_or_salvage"),
                )
                return _with_total_elapsed(retry_result, started)
        else:
            _log_prompt_llm_step(
                "response_discarded",
                sanitize_started,
                log_base,
                level="WARNING",
                provider=provider_name(request),
                optimization_mode=optimization_mode,
                reason=safe_reason(str(exc)),
            )
            return _with_total_elapsed(_discard_with_fallback(base, request, assembly, str(exc), retry_count=retry_count), started)
    try:
        specificity_started = time.perf_counter()
        _log_prompt_llm_step(
            "specificity_validate_start",
            specificity_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
        )
        validate_enhanced_prompt_specificity(prompt, request)
        _log_prompt_llm_step(
            "specificity_validate_done",
            specificity_started,
            log_base,
            provider=provider_name(request),
            optimization_mode=optimization_mode,
        )
    except ValueError as exc:
        fallback = deterministic_chinese_fallback_prompt(request, assembly)
        _log_prompt_llm_step(
            "guardrail_fallback_used",
            specificity_started if "specificity_started" in locals() else started,
            log_base,
            level="WARNING",
            provider=provider_name(request),
            optimization_mode=optimization_mode,
            reason=safe_reason(str(exc)),
        )
        return _with_total_elapsed({
            **base,
            "status": "applied",
            "provider_calls_started": True,
            "discard_reason": safe_reason(str(exc)),
            "guardrail_fallback_used": True,
            **_prompt_payload(fallback),
        }, started)

    _log_prompt_llm_step(
        "llm_applied",
        started,
        log_base,
        provider=provider_name(request),
        optimization_mode=optimization_mode,
        provider_calls_started=True,
        retry_count=retry_count,
        format_salvage_used=bool(base.get("format_salvage_used")),
    )
    return _with_total_elapsed({
        **base,
        "status": "applied",
        "provider_calls_started": True,
        "format_retry_count": retry_count,
        "format_salvage_used": bool(base.get("format_salvage_used")),
        **_prompt_payload(prompt),
    }, started)


def _retry_or_salvage_enhancement(
    base: dict[str, Any],
    registry: Any,
    request: PromptOptimizationRequest,
    assembly: dict[str, Any],
    enhanced: str,
    *,
    log_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        retry_started = time.perf_counter()
        _log_prompt_llm_step(
            "format_retry_provider_call_start",
            retry_started,
            _llm_log_context(request, log_context),
            provider=provider_name(request),
            optimization_mode=prompt_optimization_mode(request),
        )
        retry_request = ProviderDispatchRequest(
            prompt=strict_format_retry_instruction(request),
            output_dir=Path("."),
            task_type="prompt_enhancement_retry",
        )
        result = dispatch_llm_with_fallback(registry, request, retry_request)
        retried = str(result.get("text") or "")
        prompt = sanitize_enhanced_prompt(retried)
        _log_prompt_llm_step(
            "format_retry_provider_call_done",
            retry_started,
            _llm_log_context(request, log_context),
            provider=provider_name(request),
            optimization_mode=prompt_optimization_mode(request),
            retry_count=1,
        )
        return {"status": "continue", "prompt": prompt, "format_retry_count": 1}
    except (ModelGatewayError, ValueError) as retry_exc:
        retry_reason = safe_reason(str(retry_exc))
        _log_prompt_llm_step(
            "format_retry_failed",
            retry_started if "retry_started" in locals() else time.perf_counter(),
            _llm_log_context(request, log_context),
            level="WARNING",
            provider=provider_name(request),
            optimization_mode=prompt_optimization_mode(request),
            error=type(retry_exc).__name__,
            reason=retry_reason,
        )
        if retry_reason == "provider returned infrastructure error":
            return _discard_with_fallback(
                base,
                request,
                assembly,
                retry_reason,
                retry_count=1,
                salvage_used=False,
            )
        try:
            salvage_started = time.perf_counter()
            _log_prompt_llm_step(
                "format_salvage_start",
                salvage_started,
                _llm_log_context(request, log_context),
                provider=provider_name(request),
                optimization_mode=prompt_optimization_mode(request),
            )
            prompt = salvage_prompt_from_llm_article(enhanced, request)
            _log_prompt_llm_step(
                "format_salvage_done",
                salvage_started,
                _llm_log_context(request, log_context),
                provider=provider_name(request),
                optimization_mode=prompt_optimization_mode(request),
                retry_count=1,
                format_salvage_used=True,
            )
            return {
                "status": "continue",
                "prompt": prompt,
                "format_retry_count": 1,
                "format_salvage_used": True,
            }
        except ValueError:
            _log_prompt_llm_step(
                "format_salvage_failed",
                salvage_started if "salvage_started" in locals() else time.perf_counter(),
                _llm_log_context(request, log_context),
                level="WARNING",
                provider=provider_name(request),
                optimization_mode=prompt_optimization_mode(request),
                retry_count=1,
            )
            return _discard_with_fallback(
                base,
                request,
                assembly,
                str(retry_exc),
                retry_count=1,
                salvage_used=False,
            )


def _discard_with_fallback(
    base: dict[str, Any],
    request: PromptOptimizationRequest,
    assembly: dict[str, Any],
    reason: str,
    *,
    retry_count: int,
    salvage_used: bool | None = None,
) -> dict[str, Any]:
    fallback = deterministic_chinese_fallback_prompt(request, assembly)
    result = {
        **base,
        "status": "discarded",
        "provider_calls_started": True,
        "discard_reason": safe_reason(reason),
        "format_retry_count": retry_count,
        **_prompt_payload(fallback),
    }
    if salvage_used is not None:
        result["format_salvage_used"] = salvage_used
    return result


def _prompt_payload(prompt: str) -> dict[str, Any]:
    sections = sections_from_canonical(prompt)
    return {
        "optimized_prompt": prompt,
        "user_prompt": prompt,
        "user_prompt_plain": plain_prompt_from_sections(sections) or strip_user_prompt_section_headers(prompt),
        "user_prompt_sections": sections,
    }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _with_total_elapsed(payload: dict[str, Any], started: float) -> dict[str, Any]:
    timings = dict(payload.get("timings_ms") or {})
    timings["total"] = _elapsed_ms(started)
    return {**payload, "timings_ms": timings}


def _llm_log_context(request: PromptOptimizationRequest, context: dict[str, Any] | None = None) -> dict[str, Any]:
    if context:
        return dict(context)
    return {
        "node_id": request.node_id,
        "action": "prompt_optimization",
        "studio_node_type": request.node_type,
        "generation_target": request.generation_target,
    }


def _log_prompt_llm_step(
    event: str,
    started: float,
    context: dict[str, Any],
    *,
    level: str = "INFO",
    **fields: Any,
) -> None:
    payload = {
        **context,
        "stage": event,
        "elapsed_ms": fields.pop("elapsed_ms", _elapsed_ms(started)),
        **fields,
    }
    runtime_file_event("prompt", event, level=level, **payload)


_enhancement_instruction = enhancement_instruction
_strict_format_retry_instruction = strict_format_retry_instruction
_dispatch_llm_with_fallback = dispatch_llm_with_fallback
_safe_reason = safe_reason
_salvage_prompt_from_llm_article = salvage_prompt_from_llm_article
_sections_from_canonical = sections_from_canonical


__all__ = (
    "PROMPT_OPTIMIZER_PROVIDER",
    "deterministic_chinese_fallback_prompt",
    "llm_provider_gate",
    "maybe_enhance_prompt_with_llm",
    "provider_text_requested",
    "sanitize_enhanced_prompt",
)
