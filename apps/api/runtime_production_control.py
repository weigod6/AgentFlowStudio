from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentflow.harness.json_io import exclusive_file_lock
from agentflow_studio.production_control.contract import (
    SAFE_ID,
    ActorIdentity,
    AgentAssignment,
    ArtifactCandidateRegistration,
    ArtifactWriteback,
    Blocker,
    BudgetAuthorization,
    BudgetEnvelope,
    CostEstimate,
    CommandEnvelope,
    EpisodeArtifactAdapterRequest,
    ExactObjectRef,
    HumanDecision,
    HumanDecisionRequest,
    ImpactAssessment,
    Mission,
    MissionRevision,
    MoneyRange,
    PlanApprovalDecision,
    PlanRevision,
    PlanTask,
    PlanTaskSpec,
    ProductionPlan,
    ProductionRun,
    ProjectScope,
    ProviderAuthorization,
    ProviderGateDecision,
    ReferenceConstraint,
    RevisionIdentity,
    RunAttempt,
    SelectiveRevisionRequest,
)
from agentflow_studio.production_control.harness import (
    AtomicCommitError,
    AuthorizationError,
    IdempotencyConflictError,
    LedgerIntegrityError,
    ProductionControlHarness,
    StateConflictError,
    VersionConflictError,
    digest,
    exact_ref,
    rebuild_projection,
)
from apps.api.runtime_auth import RuntimeAuthStore
from apps.api.runtime_episode_bootstrap import (
    BOOTSTRAP_EPISODE_ID,
    BOOTSTRAP_EPISODE_VERSION_ID,
)
from apps.api.runtime_episode_domain_contract import (
    AssetCandidateVersion,
    ControlObjectRef,
    EntityVersionRef,
    ProductionControlProvenance,
    ProductionProjectAggregate,
    ReviewDecision,
    SafeArtifactRef,
    TenantScope,
)
from apps.api.runtime_episode_domain_routes import _require_project_scope
from apps.api.runtime_episode_domain_store import (
    AggregateNotFoundError,
    EpisodeDomainAggregateStore,
    EpisodeDomainStoreError,
)
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_store import RuntimeStore, reject_unsafe_payload, safe_id


_SAFE_ID_RE = re.compile(SAFE_ID, re.ASCII)
_CONTROL_SCHEMA = "afs.production-control.read-model.v0.2"
_DEFAULT_STAMP = "2026-07-15T00:00:00+00:00"
_CAPABILITIES = {
    "mission.write",
    "plan.write",
    "plan.approve",
    "run.execute",
    "run.control",
    "budget.record",
    "provider.evaluate",
    "decision.write",
    "artifact.write",
    "revision.write",
}


IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=160, pattern=SAFE_ID),
]


class ProductionControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MissionCommandRequest(ProductionControlModel):
    expected_version: int = Field(ge=0, strict=True)
    objective: str = Field(min_length=1, max_length=1000)
    constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    created_at: str = Field(default=_DEFAULT_STAMP, min_length=1, max_length=64)


class PlanTaskRequest(ProductionControlModel):
    title: str = Field(min_length=1, max_length=120)
    boundary: str = Field(min_length=1, max_length=1000)
    capability: str = Field(default="deterministic.worker", pattern=SAFE_ID)
    dependency_task_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=8)


class PlanCommandRequest(ProductionControlModel):
    expected_version: int = Field(ge=0, strict=True)
    tasks: tuple[PlanTaskRequest, ...] = Field(default_factory=tuple, max_length=8)
    estimated_cost_max: int = Field(default=0, ge=0, le=100000, strict=True)
    created_at: str = Field(default=_DEFAULT_STAMP, min_length=1, max_length=64)


class ApprovalCommandRequest(ProductionControlModel):
    expected_version: int = Field(ge=0, strict=True)
    created_at: str = Field(default=_DEFAULT_STAMP, min_length=1, max_length=64)


class RunActionRequest(ProductionControlModel):
    expected_version: int = Field(ge=0, strict=True)
    action: Literal[
        "progress",
        "pause",
        "resume",
        "retry",
        "waiting_human",
        "decide_human",
        "block",
        "clear_blocker",
        "provider_gate",
        "writeback",
        "complete",
        "cancel",
    ]
    decision_option: str = Field(default="确认继续", max_length=120)
    note: str = Field(default="", max_length=400)
    created_at: str = Field(default=_DEFAULT_STAMP, min_length=1, max_length=64)


def register_runtime_production_control_routes(
    app: FastAPI,
    store: RuntimeStore,
    auth: RuntimeAuthStore,
) -> None:
    @app.get("/projects/{project_id}/production-control")
    def get_production_control(project_id: str, request: Request) -> dict[str, Any]:
        scope, actor = _control_identity(project_id, request, store, auth)
        with _locked_harness(store, project_id, scope, actor) as harness:
            return {"control": _read_model(harness, store)}

    @app.post("/projects/{project_id}/production-control/mission")
    def record_mission(
        project_id: str,
        body: MissionCommandRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope, actor = _control_identity(project_id, request, store, auth)
        _guard_safe_body(body)
        with _locked_harness(store, project_id, scope, actor) as harness:
            stamp = _stamp(body.created_at)
            constraints = tuple(
                ReferenceConstraint(
                    identity=_identity(scope, f"constraint-{index}", created_at=stamp),
                    constraint_type="story" if index == 1 else "delivery",
                    rule=text,
                )
                for index, text in enumerate(
                    body.constraints or ("本轮不调用外部生成服务。",),
                    start=1,
                )
            )
            revision = MissionRevision(
                identity=_identity(scope, "mission-revision-main", created_at=stamp),
                objective=body.objective,
                reference_constraint_refs=tuple(
                    exact_ref("reference_constraint", item) for item in constraints
                ),
            )
            mission = Mission(
                identity=_identity(scope, "mission-main", created_at=stamp),
                head_revision_ref=exact_ref("mission_revision", revision),
            )
            payload = {
                "mission": mission.model_dump(mode="json"),
                "mission_revision": revision.model_dump(mode="json"),
                "constraints": [item.model_dump(mode="json") for item in constraints],
            }
            receipt = harness.execute(
                _command(
                    harness,
                    actor,
                    "mission.record",
                    payload,
                    idempotency_key,
                    expected_version=body.expected_version,
                )
            )
            return {"control": _read_model(harness, store), "receipt": receipt.model_dump(mode="json")}

    @app.post("/projects/{project_id}/production-control/plan")
    def propose_or_revise_plan(
        project_id: str,
        body: PlanCommandRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope, actor = _control_identity(project_id, request, store, auth)
        _guard_safe_body(body)
        with _locked_harness(store, project_id, scope, actor) as harness:
            stamp = _stamp(body.created_at)
            mission_revision = _latest_model(harness, "mission_revision", MissionRevision)
            if mission_revision is None:
                _raise_control_error(
                    project_id,
                    409,
                    "production_control_missing_mission",
                    "Record a mission before proposing a plan.",
                    "plan",
                )
            task_specs = _task_specs(body.tasks)
            current_plan = _latest_model(harness, "production_plan", ProductionPlan)
            if current_plan is None or body.expected_version == 1:
                plan, revision, budget, estimate = _new_plan(
                    scope,
                    mission_revision,
                    task_specs,
                    body.estimated_cost_max,
                    stamp,
                )
                payload = {
                    "plan": plan.model_dump(mode="json"),
                    "plan_revision": revision.model_dump(mode="json"),
                    "budget_envelope": budget.model_dump(mode="json"),
                    "cost_estimates": [estimate.model_dump(mode="json")],
                }
                command_type = "plan.propose"
                refs = (exact_ref("mission_revision", mission_revision),)
            else:
                if harness.projection.plan_status.get(current_plan.identity.object_id) != "proposed":
                    _raise_control_error(
                        project_id,
                        409,
                        "production_control_plan_locked",
                        "Approved plans cannot be edited.",
                        "plan",
                    )
                latest_revision = _latest_model(harness, "plan_revision", PlanRevision)
                if latest_revision is None:
                    _raise_control_error(
                        project_id,
                        500,
                        "production_control_projection_failed",
                        "Plan head cannot be rebuilt.",
                        "plan",
                    )
                next_revision = latest_revision.identity.revision + 1
                plan = ProductionPlan(
                    identity=_identity(
                        scope,
                        current_plan.identity.object_id,
                        revision=next_revision,
                        parent_revision_id=current_plan.identity.revision_id,
                        created_at=stamp,
                    ),
                    mission_revision_ref=current_plan.mission_revision_ref,
                    head_revision_ref=ExactObjectRef(
                        scope=scope,
                        object_type="plan_revision",
                        object_id=latest_revision.identity.object_id,
                        revision_id=f"{latest_revision.identity.object_id}-v{next_revision}",
                    ),
                )
                revision = PlanRevision(
                    identity=_identity(
                        scope,
                        latest_revision.identity.object_id,
                        revision=next_revision,
                        parent_revision_id=latest_revision.identity.revision_id,
                        created_at=stamp,
                    ),
                    plan_ref=exact_ref("production_plan", plan),
                    task_specs=task_specs,
                    budget_envelope_ref=latest_revision.budget_envelope_ref,
                    cost_estimate_refs=latest_revision.cost_estimate_refs,
                )
                payload = {
                    "plan": plan.model_dump(mode="json"),
                    "plan_revision": revision.model_dump(mode="json"),
                }
                command_type = "plan.revise"
                refs = (exact_ref("production_plan", current_plan), exact_ref("plan_revision", latest_revision))
            receipt = harness.execute(
                _command(
                    harness,
                    actor,
                    command_type,
                    payload,
                    idempotency_key,
                    expected_version=body.expected_version,
                    refs=refs,
                )
            )
            return {"control": _read_model(harness, store), "receipt": receipt.model_dump(mode="json")}

    @app.post("/projects/{project_id}/production-control/plan/approve")
    def approve_plan(
        project_id: str,
        body: ApprovalCommandRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope, actor = _control_identity(project_id, request, store, auth)
        with _locked_harness(store, project_id, scope, actor) as harness:
            stamp = _stamp(body.created_at)
            plan_revision = _latest_model(harness, "plan_revision", PlanRevision)
            budget = _latest_model(harness, "budget_envelope", BudgetEnvelope)
            if plan_revision is None or budget is None:
                _raise_control_error(
                    project_id,
                    409,
                    "production_control_missing_plan",
                    "Propose a plan before approval.",
                    "approve",
                )
            tasks = tuple(
                PlanTask(
                    identity=_identity(scope, spec.task_id, created_at=stamp),
                    plan_revision_ref=exact_ref("plan_revision", plan_revision),
                    boundary=spec.boundary,
                    capability=spec.capability,
                    dependency_refs=tuple(
                        ExactObjectRef(
                            scope=scope,
                            object_type="plan_task",
                            object_id=dep,
                            revision_id=f"{dep}-v1",
                        )
                        for dep in spec.dependency_task_ids
                    ),
                )
                for spec in plan_revision.task_specs
            )
            decision = PlanApprovalDecision(
                identity=_identity(scope, "approval-main", created_at=stamp),
                plan_revision_ref=exact_ref("plan_revision", plan_revision),
                decision="approved",
                approved_task_refs=tuple(exact_ref("plan_task", task) for task in tasks),
                budget_envelope_ref=exact_ref("budget_envelope", budget),
            )
            run_bundles = [
                _first_run_bundle(scope, task, index=index, stamp=stamp)
                for index, task in enumerate(tasks, start=1)
            ]
            payload = {
                "decision": decision.model_dump(mode="json"),
                "tasks": [task.model_dump(mode="json") for task in tasks],
                "runs": run_bundles,
            }
            receipt = harness.execute(
                _command(
                    harness,
                    actor,
                    "plan.approve",
                    payload,
                    idempotency_key,
                    expected_version=body.expected_version,
                    refs=(exact_ref("plan_revision", plan_revision),),
                    budget_authorization=BudgetAuthorization(
                        budget_envelope_ref=exact_ref("budget_envelope", budget),
                        admitted_amount=budget.max_budget,
                        currency=budget.estimated.currency,
                        authorization_ref="budget-provider-closed",
                    ),
                )
            )
            return {"control": _read_model(harness, store), "receipt": receipt.model_dump(mode="json")}

    @app.post("/projects/{project_id}/production-control/runs/{run_id}/actions")
    def run_action(
        project_id: str,
        run_id: str,
        body: RunActionRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        if _SAFE_ID_RE.fullmatch(run_id) is None:
            _raise_control_error(
                project_id,
                422,
                "production_control_run_id_invalid",
                "Run identity is invalid.",
                "run",
            )
        scope, actor = _control_identity(project_id, request, store, auth)
        _guard_safe_body(body)
        with _locked_harness(store, project_id, scope, actor) as harness:
            receipts = _execute_run_action(store, harness, actor, run_id, body, idempotency_key)
            episode_writeback = None
            if body.action == "writeback":
                episode_writeback = _apply_episode_asset_candidate_writeback(
                    store,
                    harness,
                    actor,
                    run_id=run_id,
                    idempotency_key=idempotency_key,
                    created_at=body.created_at,
                )
            payload = {
                "control": _read_model(harness, store),
                "receipts": [item.model_dump(mode="json") for item in receipts],
            }
            if episode_writeback is not None:
                payload["episode_writeback"] = episode_writeback
            return payload

    @app.post("/projects/{project_id}/production-control/integrity/rebuild")
    def rebuild_integrity(project_id: str, request: Request) -> dict[str, Any]:
        scope, actor = _control_identity(project_id, request, store, auth)
        with _locked_harness(store, project_id, scope, actor) as harness:
            projection = rebuild_projection(harness.scope, harness.events)
            return {
                "ok": projection.state_digest == harness.projection.state_digest,
                "event_count": len(harness.events),
                "outbox_count": len(harness.outbox),
                "projection_digest": projection.state_digest,
                "provider_dispatch_count": harness.provider_dispatch_count,
            }


def build_production_control_episode_workspace_projection(
    store: RuntimeStore,
    *,
    project_id: str,
    org_id: str,
    actor_id: str,
    episode_id: str,
    episode_version_id: str,
) -> dict[str, Any] | None:
    path = _ledger_path(store, project_id)
    if not path.is_file():
        return None
    scope = ProjectScope(org_id=org_id, project_id=project_id)
    actor = ActorIdentity(
        actor_id=actor_id,
        actor_type="human",
        authority_ref=f"runtime-membership-{actor_id}",
    )
    with exclusive_file_lock(_lock_path(store, project_id)):
        harness = ProductionControlHarness.load(path, _grants(actor), {actor.actor_id: actor})
    if harness.scope != scope:
        return None
    model = _read_model(harness, store)
    workspace = model["workspace_entry"]
    if (
        episode_id != workspace["episode_id"]
        or episode_version_id != workspace["episode_version_id"]
    ):
        return None
    return _episode_workspace_projection(model)


def _execute_run_action(
    store: RuntimeStore,
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    run_id: str,
    body: RunActionRequest,
    idempotency_key: str,
) -> list[Any]:
    run = _run_by_id(harness, run_id)
    run_ref = _ref("production_run", run)
    stamp = _stamp(body.created_at)
    version = body.expected_version
    receipts: list[Any] = []

    def execute(
        command_type: str,
        payload: dict[str, Any],
        suffix: str,
        *,
        capability_refs: tuple[ExactObjectRef, ...] = (),
    ) -> None:
        nonlocal version
        receipt = harness.execute(
            _command(
                harness,
                actor,
                command_type,
                payload,
                f"{idempotency_key}-{suffix}",
                expected_version=version,
                refs=capability_refs or (run_ref,),
            )
        )
        receipts.append(receipt)
        version += 1

    if body.action == "progress":
        execute("run.transition", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "running"}, "progress")
    elif body.action == "complete":
        execute("run.transition", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "completed"}, "complete")
    elif body.action == "pause":
        execute("run.control", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "pause-requested"}, "pause-requested")
        execute("run.control", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "paused"}, "paused")
    elif body.action == "resume":
        execute("run.control", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "resume-requested"}, "resume-requested")
        execute("run.control", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "active"}, "active")
    elif body.action == "cancel":
        execute("run.control", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "cancel-requested"}, "cancel-requested")
        execute("run.transition", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "cancelled"}, "cancelled")
    elif body.action == "retry":
        attempt = _next_attempt(harness, run_ref, stamp)
        execute(
            "run.transition",
            {
                "run_ref": run_ref.model_dump(mode="json"),
                "target_state": "retrying",
                "attempt": attempt.model_dump(mode="json"),
            },
            "retrying",
        )
        execute("run.transition", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "running"}, "retry-running")
    elif body.action == "waiting_human":
        token = _command_token(idempotency_key)
        request = HumanDecisionRequest(
            identity=_identity(harness.scope, f"human-request-{run_id}-{token}", created_at=stamp),
            run_ref=run_ref,
            options=("确认继续", "改为局部返工"),
            impact_refs=(_task_ref_for_run(run),),
            deadline="2026-07-16T00:00:00+00:00",
        )
        execute(
            "run.transition",
            {
                "run_ref": run_ref.model_dump(mode="json"),
                "target_state": "waiting-human",
                "human_decision_request": request.model_dump(mode="json"),
            },
            "waiting-human",
        )
    elif body.action == "decide_human":
        request_ref = harness.projection.open_human_requests.get(run_id)
        if request_ref is None:
            raise StateConflictError("run has no open human decision request")
        decision = HumanDecision(
            identity=_identity(harness.scope, f"human-decision-{run_id}-{harness.projection.version + 1}", created_at=stamp),
            request_ref=request_ref,
            selected_option=body.decision_option or "确认继续",
            impact_acknowledged=True,
        )
        execute("decision.record", {"human_decision": decision.model_dump(mode="json")}, "decision", capability_refs=(request_ref,))
        execute("run.transition", {"run_ref": run_ref.model_dump(mode="json"), "target_state": "running"}, "decision-running")
    elif body.action == "block":
        blocker = Blocker(
            identity=_identity(harness.scope, f"blocker-{run_id}-{harness.projection.version + 1}", created_at=stamp),
            run_ref=run_ref,
            owner_actor_id=actor.actor_id,
            reason=body.note or "等待主创提供确定性放行证据。",
            clearance_evidence_refs=(_task_ref_for_run(run),),
        )
        execute(
            "run.transition",
            {"run_ref": run_ref.model_dump(mode="json"), "target_state": "blocked", "blocker": blocker.model_dump(mode="json")},
            "blocked",
        )
    elif body.action == "clear_blocker":
        execute(
            "run.transition",
            {
                "run_ref": run_ref.model_dump(mode="json"),
                "target_state": "running",
                "clearance_evidence_refs": [_task_ref_for_run(run).model_dump(mode="json")],
            },
            "clear-blocker",
        )
    elif body.action == "provider_gate":
        receipts.append(_execute_provider_gate(harness, actor, run_ref, body, idempotency_key, version))
    elif body.action == "writeback":
        _ensure_episode_asset_candidate_draft(
            store,
            harness,
            actor,
            run_ref=run_ref,
            idempotency_key=idempotency_key,
            created_at=body.created_at,
        )
        receipts.extend(_execute_writeback_sequence(harness, actor, run, run_ref, body, idempotency_key, version))
    else:
        raise StateConflictError("unsupported run action")
    return receipts


def _execute_provider_gate(
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    run_ref: ExactObjectRef,
    body: RunActionRequest,
    idempotency_key: str,
    version: int,
) -> Any:
    budget = _latest_model(harness, "budget_envelope", BudgetEnvelope)
    if budget is None:
        raise StateConflictError("provider gate requires a budget envelope")
    token = _command_token(idempotency_key)
    decision = ProviderGateDecision(
        identity=_identity(harness.scope, f"provider-gate-{run_ref.object_id}-{token}", created_at=_stamp(body.created_at)),
        run_ref=run_ref,
        budget_envelope_ref=exact_ref("budget_envelope", budget),
        capability="remote.image",
        capability_authorized=False,
        budget_admitted=False,
        privacy_policy_satisfied=False,
        no_training_policy_satisfied=False,
        allowed=False,
        authorization_ref=None,
        privacy_policy_ref="privacy-provider-closed",
        no_training_policy_ref="no-training-provider-closed",
    )
    return harness.execute(
        _command(
            harness,
            actor,
            "provider.evaluate",
            {"provider_gate_decision": decision.model_dump(mode="json")},
            f"{idempotency_key}-provider-gate",
            expected_version=version,
            refs=(run_ref,),
            budget_authorization=BudgetAuthorization(
                budget_envelope_ref=decision.budget_envelope_ref,
                admitted_amount=0,
                currency=budget.estimated.currency,
                authorization_ref="budget-provider-closed",
            ),
            provider_authorization=ProviderAuthorization(
                capability=decision.capability,
                authorized=False,
                privacy_policy_satisfied=False,
                no_training_policy_satisfied=False,
                authorization_ref=None,
            ),
        )
    )


def _execute_writeback_sequence(
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    run: ProductionRun,
    run_ref: ExactObjectRef,
    body: RunActionRequest,
    idempotency_key: str,
    version: int,
) -> list[Any]:
    task_ref = _task_ref_for_run(run)
    attempt_ref = harness.projection.run_attempts[run_ref.object_id][-1]
    token = _command_token(idempotency_key)
    adapter = _asset_candidate_adapter_for_run(harness, run_ref, idempotency_key)
    if adapter.predecessor_ref is None:
        raise StateConflictError("episode writeback requires a target shot")
    affected = adapter.predecessor_ref
    successor = adapter.successor_ref
    protected = adapter.protected_exact_refs
    artifact_digest = digest(
        {
            "artifact": idempotency_key,
            "run": run_ref.model_dump(mode="json"),
            "successor": successor.model_dump(mode="json"),
        }
    )
    registration = ArtifactCandidateRegistration(
        identity=_identity(harness.scope, f"registration-{run_ref.object_id}-{token}", created_at=_stamp(body.created_at)),
        plan_task_ref=task_ref,
        run_ref=run_ref,
        attempt_ref=attempt_ref,
        artifact_id=f"artifact-{run_ref.object_id}-{token}",
        artifact_digest=artifact_digest,
        adapter_request=adapter,
    )
    writeback = ArtifactWriteback(
        identity=_identity(harness.scope, f"writeback-{run_ref.object_id}-{token}", created_at=_stamp(body.created_at)),
        candidate_registration_ref=exact_ref("artifact_candidate_registration", registration),
        plan_task_ref=task_ref,
        run_ref=run_ref,
        attempt_ref=attempt_ref,
        artifact_id=registration.artifact_id,
        artifact_digest=registration.artifact_digest,
        adapter_request=adapter,
    )
    revision_request = SelectiveRevisionRequest(
        identity=_identity(harness.scope, f"revision-request-{run_ref.object_id}-{token}", created_at=_stamp(body.created_at)),
        target_exact_ref=affected,
        protected_exact_refs=protected,
        requested_changes=("只返工受影响镜头，保护未受影响镜头事实。",),
        source_writeback_ref=exact_ref("artifact_writeback", writeback),
    )
    impact = ImpactAssessment(
        identity=_identity(harness.scope, f"impact-{run_ref.object_id}-{token}", created_at=_stamp(body.created_at)),
        revision_request_ref=exact_ref("selective_revision_request", revision_request),
        affected_exact_refs=(affected,),
        preserved_exact_refs=protected,
        assessment_digest=digest(
            {
                "affected": affected.model_dump(mode="json"),
                "preserved": [item.model_dump(mode="json") for item in protected],
            }
        ),
    )
    commands = [
        ("artifact.register", {"registration": registration.model_dump(mode="json")}, "artifact-register", (task_ref, run_ref, attempt_ref, affected, *protected)),
        ("artifact.writeback", {"writeback": writeback.model_dump(mode="json")}, "artifact-writeback", (exact_ref("artifact_candidate_registration", registration), affected, *protected)),
        ("selective_revision.request", {"revision_request": revision_request.model_dump(mode="json")}, "revision-request", (exact_ref("artifact_writeback", writeback), affected, *protected)),
        ("impact.assess", {"impact_assessment": impact.model_dump(mode="json")}, "impact", (exact_ref("selective_revision_request", revision_request), *protected)),
    ]
    receipts = []
    for offset, (command_type, payload, suffix, refs) in enumerate(commands):
        receipts.append(
            harness.execute(
                _command(
                    harness,
                    actor,
                    command_type,
                    payload,
                    f"{idempotency_key}-{suffix}",
                    expected_version=version + offset,
                    refs=refs,
                )
            )
        )
    return receipts


def _ensure_episode_asset_candidate_draft(
    store: RuntimeStore,
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    *,
    run_ref: ExactObjectRef,
    idempotency_key: str,
    created_at: str,
) -> None:
    adapter = _asset_candidate_adapter_for_run(harness, run_ref, idempotency_key)
    if adapter.predecessor_ref is None:
        raise StateConflictError("episode writeback requires a target shot")
    scope, aggregate = _load_episode_aggregate_for_writeback(store, harness, actor)
    target_ref = _entity_ref(adapter.predecessor_ref)
    protected_refs = tuple(_entity_ref(ref) for ref in adapter.protected_exact_refs)
    candidate_ref = _entity_ref(adapter.successor_ref)
    existing = _latest_candidate_by_entity(aggregate, candidate_ref.entity_id)
    if existing is not None:
        if existing.control_provenance is not None:
            return
        if existing.as_ref() == candidate_ref and existing.lifecycle_state == "candidate":
            return
        raise StateConflictError("episode candidate writeback identity is already used")
    if any(item.as_ref() == candidate_ref for item in aggregate.asset_candidates):
        return
    _require_latest_refs(aggregate, (target_ref, *protected_refs))
    draft = AssetCandidateVersion(
        entity_id=candidate_ref.entity_id,
        version_id=candidate_ref.version_id,
        revision=1,
        parent_version_id=None,
        lifecycle_state="candidate",
        review_state="needs_review",
        content_digest=digest(
            {
                "operation": "production_control_episode_asset_candidate_draft",
                "target_ref": target_ref.model_dump(mode="json"),
                "candidate_ref": candidate_ref.model_dump(mode="json"),
                "run_ref": run_ref.model_dump(mode="json"),
            }
        ),
        scope=scope,
        created_at=_max_stamp(aggregate.evaluated_at, _stamp(created_at)),
        target_ref=target_ref,
        artifact_ref=None,
        job_id=f"job-draft-{candidate_ref.entity_id}",
        job_state="running",
        control_provenance=None,
    )
    payload = aggregate.model_dump(mode="python")
    payload.update(
        {
            "aggregate_version": aggregate.aggregate_version + 1,
            "evaluated_at": draft.created_at,
            "asset_candidates": (*aggregate.asset_candidates, draft),
        }
    )
    updated = ProductionProjectAggregate.model_validate(payload)
    try:
        EpisodeDomainAggregateStore(store.root).save(
            updated,
            expected_aggregate_version=aggregate.aggregate_version,
            idempotency_key=f"{idempotency_key}-episode-asset-candidate-draft",
            payload_digest=digest(
                {
                    "operation": "production_control_episode_asset_candidate_draft",
                    "candidate": draft.model_dump(mode="json"),
                    "expected_aggregate_version": aggregate.aggregate_version,
                }
            ),
        )
    except EpisodeDomainStoreError as exc:
        raise StateConflictError("episode production draft candidate could not be saved") from exc


def _apply_episode_asset_candidate_writeback(
    store: RuntimeStore,
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    *,
    run_id: str,
    idempotency_key: str,
    created_at: str,
) -> dict[str, Any]:
    token = _command_token(idempotency_key)
    writeback = _writeback_by_id(harness, f"writeback-{run_id}-{token}")
    adapter = writeback.adapter_request
    if adapter.mode != "asset_candidate" or adapter.predecessor_ref is None:
        raise StateConflictError("episode writeback requires an asset candidate adapter")
    scope, aggregate = _load_episode_aggregate_for_writeback(store, harness, actor)
    aggregate_store = EpisodeDomainAggregateStore(store.root)

    target_ref = _entity_ref(adapter.predecessor_ref)
    protected_refs = tuple(_entity_ref(ref) for ref in adapter.protected_exact_refs)
    candidate_ref = _entity_ref(adapter.successor_ref)
    latest = _latest_candidate_by_entity(aggregate, candidate_ref.entity_id)
    if latest is not None and latest.control_provenance is not None:
        return {
            "status": "already_applied",
            "aggregate_version": aggregate.aggregate_version,
            "candidate_ref": latest.as_ref().model_dump(mode="json"),
            "target_ref": target_ref.model_dump(mode="json"),
            "protected_refs": [ref.model_dump(mode="json") for ref in protected_refs],
        }
    if latest is None or latest.as_ref() != candidate_ref or latest.lifecycle_state != "candidate":
        raise StateConflictError("episode writeback draft candidate is missing")

    _require_latest_refs(aggregate, (target_ref, *protected_refs))
    stamp = _next_stamp(aggregate.evaluated_at, _stamp(created_at))
    candidate = AssetCandidateVersion(
        entity_id=candidate_ref.entity_id,
        version_id=f"{candidate_ref.entity_id}-v2",
        revision=latest.revision + 1,
        parent_version_id=latest.version_id,
        lifecycle_state="approved",
        review_state="approved",
        content_digest=digest(
            {
                "artifact_id": writeback.artifact_id,
                "artifact_digest": writeback.artifact_digest,
                "target_ref": target_ref.model_dump(mode="json"),
                "writeback_ref": exact_ref("artifact_writeback", writeback).model_dump(mode="json"),
            }
        ),
        scope=scope,
        created_at=stamp,
        target_ref=target_ref,
        artifact_ref=SafeArtifactRef(
            artifact_id=writeback.artifact_id,
            artifact_type="storyboard",
            content_digest=writeback.artifact_digest,
        ),
        job_id=f"job-{writeback.identity.object_id}",
        job_state="succeeded",
        control_provenance=ProductionControlProvenance(
            plan_task_ref=_control_ref(writeback.plan_task_ref),
            run_ref=_control_ref(writeback.run_ref),
            attempt_ref=_control_ref(writeback.attempt_ref),
            writeback_ref=_control_ref(exact_ref("artifact_writeback", writeback)),
            affected_refs=(target_ref,),
            protected_refs=protected_refs,
        ),
    )
    approval = ReviewDecision(
        entity_id=f"review-{candidate_ref.entity_id}",
        version_id=f"review-{candidate_ref.entity_id}-v1",
        revision=1,
        parent_version_id=None,
        lifecycle_state="approved",
        review_state="approved",
        content_digest=digest(
            {
                "operation": "production_control_episode_asset_candidate_approval",
                "candidate_ref": candidate.as_ref().model_dump(mode="json"),
                "writeback_ref": exact_ref("artifact_writeback", writeback).model_dump(mode="json"),
            }
        ),
        scope=scope,
        created_at=stamp,
        subject_ref=candidate.as_ref(),
        decision="approve",
        note="Production control receipt closed for this deterministic candidate.",
    )
    payload = aggregate.model_dump(mode="python")
    payload.update(
        {
            "aggregate_version": aggregate.aggregate_version + 1,
            "evaluated_at": stamp,
            "asset_candidates": (*aggregate.asset_candidates, candidate),
            "review_decisions": (*aggregate.review_decisions, approval),
        }
    )
    updated = ProductionProjectAggregate.model_validate(payload)
    try:
        result = aggregate_store.save(
            updated,
            expected_aggregate_version=aggregate.aggregate_version,
            idempotency_key=f"{idempotency_key}-episode-asset-candidate-finalize",
            payload_digest=digest(
                {
                    "operation": "production_control_episode_asset_candidate_finalize",
                    "candidate": candidate.model_dump(mode="json"),
                    "approval": approval.model_dump(mode="json"),
                    "expected_aggregate_version": aggregate.aggregate_version,
                }
            ),
        )
    except EpisodeDomainStoreError as exc:
        raise StateConflictError("episode production writeback could not be saved") from exc
    return {
        "status": "applied" if not result.replayed else "replayed",
        "aggregate_version": result.aggregate.aggregate_version,
        "candidate_ref": candidate.as_ref().model_dump(mode="json"),
        "target_ref": target_ref.model_dump(mode="json"),
        "protected_refs": [ref.model_dump(mode="json") for ref in protected_refs],
    }


def _read_model(harness: ProductionControlHarness, store: RuntimeStore | None = None) -> dict[str, Any]:
    mission_revision = _latest_model(harness, "mission_revision", MissionRevision)
    plan_revision = _latest_model(harness, "plan_revision", PlanRevision)
    plan = _latest_model(harness, "production_plan", ProductionPlan)
    task_records = _records(harness, "plan_task")
    run_records = _records(harness, "production_run")
    writebacks = _records(harness, "artifact_writeback")
    impacts = _records(harness, "impact_assessment")
    gates = _records(harness, "provider_gate_decision")
    confirmed_writeback_refs = _confirmed_episode_writeback_refs(store, harness) if store is not None else None
    task_by_ref = {
        _ref_key(_ref("plan_task", PlanTask.model_validate(item))): PlanTask.model_validate(item)
        for item in task_records
    }
    runs = []
    for item in run_records:
        run = ProductionRun.model_validate(item)
        task = task_by_ref.get(_ref_key(run.task_ref))
        attempts = harness.projection.run_attempts.get(run.identity.object_id, [])
        runs.append(
            {
                "run_id": run.identity.object_id,
                "task_id": run.task_ref.object_id,
                "task_title": _task_title(run.task_ref.object_id),
                "boundary": task.boundary if task else "",
                "execution_state": harness.projection.run_execution.get(run.identity.object_id, run.execution_state),
                "control_state": harness.projection.run_control.get(run.identity.object_id, run.control_state),
                "attempt_count": len(attempts),
                "latest_attempt_id": attempts[-1].object_id if attempts else "",
                "simulated_cost_label": "¥0 · 外部生成未启用",
                "waiting_human": run.identity.object_id in harness.projection.open_human_requests,
                "blocked": run.identity.object_id in harness.projection.blockers,
            }
        )
    artifacts = []
    affected_refs: list[dict[str, str]] = []
    protected_refs: list[dict[str, str]] = []
    for item in writebacks:
        writeback = ArtifactWriteback.model_validate(item)
        if confirmed_writeback_refs is not None and _control_ref_key(
            _control_ref(exact_ref("artifact_writeback", writeback))
        ) not in confirmed_writeback_refs:
            continue
        adapter = writeback.adapter_request
        affected_ref = _writeback_affected_ref(adapter)
        affected_refs.append(affected_ref.model_dump(mode="json"))
        protected_refs.extend(ref.model_dump(mode="json") for ref in adapter.protected_exact_refs)
        artifacts.append(
            {
                "artifact_id": writeback.artifact_id,
                "task_id": writeback.plan_task_ref.object_id,
                "run_id": writeback.run_ref.object_id,
                "attempt_id": writeback.attempt_ref.object_id,
                "affected_ref": affected_ref.model_dump(mode="json"),
                "candidate_ref": adapter.successor_ref.model_dump(mode="json"),
                "predecessor_ref": adapter.predecessor_ref.model_dump(mode="json") if adapter.predecessor_ref else None,
                "protected_refs": [ref.model_dump(mode="json") for ref in adapter.protected_exact_refs],
                "operation": adapter.existing_typed_operation,
            }
        )
    provider_gate = "closed"
    if gates:
        provider_gate = "closed" if all(not ProviderGateDecision.model_validate(item).allowed for item in gates) else "authorized"
    return {
        "schema_version": _CONTROL_SCHEMA,
        "project_id": harness.scope.project_id,
        "version": harness.projection.version,
        "projection_digest": harness.projection.state_digest,
        "event_count": len(harness.events),
        "outbox_count": len(harness.outbox),
        "provider_dispatch_count": harness.provider_dispatch_count,
        "provider_gate": provider_gate,
        "mission": {
            "objective": mission_revision.objective if mission_revision else "",
            "status": "recorded" if mission_revision else "missing",
        },
        "plan": {
            "status": harness.projection.plan_status.get(plan.identity.object_id, "missing") if plan else "missing",
            "task_specs": [
                {
                    "task_id": spec.task_id,
                    "title": _task_title(spec.task_id),
                    "boundary": spec.boundary,
                    "capability": spec.capability,
                    "dependency_task_ids": list(spec.dependency_task_ids),
                }
                for spec in (plan_revision.task_specs if plan_revision else ())
            ],
        },
        "tasks": [
            {
                "task_id": PlanTask.model_validate(item).identity.object_id,
                "title": _task_title(PlanTask.model_validate(item).identity.object_id),
                "boundary": PlanTask.model_validate(item).boundary,
                "state": PlanTask.model_validate(item).execution_state,
            }
            for item in task_records
        ],
        "runs": runs,
        "human_decisions": {
            "open_count": len(harness.projection.open_human_requests),
            "open_run_ids": sorted(harness.projection.open_human_requests),
        },
        "blockers": {
            "open_count": len(harness.projection.blockers),
            "open_run_ids": sorted(harness.projection.blockers),
        },
        "artifacts": artifacts,
        "continuity": {
            "affected_refs": affected_refs,
            "protected_refs": protected_refs,
            "impact_assessment_count": len(impacts),
            "shot_local_rework_protected": bool(affected_refs and protected_refs),
        },
        "review": {
            "status": "ready" if artifacts else "waiting_for_artifacts",
            "delivery_readback": "internal_delivery_packet_ready" if artifacts else "not_ready",
            "non_claims": [
                "not_provider_smoke",
                "not_generated_media_qa",
                "not_human_acceptance",
                "not_business_validation",
            ],
        },
        "workspace_entry": {
            "episode_id": BOOTSTRAP_EPISODE_ID,
            "episode_version_id": BOOTSTRAP_EPISODE_VERSION_ID,
            "href": (
                f"/studio/episode-workspace/?project={harness.scope.project_id}"
                f"&episode={BOOTSTRAP_EPISODE_ID}&version={BOOTSTRAP_EPISODE_VERSION_ID}"
            ),
        },
        "recovery": {
            "ledger_rebuildable": True,
            "outbox_pending_count": len(harness.outbox),
        },
    }


def _episode_workspace_projection(model: dict[str, Any]) -> dict[str, Any]:
    project_id = model["project_id"]
    episode_ref = {
        "entity_type": "episode",
        "entity_id": "episode-production-control",
        "version_id": "episode-production-control-v1",
    }
    scene_ref = {
        "entity_type": "scene",
        "entity_id": "scene-production-control",
        "version_id": "scene-production-control-v1",
    }
    shots = _workspace_shots(model)
    return {
        "schema_version": "afs_episode_workspace_projection.v0.1",
        "aggregate": {
            "schema_version": "afs_episode_production_aggregate.v0.1",
            "aggregate_version": max(1, int(model["version"] or 0)),
            "evaluated_at": _DEFAULT_STAMP,
            "scope": {
                "org_id": "production-control",
                "project_id": project_id,
                "actor_id": "production-control",
            },
            "projects": [
                {
                    "entity_type": "project",
                    "entity_id": project_id,
                    "version_id": f"{project_id}-v1",
                    "revision": 1,
                    "title": "制片项目",
                    "data_policy": {
                        "visibility": "private",
                        "training_use": "denied_by_default",
                        "product_improvement_use": "denied_by_default",
                        "export_enabled": True,
                        "deletion_enabled": True,
                    },
                }
            ],
            "series": [
                {
                    "entity_type": "series",
                    "entity_id": "series-production-control",
                    "version_id": "series-production-control-v1",
                    "revision": 1,
                    "title": "制片系列",
                }
            ],
            "episodes": [{**episode_ref, "revision": 1, "title": "第一集制作计划"}],
            "scenes": [{**scene_ref, "revision": 1, "sequence": 1, "title": "制作场景"}],
            "shots": [
                {
                    "entity_type": "shot",
                    "entity_id": shot["ref"]["entity_id"],
                    "version_id": shot["ref"]["version_id"],
                    "revision": shot["revision"],
                    "scene_ref": scene_ref,
                    "sequence": shot["sequence"],
                    "duration_seconds": 3,
                }
                for shot in shots
            ],
        },
        "workspace": {
            "episode_ref": episode_ref,
            "scenes": [{"ref": scene_ref, "sequence": 1, "title": "制作场景"}],
            "shots": shots,
            "next_action": {"label": "检查生产控制交付读回", "shot_ref": shots[0]["ref"]} if shots else None,
            "recovery": {"source": "production_control_ledger", "event_count": model["event_count"]},
            "truth": {
                "scene_count": 1,
                "shot_count": len(shots),
                "duration_seconds": len(shots) * 3,
                "missing_asset_count": 0 if model["artifacts"] else len(shots),
                "generation_dispatch_count": 0,
                "playable_preview_available": False,
            },
            "delivery": {
                "current_ref": None,
                "status": "ready" if model["artifacts"] else "blocked",
                "missing_asset_count": 0 if model["artifacts"] else len(shots),
                "preview_artifact_present": False,
                "playable_preview_available": False,
                "blockers": [] if model["artifacts"] else ["delivery_not_frozen"],
            },
            "evidence_environment": {
                "provider_dispatch_count": model["provider_dispatch_count"],
                "provider_gate": model["provider_gate"],
            },
        },
    }


def _workspace_shots(model: dict[str, Any]) -> list[dict[str, Any]]:
    affected_versions = {
        item["affected_ref"]["object_id"]: item["affected_ref"]["revision_id"]
        for item in model["artifacts"]
        if item.get("affected_ref")
    }
    rows = []
    for index in range(1, max(3, len(model["runs"])) + 1):
        entity_id = f"shot-{index:03d}"
        version_id = affected_versions.get(entity_id, f"{entity_id}-v1")
        affected = entity_id in affected_versions
        rows.append(
            {
                "ref": {"entity_type": "shot", "entity_id": entity_id, "version_id": version_id},
                "revision": 2 if affected else 1,
                "scene_ref": {
                    "entity_type": "scene",
                    "entity_id": "scene-production-control",
                    "version_id": "scene-production-control-v1",
                },
                "sequence": index,
                "duration_seconds": 3,
                "lifecycle_state": "candidate" if affected else "draft",
                "review_state": "needs_review" if affected else "not_requested",
                "production_state": "rework" if affected else None,
                "selection_state": None,
                "selection_lifecycle_state": None,
                "ai_check_state": None,
                "delivery_invalid": False,
                "blocking": affected,
                "script": None,
                "thumbnail_url": None,
                "review_note": "生产控制写回后等待审核。" if affected else None,
                "facts": [{"label": "事实来源", "value": "production-control ledger"}],
                "continuity": [],
                "continuity_issue": None,
                "candidates": [],
                "selections": [],
                "agent_proposals": [],
                "agent_proposal": None,
                "prior_shot_blockers": [],
                "allowed_actions": [
                    {"action": "inspect", "enabled": True, "reason": "", "blocked_by": []},
                    {"action": "review_shot", "enabled": False, "reason": "请回到制片工作台写回候选素材。", "blocked_by": []},
                    {"action": "reassign_scene", "enabled": False, "reason": "生产控制投影只读。", "blocked_by": []},
                    {"action": "apply_continuity", "enabled": False, "reason": "请回到制片工作台写回候选素材。", "blocked_by": []},
                    {"action": "adopt_candidate", "enabled": False, "reason": "没有候选素材。", "blocked_by": []},
                    {"action": "review_selection", "enabled": False, "reason": "没有选版。", "blocked_by": []},
                    {"action": "lock_selection", "enabled": False, "reason": "没有选版。", "blocked_by": []},
                ],
            }
        )
    return rows


@contextmanager
def _locked_harness(
    store: RuntimeStore,
    project_id: str,
    scope: ProjectScope,
    actor: ActorIdentity,
):
    path = _ledger_path(store, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(_lock_path(store, project_id)):
        try:
            if path.is_file():
                harness = ProductionControlHarness.load(path, _grants(actor), {actor.actor_id: actor})
                if harness.scope != scope:
                    raise AuthorizationError("production-control ledger scope mismatch")
            else:
                harness = ProductionControlHarness(scope, _grants(actor), {actor.actor_id: actor}, file_path=path)
            yield harness
        except (
            AuthorizationError,
            VersionConflictError,
            IdempotencyConflictError,
            StateConflictError,
            AtomicCommitError,
            LedgerIntegrityError,
            ValidationError,
            ValueError,
        ) as exc:
            _raise_exception(project_id, exc)


def _control_identity(
    project_id: str,
    request: Request,
    store: RuntimeStore,
    auth: RuntimeAuthStore,
) -> tuple[ProjectScope, ActorIdentity]:
    tenant = _require_project_scope(store, auth, request, project_id)
    scope = ProjectScope(org_id=tenant.org_id, project_id=tenant.project_id)
    actor = ActorIdentity(
        actor_id=tenant.actor_id,
        actor_type="human",
        authority_ref=f"runtime-membership-{tenant.actor_id}",
    )
    return scope, actor


def _ledger_path(store: RuntimeStore, project_id: str):
    return store.projects_dir / safe_id(project_id) / "production_control" / "ledger.json"


def _lock_path(store: RuntimeStore, project_id: str):
    return store.projects_dir / safe_id(project_id) / "production_control" / "production_control.lock"


def _grants(actor: ActorIdentity) -> dict[str, set[str]]:
    return {actor.actor_id: set(_CAPABILITIES)}


def _identity(
    scope: ProjectScope,
    object_id: str,
    *,
    revision: int = 1,
    parent_revision_id: str | None = None,
    created_at: str,
) -> RevisionIdentity:
    return RevisionIdentity(
        scope=scope,
        object_id=object_id,
        revision_id=f"{object_id}-v{revision}",
        revision=revision,
        parent_revision_id=parent_revision_id,
        created_at=created_at,
    )


def _new_plan(
    scope: ProjectScope,
    mission_revision: MissionRevision,
    task_specs: tuple[PlanTaskSpec, ...],
    max_budget: int,
    stamp: str,
):
    revision_ref = ExactObjectRef(
        scope=scope,
        object_type="plan_revision",
        object_id="plan-revision-main",
        revision_id="plan-revision-main-v1",
    )
    plan = ProductionPlan(
        identity=_identity(scope, "plan-main", created_at=stamp),
        mission_revision_ref=exact_ref("mission_revision", mission_revision),
        head_revision_ref=revision_ref,
    )
    budget = BudgetEnvelope(
        identity=_identity(scope, "budget-main", created_at=stamp),
            estimated=MoneyRange(
                currency="CNY",
                minimum=0,
                maximum=max_budget,
                unit="deterministic-production-batch",
                assumption="本轮使用确定性制作流程，外部生成调用保持为零。",
            ),
        max_budget=max_budget,
    )
    estimate = CostEstimate(
        identity=_identity(scope, "estimate-main", created_at=stamp),
        plan_revision_ref=revision_ref,
        estimated=budget.estimated,
    )
    revision = PlanRevision(
        identity=_identity(scope, "plan-revision-main", created_at=stamp),
        plan_ref=exact_ref("production_plan", plan),
        task_specs=task_specs,
        budget_envelope_ref=exact_ref("budget_envelope", budget),
        cost_estimate_refs=(exact_ref("cost_estimate", estimate),),
    )
    return plan, revision, budget, estimate


def _task_specs(rows: tuple[PlanTaskRequest, ...]) -> tuple[PlanTaskSpec, ...]:
    source = rows or (
        PlanTaskRequest(title="拆解镜头", boundary="拆解制作目标为局部镜头与连续性检查。"),
        PlanTaskRequest(title="生成候选", boundary="生成本轮确定性候选，并标清预算与影响范围。"),
        PlanTaskRequest(title="审核交付", boundary="汇总写回、连续性和交付读回证据。"),
    )
    specs = [
        PlanTaskSpec(
            task_id=f"task-{index:03d}",
            boundary=item.boundary,
            capability=item.capability,
            dependency_task_ids=tuple(dep for dep in item.dependency_task_ids if dep),
        )
        for index, item in enumerate(source, start=1)
    ]
    if len(specs) < 3:
        raise StateConflictError("plan requires at least three tasks")
    return tuple(specs)


def _first_run_bundle(
    scope: ProjectScope,
    task: PlanTask,
    *,
    index: int,
    stamp: str,
) -> dict[str, Any]:
    assignment = AgentAssignment(
        identity=_identity(scope, f"assignment-{index:03d}", created_at=stamp),
        task_ref=exact_ref("plan_task", task),
        agent_id=f"deterministic-worker-{index:03d}",
        capability=task.capability,
    )
    run_ref = ExactObjectRef(
        scope=scope,
        object_type="production_run",
        object_id=f"run-{index:03d}",
        revision_id=f"run-{index:03d}-v1",
    )
    attempt = RunAttempt(
        identity=_identity(scope, f"attempt-{index:03d}-001", created_at=stamp),
        run_ref=run_ref,
        attempt_number=1,
    )
    run = ProductionRun(
        identity=_identity(scope, f"run-{index:03d}", created_at=stamp),
        task_ref=exact_ref("plan_task", task),
        assignment_ref=exact_ref("agent_assignment", assignment),
        execution_state="running",
        latest_attempt_ref=exact_ref("run_attempt", attempt),
    )
    return {
        "assignment": assignment.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "attempt": attempt.model_dump(mode="json"),
    }


def _next_attempt(harness: ProductionControlHarness, run_ref: ExactObjectRef, stamp: str) -> RunAttempt:
    attempts = harness.projection.run_attempts.get(run_ref.object_id, [])
    if not attempts:
        raise StateConflictError("run has no prior attempt")
    number = len(attempts) + 1
    return RunAttempt(
        identity=_identity(harness.scope, f"attempt-{run_ref.object_id}-{number:03d}", created_at=stamp),
        run_ref=run_ref,
        attempt_number=number,
        prior_attempt_ref=attempts[-1],
    )


def _command(
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    command_type: str,
    payload: dict[str, Any],
    key: str,
    *,
    expected_version: int,
    refs: tuple[ExactObjectRef, ...] = (),
    budget_authorization: BudgetAuthorization | None = None,
    provider_authorization: ProviderAuthorization | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=f"cmd-{key}",
        command_type=command_type,
        scope=harness.scope,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=key,
        correlation_id=f"corr-{harness.scope.project_id}",
        causation_id=f"cmd-{key}",
        capability={
            "mission.record": "mission.write",
            "plan.propose": "plan.write",
            "plan.revise": "plan.write",
            "plan.approve": "plan.approve",
            "run.transition": "run.execute",
            "run.control": "run.control",
            "decision.record": "decision.write",
            "provider.evaluate": "provider.evaluate",
            "artifact.register": "artifact.write",
            "artifact.writeback": "artifact.write",
            "selective_revision.request": "revision.write",
            "impact.assess": "revision.write",
        }[command_type],
        exact_object_refs=refs,
        budget_authorization=budget_authorization,
        provider_authorization=provider_authorization,
        payload=payload,
        payload_digest=digest(payload),
    )


def _records(harness: ProductionControlHarness, object_type: str) -> list[dict[str, Any]]:
    rows = [
        value
        for (kind, _, _), value in harness.projection.records.items()
        if kind == object_type
    ]
    return sorted(rows, key=lambda item: (item["identity"]["object_id"], item["identity"]["revision"]))


def _latest_model(harness: ProductionControlHarness, object_type: str, model: type[Any]):
    rows = _records(harness, object_type)
    if not rows:
        return None
    return model.model_validate(
        sorted(rows, key=lambda item: (item["identity"]["revision"], item["identity"]["created_at"]))[-1]
    )


def _run_by_id(harness: ProductionControlHarness, run_id: str) -> ProductionRun:
    rows = [
        ProductionRun.model_validate(item)
        for item in _records(harness, "production_run")
        if item["identity"]["object_id"] == run_id
    ]
    if not rows:
        raise StateConflictError("run does not exist")
    return rows[-1]


def _require_no_records(harness: ProductionControlHarness, object_type: str) -> None:
    if _records(harness, object_type):
        raise StateConflictError(f"{object_type} already exists")


def _ref(object_type: str, model: Any) -> ExactObjectRef:
    return exact_ref(object_type, model)


def _ref_key(ref: ExactObjectRef) -> str:
    return f"{ref.object_type}:{ref.object_id}:{ref.revision_id}"


def _writeback_by_id(harness: ProductionControlHarness, object_id: str) -> ArtifactWriteback:
    for item in _records(harness, "artifact_writeback"):
        writeback = ArtifactWriteback.model_validate(item)
        if writeback.identity.object_id == object_id:
            return writeback
    raise StateConflictError("artifact writeback was not committed")


def _writeback_affected_ref(adapter: EpisodeArtifactAdapterRequest) -> ExactObjectRef:
    if adapter.mode == "asset_candidate" and adapter.predecessor_ref is not None:
        return adapter.predecessor_ref
    return adapter.successor_ref


def _asset_candidate_adapter_for_run(
    harness: ProductionControlHarness,
    run_ref: ExactObjectRef,
    idempotency_key: str,
) -> EpisodeArtifactAdapterRequest:
    token = _command_token(idempotency_key)
    shot_index = _run_shot_index(run_ref.object_id)
    affected = ExactObjectRef(
        scope=harness.scope,
        object_type="shot",
        object_id=f"shot-{shot_index:03d}",
        revision_id=f"shot-{shot_index:03d}-v1",
    )
    successor = ExactObjectRef(
        scope=harness.scope,
        object_type="asset_candidate",
        object_id=f"candidate-{run_ref.object_id}-{token}",
        revision_id=f"candidate-{run_ref.object_id}-{token}-v1",
    )
    protected = tuple(
        ExactObjectRef(
            scope=harness.scope,
            object_type="shot",
            object_id=f"shot-{index:03d}",
            revision_id=f"shot-{index:03d}-v1",
        )
        for index in range(1, 4)
        if index != shot_index
    )
    return EpisodeArtifactAdapterRequest(
        mode="asset_candidate",
        predecessor_ref=affected,
        successor_ref=successor,
        protected_exact_refs=protected,
        existing_typed_operation="asset_candidate.create_version",
    )


def _load_episode_aggregate_for_writeback(
    store: RuntimeStore,
    harness: ProductionControlHarness,
    actor: ActorIdentity,
) -> tuple[TenantScope, ProductionProjectAggregate]:
    scope = TenantScope(
        org_id=harness.scope.org_id,
        project_id=harness.scope.project_id,
        actor_id=actor.actor_id,
    )
    aggregate_store = EpisodeDomainAggregateStore(store.root)
    try:
        aggregate = aggregate_store.load(org_id=scope.org_id, project_id=scope.project_id)
    except AggregateNotFoundError as exc:
        raise StateConflictError(
            "episode production facts are missing; create a Studio production project first"
        ) from exc
    except EpisodeDomainStoreError as exc:
        raise StateConflictError("episode production facts failed integrity checks") from exc
    if aggregate.scope != scope:
        raise StateConflictError("episode production scope does not match production control scope")
    return scope, aggregate


def _confirmed_episode_writeback_refs(
    store: RuntimeStore,
    harness: ProductionControlHarness,
) -> set[str]:
    try:
        aggregate = EpisodeDomainAggregateStore(store.root).load(
            org_id=harness.scope.org_id,
            project_id=harness.scope.project_id,
        )
    except EpisodeDomainStoreError:
        return set()
    confirmed: set[str] = set()
    latest: dict[str, AssetCandidateVersion] = {}
    for candidate in aggregate.asset_candidates:
        current = latest.get(candidate.entity_id)
        if current is None or candidate.revision > current.revision:
            latest[candidate.entity_id] = candidate
    for candidate in latest.values():
        provenance = candidate.control_provenance
        if (
            provenance is not None
            and candidate.lifecycle_state in ("approved", "locked")
            and candidate.review_state == "approved"
        ):
            confirmed.add(_control_ref_key(provenance.writeback_ref))
    return confirmed


def _latest_candidate_by_entity(
    aggregate: ProductionProjectAggregate,
    entity_id: str,
) -> AssetCandidateVersion | None:
    matches = [item for item in aggregate.asset_candidates if item.entity_id == entity_id]
    if not matches:
        return None
    return max(matches, key=lambda item: item.revision)


def _run_shot_index(run_id: str) -> int:
    match = re.fullmatch(r"run-(\d+)", run_id)
    if match is None:
        raise StateConflictError("run id cannot be mapped to a shot")
    index = int(match.group(1))
    if index < 1 or index > 3:
        raise StateConflictError("Wave 1 writeback expects one of the first three runs")
    return index


def _entity_ref(ref: ExactObjectRef) -> EntityVersionRef:
    if ref.object_type not in {"shot", "continuity_state", "asset_candidate"}:
        raise StateConflictError("writeback reference cannot be mapped to episode facts")
    return EntityVersionRef(
        entity_type=ref.object_type,  # type: ignore[arg-type]
        entity_id=ref.object_id,
        version_id=ref.revision_id,
    )


def _control_ref(ref: ExactObjectRef) -> ControlObjectRef:
    return ControlObjectRef(
        object_type=ref.object_type,
        object_id=ref.object_id,
        revision_id=ref.revision_id,
    )


def _control_ref_key(ref: ControlObjectRef) -> str:
    return f"{ref.object_type}:{ref.object_id}:{ref.revision_id}"


def _require_latest_refs(
    aggregate: ProductionProjectAggregate,
    refs: tuple[EntityVersionRef, ...],
) -> None:
    for ref in refs:
        if ref.entity_type == "shot":
            collection = aggregate.shots
        elif ref.entity_type == "continuity_state":
            collection = aggregate.continuity_states
        else:
            raise StateConflictError("writeback affected/protected refs must be shots or continuity facts")
        matches = [item for item in collection if item.entity_id == ref.entity_id]
        if not matches:
            raise StateConflictError("writeback target ref does not resolve in episode facts")
        latest = max(matches, key=lambda item: item.revision)
        if latest.as_ref() != ref:
            raise StateConflictError("episode facts changed; reload before writing back")


def _max_stamp(first: str, second: str) -> str:
    first_value = datetime.fromisoformat(_stamp(first))
    second_value = datetime.fromisoformat(_stamp(second))
    return (first_value if first_value >= second_value else second_value).isoformat()


def _next_stamp(first: str, second: str) -> str:
    first_value = datetime.fromisoformat(_stamp(first))
    second_value = datetime.fromisoformat(_stamp(second))
    value = first_value if first_value >= second_value else second_value
    if value <= first_value:
        value = first_value + timedelta(microseconds=1)
    return value.isoformat()


def _task_ref_for_run(run: ProductionRun) -> ExactObjectRef:
    return run.task_ref


def _task_title(task_id: str) -> str:
    mapping = {
        "task-001": "镜头拆解",
        "task-002": "候选写回",
        "task-003": "审核交付",
    }
    return mapping.get(task_id, task_id)


def _command_token(idempotency_key: str) -> str:
    token = safe_id(idempotency_key)
    return token[:72] or "command"


def _stamp(value: str) -> str:
    text = str(value or _DEFAULT_STAMP).strip().replace("Z", "+00:00")
    return text or _DEFAULT_STAMP


def _guard_safe_body(body: BaseModel) -> None:
    reject_unsafe_payload(body.model_dump(mode="json"))


def _raise_exception(project_id: str, exc: Exception) -> None:
    if isinstance(exc, AuthorizationError):
        _raise_control_error(
            project_id,
            403,
            "production_control_forbidden",
            "Production control access is denied.",
            "authorization",
            cause=exc,
        )
    if isinstance(exc, VersionConflictError):
        _raise_control_error(
            project_id,
            409,
            "production_control_version_conflict",
            "Production control state changed. Reload before retrying.",
            "cas",
            retryable=True,
            cause=exc,
        )
    if isinstance(exc, IdempotencyConflictError):
        _raise_control_error(
            project_id,
            409,
            "production_control_idempotency_conflict",
            "Idempotency key was already used with different command content.",
            "idempotency",
            cause=exc,
        )
    if isinstance(exc, StateConflictError):
        _raise_control_error(
            project_id,
            409,
            "production_control_state_conflict",
            "This command is not valid for the current production state.",
            "state",
            cause=exc,
        )
    if isinstance(exc, AtomicCommitError):
        _raise_control_error(
            project_id,
            500,
            "production_control_commit_failed",
            "Production control command could not be saved.",
            "commit",
            cause=exc,
        )
    if isinstance(exc, LedgerIntegrityError):
        _raise_control_error(
            project_id,
            500,
            "production_control_ledger_integrity_failed",
            "Production control ledger failed integrity verification.",
            "ledger",
            cause=exc,
        )
    _raise_control_error(
        project_id,
        422,
        "production_control_command_invalid",
        "Production control command is invalid.",
        "validation",
        cause=exc,
    )


def _raise_control_error(
    project_id: str,
    status_code: int,
    error: str,
    message: str,
    stage: str,
    *,
    retryable: bool = False,
    cause: Exception | None = None,
) -> None:
    detail = safe_error_detail(
        error,
        message=message,
        project_id=project_id,
        action="production_control",
        stage=stage,
        retryable=retryable,
    )
    exception = HTTPException(status_code=status_code, detail=detail)
    if cause is None:
        raise exception
    raise exception from cause


__all__ = (
    "build_production_control_episode_workspace_projection",
    "register_runtime_production_control_routes",
)
