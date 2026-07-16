from __future__ import annotations

from typing import Any, Callable

from fastapi import Request

from apps.api.runtime_creator_golden_trial_common import DispatchNextRequest, fingerprint, replayable_dispatch_body
from apps.api.runtime_creator_golden_trial_dispatch_steps import (
    complete_dispatch,
    record_budget_block,
    record_provider_completion,
    record_provider_start,
    writeback_candidate,
)
from apps.api.runtime_creator_golden_trial_ledger import (
    budget_gate,
    complete_idempotency,
    effective_estimated_cost,
    idempotency_replay_or_conflict,
    load_or_init_ledger,
    next_open_shot_id,
    raise_trial_error,
    require_event_count,
    trial_lock,
    write_ledger,
)
from apps.api.runtime_creator_golden_trial_projection import trial_response
from apps.api.runtime_episode_domain_contract import TenantScope
from apps.api.runtime_store import RuntimeStore


DispatchFn = Callable[..., dict[str, Any]]
WritebackFn = Callable[..., dict[str, Any]]


def dispatch_creator_golden_next_command(
    *,
    store: RuntimeStore,
    scope: TenantScope,
    project_id: str,
    body: DispatchNextRequest,
    request: Request,
    idempotency_key: str,
    dispatch_image_keyframe: DispatchFn,
    write_episode_candidate: WritebackFn,
) -> dict[str, Any]:
    body_fingerprint = fingerprint(replayable_dispatch_body(body))
    with trial_lock(store, project_id):
        ledger = load_or_init_ledger(store, scope)
        replay = idempotency_replay_or_conflict(
            ledger,
            idempotency_key,
            body_fingerprint,
            request,
            project_id,
        )
        if replay is not None:
            return replay
        require_event_count(ledger, body.expected_event_count, request, project_id)
        if ledger.get("status") not in {"approved", "running"}:
            raise_trial_error(
                request,
                project_id,
                status_code=409,
                error="creator_golden_trial_not_approved",
                message="Creator Golden Trial requires human approval before provider dispatch.",
                stage="dispatch",
            )
        next_shot_id = next_open_shot_id(ledger)
        if next_shot_id is None:
            response = trial_response(ledger, provider_calls_started=False)
            complete_idempotency(ledger, idempotency_key, body_fingerprint, response)
            write_ledger(store, project_id, ledger)
            return response
        estimate = effective_estimated_cost(ledger, body.estimated_cost_amount)
        gate = budget_gate(ledger, estimate)
        if not gate["allowed"]:
            return record_budget_block(
                store=store,
                project_id=project_id,
                ledger=ledger,
                body=body,
                idempotency_key=idempotency_key,
                body_fingerprint=body_fingerprint,
                next_shot_id=next_shot_id,
                estimate=estimate,
            )
        provider_attempt_id = record_provider_start(
            store=store,
            project_id=project_id,
            ledger=ledger,
            body=body,
            idempotency_key=idempotency_key,
            body_fingerprint=body_fingerprint,
            next_shot_id=next_shot_id,
            estimate=estimate,
        )

    dispatch_result = dispatch_image_keyframe(
        store,
        project_id,
        scope,
        shot_id=next_shot_id,
        body=body,
        provider_attempt_id=provider_attempt_id,
    )
    record_provider_completion(
        store=store,
        scope=scope,
        project_id=project_id,
        body=body,
        provider_attempt_id=provider_attempt_id,
        next_shot_id=next_shot_id,
        estimate=estimate,
        dispatch_result=dispatch_result,
    )
    episode_writeback = writeback_candidate(
        store=store,
        scope=scope,
        body=body,
        idempotency_key=idempotency_key,
        provider_attempt_id=provider_attempt_id,
        next_shot_id=next_shot_id,
        dispatch_result=dispatch_result,
        write_episode_candidate=write_episode_candidate,
    )
    return complete_dispatch(
        store=store,
        scope=scope,
        project_id=project_id,
        body=body,
        idempotency_key=idempotency_key,
        body_fingerprint=body_fingerprint,
        provider_attempt_id=provider_attempt_id,
        next_shot_id=next_shot_id,
        estimate=estimate,
        dispatch_result=dispatch_result,
        episode_writeback=episode_writeback,
    )

__all__ = ("dispatch_creator_golden_next_command",)
