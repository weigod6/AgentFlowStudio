from __future__ import annotations
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from agentflow.algorithms.asset_card_candidates import build_asset_card_candidates
from agentflow.algorithms.content_quality_evaluation import evaluate_storyboard_content_quality
from agentflow.algorithms.evidence_ledger import build_storyboard_evidence_ledger
from agentflow.algorithms.production_graph import build_storyboard_production_graph
from agentflow_studio.model_gateway.errors import ModelGatewayError
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest, load_provider_registry
from apps.api.runtime_asset_graph import attach_graph_asset_ids_to_shots, build_asset_graph
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_flow import build_flow_summary
from apps.api.runtime_jobs import runtime_job
from apps.api.runtime_llm_enhancement_dispatch import dispatch_llm_with_fallback
from apps.api.runtime_llm_enhancement_gate import llm_provider_gate
from apps.api.runtime_models import StoryboardBreakdownRequest
from apps.api.runtime_storyboard_artifacts import write_storyboard_artifacts
from apps.api.runtime_storyboard_fallback import storyboard_fallback_message
from apps.api.runtime_storyboard_fixed_assets import attach_fixed_visual_asset_refs
from apps.api.runtime_storyboard_knowledge import (
    knowledge_rule_ids,
    storyboard_instruction,
    storyboard_knowledge_context,
    storyboard_llm_request,
)
from apps.api.runtime_storyboard_local import local_storyboard_shots
from apps.api.runtime_storyboard_provider_parse import shots_from_provider_text
from apps.api.runtime_store import RuntimeStore, reject_unsafe_payload
from apps.api.runtime_tracing import artifact_refs, write_run_trace
from apps.api.runtime_visual_assets import list_visual_assets, public_visual_asset


STORYBOARD_NON_CLAIMS = [
    "not human acceptance",
    "not fixed asset memory",
    "not provider smoke when provider_calls_started is false",
]


def register_runtime_storyboard_routes(app: FastAPI, store: RuntimeStore) -> None:
    @app.post("/projects/{project_id}/storyboard-breakdowns")
    def storyboard_breakdown(project_id: str, request: StoryboardBreakdownRequest) -> dict[str, Any]:
        store.ensure_project_manifest(project_id)
        job_id = store.new_job_id("storyboard_breakdown", project_id)
        output_dir = store.run_dir(project_id, job_id)
        try:
            fixed_visual_assets = [public_visual_asset(item) for item in list_visual_assets(store, project_id, status="fixed")]
            result = build_storyboard_breakdown(project_id, request, output_dir, fixed_visual_assets=fixed_visual_assets)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=safe_error_detail("invalid_storyboard_breakdown")) from exc

        artifacts = write_storyboard_artifacts(store, output_dir, result)
        trace_path = write_run_trace(
            output_dir,
            project_id=project_id,
            job_id=job_id,
            action="storyboard_breakdown",
            status="succeeded",
            input_refs=[
                {"role": "node_id", "ref": request.node_id or "not_provided"},
                {"role": "script_text", "ref": "request_body.script_text"},
                {"role": "target_platform", "ref": request.target_platform},
                {"role": "style", "ref": request.style},
            ],
            generated_artifact_refs=artifact_refs(artifacts),
            tester_feedback={"status": "storyboard_breakdown_ready_for_human_review"},
            tool_gate_state={
                "remote_llm": str(result["provider_gate"].get("status") or "blocked"),
                "remote_asr": "not_requested",
                "remote_image": "not_requested",
                "remote_video": "not_requested",
                "remote_vision": "not_requested",
            },
        )
        artifacts["agentflow_run_trace"] = store.register_artifact(trace_path, role="agentflow_run_trace")
        public_job = store.write_job(runtime_job(job_id, project_id, "storyboard_breakdown", "succeeded", artifacts=artifacts))
        return {
            "job": public_job,
            "shots": result["shots"],
            "asset_graph": result["asset_graph"],
            "asset_auto_binding_graph": result["asset_auto_binding_graph"],
            "content_quality_report": result["content_quality_report"],
            "production_graph": result["production_graph"],
            "asset_card_candidates": result["asset_card_candidates"],
            "evidence_ledger": result["evidence_ledger"],
            "provider_gate": result["provider_gate"],
            "provider_calls_started": result["provider_calls_started"],
            "fallback_reason": result["fallback_reason"],
            "fallback_message": result["fallback_message"],
            "fallback_visible_to_user": result["fallback_visible_to_user"],
            "writes_long_term_memory": False,
            "writes_company_kb": False,
            "safe_manifest": result["safe_manifest"],
            "artifacts": artifacts,
            "flow": build_flow_summary(store, project_id),
            "non_claims": STORYBOARD_NON_CLAIMS,
        }


def build_storyboard_breakdown(
    project_id: str,
    request: StoryboardBreakdownRequest,
    output_dir: Path,
    *,
    fixed_visual_assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gate = llm_provider_gate()
    llm_request = storyboard_llm_request(request)
    storyboard_knowledge = storyboard_knowledge_context(request)
    provider_calls_started = False
    shots: list[dict[str, Any]] | None = None
    status = "local_fallback"
    discard_reason = None
    fallback_reason = "llm_gate_blocked" if gate["status"] == "blocked" else None
    if gate["status"] != "blocked":
        try:
            registry = load_provider_registry()
            dispatch_request = ProviderDispatchRequest(
                prompt=storyboard_instruction(request, storyboard_knowledge),
                output_dir=output_dir,
                task_type="storyboard_breakdown",
            )
            provider_result = dispatch_llm_with_fallback(registry, llm_request, dispatch_request)
            provider_calls_started = bool(provider_result.get("provider_calls_started", True))
            shots = shots_from_provider_text(str(provider_result.get("text") or ""), source_script_text=request.script_text)
            status = "provider_structured"
        except ValueError as exc:
            discard_reason = _safe_reason(str(exc))
            shots = None
            status = "local_fallback"
            fallback_reason = "provider_output_discarded" if provider_calls_started else "provider_output_unavailable"
        except ModelGatewayError as exc:
            discard_reason = _safe_reason(str(exc))
            shots = None
            provider_calls_started = False
            status = "local_fallback"
            fallback_reason = "provider_call_failed"
    if not shots:
        shots = local_storyboard_shots(request.script_text, request.shot_count_hint)
    fallback_visible_to_user = status == "local_fallback"
    fallback_message = storyboard_fallback_message(fallback_reason, discard_reason)
    shots = attach_fixed_visual_asset_refs(shots, fixed_visual_assets or [])
    asset_graph = build_asset_graph(shots, source_text=request.script_text, graph_source=f"storyboard_{status}")
    shots = attach_graph_asset_ids_to_shots(shots, asset_graph)
    content_quality_report = evaluate_storyboard_content_quality(
        project_id=project_id,
        node_id=request.node_id,
        script_text=request.script_text,
        shots=shots,
        asset_graph=asset_graph,
        provider_calls_started=provider_calls_started,
        shot_count_hint=request.shot_count_hint,
    )
    production_graph = build_storyboard_production_graph(
        project_id=project_id,
        script_node_id=request.node_id,
        script_text=request.script_text,
        shots=shots,
        asset_graph=asset_graph,
        content_quality_report=content_quality_report,
        fixed_visual_assets=fixed_visual_assets or [],
    )
    asset_auto_binding_graph = production_graph.get("asset_auto_binding_graph") if isinstance(production_graph.get("asset_auto_binding_graph"), dict) else {}
    asset_card_candidates = build_asset_card_candidates(project_id=project_id, asset_graph=asset_graph)
    safe_manifest = {
        "artifact_type": "agentflow_storyboard_breakdown_safe_manifest",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "node_id": request.node_id,
        "status": status,
        "provider_gate": gate,
        "provider_calls_started": provider_calls_started,
        "fallback_visible_to_user": fallback_visible_to_user,
        "fallback_reason": fallback_reason,
        "fallback_message": fallback_message,
        "raw_provider_response_stored": False,
        "generated_media_bytes_stored": False,
        "asset_nodes_created": False,
        "shot_count": len(shots),
        "asset_graph_asset_count": int(asset_graph.get("asset_count") or 0),
        "content_quality_report_status": content_quality_report["summary"]["status"],
        "production_graph_node_count": production_graph["summary"]["node_count"],
        "asset_auto_binding_suggested_count": int(
            (asset_auto_binding_graph.get("summary") or {}).get("suggested_binding_count") or 0
        ),
        "asset_auto_binding_established_count": int(
            (asset_auto_binding_graph.get("summary") or {}).get("established_binding_count") or 0
        ),
        "asset_auto_binding_blocked_count": int(
            (asset_auto_binding_graph.get("summary") or {}).get("blocked_candidate_count") or 0
        ),
        "fixed_visual_asset_source_evidence_count": sum(
            1 for item in (fixed_visual_assets or []) if isinstance(item, dict) and item.get("source_evidence")
        ),
        "asset_card_candidate_count": asset_card_candidates["summary"]["candidate_count"],
        "asset_card_project_reuse_candidate_count": int(
            (asset_card_candidates["summary"].get("reuse_scope_counts") or {}).get("project_reuse_candidate") or 0
        ),
        "knowledgebase_version": storyboard_knowledge["knowledgebase_version"],
        "knowledgebase_registry_hash": storyboard_knowledge["knowledgebase_registry_hash"],
        "knowledge_rule_ids": knowledge_rule_ids(storyboard_knowledge),
        "unsupported_addition_count": len(asset_graph.get("unsupported_additions") or []),
        "held_asset_ref_count": int(asset_graph.get("held_asset_ref_count") or 0),
        "discard_reason": discard_reason,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": STORYBOARD_NON_CLAIMS,
    }
    evidence_ledger = build_storyboard_evidence_ledger(
        project_id=project_id,
        script_node_id=request.node_id,
        provider_gate=gate,
        provider_calls_started=provider_calls_started,
        safe_manifest=safe_manifest,
        asset_graph=asset_graph,
        content_quality_report=content_quality_report,
        production_graph=production_graph,
        asset_card_candidates=asset_card_candidates,
        asset_auto_binding_graph=asset_auto_binding_graph,
    )
    safe_manifest["evidence_ledger_entry_count"] = len(evidence_ledger["evidence_items"])
    safe_manifest["evidence_ledger_stage"] = evidence_ledger["ledger_stage"]
    artifact = {
        "artifact_type": "agentflow_storyboard_breakdown_safe_artifact",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "node_id": request.node_id,
        "provider_output": provider_calls_started,
        "fallback_visible_to_user": fallback_visible_to_user,
        "fallback_reason": fallback_reason,
        "fallback_message": fallback_message,
        "shots": shots,
        "asset_graph": asset_graph,
        "asset_nodes_created": False,
        "review_state": "needs_human_review_before_asset_identification",
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }
    request_plan = {
        "artifact_type": "agentflow_storyboard_breakdown_request_plan",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "node_id": request.node_id,
        "target_platform": request.target_platform,
        "style": request.style,
        "shot_count_hint": request.shot_count_hint,
        "provider_gate": gate,
        "provider_calls_started": provider_calls_started,
        "fallback_visible_to_user": fallback_visible_to_user,
        "fallback_reason": fallback_reason,
        "fallback_message": fallback_message,
        "raw_provider_response_stored": False,
        "knowledgebase_version": storyboard_knowledge["knowledgebase_version"],
        "knowledgebase_registry_hash": storyboard_knowledge["knowledgebase_registry_hash"],
        "knowledge_rule_ids": knowledge_rule_ids(storyboard_knowledge),
        "asset_graph_contract": "candidate_asset_graph",
    }
    for payload in (safe_manifest, artifact, request_plan, asset_graph):
        reject_unsafe_payload(payload)
    reject_unsafe_payload(content_quality_report)
    reject_unsafe_payload(production_graph)
    reject_unsafe_payload(asset_auto_binding_graph)
    reject_unsafe_payload(asset_card_candidates)
    reject_unsafe_payload(evidence_ledger)
    return {
        "shots": shots,
        "provider_gate": gate,
        "provider_calls_started": provider_calls_started,
        "fallback_reason": fallback_reason,
        "fallback_message": fallback_message,
        "fallback_visible_to_user": fallback_visible_to_user,
        "safe_manifest": safe_manifest,
        "safe_artifact": artifact,
        "asset_graph": asset_graph,
        "asset_auto_binding_graph": asset_auto_binding_graph,
        "content_quality_report": content_quality_report,
        "production_graph": production_graph,
        "asset_card_candidates": asset_card_candidates,
        "evidence_ledger": evidence_ledger,
        "request_plan": request_plan,
    }


def _safe_reason(value: str) -> str:
    lowered = value.lower()
    if any(term in lowered for term in ("api", "key", "secret", "token", "authorization", "cookie")):
        return "llm provider configuration is not ready"
    return " ".join(value.split())[:160] or "llm provider is not ready"


__all__ = (
    "STORYBOARD_NON_CLAIMS",
    "build_storyboard_breakdown",
    "local_storyboard_shots",
    "register_runtime_storyboard_routes",
)
