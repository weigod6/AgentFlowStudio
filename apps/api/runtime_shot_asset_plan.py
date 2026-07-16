from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from agentflow_studio.model_gateway.errors import ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest, load_provider_registry
from apps.api.runtime_asset_profile_plan import attach_asset_profiles, build_asset_profile_plan
from apps.api.runtime_asset_graph import attach_graph_asset_ids_to_refs, build_asset_graph
from apps.api.runtime_asset_extraction import principal_asset_refs_with_diagnostics
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_llm_enhancement_dispatch import dispatch_llm_with_fallback
from apps.api.runtime_llm_enhancement_gate import llm_provider_gate
from apps.api.runtime_models import ShotAssetPlanRequest
from apps.api.runtime_shot_asset_provider import asset_refs_from_provider_text
from apps.api.runtime_shot_asset_provider_prompt import (
    asset_plan_instruction,
    asset_plan_llm_request,
)
from apps.api.runtime_shot_asset_plan_refs import (
    finalize_asset_refs,
    graph_shot,
    local_asset_refs,
    merge_asset_refs,
    source_text,
    structured_from_request,
)
from apps.api.runtime_store import RuntimeStore, reject_unsafe_payload


ASSET_PLAN_NON_CLAIMS = [
    "not human acceptance",
    "not fixed asset memory",
    "not generated media",
    "not provider smoke",
]

def register_runtime_shot_asset_plan_routes(app: FastAPI, store: RuntimeStore) -> None:
    @app.post("/projects/{project_id}/shot-asset-plans")
    def shot_asset_plan(project_id: str, request: ShotAssetPlanRequest) -> dict[str, Any]:
        try:
            store.ensure_project_manifest(project_id)
            job_id = store.new_job_id("shot_asset_plan", project_id)
            output_dir = store.run_dir(project_id, job_id)
            return build_shot_asset_plan(project_id, request, output_dir=output_dir)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=safe_error_detail("invalid_shot_asset_plan")) from exc


def build_shot_asset_plan(
    project_id: str,
    request: ShotAssetPlanRequest,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    text = source_text(request)
    shot = request.shot if isinstance(request.shot, dict) else {}
    inferred_shot = structured_from_request(shot, text)
    gate = llm_provider_gate()
    provider_calls_started = False
    discard_reason = None
    status = "local_asset_plan"
    refs: list[dict[str, Any]] = []
    dropped_refs: list[dict[str, Any]] = []

    if gate.get("status") != "blocked":
        try:
            run_dir = output_dir or (Path.cwd() / ".runtime" / "shot_asset_plan")
            run_dir.mkdir(parents=True, exist_ok=True)
            registry = load_provider_registry()
            llm_request = asset_plan_llm_request(request, text)
            provider_result = dispatch_llm_with_fallback(
                registry,
                llm_request,
                ProviderDispatchRequest(
                    prompt=asset_plan_instruction(request, text),
                    output_dir=run_dir,
                    task_type="shot_asset_plan",
                    timeout_sec=60.0,
                ),
            )
            provider_calls_started = bool(provider_result.get("provider_calls_started", True))
            refs, dropped_refs = asset_refs_from_provider_text(str(provider_result.get("text") or ""), source_text=text)
            status = "provider_structured_asset_plan"
        except ValueError as exc:
            discard_reason = _safe_reason(str(exc))
            refs = []
            dropped_refs = []
            status = "local_asset_plan"
        except ModelGatewayError as exc:
            discard_reason = _safe_reason(str(exc))
            provider_calls_started = False
            refs = []
            dropped_refs = []
            status = "local_asset_plan"

    if not refs:
        refs = local_asset_refs(request, shot, inferred_shot, text)
        refs, dropped_refs = principal_asset_refs_with_diagnostics(refs)
    else:
        refs = merge_asset_refs(refs, local_asset_refs(request, shot, inferred_shot, text))
        refs = finalize_asset_refs(refs, text)
        refs, dropped_refs = principal_asset_refs_with_diagnostics(refs, dropped_refs)

    shot_for_graph = graph_shot(shot, inferred_shot, refs, text, dropped_refs)
    asset_graph = build_asset_graph([shot_for_graph], source_text=request.script_text or text, graph_source="shot_asset_plan")
    refs = attach_graph_asset_ids_to_refs(refs, asset_graph)
    asset_profile_plan = build_asset_profile_plan(refs, text)
    refs = attach_asset_profiles(refs, text)
    safe_manifest = {
        "artifact_type": "agentflow_shot_asset_plan_safe_manifest",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "node_id": request.node_id,
        "status": status,
        "provider_gate": gate,
        "provider_calls_started": provider_calls_started,
        "discard_reason": discard_reason,
        "raw_provider_response_stored": False,
        "generated_media_bytes_stored": False,
        "asset_nodes_created": False,
        "asset_ref_count": len(refs),
        "asset_profile_count": len(asset_profile_plan),
        "asset_graph_asset_count": int(asset_graph.get("asset_count") or 0),
        "unsupported_addition_count": len(asset_graph.get("unsupported_additions") or []),
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": ASSET_PLAN_NON_CLAIMS,
    }
    payload = {
        "project_id": project_id,
        "node_id": request.node_id,
        "asset_refs": refs,
        "asset_profile_plan": asset_profile_plan,
        "asset_graph": asset_graph,
        "asset_nodes_created": False,
        "provider_gate": gate,
        "provider_calls_started": provider_calls_started,
        "safe_manifest": safe_manifest,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": ASSET_PLAN_NON_CLAIMS,
    }
    reject_unsafe_payload(safe_manifest)
    reject_unsafe_payload(asset_graph)
    reject_unsafe_payload(payload)
    return payload


def _safe_reason(value: str) -> str:
    lowered = value.lower()
    if any(term in lowered for term in ("api", "key", "secret", "token", "authorization", "cookie")):
        return "llm provider configuration is not ready"
    return " ".join(value.split())[:160] or "llm provider is not ready"


__all__ = (
    "ASSET_PLAN_NON_CLAIMS",
    "build_shot_asset_plan",
    "register_runtime_shot_asset_plan_routes",
)
