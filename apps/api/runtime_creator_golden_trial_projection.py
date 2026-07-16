from __future__ import annotations

from typing import Any

from apps.api.runtime_creator_golden_trial_common import (
    CREATOR_GOLDEN_TRIAL_SCHEMA,
    DEFAULT_CURRENCY,
    TRIAL_SHOT_IDS,
    digest,
)


def rebuild_projection(ledger: dict[str, Any]) -> dict[str, Any]:
    events = list(ledger.get("events") or [])
    status = "empty"
    objective = ""
    target_shot_ids = list(TRIAL_SHOT_IDS)
    project_ceiling = {"amount": 0.0, "currency": DEFAULT_CURRENCY}
    estimated_unit_cost = {"amount": 0.0, "currency": DEFAULT_CURRENCY, "basis": "missing"}
    dispatches: dict[str, dict[str, Any]] = {}
    provider_dispatch_count = 0
    admission_receipts: list[dict[str, Any]] = []
    human_decisions: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event_type")
        if event_type == "adapter_config.recorded":
            status = "planned"
            objective = str(event.get("objective") or "")
            target_shot_ids = [str(item) for item in event.get("target_shot_ids") or TRIAL_SHOT_IDS]
            project_ceiling = dict(event.get("project_ceiling") or project_ceiling)
            estimated_unit_cost = dict(event.get("estimated_unit_cost") or estimated_unit_cost)
        elif event_type == "adapter_approval.recorded":
            if event.get("decision") in {"approve_plan", "approve_adapter_dispatch"}:
                status = "approved"
            human_decisions.append(
                {
                    "decision": event.get("decision"),
                    "decision_scope": event.get("decision_scope") or "adapter_dispatch_only",
                    "actor_ref": event.get("actor_ref"),
                    "created_at": event.get("created_at"),
                }
            )
        elif event_type == "budget.blocked":
            status = "blocked"
            dispatches[str(event["shot_id"])] = {
                "status": "blocked",
                "reason": "budget_ceiling",
                "provider_calls_started": False,
                "estimated_cost": event.get("estimated_cost"),
            }
        elif event_type == "provider_attempt.started":
            status = "running"
            receipt = dict(event.get("cost_receipt") or {})
            if receipt:
                admission_receipts.append(receipt)
            dispatches[str(event["shot_id"])] = {
                "status": "running",
                "provider_attempt_id": event.get("provider_attempt_id"),
                "provider_service_id": event.get("provider_service_id"),
                "estimated_cost": event.get("estimated_cost"),
                "provider_calls_started": True,
            }
        elif event_type == "provider_attempt.completed":
            provider_dispatch_count += 1 if event.get("provider_calls_started") else 0
            receipt = dict(event.get("cost_receipt") or {})
            if receipt:
                admission_receipts.append(receipt)
            shot_id = str(event["shot_id"])
            dispatches[shot_id] = {
                **dispatches.get(shot_id, {}),
                "status": event.get("status"),
                "provider_attempt_id": event.get("provider_attempt_id"),
                "job_id": event.get("job_id"),
                "provider_gate": event.get("provider_gate") or {},
                "provider_calls_started": bool(event.get("provider_calls_started")),
                "selected_artifact_ref": event.get("selected_artifact_ref"),
                "candidate_previews": event.get("candidate_previews") or [],
            }
            if event.get("status") in {"succeeded", "partially_complete"}:
                status = "running"
            elif event.get("status") in {"blocked", "failed"}:
                status = "blocked"
        elif event_type == "episode_candidate.writeback":
            shot_id = str(event["shot_id"])
            dispatches[shot_id] = {
                **dispatches.get(shot_id, {}),
                "episode_writeback": {
                    "status": event.get("status"),
                    "recoverable": bool(event.get("recoverable")),
                    "aggregate_version": event.get("aggregate_version"),
                    "candidate_ref": event.get("candidate_ref"),
                    "target_ref": event.get("target_ref"),
                    "human_review_state": event.get("human_review_state"),
                    "control_provenance_status": event.get("control_provenance_status"),
                },
            }
            if all(
                (dispatches.get(shot_id) or {}).get("episode_writeback", {}).get("status") in {"written", "replayed"}
                for shot_id in target_shot_ids
            ):
                status = "awaiting_review"
            elif status != "blocked":
                status = "running"
    ledger["event_count"] = len(events)
    ledger["status"] = status
    ledger["objective"] = objective
    ledger["target_shot_ids"] = target_shot_ids
    ledger["project_ceiling"] = project_ceiling
    ledger["estimated_unit_cost"] = estimated_unit_cost
    ledger["dispatches"] = dispatches
    ledger["provider_dispatch_count"] = provider_dispatch_count
    ledger["admission_receipts"] = admission_receipts
    ledger["human_decisions"] = human_decisions
    ledger["projection_digest"] = digest(
        {
            "status": status,
            "event_count": len(events),
            "target_shot_ids": target_shot_ids,
            "dispatches": dispatches,
            "admission_receipts": admission_receipts,
            "human_decisions": human_decisions,
        }
    )
    return ledger


def public_trial(ledger: dict[str, Any]) -> dict[str, Any]:
    ledger = rebuild_projection(ledger)
    return {
        "schema_version": CREATOR_GOLDEN_TRIAL_SCHEMA,
        "project_id": ledger["project_id"],
        "status": ledger["status"],
        "event_count": ledger["event_count"],
        "projection_digest": ledger["projection_digest"],
        "objective": ledger.get("objective") or "",
        "target_shot_ids": ledger.get("target_shot_ids") or list(TRIAL_SHOT_IDS),
        "project_ceiling": ledger.get("project_ceiling") or {},
        "estimated_unit_cost": ledger.get("estimated_unit_cost") or {},
        "synthetic_admission_ceiling": ledger.get("project_ceiling") or {},
        "synthetic_unit_estimate": ledger.get("estimated_unit_cost") or {},
        "dispatches": ledger.get("dispatches") or {},
        "provider_dispatch_count": ledger.get("provider_dispatch_count") or 0,
        "admission_receipts": ledger.get("admission_receipts") or [],
        "human_decisions": ledger.get("human_decisions") or [],
        "actual_cost_status": "unknown_unverified",
        "actual_provider_receipt_status": "unknown_unverified",
        "adapter_authority": {
            "trial_ledger_role": "discardable_experiment_adapter_cache",
            "authoritative_control_source": "production-control",
            "authoritative_media_source": "episode-production-aggregate",
            "owns_mission_plan_run_attempt_cost": False,
            "owns_media_acceptance_or_delivery": False,
            "creates_production_control_objects": False,
            "production_control_writeback_status": "not_written_by_adapter_cache",
        },
        "waiting_human": ledger.get("status") in {"planned", "awaiting_review"},
        "media_quality_status": "not_evaluated",
        "human_acceptance_status": "not_requested",
        "business_validation_status": "not_claimed",
        "non_claims": [
            "this trial ledger is a discardable adapter cache, not the production control source of truth",
            "this route does not create production-control run, attempt, cost, or writeback objects",
            "configured ceiling and unit estimates are synthetic admission limits, not verified provider pricing",
            "provider smoke is separate from media quality",
            "human acceptance is not claimed",
            "business validation is not claimed",
            "actual provider billing is not proven by this route",
        ],
    }


def trial_response(
    ledger: dict[str, Any],
    *,
    provider_calls_started: bool,
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "trial": public_trial(ledger),
        "provider_calls_started": provider_calls_started,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }
    if receipt is not None:
        payload["receipt"] = receipt
    return payload


__all__ = ("public_trial", "rebuild_projection", "trial_response")
