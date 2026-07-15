from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agentflow.harness.json_io import write_json
from apps.api.runtime_models import PromptOptimizationRequest
from apps.api.runtime_context_resolver import resolve_context_bundle
from apps.api.runtime_file_logging import runtime_file_event
from apps.api.runtime_creative_runtime_contract import prompt_optimization_creative_runtime_contract
from apps.api.runtime_llm_enhancement import maybe_enhance_prompt_with_llm
from apps.api.runtime_model_call_context import prompt_optimization_model_call_context
from apps.api.runtime_prompt_memory_engine import assemble_prompt_context
from apps.api.runtime_prompt_memory_assembly import (
    CONTEXT_PRIORITY,
    extract_background_context,
    provider_gate,
)
from apps.api.runtime_prompt_memory_constants import PROMPT_MEMORY_NON_CLAIMS
from apps.api.runtime_prompt_memory_state import (
    background_context_refs,
    append_extracted_context,
    extracted_context_refs,
    load_creative_memory_state,
    public_background_counts,
    write_creative_memory_state,
)
from apps.api.runtime_prompt_text import strip_user_prompt_section_headers
from apps.api.runtime_prompt_review_summary import prompt_optimization_review_summary
from apps.api.runtime_script_generation_body import (
    is_script_generation_request,
    is_script_surface_request,
    public_script_generation_body,
    script_body_from_candidate,
    script_surface_body_from_candidate,
)
from apps.api.runtime_script_plan import build_script_plan
from apps.api.runtime_store import RuntimeStore, reject_unsafe_payload


def build_prompt_optimization(
    store: RuntimeStore,
    project_id: str,
    request: PromptOptimizationRequest,
    output_dir: Path,
    *,
    request_id: str = "",
    client_request_id: str = "",
    user_action: str = "",
    studio_node_type: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    log_context = _prompt_log_context(
        project_id,
        request,
        request_id=request_id,
        client_request_id=client_request_id,
        user_action=user_action,
        studio_node_type=studio_node_type,
    )
    _log_prompt_step("build_start", started, log_context)
    step_started = time.perf_counter()
    state = load_creative_memory_state(store, project_id)
    _log_prompt_step("state_loaded", step_started, log_context)
    assembly_state = _resolver_safe_state(state) if request.context_subgraph else state
    step_started = time.perf_counter()
    assembly = assemble_prompt_context(request, assembly_state)
    _log_prompt_step(
        "context_assembled",
        step_started,
        log_context,
        knowledge_rules_count=len(assembly.get("knowledge_rules") or []),
        has_professional_reference=bool(assembly.get("professional_reference")),
    )
    step_started = time.perf_counter()
    context_bundle = _context_bundle(store, project_id, request)
    _log_prompt_step(
        "context_bundle_resolved",
        step_started,
        log_context,
        context_bundle_present=bool(context_bundle),
        included_asset_count=len((context_bundle or {}).get("included_assets") or []),
        excluded_asset_count=len((context_bundle or {}).get("excluded_assets") or []),
    )
    step_started = time.perf_counter()
    llm_enhancement = maybe_enhance_prompt_with_llm(request, assembly, log_context=log_context)
    timings = llm_enhancement.get("timings_ms") if isinstance(llm_enhancement.get("timings_ms"), dict) else {}
    _log_prompt_step(
        "llm_enhancement_done",
        step_started,
        log_context,
        provider=llm_enhancement.get("provider"),
        optimization_mode=llm_enhancement.get("optimization_mode"),
        provider_calls_started=llm_enhancement.get("provider_calls_started"),
        llm_status=llm_enhancement.get("status"),
        discard_reason=llm_enhancement.get("discard_reason"),
        llm_elapsed_ms=timings.get("total"),
        provider_elapsed_ms=timings.get("provider_dispatch"),
        retry_or_salvage_ms=timings.get("retry_or_salvage"),
        provider_output_length=llm_enhancement.get("provider_output_length"),
        missing_sections=llm_enhancement.get("missing_sections"),
    )
    if _remote_optimizer_required(request) and llm_enhancement.get("status") != "applied":
        reason = str(llm_enhancement.get("discard_reason") or llm_enhancement.get("status") or "not_available")
        _log_prompt_step(
            "remote_optimizer_unavailable",
            started,
            log_context,
            level="WARNING",
            provider=llm_enhancement.get("provider"),
            optimization_mode=llm_enhancement.get("optimization_mode"),
            llm_status=llm_enhancement.get("status"),
            discard_reason=reason,
            provider_output_length=llm_enhancement.get("provider_output_length"),
            missing_sections=llm_enhancement.get("missing_sections"),
        )
        raise PromptOptimizationUnavailable(
            f"remote LLM prompt optimization unavailable: {reason}",
            llm_enhancement=llm_enhancement,
        )
    step_started = time.perf_counter()
    rules = assembly["knowledge_rules"]
    background_refs = background_context_refs(state)
    extracted = extract_background_context(project_id, request, assembly["selected_slots"])
    _log_prompt_step(
        "background_context_extracted",
        step_started,
        log_context,
        background_ref_count=len(background_refs),
        extracted_context_count=len(extracted),
        knowledge_rules_count=len(rules),
    )
    step_started = time.perf_counter()
    assembled_prompt = str(llm_enhancement.get("optimized_prompt") or assembly["optimized_prompt"])
    user_prompt = str(llm_enhancement.get("user_prompt") or assembly["user_prompt"])
    user_prompt_plain = str(
        llm_enhancement.get("user_prompt_plain")
        or assembly.get("user_prompt_plain")
        or strip_user_prompt_section_headers(user_prompt)
    )
    user_prompt_sections = llm_enhancement.get("user_prompt_sections") or assembly["user_prompt_sections"]
    _log_prompt_step(
        "prompt_finalized",
        step_started,
        log_context,
        optimized_changed=strip_user_prompt_section_headers(user_prompt_plain) != strip_user_prompt_section_headers(request.prompt_text),
        section_count=len(user_prompt_sections or []),
    )
    step_started = time.perf_counter()
    script_plan = build_script_plan(request)
    _log_prompt_step(
        "script_plan_built",
        step_started,
        log_context,
        script_plan_present=bool(script_plan),
    )
    script_generation_body = None
    script_surface_body = None
    if is_script_generation_request(request):
        script_generation_body = script_body_from_candidate(user_prompt_plain or user_prompt or assembled_prompt, request)
        script_body = str(script_generation_body["script_body"])
        assembled_prompt = script_body
        user_prompt = script_body
        user_prompt_plain = script_body
        user_prompt_sections = [{"title": "剧本正文", "text": script_body}]
        _log_prompt_step(
            "script_generation_body_validated",
            started,
            log_context,
            script_body_status=script_generation_body.get("status"),
            script_body_discard_reason=script_generation_body.get("discard_reason"),
            script_body_fallback_used=script_generation_body.get("fallback_used"),
        )
    elif is_script_surface_request(request):
        script_surface_body = script_surface_body_from_candidate(user_prompt_plain or user_prompt or assembled_prompt, request)
        script_body = str(script_surface_body["script_body"])
        assembled_prompt = script_body
        user_prompt = script_body
        user_prompt_plain = script_body
        user_prompt_sections = [{"title": "剧本/分镜正文", "text": script_body}]
        _log_prompt_step(
            "script_surface_body_validated",
            started,
            log_context,
            script_body_status=script_surface_body.get("status"),
            script_body_discard_reason=script_surface_body.get("discard_reason"),
            script_body_fallback_used=script_surface_body.get("fallback_used"),
        )
    if context_bundle and not script_generation_body and not script_surface_body:
        signature_segment = str(context_bundle.get("text_channel", {}).get("asset_signature_segment") or "")
        if signature_segment:
            assembled_prompt = f"{assembled_prompt}\nAsset Signatures:\n{signature_segment}"
            user_prompt = f"{user_prompt}\n资产签名：\n{signature_segment}"
            user_prompt_plain = "\n".join(part for part in (user_prompt_plain, signature_segment) if part)
    _log_prompt_step("asset_signatures_checked", started, log_context, context_bundle_present=bool(context_bundle))
    step_started = time.perf_counter()
    brief = _creative_brief(request, project_id, assembled_prompt, llm_enhancement, script_generation_body)
    if script_plan:
        brief["script_plan"] = script_plan
    if context_bundle:
        brief["context_bundle"] = context_bundle
    trace = _prompt_trace(request, project_id, assembly, background_refs, extracted, llm_enhancement, context_bundle, script_generation_body)
    if script_plan:
        trace["script_plan"] = script_plan
    safe_manifest = _safe_manifest(
        project_id,
        len(background_refs),
        len(extracted),
        state,
        assembly,
        llm_enhancement,
        context_bundle,
        script_generation_body,
    )
    prompt_review_summary = prompt_optimization_review_summary(
        store,
        output_dir,
        project_id=project_id,
        request=request,
        optimized_prompt=assembled_prompt,
    )
    safe_manifest["prompt_review_summary_ref"] = "prompt_optimization_review_summary.json"
    safe_manifest["safe_artifacts"] = [
        *safe_manifest["safe_artifacts"],
        "prompt_optimization_review_summary.json",
    ]
    if script_plan:
        safe_manifest["script_plan_ref"] = "script_plan.json"
        safe_manifest["safe_artifacts"] = [*safe_manifest["safe_artifacts"], "script_plan.json"]
    model_call_context = prompt_optimization_model_call_context(
        project_id=project_id,
        request=request,
        assembly=assembly,
        context_bundle=context_bundle,
    )
    creative_runtime_contract = prompt_optimization_creative_runtime_contract(
        project_id=project_id,
        request_id=request_id,
        request=request,
        state=state,
        assembly=assembly,
        context_bundle=context_bundle,
        model_call_context=model_call_context,
        provider_gate_state=provider_gate(),
        llm_enhancement=llm_enhancement,
    )
    safe_manifest["creative_runtime_contract_id"] = creative_runtime_contract["contract_id"]
    safe_manifest["creative_runtime_contract_ref"] = "creative_runtime_contract.json"
    safe_manifest["safe_artifacts"] = [
        *safe_manifest["safe_artifacts"],
        "creative_runtime_contract.json",
    ]
    _log_prompt_step(
        "payloads_built",
        step_started,
        log_context,
        script_plan_present=bool(script_plan),
        context_bundle_present=bool(context_bundle),
        safe_artifact_count=len(safe_manifest.get("safe_artifacts") or []),
    )
    step_started = time.perf_counter()
    for payload in (brief, trace, safe_manifest, prompt_review_summary, model_call_context, creative_runtime_contract):
        reject_unsafe_payload(payload)
    _log_prompt_step("payloads_validated", step_started, log_context)
    step_started = time.perf_counter()
    state = append_extracted_context(state, extracted)
    write_creative_memory_state(store, project_id, state)
    _log_prompt_step(
        "memory_state_written",
        step_started,
        log_context,
        extracted_context_count=len(extracted),
    )
    step_started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "model_call_context.json", model_call_context)
    write_json(output_dir / "creative_runtime_contract.json", creative_runtime_contract)
    write_json(output_dir / "creative_brief.json", brief)
    if script_plan:
        write_json(output_dir / "script_plan.json", script_plan)
    write_json(output_dir / "prompt_assembly_trace.json", trace)
    write_json(output_dir / "prompt_optimization_review_summary.json", prompt_review_summary)
    write_json(output_dir / "prompt_optimization_safe_manifest.json", safe_manifest)
    _log_prompt_step(
        "artifacts_written",
        step_started,
        log_context,
        artifact_count=6 + int(bool(script_plan)),
    )
    _log_prompt_step(
        "build_complete",
        started,
        log_context,
        provider=llm_enhancement.get("provider"),
        optimization_mode=llm_enhancement.get("optimization_mode"),
        provider_calls_started=llm_enhancement.get("provider_calls_started"),
        llm_status=llm_enhancement.get("status"),
        optimized_changed=strip_user_prompt_section_headers(user_prompt_plain) != strip_user_prompt_section_headers(request.prompt_text),
    )
    return {
        "brief": brief,
        "trace": trace,
        "safe_manifest": safe_manifest,
        "prompt_review_summary": prompt_review_summary,
        "provider_gate": provider_gate(),
        "provider_calls_started": llm_enhancement["provider_calls_started"],
        "original_prompt": request.prompt_text,
        "optimized_prompt": assembled_prompt,
        "optimization_mode": str(llm_enhancement.get("optimization_mode") or "not_applicable"),
        "user_prompt": user_prompt,
        "user_prompt_plain": user_prompt_plain,
        "user_prompt_sections": user_prompt_sections,
        "context_bundle": context_bundle,
        "model_call_context": model_call_context,
        "creative_runtime_contract": creative_runtime_contract,
        "script_plan": script_plan,
        "script_generation_body": public_script_generation_body(script_generation_body),
        "llm_provider": llm_enhancement.get("provider"),
        "llm_status": llm_enhancement.get("status"),
        "llm_discard_reason": llm_enhancement.get("discard_reason"),
        "llm_guardrail_fallback_used": bool(llm_enhancement.get("guardrail_fallback_used")),
        "llm_format_salvage_used": bool(llm_enhancement.get("format_salvage_used")),
        "llm_timings_ms": llm_enhancement.get("timings_ms") or {},
        "provider_output_length": llm_enhancement.get("provider_output_length"),
        "missing_sections": llm_enhancement.get("missing_sections"),
        "optimized_changed": strip_user_prompt_section_headers(user_prompt_plain) != strip_user_prompt_section_headers(request.prompt_text),
    }


class PromptOptimizationUnavailable(ValueError):
    def __init__(self, message: str, *, llm_enhancement: dict[str, Any]) -> None:
        super().__init__(message)
        self.llm_enhancement = llm_enhancement


def _context_bundle(
    store: RuntimeStore,
    project_id: str,
    request: PromptOptimizationRequest,
) -> dict[str, Any] | None:
    if not request.context_subgraph:
        return None
    return resolve_context_bundle(
        store,
        project_id,
        mode="optimize",
        visible_prompt=request.prompt_text,
        context_subgraph=request.context_subgraph,
        director_setup=request.director_setup,
    )


def _remote_optimizer_required(request: PromptOptimizationRequest) -> bool:
    params = request.node_parameters or {}
    return bool(params.get("remote_optimizer_required"))


def _resolver_safe_state(state: dict[str, Any]) -> dict[str, Any]:
    safe_state = dict(state)
    for field in ("characters", "scenes", "style_preferences", "user_preferences"):
        safe_state[field] = []
    return safe_state


def _prompt_log_context(
    project_id: str,
    request: PromptOptimizationRequest,
    *,
    request_id: str = "",
    client_request_id: str = "",
    user_action: str = "",
    studio_node_type: str = "",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "client_request_id": client_request_id,
        "project_id": project_id,
        "node_id": request.node_id,
        "action": "prompt_optimization",
        "user_action": user_action,
        "studio_node_type": studio_node_type or request.node_type,
        "generation_target": request.generation_target,
    }


def _log_prompt_step(
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


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _creative_brief(
    request: PromptOptimizationRequest,
    project_id: str,
    assembled_prompt: str,
    llm_enhancement: dict[str, Any],
    script_generation_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "artifact_type": "agentflow_creative_brief",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "node_id": request.node_id,
        "node_type": request.node_type,
        "original_prompt": request.prompt_text,
        "optimized_prompt": assembled_prompt,
        "generation_target": request.generation_target,
        "target_platform": request.target_platform,
        "style": request.style,
        "optimization_mode": str(llm_enhancement.get("optimization_mode") or "not_applicable"),
        "negative_constraints": [
            "Do not claim provider execution unless an explicit provider gate is opened.",
            "Do not treat background context as durable project memory.",
            "Do not include private asset paths, signed URLs, or raw provider responses.",
        ],
        "director_setup": request.director_setup.model_dump(mode="json") if request.director_setup else {"view": "not_provided"},
        "node_parameters": request.node_parameters or {},
        "asset_refs": list(request.asset_refs),
        "provider_output": False,
        "provider_calls_started": llm_enhancement["provider_calls_started"],
        "llm_enhancement": _public_llm_enhancement(llm_enhancement),
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": PROMPT_MEMORY_NON_CLAIMS,
    }
    public_script_body = public_script_generation_body(script_generation_body)
    if public_script_body:
        payload["source_idea"] = public_script_body["source_idea"]
        payload["script_generation_body"] = public_script_body
    return payload


def _prompt_trace(
    request: PromptOptimizationRequest,
    project_id: str,
    assembly: dict[str, Any],
    background_refs: list[dict[str, str]],
    extracted: list[dict[str, Any]],
    llm_enhancement: dict[str, Any],
    context_bundle: dict[str, Any] | None,
    script_generation_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "artifact_type": "agentflow_prompt_assembly_trace",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "node_id": request.node_id,
        "node_type": request.node_type,
        "input_prompt_ref": "request_body.prompt_text",
        "generation_target": request.generation_target,
        "optimization_mode": str(llm_enhancement.get("optimization_mode") or "not_applicable"),
        "context_priority": CONTEXT_PRIORITY,
        "knowledge_rules": assembly["knowledge_rules"],
        "creative_agent": assembly["creative_agent"],
        "selected_slots": assembly["selected_slots"],
        "conflict_resolution": assembly["conflict_resolution"],
        "suppressed_context": assembly["suppressed_context"],
        "professional_reference": assembly["professional_reference"],
        "director_scenario": assembly["director_scenario"],
        "background_context_refs": background_refs,
        "extracted_context_refs": extracted_context_refs(extracted),
        "asset_refs": list(request.asset_refs),
        "knowledgebase_version": assembly["knowledgebase_version"],
        "knowledgebase_registry_hash": assembly["knowledgebase_registry_hash"],
        "knowledgebase_rules_count": assembly["knowledgebase_rules_count"],
        "llm_enhancement": _public_llm_enhancement(llm_enhancement),
        "provider_calls_started": llm_enhancement["provider_calls_started"],
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": PROMPT_MEMORY_NON_CLAIMS,
    }
    if context_bundle:
        payload["context_bundle"] = context_bundle
    public_script_body = public_script_generation_body(script_generation_body)
    if public_script_body:
        payload["source_idea_ref"] = "request_body.node_parameters.source_idea"
        payload["script_generation_body"] = public_script_body
    return payload


def _safe_manifest(
    project_id: str,
    background_context_count: int,
    extracted_context_count: int,
    state: dict[str, Any],
    assembly: dict[str, Any],
    llm_enhancement: dict[str, Any],
    context_bundle: dict[str, Any] | None,
    script_generation_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "artifact_type": "agentflow_prompt_optimization_safe_manifest",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "status": "succeeded",
        "provider_gate": provider_gate(),
        "provider_calls_started": llm_enhancement["provider_calls_started"],
        "optimization_mode": str(llm_enhancement.get("optimization_mode") or "not_applicable"),
        "raw_provider_response_stored": False,
        "generated_media_bytes_stored": False,
        "llm_enhancement": _public_llm_enhancement(llm_enhancement),
        "safe_artifacts": [
            "creative_brief.json",
            "prompt_assembly_trace.json",
            "prompt_optimization_safe_manifest.json",
        ],
        "memory_policy": "background context is internal; canvas UI receives optimized prompt and safe artifact refs only",
        "background_context_count": background_context_count,
        "extracted_context_count": extracted_context_count,
        "background_counts_before_run": public_background_counts(state),
        "knowledgebase_version": assembly["knowledgebase_version"],
        "knowledgebase_registry_hash": assembly["knowledgebase_registry_hash"],
        "knowledgebase_rules_count": assembly["knowledgebase_rules_count"],
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": PROMPT_MEMORY_NON_CLAIMS,
    }
    if context_bundle:
        payload["context_bundle_mode"] = context_bundle.get("mode")
        payload["context_included_asset_count"] = len(context_bundle.get("included_assets", []))
    public_script_body = public_script_generation_body(script_generation_body)
    if public_script_body:
        payload["script_generation_body"] = public_script_body
    return payload


def _public_llm_enhancement(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "requested": bool(value.get("requested")),
        "status": str(value.get("status") or "not_requested"),
        "provider": str(value.get("provider") or "not_requested"),
        "model": str(value.get("model") or "not_requested"),
        "optimization_mode": str(value.get("optimization_mode") or "not_applicable"),
        "provider_calls_started": bool(value.get("provider_calls_started")),
        "raw_response_stored": False,
        "discard_reason": value.get("discard_reason"),
        "guardrail_fallback_used": bool(value.get("guardrail_fallback_used")),
        "format_retry_count": int(value.get("format_retry_count") or 0),
        "format_salvage_used": bool(value.get("format_salvage_used")),
    }


__all__ = (
    "PROMPT_MEMORY_NON_CLAIMS",
    "PromptOptimizationUnavailable",
    "build_prompt_optimization",
)
