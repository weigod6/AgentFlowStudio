from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentflow.algorithms.request_projection import build_request_plan
from agentflow_studio.model_gateway.errors import ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest
from apps.api.generation_path_contract import generation_path_submit_error
from apps.api.runtime_errors import RuntimeApiError
from apps.api.runtime_file_logging import runtime_file_event
from apps.api.runtime_generation_preflight import video_generation_preflight
from apps.api.runtime_image_assets import image_asset_file_path, image_asset_metadata
from apps.api.runtime_media_validation import reference_image_size_blocks
from apps.api.runtime_model_call_context import video_generation_model_call_context
from apps.api.runtime_models import VideoGenerationRequest
from apps.api.runtime_store import RuntimeStore, read_json
from apps.api.runtime_video_contract import (
    video_duration_contract,
    video_generation_path_contract,
    video_input_mode,
    video_input_source_contract,
)
from apps.api.runtime_video_candidates import safe_outputs
from apps.api.runtime_video_constants import REMOTE_VIDEO_ENV
from apps.api.runtime_video_gate import gate_closed_block, provider_not_ready_block, video_gate
from apps.api.runtime_video_manifest import (
    result_from_manifest,
    safe_manifest,
    write_json_checked,
    write_model_call_artifacts,
)
from apps.api.runtime_video_prompt import video_generation_plan, video_provider_prompt
from apps.api.runtime_video_task_state import (
    daily_submit_count,
    daily_submit_limit,
    increment_daily_submit_count,
    load_task_state,
    provider_task_for_poll,
    provider_task_for_state,
    write_task_state,
)


PROMPT_LOG_PREVIEW_CHARS = 4000
VIDEO_PROMPT_RISK_TERMS = (
    "对峙",
    "冲突",
    "蓄势",
    "武器",
    "攻击",
    "危险",
    "暴力",
    "打斗",
    "爆炸",
    "血",
    "枪",
    "刀",
    "threat",
    "conflict",
    "weapon",
    "attack",
    "violence",
    "blood",
    "gun",
    "knife",
)


def submit_video_generation(
    store: RuntimeStore,
    project_id: str,
    job_id: str,
    request: VideoGenerationRequest,
    output_dir: Path,
    *,
    load_registry: Callable[[], Any],
    request_id: str = "",
    client_request_id: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    runtime_file_event(
        "video",
        "submit_start",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        job_id=job_id,
        provider_service_id=request.provider_service_id,
        duration_sec=request.duration_sec,
        resolution=request.resolution,
        aspect_ratio=request.aspect_ratio,
        candidate_count=request.candidate_count,
        generation_path=request.generation_path or "",
        input_mode=video_input_mode(request),
        input_source_mode=video_input_source_contract(request).get("source_mode"),
    )
    if request.candidate_count != 1:
        raise RuntimeApiError(
            "invalid_candidate_count",
            "视频生成当前只支持 1 个候选。",
            stage="request_validation",
            user_action="请将候选数量设置为 1 后重新生成。",
            details={"candidate_count": request.candidate_count, "allowed": 1},
        )
    if path_error := generation_path_submit_error(request):
        raise RuntimeApiError(
            "unsupported_generation_path",
            path_error["message"],
            stage=path_error["stage"],
            user_action="Choose a supported generation path before submitting to a video provider.",
            details=path_error["details"],
        )
    try:
        first_frame_path = image_asset_file_path(store, project_id, request.first_frame_image_asset_id)
        frame_metadata = [image_asset_metadata(store, project_id, request.first_frame_image_asset_id)]
    except (KeyError, ValueError) as exc:
        raise RuntimeApiError(
            "first_frame_asset_not_found",
            "首帧图片不存在或已失效。",
            stage="first_frame_resolve",
            user_action="请重新上传图片，并在节点菜单中设为首帧后再生成视频。",
            details={"asset_id": request.first_frame_image_asset_id},
        ) from exc
    runtime_file_event(
        "video",
        "first_frame_resolved",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        job_id=job_id,
        provider_service_id=request.provider_service_id,
        first_frame_asset_id=request.first_frame_image_asset_id,
        elapsed_ms=_elapsed_ms(started),
    )
    last_frame_path = None
    if request.last_frame_image_asset_id:
        try:
            last_frame_path = image_asset_file_path(store, project_id, request.last_frame_image_asset_id)
            frame_metadata.append(image_asset_metadata(store, project_id, request.last_frame_image_asset_id))
        except (KeyError, ValueError) as exc:
            raise RuntimeApiError(
                "last_frame_asset_not_found",
                "尾帧图片不存在或已失效。",
                stage="last_frame_resolve",
                user_action="请重新上传图片，并在节点菜单中设为尾帧后再生成视频。",
                details={"asset_id": request.last_frame_image_asset_id},
            ) from exc
    preflight = video_generation_preflight(store, project_id, request)
    context_bundle = preflight.get("context_bundle")
    registry = None
    descriptor = None
    descriptor_error = None
    try:
        registry = load_registry()
        descriptor = registry.descriptor(request.provider_service_id)
    except ModelGatewayError as exc:
        descriptor_error = exc
    prompt_limit = int(getattr(descriptor, "prompt_char_limit", 4000) or 4000)
    provider_prompt = video_provider_prompt(request, context_bundle, limit=prompt_limit)
    prompt_log_fields = _provider_prompt_log_fields(provider_prompt)
    runtime_file_event(
        "video",
        "provider_prompt_built",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        job_id=job_id,
        provider_service_id=request.provider_service_id,
        duration_sec=request.duration_sec,
        resolution=request.resolution,
        aspect_ratio=request.aspect_ratio,
        elapsed_ms=_elapsed_ms(started),
        **prompt_log_fields,
    )
    generation_plan = video_generation_plan(request, context_bundle)
    model_call_context = _model_call_context(project_id, request, context_bundle)
    model_request_plan = build_request_plan(
        model_call_context=model_call_context,
        canonical_brief={"canonical_prompt": provider_prompt},
        provider_service_id=request.provider_service_id,
    )
    model_request_plan["generation_plan"] = generation_plan
    artifacts = write_model_call_artifacts(store, output_dir, model_call_context, model_request_plan)
    if descriptor_error is not None or registry is None or descriptor is None:
        reason = str(descriptor_error or "provider descriptor unavailable")
        runtime_file_event(
            "video",
            "blocked",
            level="WARNING",
            request_id=request_id,
            client_request_id=client_request_id,
            project_id=project_id,
            node_id=request.node_id,
            job_id=job_id,
            provider_service_id=request.provider_service_id,
            reason="provider_not_ready",
            message=reason,
            elapsed_ms=_elapsed_ms(started),
        )
        return _blocked_result(project_id, output_dir, context_bundle, model_call_context, artifacts, model_request_plan, provider_not_ready_block(reason))
    provider_model = _provider_model(registry, request.provider_service_id)
    required_gate = str(getattr(descriptor, "required_gate", REMOTE_VIDEO_ENV) or REMOTE_VIDEO_ENV)
    gate = video_gate(required_gate)
    if gate["status"] == "blocked":
        runtime_file_event(
            "video",
            "blocked",
            level="WARNING",
            request_id=request_id,
            client_request_id=client_request_id,
            project_id=project_id,
            node_id=request.node_id,
            job_id=job_id,
            provider_service_id=request.provider_service_id,
            reason="provider_gate_closed",
            required_gate=required_gate,
            elapsed_ms=_elapsed_ms(started),
        )
        return _blocked_result(project_id, output_dir, context_bundle, model_call_context, artifacts, model_request_plan, gate_closed_block(required_gate), gate)
    _validate_provider_request(request, descriptor)
    runtime_file_event(
        "video",
        "provider_capability_checked",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        job_id=job_id,
        provider_service_id=request.provider_service_id,
        duration_sec=request.duration_sec,
        resolution=request.resolution,
        aspect_ratio=request.aspect_ratio,
        input_mode=video_input_mode(request),
        elapsed_ms=_elapsed_ms(started),
    )
    min_edge = int(getattr(descriptor, "min_reference_image_edge_px", 0) or 0)
    if size_blocks := reference_image_size_blocks(frame_metadata, min_edge_px=min_edge, capability="video", required_gate=required_gate):
        runtime_file_event(
            "video",
            "blocked",
            level="WARNING",
            request_id=request_id,
            client_request_id=client_request_id,
            project_id=project_id,
            node_id=request.node_id,
            job_id=job_id,
            provider_service_id=request.provider_service_id,
            reason=str(size_blocks[0].get("reason") or "reference_image_size"),
            block_count=len(size_blocks),
            elapsed_ms=_elapsed_ms(started),
        )
        return _blocked_result(project_id, output_dir, context_bundle, model_call_context, artifacts, model_request_plan, size_blocks[0], gate, blocks=size_blocks)
    current_count = daily_submit_count(store, project_id)
    limit = daily_submit_limit()
    if current_count >= limit and not request.quota_override_confirmed:
        raise RuntimeApiError(
            "video_daily_quota_exceeded",
            "今日该项目视频生成次数已达到限制。",
            stage="quota_check",
            user_action="请明天再试，或在确认继续提交后重试。",
            details={
                "limit": limit,
                "current_count": current_count,
                "requires": "quota_override_confirmed",
            },
        )
    reference_paths = (first_frame_path, last_frame_path) if last_frame_path else (first_frame_path,)
    dispatch_request = ProviderDispatchRequest(
        prompt=provider_prompt,
        output_dir=output_dir,
        aspect_ratio=request.aspect_ratio,
        candidate_count=1,
        reference_image_paths=reference_paths,
        subject_reference_image_path=first_frame_path,
        duration_sec=request.duration_sec,
        resolution=request.resolution,
        motion=request.motion,
        input_mode=video_input_mode(request),
        input_source=video_input_source_contract(request),
        duration_contract=video_duration_contract(request.duration_sec),
    )
    runtime_file_event(
        "video",
        "provider_call",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        job_id=job_id,
        provider_service_id=request.provider_service_id,
        model=provider_model,
        **prompt_log_fields,
    )
    try:
        provider_started = time.perf_counter()
        provider_task = registry.submit("video", request.provider_service_id, dispatch_request)
        provider_elapsed_ms = _elapsed_ms(provider_started)
    except (ModelGatewayError, Exception) as exc:
        provider_elapsed_ms = _elapsed_ms(provider_started) if "provider_started" in locals() else ""
        provider_error_summary = _provider_error_summary(exc)
        runtime_file_event(
            "video",
            "submit_failed",
            level="ERROR",
            request_id=request_id,
            client_request_id=client_request_id,
            project_id=project_id,
            node_id=request.node_id,
            job_id=job_id,
            provider_service_id=request.provider_service_id,
            error=type(exc).__name__,
            message=str(exc),
            provider_elapsed_ms=provider_elapsed_ms,
            elapsed_ms=_elapsed_ms(started),
            **provider_error_summary,
        )
        return _poll_failed_result(
            project_id,
            output_dir,
            context_bundle,
            model_call_context,
            artifacts,
            model_request_plan,
            gate,
            str(exc),
            provider_error_summary=provider_error_summary,
        )
    increment_daily_submit_count(store, project_id)
    task_state = _task_state(request, provider_task, context_bundle, model_call_context, generation_plan, request_id=request_id, client_request_id=client_request_id)
    write_task_state(output_dir, task_state)
    runtime_file_event(
        "video",
        "submitted",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        node_id=request.node_id,
        job_id=job_id,
        provider_service_id=request.provider_service_id,
        provider_task_id=_provider_task_id(provider_task),
        status=task_state["status"],
        provider_elapsed_ms=provider_elapsed_ms,
        elapsed_ms=_elapsed_ms(started),
    )
    if task_state["status"] == "already_complete":
        return complete_video_result(
            output_dir,
            project_id,
            provider_task.get("task", {}).get("raw") or {},
            task_state,
            gate,
            artifacts=artifacts,
            model_call_context=model_call_context,
            model_request_plan=model_request_plan,
            submit_elapsed_ms=_elapsed_ms(started),
            provider_elapsed_ms=provider_elapsed_ms,
        )
    manifest = safe_manifest(
        project_id,
        status="submitted",
        provider_calls_started=True,
        provider_gate=gate,
        context_bundle=context_bundle,
        model_call_context_id=model_call_context["context_id"],
        **_manifest_contract_kwargs(model_call_context, model_request_plan),
    )
    write_json_checked(output_dir / "video_generation_safe_manifest.json", manifest)
    return result_from_manifest(status="submitted", safe_manifest=manifest, task_state=task_state, context_bundle=context_bundle, artifacts=artifacts, model_call_context=model_call_context, model_request_plan=model_request_plan)


def poll_video_generation(store: RuntimeStore, project_id: str, output_dir: Path, *, load_registry: Callable[[], Any], request_id: str = "", client_request_id: str = "") -> dict[str, Any]:
    started = time.perf_counter()
    state = load_task_state(output_dir)
    job_id = output_dir.name
    request_id = request_id or str(state.get("request_id") or "")
    client_request_id = client_request_id or str(state.get("client_request_id") or "")
    if state.get("status") in {"succeeded", "cancelled_local_only"}:
        return result_from_manifest(status=str(state["status"]), safe_manifest=read_json(output_dir / "video_generation_safe_manifest.json"), task_state=state)
    provider_service_id = str(state.get("provider_service_id") or "")
    poll_time = _utc_now()
    runtime_file_event(
        "video",
        "poll_start",
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=project_id,
        job_id=job_id,
        provider_service_id=provider_service_id,
        status=state.get("status"),
    )
    try:
        provider_started = time.perf_counter()
        raw = load_registry().poll("video", provider_service_id, provider_task_for_poll(state.get("task"), output_dir))
        provider_elapsed_ms = _elapsed_ms(provider_started)
    except (ModelGatewayError, Exception) as exc:
        provider_elapsed_ms = _elapsed_ms(provider_started) if "provider_started" in locals() else ""
        provider_error_summary = _provider_error_summary(exc)
        runtime_file_event(
            "video",
            "poll_failed",
            level="ERROR",
            request_id=request_id,
            client_request_id=client_request_id,
            project_id=project_id,
            job_id=job_id,
            provider_service_id=provider_service_id,
            error=type(exc).__name__,
            message=str(exc),
            provider_elapsed_ms=provider_elapsed_ms,
            elapsed_ms=_elapsed_ms(started),
            **provider_error_summary,
        )
        manifest = safe_manifest(
            project_id,
            status="poll_failed",
            provider_calls_started=True,
            blocks=[_provider_not_ready_block(str(exc), provider_error_summary)],
            context_bundle=_context_bundle(state),
            **_manifest_contract_kwargs_from_state(state),
        )
        write_json_checked(output_dir / "video_generation_safe_manifest.json", manifest)
        state["status"] = "poll_failed"
        state["last_poll_at"] = poll_time
        state["completed_at"] = poll_time
        write_task_state(output_dir, state)
        return result_from_manifest(status="poll_failed", safe_manifest=manifest, task_state=state)
    if str(raw.get("status") or "").lower() == "running":
        runtime_file_event(
            "video",
            "poll_running",
            request_id=request_id,
            client_request_id=client_request_id,
            project_id=project_id,
            job_id=job_id,
            provider_service_id=provider_service_id,
            provider_elapsed_ms=provider_elapsed_ms,
            elapsed_ms=_elapsed_ms(started),
        )
        manifest = safe_manifest(
            project_id,
            status="running",
            provider_calls_started=True,
            blocks=[],
            context_bundle=_context_bundle(state),
            **_manifest_contract_kwargs_from_state(state),
        )
        write_json_checked(output_dir / "video_generation_safe_manifest.json", manifest)
        state["status"] = "running"
        state.setdefault("running_started_at", poll_time)
        state["last_poll_at"] = poll_time
        state["last_provider_poll"] = {"status": "running", "task": raw.get("task") or {}, "provider_raw_persisted": False}
        write_task_state(output_dir, state)
        return result_from_manifest(status="running", safe_manifest=manifest, task_state=state)
    return complete_video_result(
        output_dir,
        project_id,
        raw,
        state,
        video_gate(REMOTE_VIDEO_ENV),
        poll_elapsed_ms=_elapsed_ms(started),
        provider_elapsed_ms=provider_elapsed_ms,
    )


def complete_video_result(
    output_dir: Path,
    project_id: str,
    raw: dict[str, Any],
    task_state: dict[str, Any],
    provider_gate: dict[str, str],
    *,
    artifacts: dict[str, Any] | None = None,
    model_call_context: dict[str, Any] | None = None,
    model_request_plan: dict[str, Any] | None = None,
    submit_elapsed_ms: float | None = None,
    poll_elapsed_ms: float | None = None,
    provider_elapsed_ms: float | None = None,
) -> dict[str, Any]:
    outputs = safe_outputs(output_dir, raw, allow_fake_placeholder=_allow_fake_video_placeholder(task_state, raw))
    context_bundle = _context_bundle(task_state)
    completed_at = _utc_now()
    status = "succeeded" if outputs else "needs_attention"
    blocks = [] if outputs else [_video_output_missing_block()]
    task_state["status"] = status
    task_state.setdefault("running_started_at", task_state.get("created_at") or completed_at)
    task_state["last_poll_at"] = completed_at
    task_state["completed_at"] = completed_at
    write_task_state(output_dir, task_state)
    manifest = safe_manifest(
        project_id,
        status=status,
        provider_calls_started=True,
        provider_gate=provider_gate,
        blocks=blocks,
        outputs=outputs,
        context_bundle=context_bundle,
        model_call_context_id=str(task_state.get("model_call_context_id") or ""),
        **_manifest_contract_kwargs_from_state(task_state),
    )
    write_json_checked(output_dir / "video_generation_safe_manifest.json", manifest)
    runtime_file_event(
        "video",
        "succeeded" if outputs else "needs_attention",
        request_id=task_state.get("request_id"),
        client_request_id=task_state.get("client_request_id"),
        project_id=project_id,
        job_id=output_dir.name,
        provider_service_id=task_state.get("provider_service_id"),
        candidate=(outputs[0].get("candidate_id") if outputs else ""),
        elapsed_ms=submit_elapsed_ms if submit_elapsed_ms is not None else poll_elapsed_ms,
        provider_elapsed_ms=provider_elapsed_ms,
    )
    return result_from_manifest(status=status, safe_manifest=manifest, task_state=task_state, outputs=outputs, context_bundle=context_bundle, artifacts=artifacts, model_call_context=model_call_context, model_request_plan=model_request_plan)


def _model_call_context(project_id: str, request: VideoGenerationRequest, context_bundle: dict[str, Any] | None) -> dict[str, Any]:
    return video_generation_model_call_context(
        project_id=project_id,
        request=request,
        context_bundle=context_bundle,
        provider_constraints={
            "capability": "video",
            "provider_service_id": request.provider_service_id,
            "required_gate": REMOTE_VIDEO_ENV,
            "generation_path": video_generation_path_contract(request)["path_id"],
            "generation_path_contract": video_generation_path_contract(request),
            "duration_sec": request.duration_sec,
            "duration_contract": video_duration_contract(request.duration_sec),
            "resolution": request.resolution,
            "aspect_ratio": request.aspect_ratio,
            "input_mode": video_input_mode(request),
            "reference_image_slots": 1 + int(bool(request.last_frame_image_asset_id)),
        },
    )


def _validate_provider_request(request: VideoGenerationRequest, descriptor: Any) -> None:
    supported_durations = [int(item) for item in getattr(descriptor, "supported_durations_sec", []) or []]
    if supported_durations and request.duration_sec not in supported_durations:
        raise RuntimeApiError(
            "unsupported_duration",
            "当前视频模型不支持该视频时长。",
            stage="provider_capability_check",
            user_action=f"请将视频时长改为：{', '.join(str(item) + 's' for item in supported_durations)}。",
            details={
                "duration_sec": request.duration_sec,
                "allowed": supported_durations,
                "provider_service_id": request.provider_service_id,
            },
        )
    input_mode = video_input_mode(request)
    supported_input_modes = [str(item) for item in getattr(descriptor, "frame_modes", []) or []]
    if supported_input_modes and input_mode not in supported_input_modes:
        raise RuntimeApiError(
            "unsupported_input_mode",
            "褰撳墠瑙嗛妯″瀷涓嶆敮鎸佽棣栧抚/棣栧熬甯ц緭鍏ユā寮忋€?",
            stage="provider_capability_check",
            user_action=f"璇锋敼涓烘ā鍨嬫敮鎸佺殑杈撳叆妯″紡锛?{', '.join(supported_input_modes)}銆?",
            details={
                "input_mode": input_mode,
                "allowed": supported_input_modes,
                "provider_service_id": request.provider_service_id,
            },
        )
    supported_resolutions = [str(item).lower() for item in getattr(descriptor, "supported_resolutions", []) or []]
    if supported_resolutions and request.resolution.lower() not in supported_resolutions:
        raise RuntimeApiError(
            "unsupported_resolution",
            "当前视频模型不支持该分辨率。",
            stage="provider_capability_check",
            user_action=f"请将分辨率改为：{', '.join(supported_resolutions)}。",
            details={
                "resolution": request.resolution,
                "allowed": supported_resolutions,
                "provider_service_id": request.provider_service_id,
            },
        )
    supported_ratios = [str(item) for item in getattr(descriptor, "supported_aspect_ratios", []) or []]
    if supported_ratios and request.aspect_ratio not in supported_ratios:
        raise RuntimeApiError(
            "unsupported_aspect_ratio",
            "当前视频模型不支持该画幅比例。",
            stage="provider_capability_check",
            user_action=f"请将画幅比例改为：{', '.join(supported_ratios)}。",
            details={
                "aspect_ratio": request.aspect_ratio,
                "allowed": supported_ratios,
                "provider_service_id": request.provider_service_id,
            },
        )


def _blocked_result(project_id: str, output_dir: Path, context_bundle: dict[str, Any] | None, model_call_context: dict[str, Any], artifacts: dict[str, Any], model_request_plan: dict[str, Any], block: dict[str, Any], provider_gate: dict[str, str] | None = None, *, blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = safe_manifest(
        project_id,
        status="blocked",
        provider_calls_started=False,
        provider_gate=provider_gate,
        blocks=blocks or [block],
        context_bundle=context_bundle,
        model_call_context_id=model_call_context["context_id"],
        **_manifest_contract_kwargs(model_call_context, model_request_plan),
    )
    write_json_checked(output_dir / "video_generation_safe_manifest.json", manifest)
    return result_from_manifest(status="blocked", safe_manifest=manifest, context_bundle=context_bundle, artifacts=artifacts, model_call_context=model_call_context, model_request_plan=model_request_plan)


def _poll_failed_result(
    project_id: str,
    output_dir: Path,
    context_bundle: dict[str, Any] | None,
    model_call_context: dict[str, Any],
    artifacts: dict[str, Any],
    model_request_plan: dict[str, Any],
    gate: dict[str, str],
    reason: str,
    *,
    provider_error_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = safe_manifest(
        project_id,
        status="poll_failed",
        provider_calls_started=True,
        provider_gate=gate,
        blocks=[_provider_not_ready_block(reason, provider_error_summary or {})],
        context_bundle=context_bundle,
        model_call_context_id=model_call_context["context_id"],
        **_manifest_contract_kwargs(model_call_context, model_request_plan),
    )
    write_json_checked(output_dir / "video_generation_safe_manifest.json", manifest)
    return result_from_manifest(status="poll_failed", safe_manifest=manifest, context_bundle=context_bundle, artifacts=artifacts, model_call_context=model_call_context, model_request_plan=model_request_plan)


def _provider_not_ready_block(reason: str, provider_error_summary: dict[str, Any]) -> dict[str, Any]:
    block: dict[str, Any] = dict(provider_not_ready_block(reason))
    block.update({key: value for key, value in provider_error_summary.items() if value not in (None, "")})
    return block


def _video_output_missing_block() -> dict[str, Any]:
    return {
        "block_id": "remote_video_output_missing",
        "reason": "Video provider did not return a reviewable video artifact.",
        "required_gate": REMOTE_VIDEO_ENV,
    }


def _allow_fake_video_placeholder(task_state: dict[str, Any], raw: dict[str, Any]) -> bool:
    service_id = str(task_state.get("provider_service_id") or "").lower()
    provider = str(raw.get("provider") or raw.get("source") or "").lower()
    return service_id.startswith("fake") or provider in {"fake", "fixture"}


def _provider_error_summary(error: Exception) -> dict[str, Any]:
    raw = getattr(error, "provider_error_summary", None)
    if not isinstance(raw, dict):
        return {}
    summary: dict[str, Any] = {}
    status = _safe_int(raw.get("provider_http_status"))
    if status:
        summary["provider_http_status"] = status
    for key in ("provider_error_stage", "provider_error_code", "provider_error_message"):
        value = _safe_summary_text(raw.get(key), limit=180 if key == "provider_error_message" else 80)
        if value:
            summary[key] = value
    if "provider_raw_response_stored" in raw:
        summary["provider_raw_response_stored"] = False
    return summary


def _provider_prompt_log_fields(prompt: str) -> dict[str, Any]:
    normalized = _compact_prompt(prompt)
    return {
        "provider_prompt": normalized[:PROMPT_LOG_PREVIEW_CHARS],
        "provider_prompt_length": len(normalized),
        "provider_prompt_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16],
        "provider_prompt_truncated": len(normalized) > PROMPT_LOG_PREVIEW_CHARS,
        "provider_prompt_risk_terms": _matched_prompt_risk_terms(normalized),
    }


def _compact_prompt(prompt: str) -> str:
    text = " ".join(str(prompt or "").split()).strip()
    text = re.sub(r"data:[^\s]+", "[data-url omitted]", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "[url omitted]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\b[a-z]:\\\S+|/(?:home|users|tmp|var)/\S+", "[path omitted]", text)
    return text


def _matched_prompt_risk_terms(prompt: str) -> list[str]:
    lowered = prompt.lower()
    matched: list[str] = []
    for term in VIDEO_PROMPT_RISK_TERMS:
        if term.lower() in lowered and term not in matched:
            matched.append(term)
    return matched[:12]


def _safe_summary_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    lowered = text.lower()
    if not text:
        return ""
    if any(fragment in lowered for fragment in ("api_key", "access_token", "refresh_token", "secret_key", "client_secret", "authorization:", "bearer ", "cookie=", "signed_url")):
        return "Video provider returned an unsafe error detail."
    if any(fragment in lowered for fragment in ("c:\\", "d:\\", "data/processed/runs", ".mp4", ".mov")):
        return "Video provider returned an unsafe error detail."
    return text[:limit]


def _safe_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _task_state(
    request: VideoGenerationRequest,
    provider_task: dict[str, Any],
    context_bundle: dict[str, Any] | None,
    model_call_context: dict[str, Any],
    generation_plan: dict[str, Any],
    *,
    request_id: str = "",
    client_request_id: str = "",
) -> dict[str, Any]:
    now = _utc_now()
    return {
        "schema_version": "afs_video_generation_task_state.v0.1",
        "status": str((provider_task.get("task") or {}).get("status") or "submitted"),
        "provider_service_id": request.provider_service_id,
        "capability": "video",
        "task": provider_task_for_state(provider_task),
        "first_frame_image_asset_id": request.first_frame_image_asset_id,
        "last_frame_image_asset_id": request.last_frame_image_asset_id,
        "input_source": video_input_source_contract(request),
        "input_mode": video_input_mode(request),
        "generation_path_contract": video_generation_path_contract(request),
        "duration_contract": video_duration_contract(request.duration_sec),
        "created_at": now,
        "submitted_at": now,
        "provider_raw_persisted": False,
        "request_id": request_id,
        "client_request_id": client_request_id,
        "context_bundle": context_bundle,
        "model_call_context_id": model_call_context["context_id"],
        "model_request_plan_ref": "model_request_plan.json",
        "video_generation_plan": generation_plan,
    }


def _manifest_contract_kwargs(model_call_context: dict[str, Any], model_request_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    reference_context = model_call_context.get("reference_context") if isinstance(model_call_context, dict) else {}
    preference_context = model_call_context.get("preference_context") if isinstance(model_call_context, dict) else {}
    provider_constraints = model_call_context.get("provider_constraints") if isinstance(model_call_context, dict) else {}
    plan = model_request_plan if isinstance(model_request_plan, dict) else {}
    input_source = plan.get("input_source") if isinstance(plan.get("input_source"), dict) else (
        reference_context.get("input_source") if isinstance(reference_context, dict) else {}
    )
    duration_contract = plan.get("duration_contract") if isinstance(plan.get("duration_contract"), dict) else (
        preference_context.get("duration_contract") if isinstance(preference_context, dict) else {}
    )
    return {
        "input_source": input_source if isinstance(input_source, dict) else {},
        "input_mode": str(provider_constraints.get("input_mode") or ""),
        "generation_path_contract": provider_constraints.get("generation_path_contract")
        if isinstance(provider_constraints.get("generation_path_contract"), dict)
        else {},
        "duration_contract": duration_contract if isinstance(duration_contract, dict) else {},
    }


def _manifest_contract_kwargs_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_source": state.get("input_source") if isinstance(state.get("input_source"), dict) else {},
        "input_mode": str(state.get("input_mode") or ""),
        "generation_path_contract": state.get("generation_path_contract")
        if isinstance(state.get("generation_path_contract"), dict)
        else {},
        "duration_contract": state.get("duration_contract") if isinstance(state.get("duration_contract"), dict) else {},
    }


def _context_bundle(state: dict[str, Any]) -> dict[str, Any] | None:
    return state.get("context_bundle") if isinstance(state.get("context_bundle"), dict) else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider_task_id(provider_task: dict[str, Any]) -> str:
    task = provider_task.get("task") if isinstance(provider_task.get("task"), dict) else {}
    return str(task.get("task_id") or task.get("id") or "")


def _provider_model(registry: Any, service_id: str) -> str:
    try:
        service = registry.store.service(service_id)
    except Exception:
        return ""
    return str(service.get("model") or "")


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
