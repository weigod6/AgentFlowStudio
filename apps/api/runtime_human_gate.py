from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from agentflow.algorithms.human_gate import build_human_gate_decision
from agentflow.harness.json_io import write_json
from apps.api.runtime_artifacts import feedback_ref
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_flow import build_flow_summary
from apps.api.runtime_jobs import runtime_job
from apps.api.runtime_models import HumanGateDecisionRequest
from apps.api.runtime_store import RuntimeStore, reject_unsafe_payload
from apps.api.runtime_tracing import artifact_refs, write_run_trace


def register_runtime_human_gate_routes(app: FastAPI, store: RuntimeStore) -> None:
    @app.post("/projects/{project_id}/human-gate-decisions")
    def record_human_gate_decision(project_id: str, request: HumanGateDecisionRequest) -> dict[str, Any]:
        store.ensure_project_manifest(project_id)
        try:
            reject_unsafe_payload(request.model_dump(mode="json"))
            job_id = store.new_job_id("human_gate_decision", project_id)
            output_dir = store.feedback_run_dir(project_id, job_id)
            output_dir.mkdir(parents=True, exist_ok=True)
            event = build_human_gate_decision(
                project_id=project_id,
                target_type=request.target_type,
                target_id=request.target_id,
                decision=request.decision,
                artifact_id=request.artifact_id,
                node_id=request.node_id,
                scope=request.scope,
                note=request.note,
                reviewed_at=request.reviewed_at,
            )
            reject_unsafe_payload(event)
            event_path = write_json(output_dir / "runtime_human_gate_decision.json", event)
            artifact = store.register_artifact(event_path, role="runtime_human_gate_decision")
            artifacts = {"runtime_human_gate_decision": artifact}
            trace_path = write_run_trace(
                output_dir,
                project_id=project_id,
                job_id=job_id,
                action="human_gate_decision",
                status="succeeded",
                input_refs=[{"role": "human_gate_request", "ref": "request_body"}],
                generated_artifact_refs=artifact_refs(artifacts),
                tester_feedback={"status": "human_gate_decision_recorded"},
            )
            artifacts["agentflow_run_trace"] = store.register_artifact(trace_path, role="agentflow_run_trace")
            job = runtime_job(job_id, project_id, "human_gate_decision", "succeeded", artifacts=artifacts)
            public_job = store.write_job(job)
            store.update_project_manifest(
                project_id,
                {"feedback_refs": [feedback_ref(artifact, event["human_gate_id"])]},
                status="in_progress",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=safe_error_detail("invalid_human_gate_decision")) from exc
        return {
            "job": public_job,
            "human_gate_decision": event,
            "artifact": artifact,
            "artifacts": artifacts,
            "flow": build_flow_summary(store, project_id),
        }


__all__ = ("register_runtime_human_gate_routes",)
