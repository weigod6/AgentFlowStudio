from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentflow.algorithms.context_resolver import merged_reference_image_refs
from agentflow.algorithms.prompt_integrity import validate_prompt_integrity
from agentflow.algorithms.request_projection import build_request_plan
from agentflow.harness.json_io import write_json
from agentflow_studio.model_gateway.errors import ModelConfigError, ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import (
    ProviderDispatchRequest,
    load_provider_registry,
)
from apps.api.runtime_file_logging import runtime_file_event
from apps.api.runtime_image_assets import resolve_reference_images
from apps.api.runtime_context_resolver import provider_prompt_from_bundle, resolve_context_bundle
from apps.api.runtime_model_call_context import keyframe_model_call_context
from apps.api.runtime_models import KeyframeGenerationRequest, PromptOptimizationRequest
from apps.api.runtime_media_validation import reference_image_size_blocks
from apps.api.runtime_asset_card_revision_prompt import asset_card_revision_reference_instruction
from apps.api.runtime_provider_dispatch import dispatch_provider_with_retry
from apps.api.runtime_keyframe_payloads import (
    keyframe_candidate_summary,
    keyframe_request_plan,
    keyframe_review_preview_refs,
    keyframe_safe_manifest,
)
from apps.api.runtime_keyframe_generation_bridge import write_keyframe_generation_bridge
from apps.api.runtime_prompt_memory_engine import assemble_prompt_context
from apps.api.runtime_prompt_memory_state import load_creative_memory_state
from apps.api.runtime_prompt_text import strip_user_prompt_section_headers
from apps.api.runtime_reference_intent import (
    ORIGINALIZE_REFERENCE_MODE,
    is_originalize_reference_request,
    originalize_reference_policy,
    reference_transform_mode_for_request,
)
from apps.api.runtime_store import RuntimeStore, reject_unsafe_payload


REMOTE_IMAGE_ENV = "AFS_ALLOW_REMOTE_IMAGE"
KEYFRAME_BACKGROUND_SYNC_ENV = "AFS_KEYFRAME_BACKGROUND_SYNC_IMAGE"
REMOTE_TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_IMAGE_PROMPT_LIMIT = 1500
DEFAULT_REFERENCE_IMAGE_SLOTS = 1
DEFAULT_IMAGE_RELAY_SERVICE_ID = "image_relay"
LEGACY_IMAGE_SERVICE_ALIASES = {"image_relay": ("codex_image",)}
KEYFRAME_NON_CLAIMS = [
    "runtime verification only",
    "not human acceptance",
    "not business validation",
    "not video provider smoke",
    "not durable memory",
]


def build_keyframe_generation(
    store: RuntimeStore,
    project_id: str,
    request: KeyframeGenerationRequest,
    output_dir: Path,
    *,
    include_fixed_assets: bool = True,
    request_id: str = "",
    client_request_id: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_file_event(
        "keyframe",
        "submit_start",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        provider_service_id=request.provider_service_id,
        aspect_ratio=request.aspect_ratio,
        candidate_count=request.candidate_count,
        asset_ref_count=len(request.asset_refs or []),
    )
    prompt_request = _prompt_request(request)
    state = load_creative_memory_state(store, project_id)
    assembly_state = _resolver_safe_state(state) if request.context_subgraph else state
    assembly = assemble_prompt_context(prompt_request, assembly_state)
    registry = None
    descriptor = _default_descriptor()
    service_request_format = ""
    try:
        registry = load_provider_registry()
        service_id = _resolve_image_service_id(registry, request.provider_service_id)
        if service_id != request.provider_service_id:
            request = request.model_copy(update={"provider_service_id": service_id})
        service_request_format = _service_request_format(registry, request.provider_service_id)
        descriptor = registry.descriptor(request.provider_service_id)
    except (ModelGatewayError, OSError, ValueError):
        registry = None
    configured_reference_slots = int(getattr(descriptor, "reference_image_slots", DEFAULT_REFERENCE_IMAGE_SLOTS))
    effective_reference_slots = _effective_reference_image_slots(
        request,
        configured_reference_slots,
        request_format=service_request_format,
    )
    context_bundle = _context_bundle(
        store,
        project_id,
        request,
        include_fixed_assets=include_fixed_assets,
        prompt_char_limit=int(getattr(descriptor, "prompt_char_limit", DEFAULT_IMAGE_PROMPT_LIMIT)),
        reference_image_slots=effective_reference_slots,
    )
    reference_images = _reference_images(
        store,
        project_id,
        request,
        context_bundle,
        limit=effective_reference_slots,
    )
    runtime_file_event(
        "keyframe",
        "references_resolved",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        provider_service_id=request.provider_service_id,
        reference_image_count=len(reference_images),
        image_reference_slots=effective_reference_slots,
    )
    is_asset_card_revision = _has_asset_card_revision(request)
    is_originalize_reference = is_originalize_reference_request(request)
    if context_bundle:
        provider_prompt = _guarded_provider_keyframe_prompt(
            provider_prompt_from_bundle(context_bundle),
            reference_count=len(reference_images),
            asset_card_revision=is_asset_card_revision,
            originalize_reference=is_originalize_reference,
            limit=int(getattr(descriptor, "prompt_char_limit", DEFAULT_IMAGE_PROMPT_LIMIT)),
        )
    else:
        provider_prompt = _guarded_provider_keyframe_prompt(
            request.optimized_prompt or assembly["creative_agent"]["provider_translation"]["prompt"],
            reference_count=len(reference_images),
            asset_card_revision=is_asset_card_revision,
            originalize_reference=is_originalize_reference,
            limit=int(getattr(descriptor, "prompt_char_limit", DEFAULT_IMAGE_PROMPT_LIMIT)),
        )
    image_operation = _image_operation_for_request(
        request,
        reference_images,
        request_format=service_request_format,
    )
    if reference_images:
        reference_instruction = _reference_prompt_instruction(request, len(reference_images))
        prompt_with_references = (
            reference_instruction
            if is_asset_card_revision
            else f"{provider_prompt}\n{reference_instruction}"
        )
        provider_prompt = _guarded_provider_keyframe_prompt(
            prompt_with_references,
            reference_count=len(reference_images),
            asset_card_revision=is_asset_card_revision,
            originalize_reference=is_originalize_reference,
            limit=int(getattr(descriptor, "prompt_char_limit", DEFAULT_IMAGE_PROMPT_LIMIT)),
        )
    required_gate = str(getattr(descriptor, "required_gate", REMOTE_IMAGE_ENV) or REMOTE_IMAGE_ENV)
    provider_gate = image_provider_gate(required_gate)
    min_reference_edge = int(getattr(descriptor, "min_reference_image_edge_px", 0) or 0)
    model_call_context = keyframe_model_call_context(
        project_id=project_id,
        request=request,
        context_bundle=context_bundle,
        provider_constraints={
            "capability": "image",
            "provider_service_id": request.provider_service_id,
            "required_gate": required_gate,
            "prompt_char_limit": int(getattr(descriptor, "prompt_char_limit", DEFAULT_IMAGE_PROMPT_LIMIT)),
            "reference_image_slots": effective_reference_slots,
            "image_operation": image_operation,
            "image_input_fidelity": "high" if image_operation == "edit" else None,
            "reference_transform_mode": reference_transform_mode_for_request(request) or None,
        },
    )
    model_request_plan = build_request_plan(
        model_call_context=model_call_context,
        canonical_brief={"canonical_prompt": provider_prompt},
        provider_service_id=request.provider_service_id,
    )

    provider_outputs: list[dict[str, Any]] = []
    status = "blocked"
    blocks = []
    provider_diagnostics: dict[str, Any] = {}
    provider_calls_started = False
    retry_count = 0
    if provider_gate["status"] == "blocked":
        blocks.append(_gate_closed_block(required_gate))
        runtime_file_event(
            "keyframe",
            "blocked",
            level="WARNING",
            request_id=request_id,
            client_request_id=client_request_id,
            project_id=project_id,
            node_id=request.node_id,
            provider_service_id=request.provider_service_id,
            reason="provider_gate_closed",
            required_gate=required_gate,
            elapsed_ms=_elapsed_ms(started),
        )
    elif size_blocks := reference_image_size_blocks(
        reference_images,
        min_edge_px=min_reference_edge,
        capability="image",
        required_gate=required_gate,
    ):
        blocks.extend(size_blocks)
        runtime_file_event(
            "keyframe",
            "blocked",
            level="WARNING",
            request_id=request_id,
            client_request_id=client_request_id,
            project_id=project_id,
            node_id=request.node_id,
            provider_service_id=request.provider_service_id,
            reason=str(size_blocks[0].get("reason") or "reference_image_size"),
            block_count=len(size_blocks),
            elapsed_ms=_elapsed_ms(started),
        )
    else:
        try:
            if registry is None:
                registry_load_started = time.perf_counter()
                registry = load_provider_registry()
                service_id = _resolve_image_service_id(registry, request.provider_service_id)
                if service_id != request.provider_service_id:
                    request = request.model_copy(update={"provider_service_id": service_id})
                service_request_format = _service_request_format(registry, request.provider_service_id)
                descriptor = registry.descriptor(request.provider_service_id)
                runtime_file_event(
                    "keyframe",
                    "provider_registry_loaded",
                    request_id=request_id,
                    client_request_id=client_request_id,
                    project_id=project_id,
                    node_id=request.node_id,
                    provider_service_id=request.provider_service_id,
                    elapsed_ms=_elapsed_ms(registry_load_started),
                )
            provider_calls_started = True
            dispatch_request = ProviderDispatchRequest(
                prompt=provider_prompt,
                output_dir=output_dir,
                task_type="asset_card_revision" if image_operation == "edit" else None,
                image_operation=image_operation,
                aspect_ratio=request.aspect_ratio,
                candidate_count=request.candidate_count,
                seed=request.seed,
                reference_image_paths=tuple(item["path"] for item in reference_images),
                subject_reference_image_path=reference_images[0]["path"] if reference_images else None,
                edit_source_image_path=reference_images[0]["path"] if image_operation == "edit" else None,
                edit_reference_image_paths=tuple(item["path"] for item in reference_images) if image_operation == "edit" else (),
                image_input_fidelity="high" if image_operation == "edit" else None,
            )
            runtime_file_event(
                "keyframe",
                "provider_call",
                request_id=request_id,
                client_request_id=client_request_id,
                project_id=project_id,
                node_id=request.node_id,
                provider_service_id=request.provider_service_id,
                model=_provider_model(registry, request.provider_service_id),
                image_operation=image_operation,
                reference_image_count=len(reference_images),
                execution_mode=str(getattr(descriptor, "execution_mode", "sync") or "sync"),
            )
            provider_started = time.perf_counter()
            if str(getattr(descriptor, "execution_mode", "sync") or "sync") == "async":
                provider_task = registry.submit("image", request.provider_service_id, dispatch_request)
                provider_elapsed_ms = _elapsed_ms(provider_started)
                task_status = str((provider_task.get("task") or {}).get("status") or "submitted")
                if task_status == "already_complete":
                    poll_started = time.perf_counter()
                    raw = registry.poll("image", request.provider_service_id, provider_task)
                    poll_elapsed_ms = _elapsed_ms(poll_started)
                    raw_status = str(raw.get("status") or "succeeded").lower()
                    if raw_status in {"succeeded", "complete", "completed", "partial", "partially_complete"}:
                        provider_outputs = _provider_outputs(raw)
                        blocks.extend(_provider_manifest_blocks(raw, required_gate, request.candidate_count, len(provider_outputs)))
                        status = _keyframe_result_status(raw_status, request.candidate_count, provider_outputs, blocks)
                        runtime_file_event(
                            "keyframe",
                            "succeeded" if status == "succeeded" else "partially_complete",
                            request_id=request_id,
                            client_request_id=client_request_id,
                            project_id=project_id,
                            node_id=request.node_id,
                            provider_service_id=request.provider_service_id,
                            provider_task_id=_provider_task_id(provider_task),
                            output_count=len(provider_outputs),
                            provider_elapsed_ms=provider_elapsed_ms,
                            poll_elapsed_ms=poll_elapsed_ms,
                            elapsed_ms=_elapsed_ms(started),
                        )
                    elif raw_status in {"running", "submitted", "pending"}:
                        status = raw_status
                        _write_task_state(
                            output_dir,
                            _task_state(
                                request=request,
                                provider_task=provider_task,
                                status=status,
                                provider_prompt=provider_prompt,
                                provider_gate=provider_gate,
                                reference_image_count=len(reference_images),
                                image_operation=image_operation,
                                context_bundle=context_bundle,
                                request_id=request_id,
                                client_request_id=client_request_id,
                            ),
                        )
                        runtime_file_event(
                            "keyframe",
                            "submitted",
                            request_id=request_id,
                            client_request_id=client_request_id,
                            project_id=project_id,
                            node_id=request.node_id,
                            provider_service_id=request.provider_service_id,
                            provider_task_id=_provider_task_id(provider_task),
                            status=status,
                            provider_elapsed_ms=provider_elapsed_ms,
                            poll_elapsed_ms=poll_elapsed_ms,
                            elapsed_ms=_elapsed_ms(started),
                        )
                    else:
                        provider_outputs = _provider_outputs(raw)
                        blocks.extend(_provider_manifest_blocks(raw, required_gate, request.candidate_count, len(provider_outputs)))
                        if provider_outputs:
                            status = "partially_complete"
                        else:
                            status = "blocked"
                            if not blocks:
                                blocks.append(_provider_failure_block(str(raw.get("error") or raw_status or "image provider did not complete"), required_gate))
                        runtime_file_event(
                            "keyframe",
                            "provider_failed",
                            level="ERROR",
                            request_id=request_id,
                            client_request_id=client_request_id,
                            project_id=project_id,
                            node_id=request.node_id,
                            provider_service_id=request.provider_service_id,
                            provider_task_id=_provider_task_id(provider_task),
                            status=raw_status,
                            reason=str(raw.get("error") or raw_status or "image provider did not complete"),
                            provider_elapsed_ms=provider_elapsed_ms,
                            poll_elapsed_ms=poll_elapsed_ms,
                            elapsed_ms=_elapsed_ms(started),
                        )
                else:
                    status = task_status
                    _write_task_state(
                        output_dir,
                        _task_state(
                            request=request,
                            provider_task=provider_task,
                            status=status,
                            provider_prompt=provider_prompt,
                            provider_gate=provider_gate,
                            reference_image_count=len(reference_images),
                            image_operation=image_operation,
                            context_bundle=context_bundle,
                            request_id=request_id,
                            client_request_id=client_request_id,
                        ),
                    )
                    runtime_file_event(
                        "keyframe",
                        "submitted",
                        request_id=request_id,
                        client_request_id=client_request_id,
                        project_id=project_id,
                        node_id=request.node_id,
                        provider_service_id=request.provider_service_id,
                        provider_task_id=_provider_task_id(provider_task),
                        status=status,
                        provider_elapsed_ms=provider_elapsed_ms,
                        elapsed_ms=_elapsed_ms(started),
                    )
            else:
                retry_disabled = bool((request.node_parameters or {}).get("disable_provider_retry"))
                if retry_disabled:
                    manifest = registry.dispatch("image", request.provider_service_id, dispatch_request)
                    retry_count = 0
                else:
                    manifest, retry_count = dispatch_provider_with_retry(
                        registry,
                        "image",
                        request.provider_service_id,
                        dispatch_request,
                    )
                provider_elapsed_ms = _elapsed_ms(provider_started)
                provider_outputs = _provider_outputs(manifest)
                blocks.extend(_provider_manifest_blocks(manifest, required_gate, request.candidate_count, len(provider_outputs)))
                status = _keyframe_result_status(str(manifest.get("status") or "succeeded"), request.candidate_count, provider_outputs, blocks)
                runtime_file_event(
                    "keyframe",
                    "succeeded" if status == "succeeded" else "partially_complete",
                    request_id=request_id,
                    client_request_id=client_request_id,
                    project_id=project_id,
                    node_id=request.node_id,
                    provider_service_id=request.provider_service_id,
                    output_count=len(provider_outputs),
                    retry_count=retry_count,
                    provider_elapsed_ms=provider_elapsed_ms,
                    elapsed_ms=_elapsed_ms(started),
                )
        except (ModelGatewayError, TimeoutError) as exc:
            retry_count = max(retry_count, int(getattr(exc, "retry_count", 0) or 0))
            status = "blocked"
            provider_elapsed_ms = _elapsed_ms(provider_started) if "provider_started" in locals() else ""
            provider_diagnostics = _provider_failure_diagnostics(
                exc,
                required_gate,
                retry_count=retry_count,
                provider_elapsed_ms=provider_elapsed_ms,
            )
            blocks.append(_provider_failure_block(str(exc), required_gate, diagnostics=provider_diagnostics))
            runtime_file_event(
                "keyframe",
                "provider_failed",
                level="ERROR",
                request_id=request_id,
                client_request_id=client_request_id,
                project_id=project_id,
                node_id=request.node_id,
                provider_service_id=request.provider_service_id,
                error=type(exc).__name__,
                reason=_safe_error(str(exc)),
                provider_stage=provider_diagnostics.get("provider_stage"),
                failure_class=provider_diagnostics.get("failure_class"),
                attempt_count=provider_diagnostics.get("attempt_count"),
                retry_count=retry_count,
                provider_elapsed_ms=provider_elapsed_ms,
                elapsed_ms=_elapsed_ms(started),
            )

    request_plan = keyframe_request_plan(
        request,
        provider_prompt,
        provider_gate,
        assembly,
        status,
        reference_images,
        context_bundle,
        KEYFRAME_NON_CLAIMS,
    )
    request_plan["image_operation"] = image_operation
    reference_transform_mode = reference_transform_mode_for_request(request)
    if reference_transform_mode:
        request_plan["reference_transform_mode"] = reference_transform_mode
    if image_operation == "edit" and reference_images:
        request_plan["edit_source_asset_id"] = reference_images[0]["public"]["asset_id"]
        request_plan["image_input_fidelity"] = "high"
    request_plan["model_call_context_id"] = model_call_context["context_id"]
    request_plan["model_request_plan_ref"] = "model_request_plan.json"
    review_preview_refs = keyframe_review_preview_refs(project_id, output_dir.name, provider_outputs)
    candidates = keyframe_candidate_summary(
        request,
        provider_prompt,
        provider_outputs,
        KEYFRAME_NON_CLAIMS,
        project_id=project_id,
        job_id=output_dir.name,
    )
    safe_manifest = keyframe_safe_manifest(
        project_id,
        request,
        status=status,
        provider_gate=provider_gate,
        blocks=blocks,
        provider_calls_started=provider_calls_started,
        output_count=len(provider_outputs),
        reference_image_count=len(reference_images),
        retry_count=retry_count,
        provider_diagnostics=provider_diagnostics,
        context_bundle=context_bundle,
        non_claims=KEYFRAME_NON_CLAIMS,
        job_id=output_dir.name,
        review_preview_refs=review_preview_refs,
    )
    generation_bridge = write_keyframe_generation_bridge(
        output_dir,
        project_id=project_id,
        request=request,
        status=status,
        provider_gate=provider_gate,
        provider_calls_started=provider_calls_started,
        reference_image_count=len(reference_images),
        blocks=blocks,
        context_bundle=context_bundle,
        model_call_context=model_call_context,
        model_request_plan=model_request_plan,
        safe_manifest=safe_manifest,
    )
    for payload in (model_call_context, model_request_plan, request_plan, candidates, safe_manifest):
        reject_unsafe_payload(payload)
    write_json(output_dir / "model_call_context.json", model_call_context)
    write_json(output_dir / "model_request_plan.json", model_request_plan)
    write_json(output_dir / "keyframe_request_plan.json", request_plan)
    write_json(output_dir / "keyframe_candidates_summary.json", candidates)
    write_json(output_dir / "keyframe_generation_safe_manifest.json", safe_manifest)
    runtime_file_event(
        "keyframe",
        "response",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        provider_service_id=request.provider_service_id,
        status=status,
        provider_calls_started=provider_calls_started,
        output_count=len(provider_outputs),
        block_count=len(blocks),
        retry_count=retry_count,
        elapsed_ms=_elapsed_ms(started),
    )
    return {
        "status": status,
        "provider_gate": provider_gate,
        "provider_calls_started": provider_calls_started,
        "provider_outputs": provider_outputs,
        "safe_manifest": safe_manifest,
        "context_bundle": context_bundle,
        "model_call_context": model_call_context,
        "model_request_plan": model_request_plan,
        "generation_bridge": generation_bridge,
        "tool_gate_state": {
            "remote_llm": "not_requested",
            "remote_asr": "blocked_by_default",
            "remote_image": provider_gate["status"],
            "remote_video": "blocked_by_default",
        },
    }


def _uses_asset_card_image_edit(request: KeyframeGenerationRequest, reference_images: list[dict[str, Any]]) -> bool:
    return _has_asset_card_revision(request) and bool(reference_images)


def _image_operation_for_request(
    request: KeyframeGenerationRequest,
    reference_images: list[dict[str, Any]],
    *,
    request_format: str = "",
) -> str:
    if _uses_asset_card_image_edit(request, reference_images):
        return "edit"
    if reference_images and _uses_openai_images_relay(request_format):
        return "edit"
    return "generate"


def _effective_reference_image_slots(
    request: KeyframeGenerationRequest,
    configured_slots: int,
    *,
    request_format: str = "",
) -> int:
    if _has_asset_card_revision(request) and configured_slots <= 0:
        return 1
    if _uses_openai_images_relay(request_format) and configured_slots <= 0 and request.asset_refs:
        return 1
    return configured_slots


def _resolve_image_service_id(registry: Any, service_id: str) -> str:
    requested = str(service_id or DEFAULT_IMAGE_RELAY_SERVICE_ID)
    try:
        registry.descriptor(requested)
        return requested
    except ModelConfigError:
        pass
    for alias in LEGACY_IMAGE_SERVICE_ALIASES.get(requested, ()):
        try:
            registry.descriptor(alias)
            return alias
        except ModelConfigError:
            continue
    return requested


def _service_request_format(registry: Any, service_id: str) -> str:
    services = getattr(getattr(registry, "store", None), "services", {})
    service = services.get(service_id) if isinstance(services, dict) else None
    if not isinstance(service, dict):
        return ""
    return str(service.get("request_format") or service.get("payload_format") or service.get("api_family") or "").strip()


def _uses_openai_images_relay(request_format: str) -> bool:
    return str(request_format or "").strip() == "openai_images"


def _context_bundle(
    store: RuntimeStore,
    project_id: str,
    request: KeyframeGenerationRequest,
    *,
    include_fixed_assets: bool,
    prompt_char_limit: int,
    reference_image_slots: int,
) -> dict[str, Any] | None:
    if not request.context_subgraph:
        return None
    visible_prompt = strip_user_prompt_section_headers(request.optimized_prompt or request.prompt_text)
    return resolve_context_bundle(
        store,
        project_id,
        mode="generate",
        visible_prompt=visible_prompt,
        context_subgraph=request.context_subgraph,
        temporary_lock_overrides=request.temporary_lock_overrides,
        temporary_asset_exclusions=request.temporary_asset_exclusions,
        include_fixed_assets=include_fixed_assets,
        style_preference=request.style,
        prompt_char_limit=prompt_char_limit,
        reference_image_slots=reference_image_slots,
        director_setup=request.director_setup,
    )


def _reference_images(
    store: RuntimeStore,
    project_id: str,
    request: KeyframeGenerationRequest,
    context_bundle: dict[str, Any] | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    refs = merged_reference_image_refs(
        request_asset_refs=request.asset_refs,
        context_bundle=context_bundle,
    )
    return resolve_reference_images(store, project_id, refs, limit=limit)


def _resolver_safe_state(state: dict[str, Any]) -> dict[str, Any]:
    safe_state = dict(state)
    for field in ("characters", "scenes", "style_preferences", "user_preferences"):
        safe_state[field] = []
    return safe_state


def image_provider_gate(required_gate: str = REMOTE_IMAGE_ENV) -> dict[str, str]:
    status = "ready_not_run" if os.environ.get(required_gate, "").strip().lower() in REMOTE_TRUE_VALUES else "blocked"
    return {"capability": "image", "env": required_gate, "status": status}


def keyframe_sync_background_plan(request: KeyframeGenerationRequest) -> dict[str, Any]:
    if os.environ.get(KEYFRAME_BACKGROUND_SYNC_ENV, "").strip().lower() not in REMOTE_TRUE_VALUES:
        return {
            "enabled": False,
            "reason": "background_sync_disabled",
            "provider_service_id": request.provider_service_id,
            "provider_gate": image_provider_gate(),
            "execution_mode": "",
        }
    try:
        registry = load_provider_registry()
        service_id = _resolve_image_service_id(registry, request.provider_service_id)
        descriptor = registry.descriptor(service_id)
    except (ModelGatewayError, OSError, ValueError):
        return {
            "enabled": False,
            "reason": "provider_registry_unavailable",
            "provider_service_id": request.provider_service_id,
            "provider_gate": image_provider_gate(),
            "execution_mode": "",
        }
    required_gate = str(getattr(descriptor, "required_gate", REMOTE_IMAGE_ENV) or REMOTE_IMAGE_ENV)
    provider_gate = image_provider_gate(required_gate)
    execution_mode = str(getattr(descriptor, "execution_mode", "sync") or "sync")
    enabled = provider_gate["status"] != "blocked" and execution_mode == "sync"
    return {
        "enabled": enabled,
        "reason": "" if enabled else ("provider_gate_closed" if provider_gate["status"] == "blocked" else "provider_not_sync"),
        "provider_service_id": service_id,
        "provider_gate": provider_gate,
        "execution_mode": execution_mode,
    }


def _prompt_request(request: KeyframeGenerationRequest) -> PromptOptimizationRequest:
    params = dict(request.node_parameters or {})
    params.setdefault("aspect_ratio", request.aspect_ratio)
    return PromptOptimizationRequest(
        node_id=request.node_id,
        node_type="image",
        prompt_text=request.prompt_text,
        generation_target="keyframe",
        target_platform=request.target_platform,
        style=request.style,
        asset_refs=list(request.asset_refs),
        director_setup=request.director_setup,
        node_parameters=params,
        context_subgraph=request.context_subgraph,
        generated_at=request.generated_at,
    )


def _provider_outputs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = []
    for item in manifest.get("outputs", []):
        if not isinstance(item, dict):
            continue
        outputs.append(
            {
                "candidate_id": item.get("candidate_id"),
                "status": item.get("status"),
                "byte_count": item.get("byte_count"),
                "sha256": item.get("sha256"),
                "width": item.get("width"),
                "height": item.get("height"),
                "aspect_ratio": item.get("aspect_ratio"),
                "provider_url_persisted": False,
            }
        )
    return outputs


def _provider_manifest_blocks(
    manifest: dict[str, Any],
    required_gate: str,
    requested_count: int,
    output_count: int,
) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    raw_blocks = manifest.get("blocks") if isinstance(manifest.get("blocks"), list) else []
    for item in raw_blocks:
        if not isinstance(item, dict):
            continue
        block_id = str(item.get("block_id") or item.get("code") or "remote_image_provider_not_ready")[:100]
        reason = _safe_error(str(item.get("reason") or item.get("message") or item.get("error") or "Image provider did not complete this item."))
        block: dict[str, str] = {
            "block_id": block_id,
            "reason": reason,
            "required_gate": str(item.get("required_gate") or required_gate),
        }
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id.startswith("candidate_"):
            block["candidate_id"] = candidate_id[:32]
        for field in ("failure_class", "provider_stage"):
            value = str(item.get(field) or "")
            if value:
                block[field] = value[:80]
        for field in ("retry_count", "attempt_count", "provider_elapsed_ms"):
            if item.get(field) is not None:
                block[field] = item[field]
        blocks.append(block)
    if output_count < requested_count and _provider_status_allows_partial(str(manifest.get("status") or "")):
        blocks.append(
            {
                "block_id": "remote_image_candidate_missing",
                "reason": "Image provider returned fewer reviewable candidates than requested.",
                "required_gate": required_gate,
            }
        )
    return blocks


def _keyframe_result_status(
    provider_status: str,
    requested_count: int,
    provider_outputs: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> str:
    normalized = str(provider_status or "").strip().lower().replace("-", "_")
    output_count = len(provider_outputs)
    if output_count > 0 and (normalized in {"partial", "partially_complete"} or output_count < requested_count or blocks):
        return "partially_complete"
    if normalized in {"failed", "blocked", "timed_out", "timeout", "skipped"}:
        return "partially_complete" if output_count else "blocked"
    return "succeeded"


def _provider_status_allows_partial(status: str) -> bool:
    normalized = str(status or "").strip().lower().replace("-", "_")
    return normalized in {"", "succeeded", "success", "complete", "completed", "partial", "partially_complete"}


def _task_state(
    *,
    request: KeyframeGenerationRequest,
    provider_task: dict[str, Any],
    status: str,
    provider_prompt: str,
    provider_gate: dict[str, str],
    reference_image_count: int,
    image_operation: str,
    context_bundle: dict[str, Any] | None,
    request_id: str = "",
    client_request_id: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": "afs_keyframe_generation_task_state.v0.1",
        "status": status,
        "provider_service_id": request.provider_service_id,
        "capability": "image",
        "task": _provider_task_for_state(provider_task),
        "request": request.model_dump(mode="json"),
        "provider_prompt": provider_prompt,
        "provider_gate": provider_gate,
        "reference_image_count": reference_image_count,
        "image_operation": image_operation,
        "context_bundle": context_bundle,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider_raw_persisted": False,
        "request_id": request_id,
        "client_request_id": client_request_id,
    }


def _provider_task_for_state(provider_task: dict[str, Any]) -> dict[str, Any]:
    task = dict(provider_task)
    inner = task.get("task")
    if isinstance(inner, dict):
        safe_inner = dict(inner)
        safe_inner.pop("output_dir", None)
        task["task"] = safe_inner
    task.pop("output_dir", None)
    return task


def _provider_task_id(provider_task: dict[str, Any]) -> str:
    task = provider_task.get("task") if isinstance(provider_task.get("task"), dict) else {}
    return str(task.get("task_id") or task.get("id") or "")


def _provider_model(registry: Any, service_id: str) -> str:
    try:
        service = registry.store.service(service_id)
    except Exception:
        return ""
    return str(service.get("model") or "")


def _write_task_state(output_dir: Path, state: dict[str, Any]) -> None:
    _write_json_checked(output_dir / "keyframe_task_state.json", state)


def _write_json_checked(path: Path, payload: dict[str, Any]) -> None:
    reject_unsafe_payload(payload)
    write_json(path, payload)


def _reference_prompt_instruction(request: KeyframeGenerationRequest, reference_count: int) -> str:
    animal_reference = _looks_like_animal_reference_request(request)
    revision = asset_card_revision_reference_instruction(request)
    reference_mode = reference_transform_mode_for_request(request)
    lines = []
    if revision:
        lines.append(revision)
    if reference_mode == ORIGINALIZE_REFERENCE_MODE:
        lines.append(originalize_reference_policy(reference_count))
    elif revision:
        lines.append(
            (
                f"Connected reference images: {reference_count}. For this asset-card revision, reference image #1 is the primary visual source of truth; "
                "other reference images are detail support only. Preserve original identity, sheet composition, proportions, camera distance, view layout, and all non-edited details."
            )
        )
    else:
        lines.append(
            (
                f"Connected reference images: {reference_count}. 参考图只作为本次显式连线的视觉参考。"
                "只保留与用户目标不冲突的相关主体特征、轮廓比例、颜色材质、镜头关系和关键视觉线索。"
                "不要把无关背景、服装、图表、界面文字或旧失败风格带入结果。"
            )
        )
    if animal_reference and reference_mode != ORIGINALIZE_REFERENCE_MODE:
        lines.append(
            "Animal subject reference: 如果参考主体是猫或动物，只保留同一动物主体的毛色、斑纹、眼睛、耳朵、尾巴和体型比例；"
            "未明确要求时不要添加人类头发、服装或拟人身份；不要把参考图当作脸部贴图素材，必须重绘为统一完整的动物主体。"
        )
    params = request.node_parameters or {}
    for item in list(params.get("connected_reference_nodes") or [])[:4]:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        title = str(item.get("title") or "reference").strip()
        if prompt:
            lines.append(f"Reference note {title}: {prompt}")
    return "\n".join(lines)


def _has_asset_card_revision(request: KeyframeGenerationRequest) -> bool:
    params = request.node_parameters if isinstance(request.node_parameters, dict) else {}
    return isinstance(params.get("asset_card_revision"), dict)


def _looks_like_animal_reference_request(request: KeyframeGenerationRequest) -> bool:
    params = request.node_parameters or {}
    parts = [request.prompt_text, request.optimized_prompt]
    for item in list(params.get("connected_reference_nodes") or [])[:4]:
        if isinstance(item, dict):
            parts.extend([str(item.get("title") or ""), str(item.get("prompt") or "")])
    text = " ".join(str(part or "") for part in parts).casefold()
    animal_terms = ("猫", "狸花猫", "黑猫", "白猫", "橘猫", "狗", "犬", "宠物", "动物", "cat", "tabby", "kitten", "feline", "dog", "puppy", "animal", "pet")
    return any(term.casefold() in text for term in animal_terms)


def _gate_closed_block(required_gate: str = REMOTE_IMAGE_ENV) -> dict[str, str]:
    return {
        "block_id": "remote_image_gate_closed",
        "reason": f"Set {required_gate}=true only for an explicit image/keyframe provider smoke.",
        "required_gate": required_gate,
    }


def _provider_failure_block(
    value: str,
    required_gate: str = REMOTE_IMAGE_ENV,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lowered = value.lower()
    if "reference_image_slots exceeded" in lowered:
        block_id = "image_reference_slots_exceeded"
    elif "does not accept reference images" in lowered:
        block_id = "image_relay_reference_unsupported"
    elif "provider service not found" in lowered:
        block_id = "image_provider_service_not_found"
    elif "invalid api key" in lowered or "http error 401" in lowered or "http error 403" in lowered:
        block_id = "image_relay_auth_not_ready"
    elif "api relay http error" in lowered:
        block_id = "image_relay_http_error"
    else:
        block_id = "remote_image_provider_not_ready"
    block: dict[str, Any] = {
        "block_id": block_id,
        "reason": _safe_error(value),
        "required_gate": required_gate,
    }
    if diagnostics:
        for key in (
            "failure_class",
            "provider_stage",
            "retry_count",
            "attempt_count",
            "provider_elapsed_ms",
        ):
            if diagnostics.get(key) not in (None, ""):
                block[key] = diagnostics[key]
    else:
        block["failure_class"] = _failure_class_for_provider_error(value, block_id)
    return block


def _provider_failure_diagnostics(
    error: Exception,
    required_gate: str,
    *,
    retry_count: int,
    provider_elapsed_ms: float | str,
) -> dict[str, Any]:
    reason = str(error)
    block = _provider_failure_block(reason, required_gate)
    failure_class = _failure_class_for_provider_error(reason, str(block.get("block_id") or ""))
    return {
        "provider_stage": _provider_stage_for_error(reason),
        "failure_class": failure_class,
        "error_type": type(error).__name__,
        "reason": _safe_error(reason),
        "required_gate": required_gate,
        "retry_count": retry_count,
        "attempt_count": retry_count + 1,
        "provider_elapsed_ms": provider_elapsed_ms if isinstance(provider_elapsed_ms, (int, float)) else 0,
    }


def _provider_stage_for_error(value: str) -> str:
    lowered = value.lower()
    if "reference_image_slots exceeded" in lowered or "does not accept reference images" in lowered:
        return "request_validation"
    if "image url download" in lowered or "artifact download" in lowered:
        return "provider_image_download"
    if "timed out" in lowered or "timeout" in lowered:
        return "provider_request_read"
    if "http error" in lowered:
        return "provider_http_status"
    if "connection" in lowered or "network" in lowered:
        return "provider_network"
    return "provider_request"


def _failure_class_for_provider_error(value: str, block_id: str = "") -> str:
    lowered = value.lower()
    normalized_block = str(block_id or "").lower()
    if "timeout" in normalized_block or "timed out" in lowered or "timeout" in lowered:
        return "provider_timeout"
    if "gate_closed" in normalized_block:
        return "provider_gate_closed"
    if "reference_image_slots exceeded" in lowered or "does not accept reference images" in lowered:
        return "validation_block"
    if "invalid api key" in lowered or "http error 401" in lowered or "http error 403" in lowered:
        return "provider_not_ready"
    if "http error" in lowered:
        return "provider_http_error"
    return "provider_failed"


def _safe_error(value: str) -> str:
    lowered = value.lower()
    if "status_code 2049" in lowered and "invalid api key" in lowered:
        return "Image relay rejected the configured credential."
    if "reference_image_slots exceeded" in lowered:
        return "Image relay reference image limit was exceeded; check provider descriptor reference_image_slots."
    if "does not accept reference images" in lowered:
        return "Image relay route does not accept reference images for generation; use an edit-capable relay route for reference-guided image generation."
    if "provider service not found" in lowered:
        return "Image relay service is not configured in the Runtime provider registry."
    if "invalid api key" in lowered or "http error 401" in lowered or "http error 403" in lowered:
        return "Image relay credential is not ready."
    if "api relay http error" in lowered:
        return "Image relay request failed at the provider HTTP boundary."
    if any(
        term in lowered
        for term in (
            "api key",
            "api_key",
            "access_token",
            "refresh_token",
            "secret_key",
            "client_secret",
            "signed_url",
            "secret",
            "token",
            "authorization",
            "cookie",
            "bearer ",
        )
    ):
        return "Image relay configuration is not ready."
    return " ".join(value.split())[:160]


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def provider_keyframe_prompt(value: str, *, limit: int = DEFAULT_IMAGE_PROMPT_LIMIT) -> str:
    lines = []
    for raw_line in strip_user_prompt_section_headers(str(value or "")).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(term in lowered for term in _internal_prompt_terms()):
            continue
        lines.append(line)
    prompt = " ".join(lines)
    prompt = " ".join(prompt.split())
    if len(prompt) <= limit:
        return prompt
    return prompt[:limit].rsplit(" ", 1)[0].strip()


def _guarded_provider_keyframe_prompt(
    value: str,
    *,
    reference_count: int = 0,
    asset_card_revision: bool = False,
    originalize_reference: bool = False,
    limit: int = DEFAULT_IMAGE_PROMPT_LIMIT,
) -> str:
    guard = _image_generation_guard(
        reference_count=reference_count,
        asset_card_revision=asset_card_revision,
        originalize_reference=originalize_reference,
    )
    reserve = len(guard) + 1
    base_limit = max(240, limit - reserve)
    prompt = provider_keyframe_prompt(value, limit=base_limit)
    if not prompt:
        return validate_prompt_integrity(provider_keyframe_prompt(guard, limit=limit), field_name="keyframe_provider_prompt")
    if "保真约束" in prompt:
        return validate_prompt_integrity(provider_keyframe_prompt(prompt, limit=limit), field_name="keyframe_provider_prompt")
    return validate_prompt_integrity(provider_keyframe_prompt(f"{prompt} {guard}", limit=limit), field_name="keyframe_provider_prompt")


def _image_generation_guard(
    *,
    reference_count: int = 0,
    asset_card_revision: bool = False,
    originalize_reference: bool = False,
) -> str:
    base = (
        "保真约束：按用户提示直接生成清晰自然的主体，主体身份、物种、材质和关键特征必须可读；"
        "不要改成图标、标志、吉祥物、矢量插画、抽象符号或无关场景，除非用户明确要求这种风格。"
    )
    if reference_count <= 0:
        return base
    if originalize_reference:
        return (
            f"{base} 原创重生参考约束：本次携带 {reference_count} 张参考图；"
            "参考图只能作为灵感、材质情绪、宽泛类别和功能方向证据，不能作为身份、比例、服装、logo、姿势、构图或未修改细节锁定项；"
            "必须重新设计为降低可识别 IP 相似度的新资产。"
        )
    if asset_card_revision:
        return (
            f"{base} 资产卡局部修订约束：本次携带 {reference_count} 张参考图；"
            "reference image #1 is primary visual source of truth；changed fields are only editable delta；不要重设计身份、比例、版式或未编辑细节。"
        )
    return (
        f"{base} 参考图约束：本次携带 {reference_count} 张参考图；"
        "参考图只补充相关视觉线索，不能覆盖用户本次明确的新目标。"
    )


class _DefaultImageDescriptor:
    prompt_char_limit = DEFAULT_IMAGE_PROMPT_LIMIT
    reference_image_slots = DEFAULT_REFERENCE_IMAGE_SLOTS
    required_gate = REMOTE_IMAGE_ENV


def _default_descriptor() -> _DefaultImageDescriptor:
    return _DefaultImageDescriptor()


def _internal_prompt_terms() -> tuple[str, ...]:
    return (
        "provider calls remain off",
        "do not claim provider execution",
        "provider gate",
        "authorization",
        "secret",
        "signed url",
        "media bytes",
        "raw provider",
        "api key",
        "agent rationale:",
        "claim_boundary",
    )


__all__ = (
    "KEYFRAME_NON_CLAIMS",
    "DEFAULT_IMAGE_PROMPT_LIMIT",
    "KEYFRAME_BACKGROUND_SYNC_ENV",
    "REMOTE_IMAGE_ENV",
    "build_keyframe_generation",
    "image_provider_gate",
    "keyframe_sync_background_plan",
    "provider_keyframe_prompt",
)
