from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EPISODE_PRODUCTION_AGGREGATE_SCHEMA_VERSION = "afs_episode_production_aggregate.v0.1"
EPISODE_PRODUCTION_CONTRACT_REVISION = "v0.1.1"
SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$"
SHA256 = r"^[a-f0-9]{64}$"

LifecycleState = Literal["draft", "candidate", "approved", "locked", "rejected", "retired"]
ReviewState = Literal["not_requested", "needs_review", "approved", "rejected"]
JobState = Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]
EntityType = Literal[
    "project",
    "series",
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


class EpisodeContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TenantScope(EpisodeContractModel):
    org_id: str = Field(pattern=SAFE_ID)
    project_id: str = Field(pattern=SAFE_ID)
    actor_id: str = Field(pattern=SAFE_ID)


class EntityVersionRef(EpisodeContractModel):
    entity_type: EntityType
    entity_id: str = Field(pattern=SAFE_ID)
    version_id: str = Field(pattern=SAFE_ID)


class SafeArtifactRef(EpisodeContractModel):
    artifact_id: str = Field(pattern=SAFE_ID)
    artifact_type: str = Field(pattern=SAFE_ID)
    content_digest: str = Field(pattern=SHA256)


class ControlObjectRef(EpisodeContractModel):
    object_type: str = Field(pattern=SAFE_ID)
    object_id: str = Field(pattern=SAFE_ID)
    revision_id: str = Field(pattern=SAFE_ID)


class ProductionControlProvenance(EpisodeContractModel):
    plan_task_ref: ControlObjectRef
    run_ref: ControlObjectRef
    attempt_ref: ControlObjectRef
    writeback_ref: ControlObjectRef
    affected_refs: tuple[EntityVersionRef, ...] = Field(min_length=1, max_length=128)
    protected_refs: tuple[EntityVersionRef, ...] = Field(default_factory=tuple, max_length=128)


class SourceEvidenceRef(EpisodeContractModel):
    source_id: str = Field(pattern=SAFE_ID)
    scope: TenantScope
    source_type: Literal["upload", "creator_input", "generated", "licensed", "derived"]
    uploaded_by: str = Field(pattern=SAFE_ID)
    rights_basis: Literal["creator_owned", "licensed", "provider_terms", "unknown"]
    allowed_uses: tuple[Literal["production", "sharing", "product_improvement", "training"], ...]
    training_status: Literal["denied", "consented", "not_applicable"] = "denied"
    provider_id: str | None = Field(default=None, pattern=SAFE_ID)
    model_id: str | None = Field(default=None, pattern=SAFE_ID)
    derived_from: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    deletion_state: Literal[
        "active",
        "requested",
        "access_revoked",
        "live_data_purged",
        "provider_purge_confirmed",
        "backup_tombstoned",
        "completed",
        "exception",
    ] = "active"

    @model_validator(mode="after")
    def training_use_matches_status(self) -> "SourceEvidenceRef":
        if len(self.allowed_uses) != len(set(self.allowed_uses)):
            raise ValueError("source allowed uses must be unique")
        if "training" in self.allowed_uses and self.training_status != "consented":
            raise ValueError("training use requires explicit consent status")
        if self.training_status == "consented" and "training" not in self.allowed_uses:
            raise ValueError("consented training status requires training in allowed_uses")
        return self


class ProjectDataPolicy(EpisodeContractModel):
    visibility: Literal["private", "project_members", "shared_link"] = "private"
    training_use: Literal["denied_by_default", "consented"] = "denied_by_default"
    product_improvement_use: Literal["denied_by_default", "consented"] = "denied_by_default"
    export_enabled: bool = True
    deletion_enabled: bool = True


class ConsentRecord(EpisodeContractModel):
    consent_id: str = Field(pattern=SAFE_ID)
    scope: TenantScope
    purpose: Literal["sharing", "product_improvement", "training"]
    data_classes: tuple[str, ...] = Field(min_length=1, max_length=32)
    provider_id: str | None = Field(default=None, pattern=SAFE_ID)
    policy_version: str = Field(pattern=SAFE_ID)
    status: Literal["granted", "withdrawn"]
    granted_at: str
    expires_at: str | None = None
    withdrawn_at: str | None = None

    @field_validator("granted_at", "expires_at", "withdrawn_at")
    @classmethod
    def timestamps_are_iso8601(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("consent timestamps must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("consent timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def withdrawal_has_timestamp(self) -> "ConsentRecord":
        if self.status == "withdrawn" and self.withdrawn_at is None:
            raise ValueError("withdrawn consent requires withdrawn_at")
        if self.status == "granted" and self.withdrawn_at is not None:
            raise ValueError("granted consent cannot carry withdrawn_at")
        granted_at = datetime.fromisoformat(self.granted_at)
        if self.expires_at is not None and datetime.fromisoformat(self.expires_at) <= granted_at:
            raise ValueError("consent expiry must be later than granted_at")
        if self.withdrawn_at is not None and datetime.fromisoformat(self.withdrawn_at) < granted_at:
            raise ValueError("consent withdrawal cannot predate granted_at")
        return self


class ProviderDataContract(EpisodeContractModel):
    provider_id: str = Field(pattern=SAFE_ID)
    surface: str = Field(min_length=1, max_length=80)
    training_use: Literal["prohibited", "possible", "required", "unknown"]
    no_training_supported: bool
    retention_days: int | None = Field(default=None, ge=0)
    deletion_api_supported: bool
    withdrawal_supported: bool
    region: str | None = Field(default=None, max_length=80)
    subprocessors_documented: bool


class VersionedFact(EpisodeContractModel):
    entity_type: EntityType
    entity_id: str = Field(pattern=SAFE_ID)
    version_id: str = Field(pattern=SAFE_ID)
    revision: int = Field(ge=1, strict=True)
    parent_version_id: str | None = Field(default=None, pattern=SAFE_ID)
    lifecycle_state: LifecycleState
    review_state: ReviewState = "not_requested"
    content_digest: str = Field(pattern=SHA256)
    scope: TenantScope
    created_at: str
    source_refs: tuple[SourceEvidenceRef, ...] = Field(default_factory=tuple, max_length=64)

    @field_validator("created_at")
    @classmethod
    def created_at_is_iso8601(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("created_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def locked_and_rejected_states_match_review(self) -> "VersionedFact":
        if self.lifecycle_state == "locked" and self.review_state != "approved":
            raise ValueError("locked content requires approved review state")
        if self.lifecycle_state == "rejected" and self.review_state != "rejected":
            raise ValueError("rejected content requires rejected review state")
        return self

    def as_ref(self) -> EntityVersionRef:
        return EntityVersionRef(
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            version_id=self.version_id,
        )


class ProjectVersion(VersionedFact):
    entity_type: Literal["project"] = "project"
    title: str = Field(min_length=1, max_length=200)
    data_policy: ProjectDataPolicy = Field(default_factory=ProjectDataPolicy)


class SeriesVersion(VersionedFact):
    entity_type: Literal["series"] = "series"
    project_ref: EntityVersionRef
    title: str = Field(min_length=1, max_length=200)


class EpisodeVersion(VersionedFact):
    entity_type: Literal["episode"] = "episode"
    series_ref: EntityVersionRef
    title: str = Field(min_length=1, max_length=200)


class SceneVersion(VersionedFact):
    entity_type: Literal["scene"] = "scene"
    episode_ref: EntityVersionRef
    sequence: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=200)


class ShotVersion(VersionedFact):
    entity_type: Literal["shot"] = "shot"
    scene_ref: EntityVersionRef
    sequence: int = Field(ge=1, strict=True)
    duration_seconds: float = Field(gt=0, le=3600)
    continuity_refs: tuple[EntityVersionRef, ...] = Field(default_factory=tuple, max_length=64)
    source_proposal_ref: EntityVersionRef | None = None


class ContinuityStateVersion(VersionedFact):
    entity_type: Literal["continuity_state"] = "continuity_state"
    subject_type: Literal["character", "scene", "prop"]
    subject_id: str = Field(pattern=SAFE_ID)
    identity_baseline: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    temporary_state: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    prohibited_changes: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    approved_asset_selection_refs: tuple[EntityVersionRef, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )


class AssetCandidateVersion(VersionedFact):
    entity_type: Literal["asset_candidate"] = "asset_candidate"
    target_ref: EntityVersionRef
    artifact_ref: SafeArtifactRef | None = None
    job_id: str | None = Field(default=None, pattern=SAFE_ID)
    job_state: JobState | None = None
    control_provenance: ProductionControlProvenance | None = None

    @model_validator(mode="after")
    def job_fields_are_paired(self) -> "AssetCandidateVersion":
        if (self.job_id is None) != (self.job_state is None):
            raise ValueError("candidate job_id and job_state must be provided together")
        if self.job_state == "succeeded" and self.artifact_ref is None:
            raise ValueError("succeeded candidate job requires a safe artifact reference")
        return self


class SelectedVersion(VersionedFact):
    entity_type: Literal["selected_version"] = "selected_version"
    target_ref: EntityVersionRef
    purpose: Literal[
        "storyboard",
        "image",
        "video",
        "audio",
        "character_reference",
        "scene_reference",
        "prop_reference",
        "voice_reference",
        "style_reference",
    ]
    candidate_ref: EntityVersionRef


class ReviewDecision(VersionedFact):
    entity_type: Literal["review_decision"] = "review_decision"
    subject_ref: EntityVersionRef
    decision: Literal["approve", "reject", "request_revision", "unlock", "retire"]
    note: str = Field(default="", max_length=800)


class AgentProposal(VersionedFact):
    entity_type: Literal["agent_proposal"] = "agent_proposal"
    target_ref: EntityVersionRef
    impact_refs: tuple[EntityVersionRef, ...] = Field(default_factory=tuple, max_length=128)
    applied_refs: tuple[EntityVersionRef, ...] = Field(default_factory=tuple, max_length=128)
    action: str = Field(min_length=1, max_length=120)
    decision_state: Literal[
        "pending",
        "accepted",
        "partially_accepted",
        "rejected",
        "executed",
        "undone",
    ] = "pending"


class DeliveryVersion(VersionedFact):
    entity_type: Literal["delivery_version"] = "delivery_version"
    episode_ref: EntityVersionRef
    selection_refs: tuple[EntityVersionRef, ...] = Field(default_factory=tuple, max_length=256)
    review_decision_refs: tuple[EntityVersionRef, ...] = Field(default_factory=tuple, max_length=256)
    preview_artifact_ref: SafeArtifactRef | None = None
    export_artifact_refs: tuple[SafeArtifactRef, ...] = Field(default_factory=tuple, max_length=64)


class AggregateMutationCommand(EpisodeContractModel):
    command_id: str = Field(pattern=SAFE_ID)
    idempotency_key: str = Field(pattern=SAFE_ID)
    expected_aggregate_version: int = Field(ge=1, strict=True)
    scope: TenantScope
    target_ref: EntityVersionRef
    action: Literal[
        "create_version",
        "request_review",
        "approve",
        "reject",
        "unlock",
        "retire",
        "select_candidate",
        "apply_proposal",
        "undo_proposal",
        "freeze_delivery",
    ]
    payload_digest: str = Field(pattern=SHA256)


class ProductionProjectAggregate(EpisodeContractModel):
    schema_version: Literal["afs_episode_production_aggregate.v0.1"] = (
        EPISODE_PRODUCTION_AGGREGATE_SCHEMA_VERSION
    )
    aggregate_version: int = Field(ge=1, strict=True)
    evaluated_at: str
    scope: TenantScope
    projects: tuple[ProjectVersion, ...] = Field(min_length=1, max_length=64)
    series: tuple[SeriesVersion, ...] = Field(default_factory=tuple, max_length=64)
    episodes: tuple[EpisodeVersion, ...] = Field(default_factory=tuple, max_length=512)
    scenes: tuple[SceneVersion, ...] = Field(default_factory=tuple, max_length=8192)
    shots: tuple[ShotVersion, ...] = Field(default_factory=tuple, max_length=65536)
    continuity_states: tuple[ContinuityStateVersion, ...] = Field(default_factory=tuple, max_length=8192)
    asset_candidates: tuple[AssetCandidateVersion, ...] = Field(default_factory=tuple, max_length=65536)
    selections: tuple[SelectedVersion, ...] = Field(default_factory=tuple, max_length=65536)
    review_decisions: tuple[ReviewDecision, ...] = Field(default_factory=tuple, max_length=65536)
    agent_proposals: tuple[AgentProposal, ...] = Field(default_factory=tuple, max_length=65536)
    deliveries: tuple[DeliveryVersion, ...] = Field(default_factory=tuple, max_length=4096)
    consent_records: tuple[ConsentRecord, ...] = Field(default_factory=tuple, max_length=1024)
    provider_contracts: tuple[ProviderDataContract, ...] = Field(default_factory=tuple, max_length=128)

    @field_validator("evaluated_at")
    @classmethod
    def evaluated_at_is_deterministic_iso8601(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("evaluated_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("evaluated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def references_form_one_project_fact_chain(self) -> "ProductionProjectAggregate":
        records = self._records()
        evaluated_at = datetime.fromisoformat(self.evaluated_at)
        index: dict[tuple[str, str, str], VersionedFact] = {}
        revision_index: dict[tuple[str, str, int], VersionedFact] = {}
        histories: dict[tuple[str, str], list[VersionedFact]] = {}
        for record in records:
            key = (record.entity_type, record.entity_id, record.version_id)
            if key in index:
                raise ValueError("entity version references must be unique")
            index[key] = record
            revision_key = (record.entity_type, record.entity_id, record.revision)
            if revision_key in revision_index:
                raise ValueError("entity revisions must be unique")
            revision_index[revision_key] = record
            histories.setdefault((record.entity_type, record.entity_id), []).append(record)
            if record.scope.org_id != self.scope.org_id or record.scope.project_id != self.scope.project_id:
                raise ValueError("every record must remain inside the aggregate tenant and project")
            if datetime.fromisoformat(record.created_at) > evaluated_at:
                raise ValueError("record created_at cannot be later than aggregate evaluated_at")

        for history in histories.values():
            ordered = sorted(history, key=lambda item: item.revision)
            if [item.revision for item in ordered] != list(range(1, len(ordered) + 1)):
                raise ValueError("entity revision history must be complete and contiguous")
            if ordered[0].parent_version_id is not None:
                raise ValueError("first entity revision cannot have a parent version")
            for previous, current in zip(ordered, ordered[1:]):
                if current.parent_version_id != previous.version_id:
                    raise ValueError("entity revision must reference the immediately preceding version")
                if datetime.fromisoformat(current.created_at) <= datetime.fromisoformat(previous.created_at):
                    raise ValueError("entity revision timestamp must be later than its parent")
                same_non_locked_state = (
                    previous.lifecycle_state == current.lifecycle_state
                    and previous.lifecycle_state not in ("locked", "retired")
                )
                if not same_non_locked_state and not is_lifecycle_transition_allowed(
                    previous.lifecycle_state,
                    current.lifecycle_state,
                ):
                    raise ValueError("entity revision uses an invalid lifecycle transition")
                if previous.lifecycle_state == "locked":
                    required_decision = (
                        "unlock" if current.lifecycle_state == "approved" else "retire"
                    )
                    finalized = any(
                        decision.subject_ref == previous.as_ref()
                        and decision.decision == required_decision
                        and decision.lifecycle_state in ("approved", "locked")
                        and decision.review_state == "approved"
                        and datetime.fromisoformat(decision.created_at)
                        >= datetime.fromisoformat(previous.created_at)
                        and datetime.fromisoformat(decision.created_at)
                        <= datetime.fromisoformat(current.created_at)
                        for decision in self.review_decisions
                    )
                    if not finalized:
                        raise ValueError(
                            "locked entity revision requires an exact finalized unlock or retire decision"
                        )
                    if current.content_digest != previous.content_digest:
                        raise ValueError("unlock or retire revision cannot change locked content")
                material_changed = current.content_digest != previous.content_digest
                if isinstance(current, AssetCandidateVersion) and isinstance(
                    previous,
                    AssetCandidateVersion,
                ):
                    material_changed = material_changed or current.artifact_ref != previous.artifact_ref
                if material_changed and (
                    current.lifecycle_state in ("approved", "locked")
                    or current.review_state == "approved"
                ):
                    reapproved = any(
                        decision.subject_ref == current.as_ref()
                        and decision.decision == "approve"
                        and decision.lifecycle_state in ("approved", "locked")
                        and decision.review_state == "approved"
                        and datetime.fromisoformat(decision.created_at)
                        >= datetime.fromisoformat(current.created_at)
                        and datetime.fromisoformat(decision.created_at) <= evaluated_at
                        for decision in self.review_decisions
                    )
                    if not reapproved:
                        raise ValueError(
                            "changed approved revision requires a new exact finalized approval decision"
                        )

        project_entity_ids = {project.entity_id for project in self.projects}
        if project_entity_ids != {self.scope.project_id}:
            raise ValueError("project history must contain only scope.project_id")
        project = max(self.projects, key=lambda item: item.revision)

        def require(ref: EntityVersionRef, expected: tuple[str, ...] | None = None) -> VersionedFact:
            if expected is not None and ref.entity_type not in expected:
                raise ValueError(f"reference must target one of: {', '.join(expected)}")
            target = index.get((ref.entity_type, ref.entity_id, ref.version_id))
            if target is None:
                raise ValueError("reference must resolve inside the aggregate")
            return target

        for consent in self.consent_records:
            if consent.scope.org_id != self.scope.org_id or consent.scope.project_id != self.scope.project_id:
                raise ValueError("consent record must remain inside the aggregate tenant and project")
        consent_ids = [consent.consent_id for consent in self.consent_records]
        if len(consent_ids) != len(set(consent_ids)):
            raise ValueError("consent ids must be unique inside the aggregate")

        def consent_is_active(consent: ConsentRecord) -> bool:
            return (
                consent.status == "granted"
                and datetime.fromisoformat(consent.granted_at) <= evaluated_at
                and (
                    consent.expires_at is None
                    or evaluated_at < datetime.fromisoformat(consent.expires_at)
                )
                and consent.withdrawn_at is None
            )

        def has_active_consent(purpose: str, provider_id: str | None = None) -> bool:
            return any(
                consent_is_active(consent)
                and consent.purpose == purpose
                and consent.provider_id == provider_id
                for consent in self.consent_records
            )

        active_purposes = {
            consent.purpose for consent in self.consent_records if consent_is_active(consent)
        }
        if project.data_policy.training_use == "consented" and "training" not in active_purposes:
            raise ValueError("training policy requires a granted training consent record")
        if (
            project.data_policy.product_improvement_use == "consented"
            and "product_improvement" not in active_purposes
        ):
            raise ValueError("product improvement policy requires a granted consent record")

        source_index: dict[str, SourceEvidenceRef] = {}
        for record in records:
            for source in record.source_refs:
                if source.scope.org_id != self.scope.org_id or source.scope.project_id != self.scope.project_id:
                    raise ValueError("source record must remain inside the aggregate tenant and project")
                existing_source = source_index.get(source.source_id)
                if existing_source is not None and existing_source != source:
                    raise ValueError("one source id cannot carry conflicting provenance or use policy")
                source_index[source.source_id] = source
                if "training" in source.allowed_uses and (
                    project.data_policy.training_use != "consented"
                    or not has_active_consent("training", source.provider_id)
                ):
                    raise ValueError("source training use requires project policy and matching active consent")
                if "product_improvement" in source.allowed_uses and (
                    project.data_policy.product_improvement_use != "consented"
                    or not has_active_consent("product_improvement", source.provider_id)
                ):
                    raise ValueError(
                        "source product improvement use requires project policy and matching active consent"
                    )
                if "sharing" in source.allowed_uses and (
                    project.data_policy.visibility == "private"
                    or not has_active_consent("sharing", source.provider_id)
                ):
                    raise ValueError("source sharing use requires visible project policy and matching consent")

        for item in self.series:
            require(item.project_ref, ("project",))
        for item in self.episodes:
            require(item.series_ref, ("series",))
        for item in self.scenes:
            require(item.episode_ref, ("episode",))
        for item in self.shots:
            require(item.scene_ref, ("scene",))
            for ref in item.continuity_refs:
                require(ref, ("continuity_state",))
        for item in self.continuity_states:
            for ref in item.approved_asset_selection_refs:
                selection = require(ref, ("selected_version",))
                if (
                    selection.target_ref != item.as_ref()  # type: ignore[attr-defined]
                ):
                    raise ValueError("continuity asset selection must target the exact continuity version")
        for item in self.asset_candidates:
            require(item.target_ref, ("shot", "continuity_state"))
            if item.control_provenance is not None:
                if item.target_ref not in item.control_provenance.affected_refs:
                    raise ValueError("candidate control provenance must name its exact target as affected")
                if item.target_ref in item.control_provenance.protected_refs:
                    raise ValueError("candidate target cannot also be protected")
                for ref in (
                    *item.control_provenance.affected_refs,
                    *item.control_provenance.protected_refs,
                ):
                    require(ref, ("shot", "continuity_state"))
        for item in self.selections:
            target = require(item.target_ref, ("shot", "continuity_state"))
            candidate = require(item.candidate_ref, ("asset_candidate",))
            if candidate.target_ref != item.target_ref:  # type: ignore[attr-defined]
                raise ValueError("selection candidate must belong to the selected target")
            reference_purposes = {
                "character_reference",
                "scene_reference",
                "prop_reference",
                "voice_reference",
                "style_reference",
            }
            if target.entity_type == "shot" and item.purpose in reference_purposes:
                raise ValueError("shot selection cannot use a continuity reference purpose")
            if target.entity_type == "continuity_state" and item.purpose not in reference_purposes:
                raise ValueError("continuity selection requires a continuity reference purpose")
            if item.lifecycle_state in ("approved", "locked") and (
                candidate.lifecycle_state not in ("approved", "locked")
                or candidate.review_state != "approved"
            ):
                raise ValueError("approved or locked selection requires a valid approved candidate")
        for item in self.review_decisions:
            require(item.subject_ref)
        proposal_index = {item.as_ref(): item for item in self.agent_proposals}
        for item in self.agent_proposals:
            target = require(item.target_ref)
            if len(item.impact_refs) != len(set(item.impact_refs)):
                raise ValueError("proposal impact refs must be unique exact shot refs")
            if len(item.applied_refs) != len(set(item.applied_refs)):
                raise ValueError("proposal applied refs must be unique exact shot refs")

            impact_shots = [require(ref, ("shot",)) for ref in item.impact_refs]
            applied_shots = [require(ref, ("shot",)) for ref in item.applied_refs]
            for shot in (*impact_shots, *applied_shots):
                if shot.scope != item.scope:
                    raise ValueError("proposal operation facts must share exact org, project, and actor scope")

            if item.decision_state in (
                "pending",
                "accepted",
                "partially_accepted",
                "rejected",
            ):
                if item.applied_refs:
                    raise ValueError("proposal decision state cannot claim applied successor refs")
                continue

            if not item.applied_refs:
                raise ValueError("executed or undone proposal requires applied successor refs")
            if target.entity_type != "continuity_state":
                raise ValueError("proposal application target must be an exact continuity state")
            if target.scope != item.scope:
                raise ValueError("proposal target must share exact org, project, and actor scope")

            impact_by_entity = {shot.entity_id: shot for shot in impact_shots}
            if len(impact_by_entity) != len(impact_shots):
                raise ValueError("proposal impact refs must name distinct shot entities")
            applied_by_entity = {shot.entity_id: shot for shot in applied_shots}
            if len(applied_by_entity) != len(applied_shots):
                raise ValueError("proposal applied refs must name distinct shot entities")
            if item.decision_state == "executed":
                if not set(applied_by_entity).issubset(impact_by_entity):
                    raise ValueError("executed proposal applied scope must be a subset of predicted impact")
                if target.parent_version_id is None:
                    raise ValueError("executed continuity target must have an exact parent version")
                old_continuity_ref = EntityVersionRef(
                    entity_type="continuity_state",
                    entity_id=target.entity_id,
                    version_id=target.parent_version_id,
                )
                old_continuity = require(old_continuity_ref, ("continuity_state",))
                if old_continuity.scope != item.scope:
                    raise ValueError(
                        "proposal continuity history must share exact org, project, and actor scope"
                    )
                if any(
                    shot.continuity_refs.count(old_continuity_ref) != 1
                    or item.target_ref in shot.continuity_refs
                    for shot in impact_shots
                ):
                    raise ValueError(
                        "every predicted impact shot must contain the exact parent continuity ref"
                    )
            else:
                if item.parent_version_id is None:
                    raise ValueError("undone proposal must identify its executed parent revision")
                parent_ref = EntityVersionRef(
                    entity_type="agent_proposal",
                    entity_id=item.entity_id,
                    version_id=item.parent_version_id,
                )
                parent_proposal = proposal_index.get(parent_ref)
                if parent_proposal is None or parent_proposal.decision_state != "executed":
                    raise ValueError("undone proposal parent must be an exact executed proposal revision")
                if set(item.impact_refs) != set(parent_proposal.applied_refs):
                    raise ValueError("undone proposal impact must exactly equal its parent applied scope")
                if set(applied_by_entity) != set(impact_by_entity):
                    raise ValueError("undone proposal must restore the full parent applied scope")
                parent_target = require(parent_proposal.target_ref, ("continuity_state",))
                if parent_target.parent_version_id is None:
                    raise ValueError("executed parent target must have an exact continuity parent")
                old_continuity_ref = parent_proposal.target_ref
                expected_restore_ref = EntityVersionRef(
                    entity_type="continuity_state",
                    entity_id=parent_target.entity_id,
                    version_id=parent_target.parent_version_id,
                )
                if item.target_ref != expected_restore_ref:
                    raise ValueError("undone proposal must target the exact prior continuity version")

            for entity_id, successor in applied_by_entity.items():
                parent = impact_by_entity[entity_id]
                if successor.parent_version_id != parent.version_id:
                    raise ValueError("applied shot must be the exact successor of its impact ref")
                if successor.source_proposal_ref != item.as_ref():
                    raise ValueError("applied shot must identify its exact source proposal revision")
                if successor.lifecycle_state != "candidate" or successor.review_state != "needs_review":
                    raise ValueError("applied shot successor must require creator review")
                if (
                    successor.scene_ref != parent.scene_ref
                    or successor.sequence != parent.sequence
                    or successor.duration_seconds != parent.duration_seconds
                    or successor.source_refs != parent.source_refs
                ):
                    raise ValueError("continuity application cannot change non-continuity shot facts")
                if old_continuity_ref not in parent.continuity_refs:
                    raise ValueError("impact shot must contain the exact replaced continuity ref")
                expected_continuity_refs = tuple(
                    item.target_ref if ref == old_continuity_ref else ref
                    for ref in parent.continuity_refs
                )
                if successor.continuity_refs != expected_continuity_refs:
                    raise ValueError("applied shot must only replace the exact continuity ref")

        for shot in self.shots:
            if shot.parent_version_id is not None:
                parent_ref = EntityVersionRef(
                    entity_type="shot",
                    entity_id=shot.entity_id,
                    version_id=shot.parent_version_id,
                )
                parent_shot = require(parent_ref, ("shot",))
                continuity_changed = (
                    shot.continuity_refs
                    != parent_shot.continuity_refs  # type: ignore[attr-defined]
                )
                if continuity_changed and shot.source_proposal_ref is None:
                    raise ValueError(
                        "shot continuity change requires an exact source proposal"
                    )
                if not continuity_changed and shot.source_proposal_ref is not None:
                    raise ValueError(
                        "shot with unchanged continuity cannot claim a source proposal"
                    )

            if shot.source_proposal_ref is None:
                continue
            proposal = proposal_index.get(shot.source_proposal_ref)
            if proposal is None:
                raise ValueError("shot source proposal ref must resolve inside the aggregate")
            if shot.scope != proposal.scope:
                raise ValueError("shot and source proposal must share exact org, project, and actor scope")
            if shot.as_ref() not in proposal.applied_refs:
                raise ValueError("shot source proposal membership must be bidirectionally exact")

        for proposal in self.agent_proposals:
            owned_refs = {
                shot.as_ref()
                for shot in self.shots
                if shot.source_proposal_ref == proposal.as_ref()
            }
            if owned_refs != set(proposal.applied_refs):
                raise ValueError("proposal applied membership must be bidirectionally exact")
        for item in self.deliveries:
            delivery_episode = require(item.episode_ref, ("episode",))
            selections = [require(ref, ("selected_version",)) for ref in item.selection_refs]
            decisions = [require(ref, ("review_decision",)) for ref in item.review_decision_refs]
            for selection in selections:
                target = require(selection.target_ref)  # type: ignore[attr-defined]
                if target.entity_type == "shot":
                    scene = require(target.scene_ref, ("scene",))  # type: ignore[attr-defined]
                    if scene.episode_ref != delivery_episode.as_ref():  # type: ignore[attr-defined]
                        raise ValueError("delivery selection must belong to the delivery episode")
                elif target.entity_type == "continuity_state":
                    episode_shot_refs = {
                        shot.as_ref()
                        for shot in self.shots
                        if require(shot.scene_ref, ("scene",)).episode_ref  # type: ignore[attr-defined]
                        == delivery_episode.as_ref()
                    }
                    if not any(
                        target.as_ref() in shot.continuity_refs
                        for shot in self.shots
                        if shot.as_ref() in episode_shot_refs
                    ):
                        raise ValueError(
                            "delivery continuity selection must be referenced by a shot in the episode"
                        )
            if item.lifecycle_state == "locked":
                if item.preview_artifact_ref is None:
                    raise ValueError("locked delivery requires a playable preview artifact")
                if not selections or any(selection.lifecycle_state != "locked" for selection in selections):
                    raise ValueError("locked delivery requires locked selections")
                approved_subjects = {
                    decision.subject_ref
                    for decision in decisions
                    if decision.decision == "approve"
                    and decision.lifecycle_state in ("approved", "locked")
                    and decision.review_state == "approved"
                    and datetime.fromisoformat(decision.created_at)
                    >= datetime.fromisoformat(
                        require(decision.subject_ref, ("selected_version",)).created_at
                    )
                    and datetime.fromisoformat(decision.created_at)
                    <= datetime.fromisoformat(item.created_at)
                }
                if any(selection.as_ref() not in approved_subjects for selection in selections):
                    raise ValueError("locked delivery requires an approval decision for every selection")

        provider_keys = [(item.provider_id, item.surface) for item in self.provider_contracts]
        if len(provider_keys) != len(set(provider_keys)):
            raise ValueError("provider data contracts must be unique by provider and surface")
        return self

    def _records(self) -> tuple[VersionedFact, ...]:
        return (
            *self.projects,
            *self.series,
            *self.episodes,
            *self.scenes,
            *self.shots,
            *self.continuity_states,
            *self.asset_candidates,
            *self.selections,
            *self.review_decisions,
            *self.agent_proposals,
            *self.deliveries,
        )


ALLOWED_LIFECYCLE_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    "draft": frozenset({"candidate", "retired"}),
    "candidate": frozenset({"approved", "rejected", "retired"}),
    "approved": frozenset({"candidate", "locked", "retired"}),
    "locked": frozenset({"approved", "retired"}),
    "rejected": frozenset({"candidate", "retired"}),
    "retired": frozenset(),
}


def is_lifecycle_transition_allowed(current: LifecycleState, target: LifecycleState) -> bool:
    return target in ALLOWED_LIFECYCLE_TRANSITIONS[current]


__all__ = (
    "AggregateMutationCommand",
    "AgentProposal",
    "AssetCandidateVersion",
    "ConsentRecord",
    "ContinuityStateVersion",
    "DeliveryVersion",
    "EntityVersionRef",
    "EpisodeVersion",
    "EPISODE_PRODUCTION_CONTRACT_REVISION",
    "EPISODE_PRODUCTION_AGGREGATE_SCHEMA_VERSION",
    "ProductionProjectAggregate",
    "ProjectDataPolicy",
    "ProjectVersion",
    "ProviderDataContract",
    "ReviewDecision",
    "SafeArtifactRef",
    "SceneVersion",
    "SelectedVersion",
    "SeriesVersion",
    "ShotVersion",
    "SourceEvidenceRef",
    "TenantScope",
    "is_lifecycle_transition_allowed",
)
