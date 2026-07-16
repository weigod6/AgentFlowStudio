from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ValidationError

from .contract import (
    PRODUCTION_CONTROL_FILE_SCHEMA_VERSION,
    ActorIdentity,
    AgentAssignment,
    ArtifactCandidateRegistration,
    ArtifactWriteback,
    Blocker,
    BudgetEnvelope,
    CommandEnvelope,
    CommandReceipt,
    CostEntry,
    CostEstimate,
    ExactObjectRef,
    HumanDecisionRequest,
    HumanDecision,
    ImpactAssessment,
    LedgerSeal,
    Mission,
    MissionRevision,
    OutboxRecord,
    PlanApprovalDecision,
    PlanRevision,
    PlanTask,
    ProductionPlan,
    ProductionRun,
    ProjectEvent,
    ProjectScope,
    ProviderGateDecision,
    ReferenceConstraint,
    RunAttempt,
    SelectiveRevisionRequest,
    is_control_transition_allowed,
    is_execution_transition_allowed,
)


class ProductionControlError(RuntimeError):
    """Base fail-closed contract error."""


class AuthorizationError(ProductionControlError):
    pass


class VersionConflictError(ProductionControlError):
    pass


class IdempotencyConflictError(ProductionControlError):
    pass


class StateConflictError(ProductionControlError):
    pass


class LedgerIntegrityError(ProductionControlError):
    pass


class AtomicCommitError(ProductionControlError):
    pass


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def exact_ref(object_type: str, model: BaseModel) -> ExactObjectRef:
    identity = model.identity  # type: ignore[attr-defined]
    return ExactObjectRef(
        scope=identity.scope,
        object_type=object_type,
        object_id=identity.object_id,
        revision_id=identity.revision_id,
    )


@dataclass
class Projection:
    scope: ProjectScope
    version: int = 0
    records: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    plan_status: dict[str, str] = field(default_factory=dict)
    plan_heads: dict[str, ExactObjectRef] = field(default_factory=dict)
    run_execution: dict[str, str] = field(default_factory=dict)
    run_control: dict[str, str] = field(default_factory=dict)
    run_attempts: dict[str, list[ExactObjectRef]] = field(default_factory=dict)
    charge_fingerprints: set[str] = field(default_factory=set)
    artifact_ids: set[str] = field(default_factory=set)
    registered_artifact_ids: set[str] = field(default_factory=set)
    writeback_targets: set[tuple[str, str, str]] = field(default_factory=set)
    open_human_requests: dict[str, ExactObjectRef] = field(default_factory=dict)
    blockers: dict[str, ExactObjectRef] = field(default_factory=dict)
    event_types: list[str] = field(default_factory=list)

    def clone(self) -> "Projection":
        return copy.deepcopy(self)

    def canonical_state(self) -> dict[str, Any]:
        return {
            "scope": self.scope.model_dump(mode="json"),
            "version": self.version,
            "records": [
                {"key": list(key), "value": value}
                for key, value in sorted(self.records.items())
            ],
            "plan_status": dict(sorted(self.plan_status.items())),
            "plan_heads": {
                key: value.model_dump(mode="json") for key, value in sorted(self.plan_heads.items())
            },
            "run_execution": dict(sorted(self.run_execution.items())),
            "run_control": dict(sorted(self.run_control.items())),
            "run_attempts": {
                key: [ref.model_dump(mode="json") for ref in refs]
                for key, refs in sorted(self.run_attempts.items())
            },
            "charge_fingerprints": sorted(self.charge_fingerprints),
            "artifact_ids": sorted(self.artifact_ids),
            "registered_artifact_ids": sorted(self.registered_artifact_ids),
            "writeback_targets": [list(value) for value in sorted(self.writeback_targets)],
            "open_human_requests": {
                key: value.model_dump(mode="json")
                for key, value in sorted(self.open_human_requests.items())
            },
            "blockers": {
                key: value.model_dump(mode="json")
                for key, value in sorted(self.blockers.items())
            },
            "event_types": list(self.event_types),
        }

    @property
    def state_digest(self) -> str:
        return digest(self.canonical_state())

    def has(self, ref: ExactObjectRef) -> bool:
        return (ref.object_type, ref.object_id, ref.revision_id) in self.records

    def get(self, ref: ExactObjectRef) -> dict[str, Any]:
        try:
            return self.records[(ref.object_type, ref.object_id, ref.revision_id)]
        except KeyError as exc:
            raise StateConflictError(f"exact ref does not resolve: {ref.object_type}:{ref.object_id}") from exc

    def register(self, object_type: str, model: BaseModel) -> ExactObjectRef:
        ref = exact_ref(object_type, model)
        if ref.scope != self.scope:
            raise AuthorizationError("record scope does not match project ledger scope")
        key = (ref.object_type, ref.object_id, ref.revision_id)
        if key in self.records:
            raise StateConflictError("duplicate stable object revision")
        identity = model.identity  # type: ignore[attr-defined]
        same_history = [
            value
            for (kind, stable_id, _), value in self.records.items()
            if kind == object_type and stable_id == identity.object_id
        ]
        if identity.revision == 1:
            if identity.parent_revision_id is not None or same_history:
                raise StateConflictError("first revision must be unique and parentless")
        else:
            parents = [
                value
                for value in same_history
                if value["identity"]["revision_id"] == identity.parent_revision_id
                and value["identity"]["revision"] == identity.revision - 1
            ]
            if len(parents) != 1:
                raise StateConflictError("revision must name the exact immediate parent")
        self.records[key] = model.model_dump(mode="json")
        return ref


EVENT_MODELS: dict[str, tuple[tuple[str, str, type[BaseModel]], ...]] = {
    "MissionRecorded": (
        ("mission", "mission", Mission),
        ("mission_revision", "mission_revision", MissionRevision),
    ),
    "PlanProposed": (
        ("production_plan", "plan", ProductionPlan),
        ("plan_revision", "plan_revision", PlanRevision),
        ("budget_envelope", "budget_envelope", BudgetEnvelope),
    ),
    "PlanRevised": (
        ("production_plan", "plan", ProductionPlan),
        ("plan_revision", "plan_revision", PlanRevision),
    ),
    "PlanApproved": (("plan_approval_decision", "decision", PlanApprovalDecision),),
    "TaskQueued": (("plan_task", "task", PlanTask),),
    "RunStarted": (
        ("agent_assignment", "assignment", AgentAssignment),
        ("production_run", "run", ProductionRun),
        ("run_attempt", "attempt", RunAttempt),
    ),
    "CostRecorded": (("cost_entry", "cost_entry", CostEntry),),
    "ProviderGateEvaluated": (
        ("provider_gate_decision", "provider_gate_decision", ProviderGateDecision),
    ),
    "ArtifactCandidateRegistered": (
        ("artifact_candidate_registration", "registration", ArtifactCandidateRegistration),
    ),
    "ArtifactWrittenBack": (("artifact_writeback", "writeback", ArtifactWriteback),),
    "HumanDecisionRecorded": (("human_decision", "human_decision", HumanDecision),),
    "SelectiveRevisionRequested": (
        ("selective_revision_request", "revision_request", SelectiveRevisionRequest),
    ),
    "ImpactAssessed": (("impact_assessment", "impact_assessment", ImpactAssessment),),
}


def _validate_scope(refs: Iterable[ExactObjectRef], scope: ProjectScope) -> None:
    if any(ref.scope != scope for ref in refs):
        raise AuthorizationError("foreign org or project exact ref rejected")


def _validate_run_combination(execution: str, control: str) -> None:
    if execution == "completed" and control != "active":
        raise StateConflictError("completed run cannot remain in a control transition")
    if execution == "cancelled" and control not in {"active", "cancel-requested"}:
        raise StateConflictError("cancelled run has invalid control state")
    if control == "resume-requested" and execution in {"blocked", "completed", "cancelled"}:
        raise StateConflictError("resume request is invalid for blocked or terminal execution")


def apply_event(projection: Projection, event: ProjectEvent) -> None:
    if event.scope != projection.scope:
        raise LedgerIntegrityError("foreign project event rejected")
    payload = event.payload
    try:
        if event.event_type == "MissionRecorded":
            constraints = [ReferenceConstraint.model_validate(value) for value in payload["constraints"]]
            mission_revision = MissionRevision.model_validate(payload["mission_revision"])
            mission = Mission.model_validate(payload["mission"])
            for constraint in constraints:
                projection.register("reference_constraint", constraint)
            revision_ref = projection.register("mission_revision", mission_revision)
            constraint_refs = tuple(exact_ref("reference_constraint", item) for item in constraints)
            if set(mission_revision.reference_constraint_refs) != set(constraint_refs):
                raise StateConflictError("mission revision must name its exact constraints")
            if mission.head_revision_ref != revision_ref:
                raise StateConflictError("mission head must be its exact immutable revision")
            projection.register("mission", mission)
        elif event.event_type == "PlanProposed":
            estimates = [CostEstimate.model_validate(value) for value in payload["cost_estimates"]]
            budget = BudgetEnvelope.model_validate(payload["budget_envelope"])
            revision = PlanRevision.model_validate(payload["plan_revision"])
            plan = ProductionPlan.model_validate(payload["plan"])
            if not projection.has(plan.mission_revision_ref):
                raise StateConflictError("plan must reference an exact mission revision")
            budget_ref = projection.register("budget_envelope", budget)
            estimate_refs = tuple(projection.register("cost_estimate", item) for item in estimates)
            if revision.budget_envelope_ref != budget_ref or set(revision.cost_estimate_refs) != set(estimate_refs):
                raise StateConflictError("plan revision budget and estimates must be exact")
            revision_ref = projection.register("plan_revision", revision)
            if plan.head_revision_ref != revision_ref:
                raise StateConflictError("plan head must be its exact immutable revision")
            projection.register("production_plan", plan)
            projection.plan_status[plan.identity.object_id] = plan.status
            projection.plan_heads[plan.identity.object_id] = revision_ref
        elif event.event_type == "PlanRevised":
            revision = PlanRevision.model_validate(payload["plan_revision"])
            plan = ProductionPlan.model_validate(payload["plan"])
            if projection.plan_status.get(plan.identity.object_id) != "proposed":
                raise StateConflictError("only proposed plan may be revised")
            if not projection.has(plan.mission_revision_ref):
                raise StateConflictError("revised plan must retain exact mission revision")
            if not projection.has(revision.budget_envelope_ref) or not all(
                projection.has(ref) for ref in revision.cost_estimate_refs
            ):
                raise StateConflictError("revised plan must retain exact budget evidence")
            if revision.plan_ref != exact_ref("production_plan", plan):
                raise StateConflictError("plan revision must bind the exact plan revision")
            revision_ref = projection.register("plan_revision", revision)
            if plan.head_revision_ref != revision_ref:
                raise StateConflictError("revised plan head must be exact")
            projection.register("production_plan", plan)
            projection.plan_heads[plan.identity.object_id] = revision_ref
        elif event.event_type == "PlanApproved":
            decision = PlanApprovalDecision.model_validate(payload["decision"])
            if not projection.has(decision.plan_revision_ref):
                raise StateConflictError("approval must reference the exact plan revision")
            projection.register("plan_approval_decision", decision)
            plan_revision = projection.get(decision.plan_revision_ref)
            plan_ref = ExactObjectRef.model_validate(plan_revision["plan_ref"])
            if projection.plan_status.get(plan_ref.object_id) != "proposed":
                raise StateConflictError("only proposed plan may be approved")
            if projection.plan_heads.get(plan_ref.object_id) != decision.plan_revision_ref:
                raise StateConflictError("approval must target the exact current plan revision")
            projection.plan_status[plan_ref.object_id] = decision.decision
        elif event.event_type == "TaskQueued":
            task = PlanTask.model_validate(payload["task"])
            if not projection.has(task.plan_revision_ref):
                raise StateConflictError("task must reference exact approved plan revision")
            revision = projection.get(task.plan_revision_ref)
            plan_ref = ExactObjectRef.model_validate(revision["plan_ref"])
            if projection.plan_status.get(plan_ref.object_id) != "approved":
                raise StateConflictError("tasks may only queue inside the atomic approval batch")
            projection.register("plan_task", task)
        elif event.event_type == "RunStarted":
            assignment = AgentAssignment.model_validate(payload["assignment"])
            run = ProductionRun.model_validate(payload["run"])
            attempt = RunAttempt.model_validate(payload["attempt"])
            if not projection.has(run.task_ref):
                raise StateConflictError("run must reference queued task")
            assignment_ref = projection.register("agent_assignment", assignment)
            if run.assignment_ref != assignment_ref:
                raise StateConflictError("run assignment ref mismatch")
            run_ref = projection.register("production_run", run)
            if attempt.run_ref != run_ref:
                raise StateConflictError("attempt must belong to exact run")
            attempt_ref = projection.register("run_attempt", attempt)
            if run.latest_attempt_ref != attempt_ref or run.execution_state != "running":
                raise StateConflictError("started run requires exact first running attempt")
            projection.run_execution[run.identity.object_id] = run.execution_state
            projection.run_control[run.identity.object_id] = run.control_state
            projection.run_attempts[run.identity.object_id] = [attempt_ref]
            _validate_run_combination(run.execution_state, run.control_state)
        elif event.event_type in {
            "RunProgressed",
            "RunWaitingHuman",
            "RunRetried",
            "RunBlocked",
            "RunCompleted",
            "RunCancelled",
        }:
            run_ref = ExactObjectRef.model_validate(payload["run_ref"])
            _validate_scope((run_ref,), projection.scope)
            projection.get(run_ref)
            current = projection.run_execution[run_ref.object_id]
            target = payload["target_state"]
            is_progress = event.event_type == "RunProgressed" and current == target == "running"
            if not is_progress and not is_execution_transition_allowed(current, target):
                raise StateConflictError(f"illegal execution transition: {current} -> {target}")
            if target == "waiting-human":
                request = HumanDecisionRequest.model_validate(payload["human_decision_request"])
                if request.run_ref != run_ref:
                    raise StateConflictError("waiting-human request must bind exact run")
                request_ref = projection.register("human_decision_request", request)
                projection.open_human_requests[run_ref.object_id] = request_ref
            elif target == "blocked":
                blocker = Blocker.model_validate(payload["blocker"])
                if blocker.run_ref != run_ref:
                    raise StateConflictError("blocker must bind exact run")
                projection.blockers[run_ref.object_id] = projection.register("blocker", blocker)
            elif current == "blocked" and target in {"queued", "running"}:
                evidence = tuple(ExactObjectRef.model_validate(value) for value in payload["clearance_evidence_refs"])
                _validate_scope(evidence, projection.scope)
                if not evidence:
                    raise StateConflictError("blocked run requires clearance evidence")
                blocker_ref = projection.blockers.get(run_ref.object_id)
                if blocker_ref is None:
                    raise StateConflictError("blocked run has no exact blocker")
                blocker = projection.get(blocker_ref)
                required_evidence = {
                    ExactObjectRef.model_validate(value)
                    for value in blocker["clearance_evidence_refs"]
                }
                if set(evidence) != required_evidence:
                    raise StateConflictError("clearance evidence must exactly satisfy the active blocker")
                if any(
                    ref.object_type in CONTROL_OBJECT_TYPES and not projection.has(ref)
                    for ref in evidence
                ):
                    raise StateConflictError("control-domain clearance evidence must resolve exactly")
                projection.blockers.pop(run_ref.object_id, None)
            if current == "waiting-human" and target == "running" and run_ref.object_id in projection.open_human_requests:
                raise StateConflictError("waiting-human run requires an exact recorded decision")
            if target == "retrying":
                attempt = RunAttempt.model_validate(payload["attempt"])
                previous = projection.run_attempts[run_ref.object_id][-1]
                if attempt.run_ref != run_ref or attempt.prior_attempt_ref != previous:
                    raise StateConflictError("retry attempt must preserve run and exact prior attempt")
                if attempt.attempt_number != len(projection.run_attempts[run_ref.object_id]) + 1:
                    raise StateConflictError("retry attempt number must be contiguous")
                projection.run_attempts[run_ref.object_id].append(
                    projection.register("run_attempt", attempt)
                )
            projection.run_execution[run_ref.object_id] = target
            _validate_run_combination(target, projection.run_control[run_ref.object_id])
        elif event.event_type == "RunControlChanged":
            run_ref = ExactObjectRef.model_validate(payload["run_ref"])
            projection.get(run_ref)
            current = projection.run_control[run_ref.object_id]
            target = payload["target_state"]
            if not is_control_transition_allowed(current, target):
                raise StateConflictError(f"illegal control transition: {current} -> {target}")
            projection.run_control[run_ref.object_id] = target
            _validate_run_combination(projection.run_execution[run_ref.object_id], target)
        elif event.event_type == "CostRecorded":
            entry = CostEntry.model_validate(payload["cost_entry"])
            if entry.charge_fingerprint in projection.charge_fingerprints:
                raise StateConflictError("duplicate provider charge identity")
            if not projection.has(entry.run_ref) or not projection.has(entry.attempt_ref):
                raise StateConflictError("cost must bind exact run and attempt")
            attempt = projection.get(entry.attempt_ref)
            if ExactObjectRef.model_validate(attempt["run_ref"]) != entry.run_ref:
                raise StateConflictError("cost attempt must belong to the exact run")
            projection.register("cost_entry", entry)
            projection.charge_fingerprints.add(entry.charge_fingerprint)
        elif event.event_type == "ProviderGateEvaluated":
            decision = ProviderGateDecision.model_validate(payload["provider_gate_decision"])
            if not projection.has(decision.run_ref):
                raise StateConflictError("provider gate must bind exact run")
            if not projection.has(decision.budget_envelope_ref):
                raise StateConflictError("provider gate must bind exact budget envelope")
            projection.register("provider_gate_decision", decision)
        elif event.event_type == "HumanDecisionRecorded":
            decision = HumanDecision.model_validate(payload["human_decision"])
            if not projection.has(decision.request_ref):
                raise StateConflictError("human decision must bind exact request")
            request = projection.get(decision.request_ref)
            if decision.selected_option not in request["options"] or not decision.impact_acknowledged:
                raise StateConflictError("human decision must select an offered option and acknowledge impact")
            run_ref = ExactObjectRef.model_validate(request["run_ref"])
            if projection.open_human_requests.get(run_ref.object_id) != decision.request_ref:
                raise StateConflictError("human decision request is not the exact open request")
            projection.register("human_decision", decision)
            projection.open_human_requests.pop(run_ref.object_id)
        elif event.event_type == "ArtifactCandidateRegistered":
            registration = ArtifactCandidateRegistration.model_validate(payload["registration"])
            refs = (registration.plan_task_ref, registration.run_ref, registration.attempt_ref)
            if not all(projection.has(ref) for ref in refs):
                raise StateConflictError("candidate provenance must resolve exactly")
            run = projection.get(registration.run_ref)
            attempt = projection.get(registration.attempt_ref)
            if (
                ExactObjectRef.model_validate(run["task_ref"]) != registration.plan_task_ref
                or ExactObjectRef.model_validate(attempt["run_ref"]) != registration.run_ref
            ):
                raise StateConflictError("candidate provenance crosses task, run, or attempt boundary")
            if registration.artifact_id in projection.registered_artifact_ids:
                raise StateConflictError("duplicate artifact candidate identity")
            projection.register("artifact_candidate_registration", registration)
            projection.registered_artifact_ids.add(registration.artifact_id)
        elif event.event_type == "ArtifactWrittenBack":
            writeback = ArtifactWriteback.model_validate(payload["writeback"])
            refs = (writeback.plan_task_ref, writeback.run_ref, writeback.attempt_ref)
            if not all(projection.has(ref) for ref in refs):
                raise StateConflictError("writeback provenance must resolve exactly")
            if not projection.has(writeback.candidate_registration_ref):
                raise StateConflictError("writeback must bind exact candidate registration")
            registration = projection.get(writeback.candidate_registration_ref)
            if (
                ExactObjectRef.model_validate(registration["plan_task_ref"]) != writeback.plan_task_ref
                or ExactObjectRef.model_validate(registration["run_ref"]) != writeback.run_ref
                or ExactObjectRef.model_validate(registration["attempt_ref"]) != writeback.attempt_ref
                or
                registration["artifact_id"] != writeback.artifact_id
                or registration["artifact_digest"] != writeback.artifact_digest
                or registration["adapter_request"] != writeback.adapter_request.model_dump(mode="json")
            ):
                raise StateConflictError("writeback must preserve registered artifact and adapter intent")
            target = writeback.adapter_request.successor_ref
            target_key = (target.object_type, target.object_id, target.revision_id)
            if writeback.artifact_id in projection.artifact_ids or target_key in projection.writeback_targets:
                raise StateConflictError("duplicate artifact writeback or exact successor")
            projection.register("artifact_writeback", writeback)
            projection.artifact_ids.add(writeback.artifact_id)
            projection.writeback_targets.add(target_key)
        elif event.event_type == "SelectiveRevisionRequested":
            request = SelectiveRevisionRequest.model_validate(payload["revision_request"])
            if not projection.has(request.source_writeback_ref):
                raise StateConflictError("revision request must bind exact writeback provenance")
            if request.target_exact_ref in request.protected_exact_refs:
                raise StateConflictError("revision target cannot be protected")
            projection.register("selective_revision_request", request)
        elif event.event_type == "ImpactAssessed":
            assessment = ImpactAssessment.model_validate(payload["impact_assessment"])
            if not projection.has(assessment.revision_request_ref):
                raise StateConflictError("impact assessment must bind exact revision request")
            request = projection.get(assessment.revision_request_ref)
            protected_refs = {
                ExactObjectRef.model_validate(value) for value in request["protected_exact_refs"]
            }
            if not set(assessment.preserved_exact_refs).isdisjoint(assessment.affected_exact_refs):
                raise StateConflictError("impact cannot both affect and preserve an exact ref")
            if protected_refs & set(assessment.affected_exact_refs):
                raise StateConflictError("impact cannot affect a protected exact ref")
            if not protected_refs.issubset(assessment.preserved_exact_refs):
                raise StateConflictError("impact must preserve every protected exact ref")
            projection.register("impact_assessment", assessment)
        else:
            raise LedgerIntegrityError(f"unsupported event type: {event.event_type}")
    except (KeyError, ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, ProductionControlError):
            raise
        raise LedgerIntegrityError(f"invalid {event.event_type} payload") from exc
    projection.event_types.append(event.event_type)


COMMAND_CAPABILITIES = {
    "mission.record": "mission.write",
    "plan.propose": "plan.write",
    "plan.revise": "plan.write",
    "plan.approve": "plan.approve",
    "run.start": "run.execute",
    "run.transition": "run.execute",
    "run.control": "run.control",
    "cost.record": "budget.record",
    "provider.evaluate": "provider.evaluate",
    "decision.record": "decision.write",
    "artifact.register": "artifact.write",
    "artifact.writeback": "artifact.write",
    "selective_revision.request": "revision.write",
    "impact.assess": "revision.write",
}

CONTROL_OBJECT_TYPES = {
    "mission",
    "mission_revision",
    "reference_constraint",
    "production_plan",
    "plan_revision",
    "plan_task",
    "plan_approval_decision",
    "agent_assignment",
    "production_run",
    "run_attempt",
    "budget_envelope",
    "cost_estimate",
    "cost_entry",
    "provider_gate_decision",
    "blocker",
    "human_decision_request",
    "human_decision",
    "artifact_candidate_registration",
    "artifact_writeback",
    "selective_revision_request",
    "impact_assessment",
}


class ProductionControlHarness:
    """Deterministic, provider-free contract harness; not a production database."""

    def __init__(
        self,
        scope: ProjectScope,
        actor_capabilities: dict[str, set[str]],
        actor_identities: dict[str, ActorIdentity],
        *,
        file_path: Path | None = None,
    ) -> None:
        self.scope = scope
        self.actor_capabilities = {key: set(value) for key, value in actor_capabilities.items()}
        self.actor_identities = dict(actor_identities)
        if set(self.actor_capabilities) != set(self.actor_identities):
            raise AuthorizationError("actor identities and capability grants must have identical keys")
        if any(key != identity.actor_id for key, identity in self.actor_identities.items()):
            raise AuthorizationError("actor grant key must equal exact actor identity")
        self.file_path = file_path
        self.events: tuple[ProjectEvent, ...] = ()
        self.outbox: tuple[OutboxRecord, ...] = ()
        self._receipts: dict[str, tuple[str, CommandReceipt]] = {}
        self.projection = Projection(scope=scope)
        self.provider_dispatch_count = 0
        self._lock = threading.RLock()

    def execute(self, command: CommandEnvelope, *, fail_before_commit: bool = False) -> CommandReceipt:
        with self._lock:
            self._authorize(command)
            if digest(command.payload) != command.payload_digest:
                raise IdempotencyConflictError("payload digest mismatch")
            semantic_digest = self._semantic_command_digest(command)
            prior = self._receipts.get(command.idempotency_key)
            if prior is not None:
                prior_digest, receipt = prior
                if prior_digest != semantic_digest:
                    raise IdempotencyConflictError("same idempotency key used with different payload")
                return receipt
            if command.expected_version != self.projection.version:
                raise VersionConflictError(
                    f"stale expected_version {command.expected_version}; current {self.projection.version}"
                )
            self._guard_known_target_states(command)
            specs, result_refs = self._event_specs(command)
            _validate_scope(_embedded_exact_refs(specs), self.scope)
            new_version = self.projection.version + 1
            receipt_id = digest(
                {
                    "scope": self.scope.model_dump(mode="json"),
                    "idempotency_key": command.idempotency_key,
                    "semantic_digest": semantic_digest,
                    "version": new_version,
                }
            )
            staged_events = self._stage_events(command, receipt_id, new_version, specs)
            receipt = CommandReceipt(
                receipt_id=receipt_id,
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                command_digest=semantic_digest,
                accepted_version=new_version,
                event_ids=tuple(event.event_id for event in staged_events),
                result_refs=tuple(result_refs),
            )
            staged_outbox = tuple(self._outbox_for(event) for event in staged_events)
            candidate_events = self.events + staged_events
            candidate_outbox = self.outbox + staged_outbox
            candidate_receipts = dict(self._receipts)
            candidate_receipts[command.idempotency_key] = (semantic_digest, receipt)
            candidate_projection = rebuild_projection(self.scope, candidate_events)
            if candidate_projection.version != new_version:
                raise AtomicCommitError("staged projection version mismatch")
            if fail_before_commit:
                raise AtomicCommitError("injected failure before atomic commit")
            if self.file_path is not None:
                self._write_commit_envelope(
                    candidate_events,
                    candidate_outbox,
                    candidate_receipts,
                    candidate_projection,
                )
            self.events = candidate_events
            self.outbox = candidate_outbox
            self._receipts = candidate_receipts
            self.projection = candidate_projection
            return receipt

    def _authorize(self, command: CommandEnvelope) -> None:
        if command.scope != self.scope:
            raise AuthorizationError("foreign organization or project rejected")
        allowed = self.actor_capabilities.get(command.actor.actor_id)
        expected_actor = self.actor_identities.get(command.actor.actor_id)
        required = COMMAND_CAPABILITIES.get(command.command_type)
        if (
            allowed is None
            or expected_actor != command.actor
            or required is None
            or command.capability != required
            or required not in allowed
        ):
            raise AuthorizationError("actor or capability is not authorized")
        _validate_scope(command.exact_object_refs, self.scope)
        if command.budget_authorization is not None:
            _validate_scope((command.budget_authorization.budget_envelope_ref,), self.scope)
        if command.command_type == "plan.approve" and command.budget_authorization is None:
            raise AuthorizationError("plan approval requires exact budget authorization")
        if command.command_type == "provider.evaluate" and (
            command.provider_authorization is None or command.budget_authorization is None
        ):
            raise AuthorizationError("provider evaluation requires provider and budget authorization")
        if command.causation_id != command.command_id and command.causation_id not in {
            event.event_id for event in self.events
        }:
            raise AuthorizationError("causation must resolve to this command or a prior project event")

    def _guard_known_target_states(self, command: CommandEnvelope) -> None:
        for ref in command.exact_object_refs:
            if not self.projection.has(ref):
                continue
            record = self.projection.get(ref)
            state = record.get("identity", {}).get("state")
            if state in {"retired", "cancelled", "locked"}:
                raise StateConflictError(f"cannot write {state} object")
        if command.command_type.startswith("run.") and command.exact_object_refs:
            run_ref = command.exact_object_refs[0]
            execution = self.projection.run_execution.get(run_ref.object_id)
            if execution in {"completed", "cancelled"}:
                raise StateConflictError("terminal run rejects new writes")

    @staticmethod
    def _semantic_command_digest(command: CommandEnvelope) -> str:
        value = command.model_dump(mode="json")
        for field_name in ("command_id", "idempotency_key", "correlation_id", "causation_id"):
            value.pop(field_name, None)
        return digest(value)

    def _event_specs(
        self,
        command: CommandEnvelope,
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[ExactObjectRef]]:
        payload = command.payload
        if command.command_type == "mission.record":
            mission = Mission.model_validate(payload["mission"])
            revision = MissionRevision.model_validate(payload["mission_revision"])
            constraints = [ReferenceConstraint.model_validate(value) for value in payload["constraints"]]
            return [(
                "MissionRecorded",
                {
                    "mission": mission.model_dump(mode="json"),
                    "mission_revision": revision.model_dump(mode="json"),
                    "constraints": [item.model_dump(mode="json") for item in constraints],
                },
            )], [exact_ref("mission", mission), exact_ref("mission_revision", revision)]
        if command.command_type == "plan.propose":
            plan = ProductionPlan.model_validate(payload["plan"])
            revision = PlanRevision.model_validate(payload["plan_revision"])
            budget = BudgetEnvelope.model_validate(payload["budget_envelope"])
            estimates = [CostEstimate.model_validate(value) for value in payload["cost_estimates"]]
            return [(
                "PlanProposed",
                {
                    "plan": plan.model_dump(mode="json"),
                    "plan_revision": revision.model_dump(mode="json"),
                    "budget_envelope": budget.model_dump(mode="json"),
                    "cost_estimates": [item.model_dump(mode="json") for item in estimates],
                },
            )], [exact_ref("production_plan", plan), exact_ref("plan_revision", revision)]
        if command.command_type == "plan.revise":
            plan = ProductionPlan.model_validate(payload["plan"])
            revision = PlanRevision.model_validate(payload["plan_revision"])
            return [(
                "PlanRevised",
                {
                    "plan": plan.model_dump(mode="json"),
                    "plan_revision": revision.model_dump(mode="json"),
                },
            )], [exact_ref("production_plan", plan), exact_ref("plan_revision", revision)]
        if command.command_type == "plan.approve":
            decision = PlanApprovalDecision.model_validate(payload["decision"])
            tasks = [PlanTask.model_validate(value) for value in payload["tasks"]]
            run_bundles = list(payload.get("runs") or [])
            if len(tasks) < 3:
                raise StateConflictError("plan.approve requires at least three bounded tasks")
            task_refs = tuple(exact_ref("plan_task", task) for task in tasks)
            if len(task_refs) != len(set(task_refs)) or set(task_refs) != set(decision.approved_task_refs):
                raise StateConflictError("approval decision and unique task batch must match exactly")
            plan_revision = PlanRevision.model_validate(self.projection.get(decision.plan_revision_ref))
            task_ref_by_id = {task.identity.object_id: exact_ref("plan_task", task) for task in tasks}
            spec_by_id = {spec.task_id: spec for spec in plan_revision.task_specs}
            if set(task_ref_by_id) != set(spec_by_id):
                raise StateConflictError("approval task stable identities must match the plan revision")
            for task in tasks:
                spec = spec_by_id[task.identity.object_id]
                expected_dependencies = tuple(
                    task_ref_by_id[dependency_id] for dependency_id in spec.dependency_task_ids
                )
                if (
                    task.plan_revision_ref != decision.plan_revision_ref
                    or task.boundary != spec.boundary
                    or task.capability != spec.capability
                    or task.dependency_refs != expected_dependencies
                ):
                    raise StateConflictError("approval task content must match the immutable plan specification")
            budget_auth = command.budget_authorization
            assert budget_auth is not None
            if budget_auth.budget_envelope_ref != decision.budget_envelope_ref:
                raise AuthorizationError("approval budget authorization must bind exact envelope")
            budget = BudgetEnvelope.model_validate(self.projection.get(decision.budget_envelope_ref))
            if budget_auth.currency != budget.estimated.currency or budget_auth.admitted_amount > budget.max_budget:
                raise AuthorizationError("approval exceeds exact budget admission")
            specs = [("PlanApproved", {"decision": decision.model_dump(mode="json")})]
            specs.extend(("TaskQueued", {"task": task.model_dump(mode="json")}) for task in tasks)
            result_refs = [exact_ref("plan_approval_decision", decision), *task_refs]
            if run_bundles:
                if len(run_bundles) != len(tasks):
                    raise StateConflictError("approval run batch must cover every approved task")
                covered_task_refs: set[ExactObjectRef] = set()
                for item in run_bundles:
                    assignment = AgentAssignment.model_validate(item["assignment"])
                    run = ProductionRun.model_validate(item["run"])
                    attempt = RunAttempt.model_validate(item["attempt"])
                    assignment_ref = exact_ref("agent_assignment", assignment)
                    run_ref = exact_ref("production_run", run)
                    attempt_ref = exact_ref("run_attempt", attempt)
                    if (
                        assignment.task_ref not in task_refs
                        or run.task_ref != assignment.task_ref
                        or run.assignment_ref != assignment_ref
                        or attempt.run_ref != run_ref
                        or attempt.attempt_number != 1
                        or attempt.prior_attempt_ref is not None
                        or run.latest_attempt_ref != attempt_ref
                        or run.execution_state != "running"
                    ):
                        raise StateConflictError("approval run batch must start exact first attempts for approved tasks")
                    if run.task_ref in covered_task_refs:
                        raise StateConflictError("approval run batch cannot duplicate a task")
                    covered_task_refs.add(run.task_ref)
                    specs.append(
                        (
                            "RunStarted",
                            {
                                "assignment": assignment.model_dump(mode="json"),
                                "run": run.model_dump(mode="json"),
                                "attempt": attempt.model_dump(mode="json"),
                            },
                        )
                    )
                    result_refs.extend([run_ref, attempt_ref])
                if covered_task_refs != set(task_refs):
                    raise StateConflictError("approval run batch must match approved task refs")
            return specs, result_refs
        if command.command_type == "run.start":
            assignment = AgentAssignment.model_validate(payload["assignment"])
            run = ProductionRun.model_validate(payload["run"])
            attempt = RunAttempt.model_validate(payload["attempt"])
            return [(
                "RunStarted",
                {
                    "assignment": assignment.model_dump(mode="json"),
                    "run": run.model_dump(mode="json"),
                    "attempt": attempt.model_dump(mode="json"),
                },
            )], [exact_ref("production_run", run), exact_ref("run_attempt", attempt)]
        if command.command_type == "run.transition":
            run_ref = ExactObjectRef.model_validate(payload["run_ref"])
            target = payload["target_state"]
            names = {
                "running": "RunProgressed",
                "waiting-human": "RunWaitingHuman",
                "retrying": "RunRetried",
                "blocked": "RunBlocked",
                "completed": "RunCompleted",
                "cancelled": "RunCancelled",
                "queued": "RunProgressed",
            }
            if target not in names:
                raise StateConflictError("unknown execution state")
            return [(names[target], dict(payload))], [run_ref]
        if command.command_type == "run.control":
            run_ref = ExactObjectRef.model_validate(payload["run_ref"])
            return [("RunControlChanged", dict(payload))], [run_ref]
        if command.command_type == "cost.record":
            entry = CostEntry.model_validate(payload["cost_entry"])
            return [("CostRecorded", {"cost_entry": entry.model_dump(mode="json")})], [
                exact_ref("cost_entry", entry)
            ]
        if command.command_type == "provider.evaluate":
            decision = ProviderGateDecision.model_validate(payload["provider_gate_decision"])
            auth = command.provider_authorization
            budget_auth = command.budget_authorization
            assert auth is not None
            assert budget_auth is not None
            if (
                auth.capability != decision.capability
                or auth.authorized != decision.capability_authorized
                or auth.privacy_policy_satisfied != decision.privacy_policy_satisfied
                or auth.no_training_policy_satisfied != decision.no_training_policy_satisfied
                or auth.authorization_ref != decision.authorization_ref
            ):
                raise AuthorizationError("provider decision must match exact command authorization")
            budget = BudgetEnvelope.model_validate(self.projection.get(decision.budget_envelope_ref))
            if (
                budget_auth.budget_envelope_ref != decision.budget_envelope_ref
                or budget_auth.currency != budget.estimated.currency
                or budget_auth.admitted_amount > budget.max_budget
                or (decision.budget_admitted and budget_auth.admitted_amount <= 0)
            ):
                raise AuthorizationError("provider decision must match exact budget admission")
            return [(
                "ProviderGateEvaluated",
                {"provider_gate_decision": decision.model_dump(mode="json")},
            )], [exact_ref("provider_gate_decision", decision)]
        if command.command_type == "decision.record":
            decision = HumanDecision.model_validate(payload["human_decision"])
            return [("HumanDecisionRecorded", {"human_decision": decision.model_dump(mode="json")})], [
                exact_ref("human_decision", decision)
            ]
        if command.command_type == "artifact.register":
            registration = ArtifactCandidateRegistration.model_validate(payload["registration"])
            return [(
                "ArtifactCandidateRegistered",
                {"registration": registration.model_dump(mode="json")},
            )], [exact_ref("artifact_candidate_registration", registration)]
        if command.command_type == "artifact.writeback":
            writeback = ArtifactWriteback.model_validate(payload["writeback"])
            return [("ArtifactWrittenBack", {"writeback": writeback.model_dump(mode="json")})], [
                exact_ref("artifact_writeback", writeback),
                writeback.adapter_request.successor_ref,
            ]
        if command.command_type == "selective_revision.request":
            request = SelectiveRevisionRequest.model_validate(payload["revision_request"])
            return [(
                "SelectiveRevisionRequested",
                {"revision_request": request.model_dump(mode="json")},
            )], [exact_ref("selective_revision_request", request)]
        if command.command_type == "impact.assess":
            assessment = ImpactAssessment.model_validate(payload["impact_assessment"])
            return [(
                "ImpactAssessed",
                {"impact_assessment": assessment.model_dump(mode="json")},
            )], [exact_ref("impact_assessment", assessment)]
        raise ProductionControlError(f"unsupported command type: {command.command_type}")

    def _stage_events(
        self,
        command: CommandEnvelope,
        receipt_id: str,
        project_version: int,
        specs: list[tuple[str, dict[str, Any]]],
    ) -> tuple[ProjectEvent, ...]:
        staged: list[ProjectEvent] = []
        previous = self.events[-1].integrity_digest if self.events else None
        sequence = len(self.events)
        batch_count = len(specs)
        for batch_index, (event_type, payload) in enumerate(specs, start=1):
            sequence += 1
            base = {
                "schema_version": "afs.production-control.event.v0.1",
                "scope": self.scope.model_dump(mode="json"),
                "project_sequence": sequence,
                "project_version": project_version,
                "batch_index": batch_index,
                "batch_count": batch_count,
                "event_type": event_type,
                "correlation_id": command.correlation_id,
                "causation_id": command.causation_id,
                "command_receipt_id": receipt_id,
                "previous_event_digest": previous,
                "payload": payload,
            }
            event_id = digest({"event": base, "ordinal": sequence})
            integrity = digest({**base, "event_id": event_id})
            event = ProjectEvent(
                **base,
                event_id=event_id,
                integrity_digest=integrity,
            )
            staged.append(event)
            previous = integrity
        return tuple(staged)

    @staticmethod
    def _outbox_for(event: ProjectEvent) -> OutboxRecord:
        return OutboxRecord(
            outbox_id=digest({"event_id": event.event_id, "kind": "production-control-outbox"}),
            scope=event.scope,
            event_id=event.event_id,
            event_type=event.event_type,
            payload_digest=digest(event.payload),
        )

    @staticmethod
    def _seal(events: tuple[ProjectEvent, ...]) -> LedgerSeal:
        event_digests = [event.integrity_digest for event in events]
        return LedgerSeal(
            event_count=len(events),
            last_event_digest=event_digests[-1] if event_digests else None,
            ledger_digest=digest(event_digests),
        )

    def _commit_payload(
        self,
        events: tuple[ProjectEvent, ...],
        outbox: tuple[OutboxRecord, ...],
        receipts: dict[str, tuple[str, CommandReceipt]],
        projection: Projection,
    ) -> dict[str, Any]:
        return {
            "schema_version": PRODUCTION_CONTROL_FILE_SCHEMA_VERSION,
            "scope": self.scope.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in events],
            "outbox": [item.model_dump(mode="json") for item in outbox],
            "receipts": {
                key: {
                    "semantic_digest": semantic_digest,
                    "receipt": receipt.model_dump(mode="json"),
                }
                for key, (semantic_digest, receipt) in sorted(receipts.items())
            },
            "ledger_seal": self._seal(events).model_dump(mode="json"),
            "projection_digest": projection.state_digest,
            "provider_dispatch_count": 0,
        }

    def _write_commit_envelope(
        self,
        events: tuple[ProjectEvent, ...],
        outbox: tuple[OutboxRecord, ...],
        receipts: dict[str, tuple[str, CommandReceipt]],
        projection: Projection,
    ) -> None:
        assert self.file_path is not None
        payload = self._commit_payload(events, outbox, receipts, projection)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.file_path.name}.", suffix=".tmp", dir=self.file_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(payload))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.file_path)
            if os.name != "nt":
                directory_fd = os.open(self.file_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception as exc:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise AtomicCommitError("file-safe commit envelope was not installed") from exc

    @classmethod
    def load(
        cls,
        file_path: Path,
        actor_capabilities: dict[str, set[str]],
        actor_identities: dict[str, ActorIdentity],
    ) -> "ProductionControlHarness":
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != PRODUCTION_CONTROL_FILE_SCHEMA_VERSION:
                raise LedgerIntegrityError("unsupported file harness schema")
            scope = ProjectScope.model_validate(raw["scope"])
            events = tuple(ProjectEvent.model_validate(value) for value in raw["events"])
            outbox = tuple(OutboxRecord.model_validate(value) for value in raw["outbox"])
            seal = LedgerSeal.model_validate(raw["ledger_seal"])
            if seal != cls._seal(events):
                raise LedgerIntegrityError("ledger tail anchor or digest mismatch")
            verify_events(scope, events)
            event_ids = {event.event_id for event in events}
            expected_outbox = tuple(cls._outbox_for(event) for event in events)
            if outbox != expected_outbox:
                raise LedgerIntegrityError("ledger and outbox are not an atomic one-to-one commit")
            receipts: dict[str, tuple[str, CommandReceipt]] = {}
            for key, value in raw["receipts"].items():
                receipt = CommandReceipt.model_validate(value["receipt"])
                semantic_digest = value["semantic_digest"]
                _validate_scope(receipt.result_refs, scope)
                expected_receipt_id = digest(
                    {
                        "scope": scope.model_dump(mode="json"),
                        "idempotency_key": key,
                        "semantic_digest": semantic_digest,
                        "version": receipt.accepted_version,
                    }
                )
                if (
                    receipt.idempotency_key != key
                    or semantic_digest != receipt.command_digest
                    or receipt.receipt_id != expected_receipt_id
                    or not set(receipt.event_ids).issubset(event_ids)
                ):
                    raise LedgerIntegrityError("receipt does not bind committed events")
                receipts[key] = (semantic_digest, receipt)
            receipt_by_id = {receipt.receipt_id: receipt for _, receipt in receipts.values()}
            if len(receipt_by_id) != len(receipts):
                raise LedgerIntegrityError("receipt ids must be unique")
            event_groups: dict[str, list[ProjectEvent]] = {}
            for event in events:
                event_groups.setdefault(event.command_receipt_id, []).append(event)
            if set(event_groups) != set(receipt_by_id):
                raise LedgerIntegrityError("every command batch requires one exact receipt")
            prior_event_ids: set[str] = set()
            for receipt_id, group in event_groups.items():
                receipt = receipt_by_id[receipt_id]
                if tuple(event.event_id for event in group) != receipt.event_ids:
                    raise LedgerIntegrityError("receipt event membership is not exact and ordered")
                if receipt.accepted_version != group[0].project_version:
                    raise LedgerIntegrityError("receipt version does not match its command batch")
                if any(
                    event.correlation_id != group[0].correlation_id
                    or event.causation_id != group[0].causation_id
                    for event in group
                ):
                    raise LedgerIntegrityError("command batch correlation or causation diverged")
                if group[0].causation_id != receipt.command_id and group[0].causation_id not in prior_event_ids:
                    raise LedgerIntegrityError("causation does not resolve to command or prior event")
                prior_event_ids.update(event.event_id for event in group)
            projection = rebuild_projection(scope, events)
            if raw.get("projection_digest") != projection.state_digest:
                raise LedgerIntegrityError("projection digest does not rebuild deterministically")
            if raw.get("provider_dispatch_count") != 0:
                raise LedgerIntegrityError("deterministic harness cannot contain provider dispatch")
        except LedgerIntegrityError:
            raise
        except Exception as exc:
            raise LedgerIntegrityError("corrupt or truncated file harness rejected") from exc
        harness = cls(scope, actor_capabilities, actor_identities, file_path=file_path)
        harness.events = events
        harness.outbox = outbox
        harness._receipts = receipts
        harness.projection = projection
        return harness


def verify_events(scope: ProjectScope, events: tuple[ProjectEvent, ...]) -> None:
    previous: str | None = None
    seen_ids: set[str] = set()
    version = 0
    current_receipt: str | None = None
    current_version = 0
    batch_position = 0
    batch_count = 0
    for index, event in enumerate(events, start=1):
        if event.scope != scope or event.project_sequence != index:
            raise LedgerIntegrityError("event scope or sequence is not contiguous")
        if event.event_id in seen_ids:
            raise LedgerIntegrityError("duplicate event id")
        if event.previous_event_digest != previous:
            raise LedgerIntegrityError("event hash chain is broken")
        base = event.model_dump(mode="json")
        claimed_integrity = base.pop("integrity_digest")
        if digest(base) != claimed_integrity:
            raise LedgerIntegrityError("event integrity digest mismatch")
        base_without_event_id = dict(base)
        event_id = base_without_event_id.pop("event_id")
        if digest({"event": base_without_event_id, "ordinal": index}) != event_id:
            raise LedgerIntegrityError("event id is not deterministic")
        if event.command_receipt_id != current_receipt:
            if current_receipt is not None:
                version = current_version
            if event.project_version != version + 1:
                raise LedgerIntegrityError("command batch project version is not contiguous")
            current_receipt = event.command_receipt_id
            current_version = event.project_version
            batch_position = 1
            batch_count = event.batch_count
            if event.batch_index != 1:
                raise LedgerIntegrityError("command batch must start at index one")
        elif event.project_version != current_version:
            raise LedgerIntegrityError("one command batch must share one project version")
        else:
            batch_position += 1
            if event.batch_count != batch_count or event.batch_index != batch_position:
                raise LedgerIntegrityError("command batch index or count is not contiguous")
        next_is_new_batch = index == len(events) or events[index].command_receipt_id != current_receipt
        if next_is_new_batch and batch_position != batch_count:
            raise LedgerIntegrityError("command batch is truncated")
        seen_ids.add(event.event_id)
        previous = event.integrity_digest


def rebuild_projection(scope: ProjectScope, events: tuple[ProjectEvent, ...]) -> Projection:
    verify_events(scope, events)
    projection = Projection(scope=scope)
    current_receipt: str | None = None
    current_version = 0
    for event in events:
        if current_receipt is not None and event.command_receipt_id != current_receipt:
            projection.version = current_version
        current_receipt = event.command_receipt_id
        current_version = event.project_version
        apply_event(projection, event)
    if current_receipt is not None:
        projection.version = current_version
    return projection


def _embedded_exact_refs(value: Any) -> tuple[ExactObjectRef, ...]:
    refs: list[ExactObjectRef] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if {"scope", "object_type", "object_id", "revision_id"}.issubset(item):
                refs.append(ExactObjectRef.model_validate(item))
                return
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return tuple(refs)


__all__ = (
    "AtomicCommitError",
    "AuthorizationError",
    "IdempotencyConflictError",
    "LedgerIntegrityError",
    "ProductionControlError",
    "ProductionControlHarness",
    "StateConflictError",
    "VersionConflictError",
    "canonical_json",
    "digest",
    "exact_ref",
    "rebuild_projection",
)
