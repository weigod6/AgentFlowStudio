from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from apps.api.runtime_creator_golden_trial_common import (
    ApproveRequest,
    DispatchNextRequest,
    IdempotencyKey,
    MissionRequest,
    fingerprint,
    stamp,
)
from apps.api.runtime_creator_golden_trial_dispatch import dispatch_creator_golden_next_command
from apps.api.runtime_creator_golden_trial_ledger import (
    append_event,
    complete_idempotency,
    idempotency_replay_or_conflict,
    load_or_init_ledger,
    raise_trial_error,
    require_event_count,
    trial_lock,
    write_ledger,
)
from apps.api.runtime_creator_golden_trial_projection import public_trial, trial_response
from apps.api.runtime_creator_golden_trial_service import (
    dispatch_image_keyframe,
    write_episode_candidate,
)
from apps.api.runtime_episode_domain_routes import _require_project_scope
from apps.api.runtime_store import RuntimeStore


_dispatch_image_keyframe = dispatch_image_keyframe
_write_episode_candidate = write_episode_candidate


def register_runtime_creator_golden_trial_routes(
    app: FastAPI,
    store: RuntimeStore,
    auth: Any,
) -> None:
    @app.get("/projects/{project_id}/creator-golden-trial")
    def get_creator_golden_trial(project_id: str, request: Request) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        with trial_lock(store, project_id):
            ledger = load_or_init_ledger(store, scope)
            return {"trial": public_trial(ledger)}

    @app.post("/projects/{project_id}/creator-golden-trial/mission")
    def record_creator_golden_mission(
        project_id: str,
        body: MissionRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        body_fingerprint = fingerprint(body.model_dump(mode="json"))
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
            if ledger.get("event_count", 0) != 0:
                raise_trial_error(
                    request,
                    project_id,
                    status_code=409,
                    error="creator_golden_trial_already_started",
                    message="Creator Golden Trial already has an adapter configuration.",
                    stage="mission",
                )
            event = {
                "event_type": "adapter_config.recorded",
                "objective": body.objective,
                "constraints": list(body.constraints),
                "ledger_role": "discardable_experiment_adapter_cache",
                "authoritative_control_source": "production-control",
                "authoritative_media_source": "episode-production-aggregate",
                "owns_mission_plan_run_attempt_cost": False,
                "project_ceiling": {
                    "amount": body.project_ceiling_amount,
                    "currency": body.currency,
                    "basis": "synthetic_admission_ceiling",
                    "actual_provider_billing_verified": False,
                },
                "estimated_unit_cost": {
                    "amount": body.estimated_unit_cost_amount,
                    "currency": body.currency,
                    "basis": "synthetic_admission_estimate",
                    "actual_cost_claimed": False,
                    "actual_provider_billing_verified": False,
                },
                "target_shot_ids": ["shot-001", "shot-002", "shot-003"],
                "created_at": stamp(body.created_at),
            }
            append_event(ledger, event)
            response = trial_response(ledger, provider_calls_started=False)
            complete_idempotency(ledger, idempotency_key, body_fingerprint, response)
            write_ledger(store, project_id, ledger)
            return response

    @app.post("/projects/{project_id}/creator-golden-trial/approve")
    def approve_creator_golden_trial(
        project_id: str,
        body: ApproveRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        body_fingerprint = fingerprint(body.model_dump(mode="json"))
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
            if ledger.get("status") != "planned":
                raise_trial_error(
                    request,
                    project_id,
                    status_code=409,
                    error="creator_golden_trial_not_planned",
                    message="Creator Golden Trial needs a mission before approval.",
                    stage="approve",
                )
            event = {
                "event_type": "adapter_approval.recorded",
                "decision": "approve_adapter_dispatch",
                "decision_scope": "adapter_dispatch_only",
                "ledger_role": "discardable_experiment_adapter_cache",
                "authoritative_control_source": "production-control",
                "actor_ref": scope.actor_id,
                "created_at": stamp(body.created_at),
            }
            append_event(ledger, event)
            response = trial_response(ledger, provider_calls_started=False)
            complete_idempotency(ledger, idempotency_key, body_fingerprint, response)
            write_ledger(store, project_id, ledger)
            return response

    @app.post("/projects/{project_id}/creator-golden-trial/dispatch-next")
    def dispatch_creator_golden_next(
        project_id: str,
        body: DispatchNextRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        return dispatch_creator_golden_next_command(
            store=store,
            scope=scope,
            project_id=project_id,
            body=body,
            request=request,
            idempotency_key=idempotency_key,
            dispatch_image_keyframe=_dispatch_image_keyframe,
            write_episode_candidate=_write_episode_candidate,
        )


__all__ = ("register_runtime_creator_golden_trial_routes",)
