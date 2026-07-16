from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PRODUCTION_CONTROL_CONTRACT_REVISION = "v0.1"
PRODUCTION_CONTROL_EVENT_SCHEMA_VERSION = "afs.production-control.event.v0.1"
PRODUCTION_CONTROL_FILE_SCHEMA_VERSION = "afs.production-control.file-harness.v0.1"
SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"
SHA256 = r"^[a-f0-9]{64}$"

ExecutionState = Literal[
    "queued",
    "running",
    "waiting-human",
    "retrying",
    "blocked",
    "completed",
    "cancelled",
]
ControlState = Literal[
    "active",
    "pause-requested",
    "paused",
    "resume-requested",
    "cancel-requested",
]
ObjectState = Literal["active", "locked", "retired", "cancelled"]
ObjectType = Literal[
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
    "project",
    "episode",
    "scene",
    "shot",
    "continuity_state",
    "asset_candidate",
    "selected_version",
    "review_decision",
    "delivery_version",
    "agent_proposal",
]


def _require_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectScope(ContractModel):
    org_id: str = Field(pattern=SAFE_ID)
    project_id: str = Field(pattern=SAFE_ID)


class ActorIdentity(ContractModel):
    actor_id: str = Field(pattern=SAFE_ID)
    actor_type: Literal["human", "agent", "service"]
    authority_ref: str = Field(pattern=SAFE_ID)


class ExactObjectRef(ContractModel):
    scope: ProjectScope
    object_type: ObjectType
    object_id: str = Field(pattern=SAFE_ID)
    revision_id: str = Field(pattern=SAFE_ID)


class RevisionIdentity(ContractModel):
    scope: ProjectScope
    object_id: str = Field(pattern=SAFE_ID)
    revision_id: str = Field(pattern=SAFE_ID)
    revision: int = Field(ge=1, strict=True)
    parent_revision_id: str | None = Field(default=None, pattern=SAFE_ID)
    created_at: str
    state: ObjectState = "active"

    _timestamp = field_validator("created_at")(_require_timestamp)


class Mission(ContractModel):
    identity: RevisionIdentity
    head_revision_ref: ExactObjectRef


class MissionRevision(ContractModel):
    identity: RevisionIdentity
    objective: str = Field(min_length=1, max_length=4000)
    reference_constraint_refs: tuple[ExactObjectRef, ...] = Field(default_factory=tuple)


class ReferenceConstraint(ContractModel):
    identity: RevisionIdentity
    constraint_type: Literal["story", "character", "visual", "rights", "privacy", "delivery"]
    rule: str = Field(min_length=1, max_length=2000)
    source_ref: ExactObjectRef | None = None


class MoneyRange(ContractModel):
    currency: str = Field(min_length=3, max_length=8)
    minimum: int = Field(ge=0, strict=True)
    maximum: int = Field(ge=0, strict=True)
    unit: str = Field(min_length=1, max_length=80)
    assumption: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def range_is_ordered(self) -> "MoneyRange":
        if self.maximum < self.minimum:
            raise ValueError("estimated range maximum cannot be below minimum")
        return self


class BudgetEnvelope(ContractModel):
    identity: RevisionIdentity
    estimated: MoneyRange
    max_budget: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def maximum_is_admissible(self) -> "BudgetEnvelope":
        if self.estimated.maximum > self.max_budget:
            raise ValueError("estimated maximum cannot exceed max budget")
        return self


class CostEstimate(ContractModel):
    identity: RevisionIdentity
    plan_revision_ref: ExactObjectRef
    task_ref: ExactObjectRef | None = None
    estimated: MoneyRange


class PlanTaskSpec(ContractModel):
    task_id: str = Field(pattern=SAFE_ID)
    boundary: str = Field(min_length=1, max_length=1000)
    capability: str = Field(pattern=SAFE_ID)
    dependency_task_ids: tuple[str, ...] = Field(default_factory=tuple)


class ProductionPlan(ContractModel):
    identity: RevisionIdentity
    mission_revision_ref: ExactObjectRef
    head_revision_ref: ExactObjectRef
    status: Literal["proposed", "approved", "locked", "retired", "cancelled"] = "proposed"


class PlanRevision(ContractModel):
    identity: RevisionIdentity
    plan_ref: ExactObjectRef
    task_specs: tuple[PlanTaskSpec, ...] = Field(min_length=3)
    budget_envelope_ref: ExactObjectRef
    cost_estimate_refs: tuple[ExactObjectRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def task_ids_are_unique(self) -> "PlanRevision":
        ids = [task.task_id for task in self.task_specs]
        if len(ids) != len(set(ids)):
            raise ValueError("plan task specs must use unique stable task ids")
        if any(dep not in set(ids) for task in self.task_specs for dep in task.dependency_task_ids):
            raise ValueError("task dependency must resolve inside the plan revision")
        return self


class PlanTask(ContractModel):
    identity: RevisionIdentity
    plan_revision_ref: ExactObjectRef
    boundary: str = Field(min_length=1, max_length=1000)
    capability: str = Field(pattern=SAFE_ID)
    dependency_refs: tuple[ExactObjectRef, ...] = Field(default_factory=tuple)
    execution_state: Literal["queued", "cancelled"] = "queued"


class PlanApprovalDecision(ContractModel):
    identity: RevisionIdentity
    plan_revision_ref: ExactObjectRef
    decision: Literal["approved", "rejected"]
    approved_task_refs: tuple[ExactObjectRef, ...] = Field(default_factory=tuple)
    budget_envelope_ref: ExactObjectRef

    @model_validator(mode="after")
    def approval_has_three_tasks(self) -> "PlanApprovalDecision":
        if self.decision == "approved" and len(self.approved_task_refs) < 3:
            raise ValueError("approved plan must atomically create at least three tasks")
        if self.decision == "rejected" and self.approved_task_refs:
            raise ValueError("rejected plan cannot approve tasks")
        return self


class AgentAssignment(ContractModel):
    identity: RevisionIdentity
    task_ref: ExactObjectRef
    agent_id: str = Field(pattern=SAFE_ID)
    capability: str = Field(pattern=SAFE_ID)


class ProductionRun(ContractModel):
    identity: RevisionIdentity
    task_ref: ExactObjectRef
    assignment_ref: ExactObjectRef
    execution_state: ExecutionState = "queued"
    control_state: ControlState = "active"
    latest_attempt_ref: ExactObjectRef | None = None


class RunAttempt(ContractModel):
    identity: RevisionIdentity
    run_ref: ExactObjectRef
    attempt_number: int = Field(ge=1, strict=True)
    prior_attempt_ref: ExactObjectRef | None = None


class CostEntry(ContractModel):
    identity: RevisionIdentity
    run_ref: ExactObjectRef
    attempt_ref: ExactObjectRef
    kind: Literal["estimated", "committed", "actual"]
    amount: int = Field(ge=0, strict=True)
    currency: str = Field(min_length=3, max_length=8)
    charge_fingerprint: str = Field(pattern=SHA256)


class ProviderGateDecision(ContractModel):
    identity: RevisionIdentity
    run_ref: ExactObjectRef
    budget_envelope_ref: ExactObjectRef
    capability: str = Field(pattern=SAFE_ID)
    capability_authorized: bool = False
    budget_admitted: bool = False
    privacy_policy_satisfied: bool = False
    no_training_policy_satisfied: bool = False
    allowed: bool = False
    authorization_ref: str | None = Field(default=None, pattern=SAFE_ID)
    privacy_policy_ref: str = Field(pattern=SAFE_ID)
    no_training_policy_ref: str = Field(pattern=SAFE_ID)

    @model_validator(mode="after")
    def allowed_requires_all_gates(self) -> "ProviderGateDecision":
        required = (
            self.capability_authorized,
            self.budget_admitted,
            self.privacy_policy_satisfied,
            self.no_training_policy_satisfied,
            self.authorization_ref is not None,
        )
        if self.allowed and not all(required):
            raise ValueError("provider allow requires capability, budget, privacy, no-training, and authority")
        return self


class Blocker(ContractModel):
    identity: RevisionIdentity
    run_ref: ExactObjectRef
    owner_actor_id: str = Field(pattern=SAFE_ID)
    reason: str = Field(min_length=1, max_length=1000)
    clearance_evidence_refs: tuple[ExactObjectRef, ...] = Field(min_length=1)


class HumanDecisionRequest(ContractModel):
    identity: RevisionIdentity
    run_ref: ExactObjectRef
    options: tuple[str, ...] = Field(min_length=2, max_length=16)
    impact_refs: tuple[ExactObjectRef, ...] = Field(min_length=1)
    deadline: str

    _deadline = field_validator("deadline")(_require_timestamp)


class HumanDecision(ContractModel):
    identity: RevisionIdentity
    request_ref: ExactObjectRef
    selected_option: str = Field(min_length=1, max_length=200)
    impact_acknowledged: bool


class EpisodeArtifactAdapterRequest(ContractModel):
    adapter_contract: Literal["episode-artifact-writeback.additive.v0.1"] = (
        "episode-artifact-writeback.additive.v0.1"
    )
    mode: Literal["asset_candidate", "shot_successor"]
    predecessor_ref: ExactObjectRef | None = None
    successor_ref: ExactObjectRef
    protected_exact_refs: tuple[ExactObjectRef, ...] = Field(default_factory=tuple)
    existing_typed_operation: Literal[
        "asset_candidate.create_version",
        "shot.reassign_scene",
        "continuity.apply_proposal",
    ]
    continuity_source_proposal_ref: ExactObjectRef | None = None

    @model_validator(mode="after")
    def preserves_frozen_episode_semantics(self) -> "EpisodeArtifactAdapterRequest":
        if self.mode == "asset_candidate":
            if self.successor_ref.object_type != "asset_candidate":
                raise ValueError("asset candidate adapter must target an exact asset_candidate")
            if self.existing_typed_operation != "asset_candidate.create_version":
                raise ValueError("asset candidate writeback maps to existing create_version")
        else:
            if self.predecessor_ref is None:
                raise ValueError("shot successor adapter requires exact predecessor")
            if self.predecessor_ref.object_type != "shot" or self.successor_ref.object_type != "shot":
                raise ValueError("shot successor adapter requires exact shot refs")
            if self.predecessor_ref.object_id != self.successor_ref.object_id:
                raise ValueError("shot successor must preserve stable shot identity")
            if self.predecessor_ref.revision_id == self.successor_ref.revision_id:
                raise ValueError("shot successor must create a new immutable revision")
            if not self.protected_exact_refs:
                raise ValueError("shot successor must name protected exact refs")
            if self.existing_typed_operation == "asset_candidate.create_version":
                raise ValueError("visual artifact writeback cannot create a bare shot successor")
            if self.existing_typed_operation == "continuity.apply_proposal" and (
                self.continuity_source_proposal_ref is None
                or self.continuity_source_proposal_ref.object_type != "agent_proposal"
            ):
                raise ValueError("continuity apply must preserve exact source_proposal_ref")
        changed = {self.predecessor_ref, self.successor_ref}
        if any(ref in changed for ref in self.protected_exact_refs):
            raise ValueError("changed target cannot also be a protected exact ref")
        return self


class ArtifactWriteback(ContractModel):
    identity: RevisionIdentity
    candidate_registration_ref: ExactObjectRef
    plan_task_ref: ExactObjectRef
    run_ref: ExactObjectRef
    attempt_ref: ExactObjectRef
    artifact_id: str = Field(pattern=SAFE_ID)
    artifact_digest: str = Field(pattern=SHA256)
    adapter_request: EpisodeArtifactAdapterRequest


class ArtifactCandidateRegistration(ContractModel):
    identity: RevisionIdentity
    plan_task_ref: ExactObjectRef
    run_ref: ExactObjectRef
    attempt_ref: ExactObjectRef
    artifact_id: str = Field(pattern=SAFE_ID)
    artifact_digest: str = Field(pattern=SHA256)
    adapter_request: EpisodeArtifactAdapterRequest


class SelectiveRevisionRequest(ContractModel):
    identity: RevisionIdentity
    target_exact_ref: ExactObjectRef
    protected_exact_refs: tuple[ExactObjectRef, ...] = Field(min_length=1)
    requested_changes: tuple[str, ...] = Field(min_length=1)
    source_writeback_ref: ExactObjectRef


class ImpactAssessment(ContractModel):
    identity: RevisionIdentity
    revision_request_ref: ExactObjectRef
    affected_exact_refs: tuple[ExactObjectRef, ...]
    preserved_exact_refs: tuple[ExactObjectRef, ...] = Field(min_length=1)
    assessment_digest: str = Field(pattern=SHA256)


class BudgetAuthorization(ContractModel):
    budget_envelope_ref: ExactObjectRef
    admitted_amount: int = Field(ge=0, strict=True)
    currency: str = Field(min_length=3, max_length=8)
    authorization_ref: str = Field(pattern=SAFE_ID)


class ProviderAuthorization(ContractModel):
    capability: str = Field(pattern=SAFE_ID)
    authorized: bool = False
    privacy_policy_satisfied: bool = False
    no_training_policy_satisfied: bool = False
    authorization_ref: str | None = Field(default=None, pattern=SAFE_ID)


class CommandEnvelope(ContractModel):
    command_id: str = Field(pattern=SAFE_ID)
    command_type: str = Field(pattern=SAFE_ID)
    scope: ProjectScope
    actor: ActorIdentity
    expected_version: int = Field(ge=0, strict=True)
    idempotency_key: str = Field(pattern=SAFE_ID)
    correlation_id: str = Field(pattern=SAFE_ID)
    causation_id: str = Field(pattern=SAFE_ID)
    capability: str = Field(pattern=SAFE_ID)
    exact_object_refs: tuple[ExactObjectRef, ...] = Field(default_factory=tuple)
    budget_authorization: BudgetAuthorization | None = None
    provider_authorization: ProviderAuthorization | None = None
    payload: dict[str, Any]
    payload_digest: str = Field(pattern=SHA256)


class CommandReceipt(ContractModel):
    receipt_id: str = Field(pattern=SAFE_ID)
    command_id: str = Field(pattern=SAFE_ID)
    idempotency_key: str = Field(pattern=SAFE_ID)
    command_digest: str = Field(pattern=SHA256)
    accepted_version: int = Field(ge=1, strict=True)
    event_ids: tuple[str, ...] = Field(min_length=1)
    result_refs: tuple[ExactObjectRef, ...] = Field(default_factory=tuple)


class ProjectEvent(ContractModel):
    schema_version: Literal["afs.production-control.event.v0.1"] = (
        PRODUCTION_CONTROL_EVENT_SCHEMA_VERSION
    )
    scope: ProjectScope
    project_sequence: int = Field(ge=1, strict=True)
    project_version: int = Field(ge=1, strict=True)
    batch_index: int = Field(ge=1, strict=True)
    batch_count: int = Field(ge=1, strict=True)
    event_id: str = Field(pattern=SHA256)
    event_type: str = Field(pattern=SAFE_ID)
    correlation_id: str = Field(pattern=SAFE_ID)
    causation_id: str = Field(pattern=SAFE_ID)
    command_receipt_id: str = Field(pattern=SAFE_ID)
    previous_event_digest: str | None = Field(default=None, pattern=SHA256)
    payload: dict[str, Any]
    integrity_digest: str = Field(pattern=SHA256)


class OutboxRecord(ContractModel):
    outbox_id: str = Field(pattern=SHA256)
    scope: ProjectScope
    event_id: str = Field(pattern=SHA256)
    event_type: str = Field(pattern=SAFE_ID)
    payload_digest: str = Field(pattern=SHA256)
    status: Literal["pending"] = "pending"


class LedgerSeal(ContractModel):
    event_count: int = Field(ge=0, strict=True)
    last_event_digest: str | None = Field(default=None, pattern=SHA256)
    ledger_digest: str = Field(pattern=SHA256)


EXECUTION_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    "queued": frozenset({"running", "blocked", "cancelled"}),
    "running": frozenset({"waiting-human", "retrying", "blocked", "completed", "cancelled"}),
    "waiting-human": frozenset({"running", "blocked", "cancelled"}),
    "retrying": frozenset({"running", "blocked", "cancelled"}),
    "blocked": frozenset({"queued", "running", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}

CONTROL_TRANSITIONS: dict[ControlState, frozenset[ControlState]] = {
    "active": frozenset({"pause-requested", "cancel-requested"}),
    "pause-requested": frozenset({"paused", "cancel-requested"}),
    "paused": frozenset({"resume-requested", "cancel-requested"}),
    "resume-requested": frozenset({"active", "cancel-requested"}),
    "cancel-requested": frozenset(),
}


def is_execution_transition_allowed(current: ExecutionState, target: ExecutionState) -> bool:
    return target in EXECUTION_TRANSITIONS[current]


def is_control_transition_allowed(current: ControlState, target: ControlState) -> bool:
    return target in CONTROL_TRANSITIONS[current]
