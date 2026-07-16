from __future__ import annotations

from typing import Any, Callable

from apps.api.runtime_creator_golden_trial_common import DispatchNextRequest, object_id, stamp
from apps.api.runtime_creator_golden_trial_ledger import (
    append_event,
    complete_idempotency,
    cost_receipt,
    load_or_init_ledger,
    mark_idempotency_pending,
    money,
    production_control_boundary,
    reserved_cost_amount,
    trial_lock,
    write_ledger,
)
from apps.api.runtime_creator_golden_trial_projection import trial_response
from apps.api.runtime_episode_domain_contract import SafeArtifactRef, TenantScope
from apps.api.runtime_episode_domain_store import EpisodeDomainStoreError
from apps.api.runtime_store import RuntimeStore, safe_id


WritebackFn = Callable[..., dict[str, Any]]


def record_budget_block(
    *,
    store: RuntimeStore,
    project_id: str,
    ledger: dict[str, Any],
    body: DispatchNextRequest,
    idempotency_key: str,
    body_fingerprint: str,
    next_shot_id: str,
    estimate: float,
) -> dict[str, Any]:
    event = {
        "event_type": "budget.blocked",
        "shot_id": next_shot_id,
        "capability": body.capability,
        "provider_service_id": safe_id(body.provider_service_id),
        "estimated_cost": money(estimate, ledger),
        "project_ceiling": ledger["project_ceiling"],
        "spent_or_reserved": money(reserved_cost_amount(ledger), ledger),
        "provider_calls_started": False,
        "created_at": stamp(body.generated_at),
    }
    append_event(ledger, event)
    response = trial_response(
        ledger,
        provider_calls_started=False,
        receipt={
            "status": "blocked",
            "reason": "budget_ceiling",
            "provider_calls_started": False,
            "estimated_cost": event["estimated_cost"],
            "production_control_boundary": production_control_boundary(),
        },
    )
    complete_idempotency(ledger, idempotency_key, body_fingerprint, response)
    write_ledger(store, project_id, ledger)
    return response


def record_provider_start(
    *,
    store: RuntimeStore,
    project_id: str,
    ledger: dict[str, Any],
    body: DispatchNextRequest,
    idempotency_key: str,
    body_fingerprint: str,
    next_shot_id: str,
    estimate: float,
) -> str:
    provider_attempt_id = object_id("provider-attempt", idempotency_key, next_shot_id)
    append_event(
        ledger,
        {
            "event_type": "provider_attempt.started",
            "provider_attempt_id": provider_attempt_id,
            "shot_id": next_shot_id,
            "capability": body.capability,
            "provider_service_id": safe_id(body.provider_service_id),
            "estimated_cost": money(estimate, ledger),
            "cost_receipt": cost_receipt(
                provider_attempt_id,
                status="estimated",
                amount=estimate,
                ledger=ledger,
                actual_cost_claimed=False,
            ),
            "idempotency_key": idempotency_key,
            "created_at": stamp(body.generated_at),
        },
    )
    mark_idempotency_pending(ledger, idempotency_key, body_fingerprint)
    write_ledger(store, project_id, ledger)
    return provider_attempt_id


def record_provider_completion(
    *,
    store: RuntimeStore,
    scope: TenantScope,
    project_id: str,
    body: DispatchNextRequest,
    provider_attempt_id: str,
    next_shot_id: str,
    estimate: float,
    dispatch_result: dict[str, Any],
) -> None:
    with trial_lock(store, project_id):
        ledger = load_or_init_ledger(store, scope)
        append_event(
            ledger,
            {
                "event_type": "provider_attempt.completed",
                "provider_attempt_id": provider_attempt_id,
                "shot_id": next_shot_id,
                "status": dispatch_result["status"],
                "job_id": dispatch_result.get("job_id"),
                "provider_gate": dispatch_result.get("provider_gate") or {},
                "provider_calls_started": bool(dispatch_result.get("provider_calls_started")),
                "safe_manifest_status": (dispatch_result.get("safe_manifest") or {}).get("status"),
                "selected_artifact_ref": dispatch_result.get("selected_artifact_ref"),
                "candidate_previews": dispatch_result.get("candidate_previews") or [],
                "cost_receipt": cost_receipt(
                    provider_attempt_id,
                    status="recorded" if dispatch_result.get("provider_calls_started") else "estimated",
                    amount=estimate,
                    ledger=ledger,
                    actual_cost_claimed=False,
                ),
                "created_at": stamp(body.generated_at),
            },
        )
        write_ledger(store, project_id, ledger)


def writeback_candidate(
    *,
    store: RuntimeStore,
    scope: TenantScope,
    body: DispatchNextRequest,
    idempotency_key: str,
    provider_attempt_id: str,
    next_shot_id: str,
    dispatch_result: dict[str, Any],
    write_episode_candidate: WritebackFn,
) -> dict[str, Any] | None:
    if not dispatch_result.get("selected_artifact_ref"):
        return None
    try:
        return write_episode_candidate(
            store,
            scope,
            shot_id=next_shot_id,
            artifact_ref=SafeArtifactRef.model_validate(dispatch_result["selected_artifact_ref"]),
            job_id=str(dispatch_result.get("job_id") or provider_attempt_id),
            created_at=stamp(body.generated_at),
            idempotency_key=f"{idempotency_key}-episode-candidate",
        )
    except EpisodeDomainStoreError:
        return {
            "status": "failed",
            "recoverable": True,
            "shot_id": next_shot_id,
            "control_provenance_status": "not_written_by_adapter_cache",
        }


def complete_dispatch(
    *,
    store: RuntimeStore,
    scope: TenantScope,
    project_id: str,
    body: DispatchNextRequest,
    idempotency_key: str,
    body_fingerprint: str,
    provider_attempt_id: str,
    next_shot_id: str,
    estimate: float,
    dispatch_result: dict[str, Any],
    episode_writeback: dict[str, Any] | None,
) -> dict[str, Any]:
    with trial_lock(store, project_id):
        ledger = load_or_init_ledger(store, scope)
        if episode_writeback is not None:
            append_event(
                ledger,
                {
                    "event_type": "episode_candidate.writeback",
                    "provider_attempt_id": provider_attempt_id,
                    "shot_id": next_shot_id,
                    **episode_writeback,
                    "created_at": stamp(body.generated_at),
                },
            )
        response = trial_response(
            ledger,
            provider_calls_started=bool(dispatch_result.get("provider_calls_started")),
            receipt={
                "status": dispatch_result["status"],
                "provider_attempt_id": provider_attempt_id,
                "job_id": dispatch_result.get("job_id"),
                "provider_calls_started": bool(dispatch_result.get("provider_calls_started")),
                "episode_writeback": episode_writeback,
                "control_writeback_status": "not_written_by_adapter_cache",
                "production_control_boundary": production_control_boundary(),
                "cost_receipt": cost_receipt(
                    provider_attempt_id,
                    status="recorded" if dispatch_result.get("provider_calls_started") else "estimated",
                    amount=estimate,
                    ledger=ledger,
                    actual_cost_claimed=False,
                ),
            },
        )
        complete_idempotency(ledger, idempotency_key, body_fingerprint, response)
        write_ledger(store, project_id, ledger)
        return response


__all__ = (
    "complete_dispatch",
    "record_budget_block",
    "record_provider_completion",
    "record_provider_start",
    "writeback_candidate",
)
