from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, Request

from agentflow.harness.json_io import exclusive_file_lock, write_json
from apps.api.runtime_creator_golden_trial_common import (
    CREATOR_GOLDEN_TRIAL_SCHEMA,
    DEFAULT_CURRENCY,
    TRIAL_SHOT_IDS,
    digest,
    object_id,
)
from apps.api.runtime_creator_golden_trial_projection import rebuild_projection
from apps.api.runtime_episode_domain_contract import TenantScope
from apps.api.runtime_episode_domain_store import EpisodeDomainStoreError
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_store import RuntimeStore, read_json, safe_id


def trial_lock(store: RuntimeStore, project_id: str):
    path = trial_dir(store, project_id) / "creator_golden_trial.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return exclusive_file_lock(path)


def trial_dir(store: RuntimeStore, project_id: str) -> Path:
    return store.projects_dir / safe_id(project_id) / "creator_golden_trial"


def ledger_path(store: RuntimeStore, project_id: str) -> Path:
    return trial_dir(store, project_id) / "ledger.json"


def load_or_init_ledger(store: RuntimeStore, scope: TenantScope) -> dict[str, Any]:
    path = ledger_path(store, scope.project_id)
    if path.is_file():
        ledger = read_json(path)
        if ledger.get("project_id") != scope.project_id or (ledger.get("scope") or {}).get("org_id") != scope.org_id:
            raise EpisodeDomainStoreError("creator golden trial scope mismatch")
        return rebuild_projection(ledger)
    return rebuild_projection(
        {
            "schema_version": CREATOR_GOLDEN_TRIAL_SCHEMA,
            "project_id": scope.project_id,
            "scope": scope.model_dump(mode="json"),
            "events": [],
            "idempotency_records": {},
        }
    )


def write_ledger(store: RuntimeStore, project_id: str, ledger: dict[str, Any]) -> None:
    path = ledger_path(store, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, rebuild_projection(ledger))


def append_event(ledger: dict[str, Any], event: dict[str, Any]) -> None:
    events = list(ledger.get("events") or [])
    previous_digest = str(events[-1]["event_digest"]) if events else ""
    sequence = len(events) + 1
    payload = {
        "sequence": sequence,
        "previous_event_digest": previous_digest,
        **event,
    }
    event_id = object_id("trial-event", str(sequence), previous_digest, event.get("event_type", "event"))
    payload["event_id"] = event_id
    payload["event_digest"] = digest(payload)
    events.append(payload)
    ledger["events"] = events
    rebuild_projection(ledger)


def idempotency_replay_or_conflict(
    ledger: dict[str, Any],
    key: str,
    body_fingerprint: str,
    request: Request,
    project_id: str,
) -> dict[str, Any] | None:
    record = (ledger.get("idempotency_records") or {}).get(key)
    if record is None:
        return None
    if record.get("fingerprint") != body_fingerprint:
        raise_trial_error(
            request,
            project_id,
            status_code=409,
            error="creator_golden_trial_idempotency_conflict",
            message="Idempotency key has already been used for a different command.",
            stage="idempotency",
        )
    if record.get("status") == "pending":
        raise_trial_error(
            request,
            project_id,
            status_code=409,
            error="creator_golden_trial_idempotency_pending",
            message="A provider attempt for this idempotency key is already pending.",
            stage="idempotency",
            retryable=True,
        )
    response = record.get("response")
    if not isinstance(response, dict):
        raise_trial_error(
            request,
            project_id,
            status_code=500,
            error="creator_golden_trial_idempotency_corrupt",
            message="Stored idempotency response is invalid.",
            stage="idempotency",
        )
    return response


def mark_idempotency_pending(ledger: dict[str, Any], key: str, body_fingerprint: str) -> None:
    records = dict(ledger.get("idempotency_records") or {})
    records[key] = {"fingerprint": body_fingerprint, "status": "pending"}
    ledger["idempotency_records"] = records


def complete_idempotency(
    ledger: dict[str, Any],
    key: str,
    body_fingerprint: str,
    response: dict[str, Any],
) -> None:
    records = dict(ledger.get("idempotency_records") or {})
    records[key] = {
        "fingerprint": body_fingerprint,
        "status": "completed",
        "response": response,
        "response_sha256": digest(response),
    }
    ledger["idempotency_records"] = records


def require_event_count(ledger: dict[str, Any], expected: int, request: Request, project_id: str) -> None:
    if int(ledger.get("event_count") or 0) != expected:
        raise_trial_error(
            request,
            project_id,
            status_code=409,
            error="creator_golden_trial_version_conflict",
            message="Creator Golden Trial state changed. Reload before retrying.",
            stage="cas",
            retryable=True,
        )


def next_open_shot_id(ledger: dict[str, Any]) -> str | None:
    dispatches = ledger.get("dispatches") or {}
    for shot_id in ledger.get("target_shot_ids") or TRIAL_SHOT_IDS:
        dispatch = dispatches.get(shot_id) or {}
        writeback = dispatch.get("episode_writeback") or {}
        if writeback.get("status") in {"written", "replayed"}:
            continue
        if dispatch:
            return None
        if dispatch.get("status") != "blocked":
            return str(shot_id)
    return None


def estimated_unit_cost(ledger: dict[str, Any]) -> float:
    estimate = ledger.get("estimated_unit_cost") or {}
    return float(estimate.get("amount") or 0)


def effective_estimated_cost(ledger: dict[str, Any], caller_estimate: float | None) -> float:
    stored = estimated_unit_cost(ledger)
    if caller_estimate is None:
        return stored
    return max(stored, float(caller_estimate))


def reserved_cost_amount(ledger: dict[str, Any]) -> float:
    total = 0.0
    seen: set[str] = set()
    for event in ledger.get("events") or []:
        if event.get("event_type") != "provider_attempt.started":
            continue
        attempt_id = str(event.get("provider_attempt_id") or "")
        if not attempt_id or attempt_id in seen:
            continue
        seen.add(attempt_id)
        total += float((event.get("estimated_cost") or {}).get("amount") or 0)
    return total


def budget_gate(ledger: dict[str, Any], estimate: float) -> dict[str, Any]:
    ceiling = float((ledger.get("project_ceiling") or {}).get("amount") or 0)
    reserved = reserved_cost_amount(ledger)
    return {
        "allowed": estimate > 0 and reserved + estimate <= ceiling,
        "ceiling": ceiling,
        "reserved": reserved,
        "estimate": estimate,
        "ceiling_kind": "synthetic_admission_ceiling",
        "actual_cost_status": "unknown_unverified",
    }


def money(amount: float, ledger: dict[str, Any]) -> dict[str, Any]:
    return {
        "amount": round(float(amount), 6),
        "currency": str((ledger.get("project_ceiling") or {}).get("currency") or DEFAULT_CURRENCY),
    }


def cost_receipt(
    provider_attempt_id: str,
    *,
    status: Literal["estimated", "recorded", "failed"],
    amount: float,
    ledger: dict[str, Any],
    actual_cost_claimed: bool,
) -> dict[str, Any]:
    return {
        "receipt_id": object_id("cost-receipt", provider_attempt_id, status),
        "receipt_kind": "synthetic_admission",
        "provider_attempt_id": provider_attempt_id,
        "status": status,
        "admission_ceiling_kind": "synthetic",
        "estimated_cost": money(amount, ledger),
        "actual_cost": {
            "status": "unknown_unverified",
            "amount": None,
            "currency": str((ledger.get("project_ceiling") or {}).get("currency") or DEFAULT_CURRENCY),
        },
        "actual_cost_claimed": actual_cost_claimed,
        "actual_provider_billing_verified": False,
        "production_control_boundary": production_control_boundary(),
    }


def production_control_boundary() -> dict[str, Any]:
    return {
        "trial_ledger_role": "discardable_experiment_adapter_cache",
        "authoritative_control_source": "production-control",
        "creates_production_control_objects": False,
        "created_production_control_run_id": None,
        "created_production_control_attempt_id": None,
        "created_production_control_cost_receipt_id": None,
        "production_control_writeback_status": "not_written_by_adapter_cache",
    }


def raise_trial_error(
    request: Request,
    project_id: str,
    *,
    status_code: int,
    error: str,
    message: str,
    stage: str,
    retryable: bool = False,
) -> None:
    detail = safe_error_detail(
        error,
        message=message,
        project_id=project_id,
        action="creator_golden_trial",
        stage=stage,
        retryable=retryable,
        details={"provider_calls_started": False},
    )
    raise HTTPException(status_code=status_code, detail=detail)


__all__ = (
    "append_event",
    "budget_gate",
    "complete_idempotency",
    "cost_receipt",
    "effective_estimated_cost",
    "estimated_unit_cost",
    "idempotency_replay_or_conflict",
    "load_or_init_ledger",
    "mark_idempotency_pending",
    "money",
    "next_open_shot_id",
    "raise_trial_error",
    "require_event_count",
    "reserved_cost_amount",
    "trial_lock",
    "write_ledger",
)
