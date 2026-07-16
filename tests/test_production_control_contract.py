from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentflow_studio.production_control.contract import (
    ActorIdentity,
    AgentAssignment,
    ArtifactCandidateRegistration,
    ArtifactWriteback,
    Blocker,
    BudgetAuthorization,
    BudgetEnvelope,
    CommandEnvelope,
    CostEntry,
    CostEstimate,
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
    ProviderGateDecision,
    ProviderAuthorization,
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


STAMP = "2026-07-15T05:00:00+00:00"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


@pytest.fixture
def scope() -> ProjectScope:
    return ProjectScope(org_id="org-1", project_id="project-1")


@pytest.fixture
def actor() -> ActorIdentity:
    return ActorIdentity(actor_id="creator-1", actor_type="human", authority_ref="membership-1")


@pytest.fixture
def capabilities() -> dict[str, set[str]]:
    return {
        "creator-1": {
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
    }


def identity(
    scope: ProjectScope,
    object_id: str,
    revision_id: str | None = None,
    *,
    revision: int = 1,
    parent_revision_id: str | None = None,
    state: str = "active",
) -> RevisionIdentity:
    return RevisionIdentity(
        scope=scope,
        object_id=object_id,
        revision_id=revision_id or f"{object_id}-v{revision}",
        revision=revision,
        parent_revision_id=parent_revision_id,
        created_at=STAMP,
        state=state,
    )


def command(
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    command_type: str,
    capability: str,
    payload: dict,
    key: str,
    *,
    refs: tuple[ExactObjectRef, ...] = (),
    expected_version: int | None = None,
    budget_authorization: BudgetAuthorization | None = None,
    provider_authorization: ProviderAuthorization | None = None,
) -> CommandEnvelope:
    command_id = f"cmd-{key}"
    return CommandEnvelope(
        command_id=command_id,
        command_type=command_type,
        scope=harness.scope,
        actor=actor,
        expected_version=harness.projection.version if expected_version is None else expected_version,
        idempotency_key=key,
        correlation_id="corr-production-control-v01",
        causation_id=command_id,
        capability=capability,
        exact_object_refs=refs,
        budget_authorization=budget_authorization,
        provider_authorization=provider_authorization,
        payload=payload,
        payload_digest=digest(payload),
    )


def bootstrap_to_approved_plan(
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    *,
    execute_approval: bool = True,
) -> dict[str, object]:
    constraint = ReferenceConstraint(
        identity=identity(harness.scope, "constraint-shot8"),
        constraint_type="story",
        rule="Shot8 exact identity and version must remain unchanged.",
    )
    constraint_ref = exact_ref("reference_constraint", constraint)
    mission_revision = MissionRevision(
        identity=identity(harness.scope, "mission-revision-1"),
        objective="Produce the bounded first episode plan.",
        reference_constraint_refs=(constraint_ref,),
    )
    mission = Mission(
        identity=identity(harness.scope, "mission-1"),
        head_revision_ref=exact_ref("mission_revision", mission_revision),
    )
    mission_payload = {
        "mission": mission.model_dump(mode="json"),
        "mission_revision": mission_revision.model_dump(mode="json"),
        "constraints": [constraint.model_dump(mode="json")],
    }
    mission_command = command(
        harness, actor, "mission.record", "mission.write", mission_payload, "mission-record-1"
    )
    harness.execute(mission_command)

    plan = ProductionPlan(
        identity=identity(harness.scope, "plan-1"),
        mission_revision_ref=exact_ref("mission_revision", mission_revision),
        head_revision_ref=ExactObjectRef(
            scope=harness.scope,
            object_type="plan_revision",
            object_id="plan-revision-1",
            revision_id="plan-revision-1-v1",
        ),
    )
    budget = BudgetEnvelope(
        identity=identity(harness.scope, "budget-1"),
        estimated=MoneyRange(
            currency="CNY",
            minimum=100,
            maximum=300,
            unit="deterministic-task-batch",
            assumption="Provider dispatch remains zero.",
        ),
        max_budget=300,
    )
    plan_revision_ref = plan.head_revision_ref
    estimate = CostEstimate(
        identity=identity(harness.scope, "estimate-1"),
        plan_revision_ref=plan_revision_ref,
        estimated=budget.estimated,
    )
    specs = tuple(
        PlanTaskSpec(
            task_id=f"task-{index}",
            boundary=f"Bounded deterministic lane {index}",
            capability="deterministic.harness",
        )
        for index in range(1, 4)
    )
    plan_revision = PlanRevision(
        identity=identity(harness.scope, "plan-revision-1"),
        plan_ref=exact_ref("production_plan", plan),
        task_specs=specs,
        budget_envelope_ref=exact_ref("budget_envelope", budget),
        cost_estimate_refs=(exact_ref("cost_estimate", estimate),),
    )
    proposal_payload = {
        "plan": plan.model_dump(mode="json"),
        "plan_revision": plan_revision.model_dump(mode="json"),
        "budget_envelope": budget.model_dump(mode="json"),
        "cost_estimates": [estimate.model_dump(mode="json")],
    }
    harness.execute(
        command(
            harness,
            actor,
            "plan.propose",
            "plan.write",
            proposal_payload,
            "plan-propose-1",
            refs=(exact_ref("mission_revision", mission_revision),),
        )
    )

    tasks = tuple(
        PlanTask(
            identity=identity(harness.scope, spec.task_id),
            plan_revision_ref=exact_ref("plan_revision", plan_revision),
            boundary=spec.boundary,
            capability=spec.capability,
        )
        for spec in specs
    )
    decision = PlanApprovalDecision(
        identity=identity(harness.scope, "approval-1"),
        plan_revision_ref=exact_ref("plan_revision", plan_revision),
        decision="approved",
        approved_task_refs=tuple(exact_ref("plan_task", task) for task in tasks),
        budget_envelope_ref=exact_ref("budget_envelope", budget),
    )
    approval_payload = {
        "decision": decision.model_dump(mode="json"),
        "tasks": [task.model_dump(mode="json") for task in tasks],
    }
    approval_command = command(
        harness,
        actor,
        "plan.approve",
        "plan.approve",
        approval_payload,
        "plan-approve-1",
        refs=(exact_ref("plan_revision", plan_revision),),
        budget_authorization=BudgetAuthorization(
            budget_envelope_ref=exact_ref("budget_envelope", budget),
            admitted_amount=budget.max_budget,
            currency=budget.estimated.currency,
            authorization_ref="budget-approval-1",
        ),
    )
    approval_receipt = harness.execute(approval_command) if execute_approval else None
    return {
        "mission_command": mission_command,
        "plan_revision": plan_revision,
        "plan": plan,
        "budget": budget,
        "tasks": tasks,
        "decision": decision,
        "approval_command": approval_command,
        "approval_receipt": approval_receipt,
    }


def start_run(
    harness: ProductionControlHarness,
    actor: ActorIdentity,
    task: PlanTask,
    *,
    suffix: str = "1",
) -> dict[str, object]:
    assignment = AgentAssignment(
        identity=identity(harness.scope, f"assignment-{suffix}"),
        task_ref=exact_ref("plan_task", task),
        agent_id=f"agent-deterministic-{suffix}",
        capability="deterministic.harness",
    )
    run_ref = ExactObjectRef(
        scope=harness.scope,
        object_type="production_run",
        object_id=f"run-{suffix}",
        revision_id=f"run-{suffix}-v1",
    )
    attempt = RunAttempt(
        identity=identity(harness.scope, f"attempt-{suffix}"),
        run_ref=run_ref,
        attempt_number=1,
    )
    run = ProductionRun(
        identity=identity(harness.scope, f"run-{suffix}"),
        task_ref=exact_ref("plan_task", task),
        assignment_ref=exact_ref("agent_assignment", assignment),
        execution_state="running",
        latest_attempt_ref=exact_ref("run_attempt", attempt),
    )
    payload = {
        "assignment": assignment.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "attempt": attempt.model_dump(mode="json"),
    }
    harness.execute(
        command(
            harness,
            actor,
            "run.start",
            "run.execute",
            payload,
            f"run-start-{suffix}",
            refs=(exact_ref("plan_task", task),),
        )
    )
    return {"assignment": assignment, "run": run, "attempt": attempt}


def test_plan_approve_is_atomic_and_replay_safe(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor)
    receipt = data["approval_receipt"]
    assert receipt == harness.execute(data["approval_command"])
    assert harness.projection.event_types[-4:] == [
        "PlanApproved",
        "TaskQueued",
        "TaskQueued",
        "TaskQueued",
    ]
    assert len([key for key in harness.projection.records if key[0] == "plan_task"]) == 3
    assert len(harness.events) == len(harness.outbox)

    conflicting_payload = dict(data["approval_command"].payload)
    conflicting_payload["tasks"] = conflicting_payload["tasks"][:-1]
    conflict = command(
        harness,
        actor,
        "plan.approve",
        "plan.approve",
        conflicting_payload,
        "plan-approve-1",
        refs=data["approval_command"].exact_object_refs,
        budget_authorization=data["approval_command"].budget_authorization,
    )
    with pytest.raises(IdempotencyConflictError):
        harness.execute(conflict)


def test_plan_revision_is_immutable_and_old_head_cannot_be_approved(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor, execute_approval=False)
    old_plan = data["plan"]
    old_revision = data["plan_revision"]
    new_plan_ref = ExactObjectRef(
        scope=scope,
        object_type="production_plan",
        object_id=old_plan.identity.object_id,
        revision_id="plan-1-v2",
    )
    new_revision = PlanRevision(
        identity=identity(
            scope,
            old_revision.identity.object_id,
            "plan-revision-1-v2",
            revision=2,
            parent_revision_id=old_revision.identity.revision_id,
        ),
        plan_ref=new_plan_ref,
        task_specs=old_revision.task_specs,
        budget_envelope_ref=old_revision.budget_envelope_ref,
        cost_estimate_refs=old_revision.cost_estimate_refs,
    )
    new_plan = ProductionPlan(
        identity=identity(
            scope,
            old_plan.identity.object_id,
            "plan-1-v2",
            revision=2,
            parent_revision_id=old_plan.identity.revision_id,
        ),
        mission_revision_ref=old_plan.mission_revision_ref,
        head_revision_ref=exact_ref("plan_revision", new_revision),
    )
    harness.execute(
        command(
            harness,
            actor,
            "plan.revise",
            "plan.write",
            {
                "plan": new_plan.model_dump(mode="json"),
                "plan_revision": new_revision.model_dump(mode="json"),
            },
            "plan-revise-1",
            refs=(exact_ref("production_plan", old_plan), exact_ref("plan_revision", old_revision)),
        )
    )
    assert harness.projection.plan_heads["plan-1"] == exact_ref("plan_revision", new_revision)
    with pytest.raises(StateConflictError, match="current plan revision"):
        harness.execute(data["approval_command"].model_copy(update={"expected_version": harness.projection.version}))


def test_plan_approval_tasks_must_match_frozen_specs(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor, execute_approval=False)
    rogue_tasks = tuple(
        PlanTask(
            identity=identity(scope, f"rogue-{index}"),
            plan_revision_ref=exact_ref("plan_revision", data["plan_revision"]),
            boundary=f"Unapproved boundary {index}",
            capability="deterministic.harness",
        )
        for index in range(1, 4)
    )
    decision = PlanApprovalDecision(
        identity=identity(scope, "rogue-approval"),
        plan_revision_ref=exact_ref("plan_revision", data["plan_revision"]),
        decision="approved",
        approved_task_refs=tuple(exact_ref("plan_task", task) for task in rogue_tasks),
        budget_envelope_ref=exact_ref("budget_envelope", data["budget"]),
    )
    before = harness.projection.state_digest
    with pytest.raises(StateConflictError, match="stable identities"):
        harness.execute(
            command(
                harness,
                actor,
                "plan.approve",
                "plan.approve",
                {
                    "decision": decision.model_dump(mode="json"),
                    "tasks": [task.model_dump(mode="json") for task in rogue_tasks],
                },
                "rogue-approval",
                refs=(decision.plan_revision_ref,),
                budget_authorization=data["approval_command"].budget_authorization,
            )
        )
    assert harness.projection.state_digest == before
    drifted_tasks = list(data["tasks"])
    drifted_tasks[0] = drifted_tasks[0].model_copy(update={"boundary": "Changed after proposal"})
    with pytest.raises(StateConflictError, match="immutable plan specification"):
        harness.execute(
            command(
                harness,
                actor,
                "plan.approve",
                "plan.approve",
                {
                    "decision": data["decision"].model_dump(mode="json"),
                    "tasks": [task.model_dump(mode="json") for task in drifted_tasks],
                },
                "drifted-approval",
                refs=(data["decision"].plan_revision_ref,),
                budget_authorization=data["approval_command"].budget_authorization,
            )
        )
    assert harness.projection.state_digest == before


def test_approval_failure_rolls_back_events_projection_receipt_and_outbox(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor, execute_approval=False)
    before = (harness.events, harness.outbox, harness.projection.state_digest, dict(harness._receipts))
    with pytest.raises(AtomicCommitError):
        harness.execute(data["approval_command"], fail_before_commit=True)
    assert before == (
        harness.events,
        harness.outbox,
        harness.projection.state_digest,
        harness._receipts,
    )
    assert harness.projection.plan_status["plan-1"] == "proposed"
    assert not [key for key in harness.projection.records if key[0] == "plan_task"]

    malformed_payload = {
        "decision": data["decision"].model_dump(mode="json"),
        "tasks": [task.model_dump(mode="json") for task in data["tasks"][:2]],
    }
    with pytest.raises(StateConflictError):
        harness.execute(
            command(
                harness,
                actor,
                "plan.approve",
                "plan.approve",
                malformed_payload,
                "plan-approve-only-two-tasks",
                refs=(exact_ref("plan_revision", data["plan_revision"]),),
                budget_authorization=data["approval_command"].budget_authorization,
            )
        )
    assert before == (
        harness.events,
        harness.outbox,
        harness.projection.state_digest,
        harness._receipts,
    )

    receipt = harness.execute(data["approval_command"])
    assert receipt.accepted_version == harness.projection.version
    stale = command(
        harness,
        actor,
        "run.start",
        "run.execute",
        {},
        "stale-new-key",
        expected_version=harness.projection.version - 1,
    )
    before = (harness.events, harness.outbox, harness.projection.state_digest, dict(harness._receipts))
    with pytest.raises(VersionConflictError):
        harness.execute(stale)
    assert before == (
        harness.events,
        harness.outbox,
        harness.projection.state_digest,
        harness._receipts,
    )

    task = data["tasks"][0]
    run_data = start_run(harness, actor, task)
    transition = command(
        harness,
        actor,
        "run.transition",
        "run.execute",
        {"run_ref": exact_ref("production_run", run_data["run"]).model_dump(mode="json"), "target_state": "completed"},
        "run-complete-injected",
        refs=(exact_ref("production_run", run_data["run"]),),
    )
    before = (harness.events, harness.outbox, harness.projection.state_digest, dict(harness._receipts))
    with pytest.raises(AtomicCommitError):
        harness.execute(transition, fail_before_commit=True)
    assert before == (
        harness.events,
        harness.outbox,
        harness.projection.state_digest,
        harness._receipts,
    )


def test_scope_actor_refs_and_payload_conflicts_fail_closed(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    foreign_scope = ProjectScope(org_id="org-2", project_id="project-1")
    foreign_ref = ExactObjectRef(
        scope=foreign_scope,
        object_type="mission_revision",
        object_id="foreign",
        revision_id="foreign-v1",
    )
    payload = {"invalid": True}
    before = harness.projection.state_digest
    with pytest.raises(AuthorizationError):
        harness.execute(
            command(
                harness,
                actor,
                "plan.propose",
                "plan.write",
                payload,
                "foreign-ref",
                refs=(foreign_ref,),
            )
        )
    assert harness.projection.state_digest == before

    intruder = ActorIdentity(actor_id="intruder", actor_type="human", authority_ref="none")
    with pytest.raises(AuthorizationError):
        harness.execute(command(harness, intruder, "mission.record", "mission.write", payload, "intruder"))
    spoofed_authority = ActorIdentity(
        actor_id=actor.actor_id,
        actor_type="agent",
        authority_ref="different-authority",
    )
    with pytest.raises(AuthorizationError):
        harness.execute(
            command(
                harness,
                spoofed_authority,
                "mission.record",
                "mission.write",
                payload,
                "spoofed-authority",
            )
        )


def test_waiting_human_blocked_control_and_retry_boundaries(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor)
    run_data = start_run(harness, actor, data["tasks"][0])
    run_ref = exact_ref("production_run", run_data["run"])

    invalid_pause = command(
        harness,
        actor,
        "run.control",
        "run.control",
        {"run_ref": run_ref.model_dump(mode="json"), "target_state": "paused"},
        "invalid-direct-pause",
        refs=(run_ref,),
    )
    before = harness.projection.state_digest
    with pytest.raises(StateConflictError):
        harness.execute(invalid_pause)
    assert harness.projection.state_digest == before

    for index, target in enumerate(("pause-requested", "paused", "resume-requested", "active"), start=1):
        harness.execute(
            command(
                harness,
                actor,
                "run.control",
                "run.control",
                {"run_ref": run_ref.model_dump(mode="json"), "target_state": target},
                f"control-{index}",
                refs=(run_ref,),
            )
        )
    assert not harness.projection.blockers

    request = HumanDecisionRequest(
        identity=identity(scope, "decision-request-1"),
        run_ref=run_ref,
        options=("keep", "revise"),
        impact_refs=(data["tasks"][0].plan_revision_ref,),
        deadline="2026-07-16T05:00:00+00:00",
    )
    harness.execute(
        command(
            harness,
            actor,
            "run.transition",
            "run.execute",
            {
                "run_ref": run_ref.model_dump(mode="json"),
                "target_state": "waiting-human",
                "human_decision_request": request.model_dump(mode="json"),
            },
            "run-waiting-human",
            refs=(run_ref,),
        )
    )
    resume_payload = {"run_ref": run_ref.model_dump(mode="json"), "target_state": "running"}
    with pytest.raises(StateConflictError):
        harness.execute(
            command(harness, actor, "run.transition", "run.execute", resume_payload, "resume-without-decision", refs=(run_ref,))
        )
    decision = HumanDecision(
        identity=identity(scope, "human-decision-1"),
        request_ref=exact_ref("human_decision_request", request),
        selected_option="keep",
        impact_acknowledged=True,
    )
    harness.execute(
        command(
            harness,
            actor,
            "decision.record",
            "decision.write",
            {"human_decision": decision.model_dump(mode="json")},
            "human-decision-1",
            refs=(exact_ref("human_decision_request", request),),
        )
    )
    harness.execute(
        command(harness, actor, "run.transition", "run.execute", resume_payload, "resume-after-decision", refs=(run_ref,))
    )

    clearance_ref = data["tasks"][0].plan_revision_ref
    blocker = Blocker(
        identity=identity(scope, "blocker-1"),
        run_ref=run_ref,
        owner_actor_id="creator-1",
        reason="Needs deterministic clearance evidence.",
        clearance_evidence_refs=(clearance_ref,),
    )
    harness.execute(
        command(
            harness,
            actor,
            "run.transition",
            "run.execute",
            {"run_ref": run_ref.model_dump(mode="json"), "target_state": "blocked", "blocker": blocker.model_dump(mode="json")},
            "run-blocked",
            refs=(run_ref,),
        )
    )
    with pytest.raises((LedgerIntegrityError, StateConflictError)):
        harness.execute(
            command(
                harness,
                actor,
                "run.transition",
                "run.execute",
                {"run_ref": run_ref.model_dump(mode="json"), "target_state": "running", "clearance_evidence_refs": []},
                "blocked-no-clearance",
                refs=(run_ref,),
            )
        )
    with pytest.raises(StateConflictError, match="active blocker"):
        harness.execute(
            command(
                harness,
                actor,
                "run.transition",
                "run.execute",
                {
                    "run_ref": run_ref.model_dump(mode="json"),
                    "target_state": "running",
                    "clearance_evidence_refs": [
                        exact_ref("plan_task", data["tasks"][0]).model_dump(mode="json")
                    ],
                },
                "blocked-wrong-clearance",
                refs=(run_ref,),
            )
        )
    harness.execute(
        command(
            harness,
            actor,
            "run.transition",
            "run.execute",
            {
                "run_ref": run_ref.model_dump(mode="json"),
                "target_state": "running",
                "clearance_evidence_refs": [clearance_ref.model_dump(mode="json")],
            },
            "blocked-cleared",
            refs=(run_ref,),
        )
    )

    retry_attempt = RunAttempt(
        identity=identity(scope, "attempt-2"),
        run_ref=run_ref,
        attempt_number=2,
        prior_attempt_ref=exact_ref("run_attempt", run_data["attempt"]),
    )
    harness.execute(
        command(
            harness,
            actor,
            "run.transition",
            "run.execute",
            {"run_ref": run_ref.model_dump(mode="json"), "target_state": "retrying", "attempt": retry_attempt.model_dump(mode="json")},
            "run-retry-1",
            refs=(run_ref,),
        )
    )
    assert len(harness.projection.run_attempts["run-1"]) == 2
    assert not harness.projection.charge_fingerprints
    assert not harness.projection.artifact_ids

    harness.execute(
        command(
            harness,
            actor,
            "run.transition",
            "run.execute",
            {"run_ref": run_ref.model_dump(mode="json"), "target_state": "running"},
            "retry-resumed",
            refs=(run_ref,),
        )
    )
    harness.execute(
        command(
            harness,
            actor,
            "run.control",
            "run.control",
            {"run_ref": run_ref.model_dump(mode="json"), "target_state": "cancel-requested"},
            "cancel-requested",
            refs=(run_ref,),
        )
    )
    cancel_command = command(
        harness,
        actor,
        "run.transition",
        "run.execute",
        {"run_ref": run_ref.model_dump(mode="json"), "target_state": "cancelled"},
        "run-cancelled",
        refs=(run_ref,),
    )
    cancel_receipt = harness.execute(cancel_command)
    assert harness.execute(cancel_command) == cancel_receipt
    assert harness.projection.run_execution["run-1"] == "cancelled"
    assert len(harness.projection.run_attempts["run-1"]) == 2
    with pytest.raises(StateConflictError, match="terminal run"):
        harness.execute(
            command(
                harness,
                actor,
                "run.transition",
                "run.execute",
                {"run_ref": run_ref.model_dump(mode="json"), "target_state": "running"},
                "write-after-cancel",
                refs=(run_ref,),
            )
        )


def test_cost_writeback_provenance_and_shot7_shot8_protection(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor)
    task = data["tasks"][0]
    run_data = start_run(harness, actor, task)
    run_ref = exact_ref("production_run", run_data["run"])
    attempt_ref = exact_ref("run_attempt", run_data["attempt"])

    entry = CostEntry(
        identity=identity(scope, "cost-1"),
        run_ref=run_ref,
        attempt_ref=attempt_ref,
        kind="actual",
        amount=0,
        currency="CNY",
        charge_fingerprint=DIGEST_A,
    )
    harness.execute(
        command(
            harness,
            actor,
            "cost.record",
            "budget.record",
            {"cost_entry": entry.model_dump(mode="json")},
            "cost-record-1",
            refs=(run_ref, attempt_ref),
        )
    )
    duplicate = entry.model_copy(update={"identity": identity(scope, "cost-2")})
    with pytest.raises(StateConflictError):
        harness.execute(
            command(
                harness,
                actor,
                "cost.record",
                "budget.record",
                {"cost_entry": duplicate.model_dump(mode="json")},
                "cost-record-duplicate-fingerprint",
                refs=(run_ref, attempt_ref),
            )
        )

    shot7 = ExactObjectRef(scope=scope, object_type="shot", object_id="shot-007", revision_id="shot-007-v1")
    shot8 = ExactObjectRef(scope=scope, object_type="shot", object_id="shot-008", revision_id="shot-008-v1")
    candidate = ExactObjectRef(
        scope=scope,
        object_type="asset_candidate",
        object_id="candidate-shot-007",
        revision_id="candidate-shot-007-v1",
    )
    adapter = EpisodeArtifactAdapterRequest(
        mode="asset_candidate",
        successor_ref=candidate,
        protected_exact_refs=(shot8,),
        existing_typed_operation="asset_candidate.create_version",
    )
    registration = ArtifactCandidateRegistration(
        identity=identity(scope, "registration-1"),
        plan_task_ref=exact_ref("plan_task", task),
        run_ref=run_ref,
        attempt_ref=attempt_ref,
        artifact_id="artifact-shot7-v1",
        artifact_digest=DIGEST_B,
        adapter_request=adapter,
    )
    harness.execute(
        command(
            harness,
            actor,
            "artifact.register",
            "artifact.write",
            {"registration": registration.model_dump(mode="json")},
            "artifact-register-1",
            refs=(exact_ref("plan_task", task), run_ref, attempt_ref, shot7, shot8),
        )
    )
    writeback = ArtifactWriteback(
        identity=identity(scope, "writeback-1"),
        candidate_registration_ref=exact_ref("artifact_candidate_registration", registration),
        plan_task_ref=exact_ref("plan_task", task),
        run_ref=run_ref,
        attempt_ref=attempt_ref,
        artifact_id=registration.artifact_id,
        artifact_digest=registration.artifact_digest,
        adapter_request=adapter,
    )
    harness.execute(
        command(
            harness,
            actor,
            "artifact.writeback",
            "artifact.write",
            {"writeback": writeback.model_dump(mode="json")},
            "artifact-writeback-1",
            refs=(exact_ref("artifact_candidate_registration", registration), shot7, shot8),
        )
    )
    assert harness.projection.artifact_ids == {"artifact-shot7-v1"}
    assert shot8.model_dump(mode="json") in writeback.model_dump(mode="json")["adapter_request"]["protected_exact_refs"]

    duplicate = writeback.model_copy(update={"identity": identity(scope, "writeback-2")})
    with pytest.raises(StateConflictError):
        harness.execute(
            command(
                harness,
                actor,
                "artifact.writeback",
                "artifact.write",
                {"writeback": duplicate.model_dump(mode="json")},
                "artifact-writeback-duplicate",
                refs=(exact_ref("artifact_candidate_registration", registration), shot8),
            )
        )

    with pytest.raises(ValidationError, match="bare shot successor"):
        EpisodeArtifactAdapterRequest(
            mode="shot_successor",
            predecessor_ref=shot7,
            successor_ref=shot7.model_copy(update={"revision_id": "shot-007-v2"}),
            protected_exact_refs=(shot8,),
            existing_typed_operation="asset_candidate.create_version",
        )


def test_cost_and_artifact_provenance_cannot_cross_run_boundaries(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor)
    run_one = start_run(harness, actor, data["tasks"][0], suffix="one")
    run_two = start_run(harness, actor, data["tasks"][1], suffix="two")
    run_one_ref = exact_ref("production_run", run_one["run"])
    attempt_two_ref = exact_ref("run_attempt", run_two["attempt"])
    crossed_cost = CostEntry(
        identity=identity(scope, "crossed-cost"),
        run_ref=run_one_ref,
        attempt_ref=attempt_two_ref,
        kind="actual",
        amount=1,
        currency="CNY",
        charge_fingerprint=DIGEST_A,
    )
    with pytest.raises(StateConflictError, match="exact run"):
        harness.execute(
            command(
                harness,
                actor,
                "cost.record",
                "budget.record",
                {"cost_entry": crossed_cost.model_dump(mode="json")},
                "crossed-cost",
                refs=(run_one_ref, attempt_two_ref),
            )
        )

    candidate = ExactObjectRef(
        scope=scope,
        object_type="asset_candidate",
        object_id="crossed-candidate",
        revision_id="crossed-candidate-v1",
    )
    adapter = EpisodeArtifactAdapterRequest(
        mode="asset_candidate",
        successor_ref=candidate,
        existing_typed_operation="asset_candidate.create_version",
    )
    crossed_registration = ArtifactCandidateRegistration(
        identity=identity(scope, "crossed-registration"),
        plan_task_ref=exact_ref("plan_task", data["tasks"][0]),
        run_ref=run_one_ref,
        attempt_ref=attempt_two_ref,
        artifact_id="crossed-artifact",
        artifact_digest=DIGEST_B,
        adapter_request=adapter,
    )
    with pytest.raises(StateConflictError, match="crosses task, run, or attempt"):
        harness.execute(
            command(
                harness,
                actor,
                "artifact.register",
                "artifact.write",
                {"registration": crossed_registration.model_dump(mode="json")},
                "crossed-registration",
            )
        )

    valid_registration = crossed_registration.model_copy(
        update={
            "identity": identity(scope, "valid-registration"),
            "attempt_ref": exact_ref("run_attempt", run_one["attempt"]),
        }
    )
    harness.execute(
        command(
            harness,
            actor,
            "artifact.register",
            "artifact.write",
            {"registration": valid_registration.model_dump(mode="json")},
            "valid-registration",
        )
    )
    crossed_writeback = ArtifactWriteback(
        identity=identity(scope, "crossed-writeback"),
        candidate_registration_ref=exact_ref(
            "artifact_candidate_registration", valid_registration
        ),
        plan_task_ref=exact_ref("plan_task", data["tasks"][1]),
        run_ref=exact_ref("production_run", run_two["run"]),
        attempt_ref=attempt_two_ref,
        artifact_id=valid_registration.artifact_id,
        artifact_digest=valid_registration.artifact_digest,
        adapter_request=valid_registration.adapter_request,
    )
    with pytest.raises(StateConflictError, match="preserve registered artifact"):
        harness.execute(
            command(
                harness,
                actor,
                "artifact.writeback",
                "artifact.write",
                {"writeback": crossed_writeback.model_dump(mode="json")},
                "crossed-writeback",
            )
        )


def test_selective_revision_and_impact_preserve_exact_refs(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor)
    task = data["tasks"][0]
    run_data = start_run(harness, actor, task)
    run_ref = exact_ref("production_run", run_data["run"])
    attempt_ref = exact_ref("run_attempt", run_data["attempt"])
    shot7 = ExactObjectRef(scope=scope, object_type="shot", object_id="shot-007", revision_id="shot-007-v1")
    shot8 = ExactObjectRef(scope=scope, object_type="shot", object_id="shot-008", revision_id="shot-008-v1")
    candidate = ExactObjectRef(scope=scope, object_type="asset_candidate", object_id="candidate-7", revision_id="candidate-7-v1")
    adapter = EpisodeArtifactAdapterRequest(
        mode="asset_candidate",
        successor_ref=candidate,
        protected_exact_refs=(shot8,),
        existing_typed_operation="asset_candidate.create_version",
    )
    registration = ArtifactCandidateRegistration(
        identity=identity(scope, "registration-selective"),
        plan_task_ref=exact_ref("plan_task", task),
        run_ref=run_ref,
        attempt_ref=attempt_ref,
        artifact_id="artifact-selective",
        artifact_digest=DIGEST_A,
        adapter_request=adapter,
    )
    harness.execute(command(harness, actor, "artifact.register", "artifact.write", {"registration": registration.model_dump(mode="json")}, "register-selective"))
    writeback = ArtifactWriteback(
        identity=identity(scope, "writeback-selective"),
        candidate_registration_ref=exact_ref("artifact_candidate_registration", registration),
        plan_task_ref=registration.plan_task_ref,
        run_ref=run_ref,
        attempt_ref=attempt_ref,
        artifact_id=registration.artifact_id,
        artifact_digest=registration.artifact_digest,
        adapter_request=adapter,
    )
    harness.execute(command(harness, actor, "artifact.writeback", "artifact.write", {"writeback": writeback.model_dump(mode="json")}, "writeback-selective"))
    request = SelectiveRevisionRequest(
        identity=identity(scope, "revision-request-1"),
        target_exact_ref=shot7,
        protected_exact_refs=(shot8,),
        requested_changes=("lamp", "scar", "rain", "expression"),
        source_writeback_ref=exact_ref("artifact_writeback", writeback),
    )
    harness.execute(command(harness, actor, "selective_revision.request", "revision.write", {"revision_request": request.model_dump(mode="json")}, "revision-request-1"))
    invalid_assessment = ImpactAssessment(
        identity=identity(scope, "impact-invalid"),
        revision_request_ref=exact_ref("selective_revision_request", request),
        affected_exact_refs=(shot8,),
        preserved_exact_refs=(shot7,),
        assessment_digest=DIGEST_A,
    )
    with pytest.raises(StateConflictError, match="protected exact ref"):
        harness.execute(
            command(
                harness,
                actor,
                "impact.assess",
                "revision.write",
                {"impact_assessment": invalid_assessment.model_dump(mode="json")},
                "impact-invalid",
            )
        )
    assessment = ImpactAssessment(
        identity=identity(scope, "impact-1"),
        revision_request_ref=exact_ref("selective_revision_request", request),
        affected_exact_refs=(shot7,),
        preserved_exact_refs=(shot8,),
        assessment_digest=DIGEST_B,
    )
    harness.execute(command(harness, actor, "impact.assess", "revision.write", {"impact_assessment": assessment.model_dump(mode="json")}, "impact-1"))
    assert harness.projection.event_types[-2:] == ["SelectiveRevisionRequested", "ImpactAssessed"]


def test_provider_gate_defaults_closed_and_dispatch_is_always_zero(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    data = bootstrap_to_approved_plan(harness, actor)
    run_data = start_run(harness, actor, data["tasks"][0])
    decision = ProviderGateDecision(
        identity=identity(scope, "provider-gate-1"),
        run_ref=exact_ref("production_run", run_data["run"]),
        budget_envelope_ref=exact_ref("budget_envelope", data["budget"]),
        capability="image.generate",
        capability_authorized=False,
        budget_admitted=True,
        privacy_policy_satisfied=True,
        no_training_policy_satisfied=True,
        allowed=False,
        privacy_policy_ref="privacy-policy-v1",
        no_training_policy_ref="no-training-policy-v1",
    )
    harness.execute(
        command(
            harness,
            actor,
            "provider.evaluate",
            "provider.evaluate",
            {"provider_gate_decision": decision.model_dump(mode="json")},
            "provider-gate-closed",
            budget_authorization=BudgetAuthorization(
                budget_envelope_ref=decision.budget_envelope_ref,
                admitted_amount=data["budget"].max_budget,
                currency=data["budget"].estimated.currency,
                authorization_ref="provider-budget-admission-1",
            ),
            provider_authorization=ProviderAuthorization(
                capability="image.generate",
                authorized=False,
                privacy_policy_satisfied=True,
                no_training_policy_satisfied=True,
            ),
        )
    )
    assert harness.provider_dispatch_count == 0
    assert all(event.event_type != "ProviderDispatched" for event in harness.events)
    allowed = ProviderGateDecision(
        identity=identity(scope, "provider-gate-allowed"),
        run_ref=decision.run_ref,
        budget_envelope_ref=decision.budget_envelope_ref,
        capability="image.generate",
        capability_authorized=True,
        budget_admitted=True,
        privacy_policy_satisfied=True,
        no_training_policy_satisfied=True,
        allowed=True,
        authorization_ref="provider-authorization-allowed",
        privacy_policy_ref="privacy-policy-v1",
        no_training_policy_ref="no-training-policy-v1",
    )
    with pytest.raises(AuthorizationError, match="budget authorization"):
        harness.execute(
            command(
                harness,
                actor,
                "provider.evaluate",
                "provider.evaluate",
                {"provider_gate_decision": allowed.model_dump(mode="json")},
                "provider-gate-no-budget",
                provider_authorization=ProviderAuthorization(
                    capability="image.generate",
                    authorized=True,
                    privacy_policy_satisfied=True,
                    no_training_policy_satisfied=True,
                    authorization_ref="provider-authorization-allowed",
                ),
            )
        )
    assert harness.provider_dispatch_count == 0
    with pytest.raises(ValidationError):
        ProviderGateDecision(
            identity=identity(scope, "provider-gate-invalid"),
            run_ref=decision.run_ref,
            budget_envelope_ref=decision.budget_envelope_ref,
            capability="image.generate",
            capability_authorized=False,
            budget_admitted=True,
            privacy_policy_satisfied=True,
            no_training_policy_satisfied=True,
            allowed=True,
            privacy_policy_ref="privacy-policy-v1",
            no_training_policy_ref="no-training-policy-v1",
        )


def test_file_safe_restart_rebuild_and_corruption_fail_closed(
    tmp_path: Path,
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    path = tmp_path / "production-control.json"
    harness = ProductionControlHarness(
        scope, capabilities, {actor.actor_id: actor}, file_path=path
    )
    data = bootstrap_to_approved_plan(harness, actor)
    original_bytes = path.read_bytes()
    loaded = ProductionControlHarness.load(path, capabilities, {actor.actor_id: actor})
    assert loaded.projection.state_digest == harness.projection.state_digest
    assert loaded.projection.canonical_state() == rebuild_projection(scope, loaded.events).canonical_state()
    assert loaded.execute(data["approval_command"]) == data["approval_receipt"]
    assert path.read_bytes() == original_bytes
    assert loaded.provider_dispatch_count == 0

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["events"] = payload["events"][:-1]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LedgerIntegrityError):
        ProductionControlHarness.load(path, capabilities, {actor.actor_id: actor})

    path.write_bytes(original_bytes)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["outbox"][0]["event_type"] = "PlanApproved"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LedgerIntegrityError):
        ProductionControlHarness.load(path, capabilities, {actor.actor_id: actor})

    path.write_bytes(original_bytes)
    payload = json.loads(path.read_text(encoding="utf-8"))
    first_receipt = next(iter(payload["receipts"].values()))
    first_receipt["receipt"]["accepted_version"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LedgerIntegrityError):
        ProductionControlHarness.load(path, capabilities, {actor.actor_id: actor})

    path.write_bytes(original_bytes)
    payload = json.loads(path.read_text(encoding="utf-8"))
    first_receipt = next(iter(payload["receipts"].values()))
    first_receipt["receipt"]["result_refs"][0]["scope"]["org_id"] = "foreign-org"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LedgerIntegrityError):
        ProductionControlHarness.load(path, capabilities, {actor.actor_id: actor})

    path.write_bytes(original_bytes)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["events"][0]["payload"]["mission"]["identity"]["scope"]["org_id"] = "foreign-org"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LedgerIntegrityError):
        ProductionControlHarness.load(path, capabilities, {actor.actor_id: actor})


def test_concurrent_same_version_approvals_commit_exactly_one_batch(
    scope: ProjectScope,
    actor: ActorIdentity,
    capabilities: dict[str, set[str]],
) -> None:
    harness = ProductionControlHarness(scope, capabilities, {actor.actor_id: actor})
    constraint = ReferenceConstraint(identity=identity(scope, "c1"), constraint_type="story", rule="fixed")
    revision = MissionRevision(identity=identity(scope, "mr1"), objective="test", reference_constraint_refs=(exact_ref("reference_constraint", constraint),))
    mission = Mission(identity=identity(scope, "m1"), head_revision_ref=exact_ref("mission_revision", revision))
    payload = {"mission": mission.model_dump(mode="json"), "mission_revision": revision.model_dump(mode="json"), "constraints": [constraint.model_dump(mode="json")]}
    first = command(harness, actor, "mission.record", "mission.write", payload, "concurrent-1", expected_version=0)
    second = command(harness, actor, "mission.record", "mission.write", payload, "concurrent-2", expected_version=0)

    def run(item: CommandEnvelope) -> str:
        try:
            harness.execute(item)
            return "committed"
        except VersionConflictError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (first, second)))
    assert sorted(results) == ["committed", "stale"]
    assert len(harness.events) == 1
    assert len(harness.outbox) == 1
    assert len(harness._receipts) == 1


def test_frozen_episode_contract_blobs_do_not_drift() -> None:
    expected = {
        "apps/api/runtime_episode_domain_contract.py": "9f943e442f428fc5232e9f6f741fb18cdc673846",
        "apps/api/runtime_episode_domain_store.py": "7bcd1f0925fb7db436f105bd7fd92bb6a8e27370",
        "apps/api/runtime_episode_continuity_service.py": "aa6fad1437c246ab581e93e26b7f2c206380e295",
        "apps/api/runtime_episode_review_delivery_service.py": "79c4dd0ae174c102c3ed36a911c0e805ccf76ba6",
        "apps/api/runtime_episode_command_routes.py": "91b3607ca35618019383254eb480a1be045d4c99",
        "docs/architecture/AFS_EPISODE_PRODUCTION_FACT_CONTRACT.md": "fc0fa77a50c9b2cf71043ff5d9d190cfbb47cf0d",
    }
    actual = {
        path: subprocess.check_output(["git", "hash-object", path], text=True).strip()
        for path in expected
    }
    assert actual == expected
