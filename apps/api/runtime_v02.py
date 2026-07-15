from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from agentflow.contracts.project_manifest import validate_project_manifest
from agentflow.harness.json_io import write_json
from apps.api.runtime_artifacts import feedback_ref
from apps.api.runtime_canvas_draft import build_canvas_draft
from apps.api.runtime_events import runtime_review_decision_event
from apps.api.runtime_flow import build_flow_summary
from apps.api.runtime_jobs import runtime_job
from apps.api.runtime_models import (
    CanvasDraftRequest,
    ContentCardRegisterRequest,
    ProjectImportRequest,
    ReviewDecisionRecordRequest,
    SceneInspectorUpdateRequest,
    SourceAssetRegisterRequest,
)
from apps.api.runtime_store import RuntimeStore, project_summary, read_json, reject_unsafe_payload
from apps.api.runtime_tracing import artifact_refs, write_run_trace


NON_CLAIMS = ["not human acceptance", "not business validation", "not durable memory"]


def register_runtime_v02_routes(app: FastAPI, store: RuntimeStore) -> None:
    @app.get("/projects")
    def list_projects() -> dict[str, Any]:
        return {
            "projects": store.list_project_summaries(),
            "safe_ref_policy": "frontend receives summaries and artifact_id refs only",
            "non_claims": NON_CLAIMS,
        }

    @app.post("/projects/import")
    def import_project(request: ProjectImportRequest) -> dict[str, Any]:
        try:
            manifest = store.import_project_manifest(request.manifest)
            artifact = store.register_artifact(store.project_manifest_path(str(manifest["project_id"])), role="project_manifest")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "project_id": manifest["project_id"],
            "manifest": manifest,
            "summary": project_summary(manifest, artifact),
            "artifact": artifact,
            "flow": build_flow_summary(store, str(manifest["project_id"])),
            "non_claims": NON_CLAIMS,
        }

    @app.post("/projects/{project_id}/source-assets")
    def register_source_asset(project_id: str, request: SourceAssetRegisterRequest) -> dict[str, Any]:
        asset_ref = {
            "asset_id": request.asset_id,
            "asset_type": request.asset_type,
            "label": request.label,
            "summary": request.summary,
            "ref_kind": "safe_summary",
            "does_not_store_private_asset_bytes": True,
        }
        try:
            manifest = store.add_source_asset(project_id, asset_ref)
            artifact = store.register_artifact(store.project_manifest_path(project_id), role="project_manifest")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "project_id": project_id,
            "asset": asset_ref,
            "manifest": manifest,
            "summary": project_summary(manifest, artifact),
            "artifact": artifact,
            "flow": build_flow_summary(store, project_id),
            "non_claims": NON_CLAIMS,
        }

    @app.post("/projects/{project_id}/content-cards")
    def register_content_card(project_id: str, request: ContentCardRegisterRequest) -> dict[str, Any]:
        content_card = {
            "card_id": request.card_id,
            "card_type": request.card_type,
            "title": request.title,
            "summary": request.summary,
            "target_platform": request.target_platform,
            "status": "ready_not_run",
            "ref_kind": "content_card_summary",
            "does_not_store_private_asset_bytes": True,
        }
        try:
            manifest = store.add_content_card(project_id, content_card)
            artifact = store.register_artifact(store.project_manifest_path(project_id), role="project_manifest")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "project_id": project_id,
            "content_card": content_card,
            "manifest": manifest,
            "summary": project_summary(manifest, artifact),
            "artifact": artifact,
            "flow": build_flow_summary(store, project_id),
            "non_claims": NON_CLAIMS,
        }

    @app.post("/projects/{project_id}/canvas-draft")
    def draft_canvas(project_id: str, request: CanvasDraftRequest) -> dict[str, Any]:
        try:
            manifest = store.ensure_project_manifest(project_id)
            draft, cards = build_canvas_draft(manifest, generated_at=request.generated_at)
            reject_unsafe_payload(draft)
            for card in cards:
                reject_unsafe_payload(card)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job_id = store.new_job_id("draft_canvas", project_id)
        output_dir = store.run_dir(project_id, job_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json(output_dir / "runtime_canvas_draft.json", draft)
        artifacts = {
            "runtime_canvas_draft": store.register_artifact(
                output_dir / "runtime_canvas_draft.json",
                role="runtime_canvas_draft",
            )
        }
        source_refs = [
            {"role": "source_asset", "ref": str(item.get("asset_id") or item.get("label") or "source")}
            for item in manifest.get("source_assets", [])
            if isinstance(item, dict)
        ]
        trace_path = write_run_trace(
            output_dir,
            project_id=project_id,
            job_id=job_id,
            action="draft_canvas",
            status="succeeded",
            input_refs=source_refs,
            generated_artifact_refs=artifact_refs(artifacts),
            tool_gate_state={"provider_calls_started": False},
        )
        artifacts["agentflow_run_trace"] = store.register_artifact(trace_path, role="agentflow_run_trace")
        public_job = store.write_job(runtime_job(job_id, project_id, "draft_canvas", "succeeded", artifacts=artifacts))
        updated = store.update_project_manifest(project_id, {"content_cards": cards}, status="in_progress")
        artifact = store.register_artifact(store.project_manifest_path(project_id), role="project_manifest")
        return {
            "job": public_job,
            "draft": draft,
            "content_cards": cards,
            "manifest": updated,
            "artifact": artifact,
            "artifacts": artifacts,
            "flow": build_flow_summary(store, project_id),
            "non_claims": NON_CLAIMS,
        }

    @app.post("/projects/{project_id}/scene-inspector")
    def update_scene_inspector(project_id: str, request: SceneInspectorUpdateRequest) -> dict[str, Any]:
        scene_inspector = {
            "prompt": request.prompt,
            "reference_summary": request.reference_summary,
            "style_direction": request.style_direction,
            "retry_intent": request.retry_intent,
            "ref_kind": "scene_inspector_summary",
        }
        try:
            manifest = store.update_content_card(project_id, request.card_id, {"inspector": scene_inspector})
            artifact = store.register_artifact(store.project_manifest_path(project_id), role="project_manifest")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "project_id": project_id,
            "card_id": request.card_id,
            "scene_inspector": scene_inspector,
            "manifest": manifest,
            "summary": project_summary(manifest, artifact),
            "artifact": artifact,
            "flow": build_flow_summary(store, project_id),
            "non_claims": NON_CLAIMS,
        }

    @app.post("/projects/{project_id}/review-decisions")
    def record_review_decision(project_id: str, request: ReviewDecisionRecordRequest) -> dict[str, Any]:
        store.ensure_project_manifest(project_id)
        job_id = store.new_job_id("record_review_decision", project_id)
        output_dir = store.feedback_run_dir(project_id, job_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        event = runtime_review_decision_event(
            project_id,
            request.card_id,
            request.decision,
            request.note,
            request.generated_at,
            candidate_id=request.candidate_id,
            artifact_id=request.artifact_id,
        )
        try:
            reject_unsafe_payload(event)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        write_json(output_dir / "runtime_review_decision.json", event)
        artifact_ref = store.register_artifact(output_dir / "runtime_review_decision.json", role="runtime_review_decision")
        artifacts = {"runtime_review_decision": artifact_ref}
        trace_path = write_run_trace(
            output_dir,
            project_id=project_id,
            job_id=job_id,
            action="record_review_decision",
            status="succeeded",
            input_refs=[{"role": "scene_card", "ref": request.card_id}, {"role": "decision", "ref": request.decision}],
            generated_artifact_refs=artifact_refs(artifacts),
            tester_feedback={"status": "recorded_review_decision", "decision": request.decision},
        )
        artifacts["agentflow_run_trace"] = store.register_artifact(trace_path, role="agentflow_run_trace")
        public_job = store.write_job(runtime_job(job_id, project_id, "record_review_decision", "succeeded", artifacts=artifacts))
        manifest = store.update_project_manifest(
            project_id,
            {"feedback_refs": [feedback_ref(artifact_ref, event.get("review_id", job_id))]},
            status="in_progress",
        )
        return {
            "job": public_job,
            "review_decision": event,
            "artifact": artifact_ref,
            "manifest": manifest,
            "flow": build_flow_summary(store, project_id),
        }

    @app.get("/projects/{project_id}/export")
    def export_project(project_id: str) -> dict[str, Any]:
        path = store.project_manifest_path(project_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="project manifest not found")
        manifest = read_json(path)
        reject_unsafe_payload(manifest)
        validate_project_manifest(manifest)
        artifact = store.register_artifact(path, role="project_manifest")
        return {
            "project_id": project_id,
            "download_filename": f"{project_id}.project_manifest.json",
            "manifest": manifest,
            "artifact": artifact,
            "non_claims": NON_CLAIMS,
        }


__all__ = ("NON_CLAIMS", "register_runtime_v02_routes")
