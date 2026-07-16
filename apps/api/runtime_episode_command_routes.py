from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, Union

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.api.runtime_auth import RuntimeAuthStore
from apps.api.runtime_episode_continuity_service import (
    ContinuityServiceError,
    apply_change,
    plan_change,
    undo_change,
)
from apps.api.runtime_episode_authoring_service import (
    AuthoringReferenceError,
    AuthoringScopeError,
    AuthoringStateError,
    AuthoringVersionConflictError,
    create_authoring_entity,
    reorder_authoring_entities,
    restore_shot_as_new,
    revise_authoring_entity,
    revise_shot_intent,
)
from apps.api.runtime_episode_creator_workflow_service import (
    CreatorWorkflowReferenceError,
    CreatorWorkflowScopeError,
    CreatorWorkflowStateError,
    CreatorWorkflowVersionConflictError,
    reassign_shot_scene,
    review_shot_candidate,
    select_shot_candidate_if_ready,
)
from apps.api.runtime_episode_domain_contract import (
    SAFE_ID,
    EntityVersionRef,
    ProductionProjectAggregate,
    SafeArtifactRef,
    SourceEvidenceRef,
    TenantScope,
)
from apps.api.runtime_episode_domain_routes import (
    EpisodeProductionProjectAggregate,
    EpisodeSafeArtifactRef,
    IdempotencyKey,
    _raise_api_error,
    _raise_store_error,
    _require_project_scope,
    _safe_aggregate_payload,
)
from apps.api.runtime_episode_domain_store import (
    EpisodeDomainAggregateStore,
    EpisodeDomainStoreError,
)
from apps.api.runtime_episode_review_delivery_service import (
    ArtifactAvailabilityProof,
    DeliveryNotReadyError,
    ReviewDeliveryReferenceError,
    ReviewDeliveryScopeError,
    ReviewDeliveryStateError,
    ReviewDeliveryVersionConflictError,
    freeze_delivery,
    lock_selection,
    request_selection_revision,
    restore_selection,
    review_selection,
    unlock_delivery,
    unlock_selection,
)
from apps.api.runtime_store import RuntimeStore


class EpisodeCommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EpisodeCommandBase(EpisodeCommandModel):
    expected_aggregate_version: int = Field(ge=1, strict=True)


class SeriesCreatePayload(EpisodeCommandModel):
    entity_type: Literal["series"]
    project_ref: EntityVersionRef
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    creative_intent: str = Field(default="", max_length=4000)


class StoryBibleCreatePayload(EpisodeCommandModel):
    entity_type: Literal["story_bible"]
    project_ref: EntityVersionRef
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=8000)
    world_rules: tuple[str, ...] = Field(default_factory=tuple, max_length=256)


class ArcCreatePayload(EpisodeCommandModel):
    entity_type: Literal["arc"]
    series_ref: EntityVersionRef
    story_bible_ref: EntityVersionRef | None = None
    sequence: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    creative_intent: str = Field(default="", max_length=4000)


class EpisodeCreatePayload(EpisodeCommandModel):
    entity_type: Literal["episode"]
    series_ref: EntityVersionRef
    arc_ref: EntityVersionRef | None = None
    sequence: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    creative_intent: str = Field(default="", max_length=4000)
    reference_set_ref: EntityVersionRef | None = None


class SceneCreatePayload(EpisodeCommandModel):
    entity_type: Literal["scene"]
    episode_ref: EntityVersionRef
    sequence: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    creative_intent: str = Field(default="", max_length=4000)
    reference_set_ref: EntityVersionRef | None = None


class ShotCreatePayload(EpisodeCommandModel):
    entity_type: Literal["shot"]
    scene_ref: EntityVersionRef
    sequence: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    creative_intent: str = Field(default="", max_length=4000)
    duration_seconds: float = Field(gt=0, le=3600)
    reference_set_ref: EntityVersionRef | None = None


class ReferenceAssetCreatePayload(EpisodeCommandModel):
    entity_type: Literal["reference_asset"]
    project_ref: EntityVersionRef
    asset_kind: Literal["human", "animal", "scene", "location", "prop", "style", "voice"]
    label: str = Field(min_length=1, max_length=200)
    identity: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    approval_state: Literal["pending_human", "approved", "rejected"] = "pending_human"
    human_confirmed: bool = False
    source_refs: tuple[SourceEvidenceRef, ...] = Field(default_factory=tuple, max_length=64)


class ReferenceSetCreatePayload(EpisodeCommandModel):
    entity_type: Literal["reference_set"]
    project_ref: EntityVersionRef
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    scope_kind: Literal["project", "series", "arc", "episode", "scene", "shot"] = "project"
    scope_refs: tuple[EntityVersionRef, ...] = Field(default_factory=tuple, max_length=256)
    asset_refs: tuple[EntityVersionRef, ...] = Field(default_factory=tuple, max_length=256)
    approval_state: Literal["pending_human", "approved", "rejected"] = "pending_human"
    human_confirmed: bool = False


AuthoringCreatePayload = Annotated[
    Union[
        SeriesCreatePayload,
        StoryBibleCreatePayload,
        ArcCreatePayload,
        EpisodeCreatePayload,
        SceneCreatePayload,
        ShotCreatePayload,
        ReferenceAssetCreatePayload,
        ReferenceSetCreatePayload,
    ],
    Field(discriminator="entity_type"),
]


class AuthoringCreateCommand(EpisodeCommandBase):
    action: Literal["authoring.create"]
    entity_id: str = Field(pattern=SAFE_ID)
    version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    entity: AuthoringCreatePayload


class ProjectEditPayload(EpisodeCommandModel):
    entity_type: Literal["project"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    creative_intent: str | None = Field(default=None, max_length=4000)
    ip_profile: str | None = Field(default=None, max_length=4000)


class SeriesEditPayload(EpisodeCommandModel):
    entity_type: Literal["series"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    creative_intent: str | None = Field(default=None, max_length=4000)


class StoryBibleEditPayload(EpisodeCommandModel):
    entity_type: Literal["story_bible"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=8000)
    world_rules: tuple[str, ...] | None = Field(default=None, max_length=256)


class ArcEditPayload(EpisodeCommandModel):
    entity_type: Literal["arc"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    creative_intent: str | None = Field(default=None, max_length=4000)


class EpisodeEditPayload(EpisodeCommandModel):
    entity_type: Literal["episode"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    creative_intent: str | None = Field(default=None, max_length=4000)
    reference_set_ref: EntityVersionRef | None = None


class SceneEditPayload(EpisodeCommandModel):
    entity_type: Literal["scene"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    creative_intent: str | None = Field(default=None, max_length=4000)
    reference_set_ref: EntityVersionRef | None = None


class ReferenceAssetEditPayload(EpisodeCommandModel):
    entity_type: Literal["reference_asset"]
    label: str | None = Field(default=None, min_length=1, max_length=200)
    identity: str | None = Field(default=None, min_length=1, max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    approval_state: Literal["pending_human", "approved", "rejected"] | None = None
    human_confirmed: bool | None = None


class ReferenceSetEditPayload(EpisodeCommandModel):
    entity_type: Literal["reference_set"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    scope_kind: Literal["project", "series", "arc", "episode", "scene", "shot"] | None = None
    scope_refs: tuple[EntityVersionRef, ...] | None = Field(default=None, max_length=256)
    asset_refs: tuple[EntityVersionRef, ...] | None = Field(default=None, max_length=256)
    approval_state: Literal["pending_human", "approved", "rejected"] | None = None
    human_confirmed: bool | None = None


AuthoringEditPayload = Annotated[
    Union[
        ProjectEditPayload,
        SeriesEditPayload,
        StoryBibleEditPayload,
        ArcEditPayload,
        EpisodeEditPayload,
        SceneEditPayload,
        ReferenceAssetEditPayload,
        ReferenceSetEditPayload,
    ],
    Field(discriminator="entity_type"),
]


class AuthoringReviseCommand(EpisodeCommandBase):
    action: Literal["authoring.revise"]
    target_ref: EntityVersionRef
    new_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    changes: AuthoringEditPayload


class AuthoringReorderCommand(EpisodeCommandBase):
    action: Literal["authoring.reorder"]
    ordered_refs: tuple[EntityVersionRef, ...] = Field(min_length=1, max_length=4096)
    new_version_ids: tuple[str, ...] = Field(min_length=1, max_length=4096)
    created_at: str = Field(min_length=1, max_length=64)


class ShotCreativePatch(EpisodeCommandModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    creative_intent: str | None = Field(default=None, max_length=4000)
    duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    reference_set_ref: EntityVersionRef | None = None


class ShotReviseIntentCommand(EpisodeCommandBase):
    action: Literal["shot.revise_intent"]
    shot_ref: EntityVersionRef
    new_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    changes: ShotCreativePatch
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed_direct_refs: tuple[EntityVersionRef, ...] = Field(max_length=128)
    confirmed_transitive_refs: tuple[EntityVersionRef, ...] = Field(max_length=4096)
    confirmed_protected_refs: tuple[EntityVersionRef, ...] = Field(max_length=65536)


class ShotRestoreCommand(EpisodeCommandBase):
    action: Literal["shot.restore"]
    historical_ref: EntityVersionRef
    current_ref: EntityVersionRef
    new_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed_direct_refs: tuple[EntityVersionRef, ...] = Field(max_length=128)
    confirmed_transitive_refs: tuple[EntityVersionRef, ...] = Field(max_length=4096)
    confirmed_protected_refs: tuple[EntityVersionRef, ...] = Field(max_length=65536)


class ShotReassignSceneCommand(EpisodeCommandBase):
    action: Literal["shot.reassign_scene"]
    shot_ref: EntityVersionRef
    scene_ref: EntityVersionRef
    new_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)


class ShotReviewCommand(EpisodeCommandBase):
    action: Literal["shot.review"]
    shot_ref: EntityVersionRef
    decision: Literal["approve", "reject"]
    shot_version_id: str = Field(pattern=SAFE_ID)
    decision_entity_id: str = Field(pattern=SAFE_ID)
    decision_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


class ContinuityApplyCommand(EpisodeCommandBase):
    action: Literal["continuity.apply"]
    old_continuity_ref: EntityVersionRef
    new_version_id: str = Field(pattern=SAFE_ID)
    proposal_entity_id: str = Field(pattern=SAFE_ID)
    planned_at: str = Field(min_length=1, max_length=64)
    applied_at: str = Field(min_length=1, max_length=64)
    identity_baseline: tuple[str, ...] | None = Field(default=None, max_length=128)
    temporary_state: tuple[str, ...] | None = Field(default=None, max_length=128)
    prohibited_changes: tuple[str, ...] | None = Field(default=None, max_length=128)
    selected_shot_refs: tuple[EntityVersionRef, ...] = Field(max_length=4096)


class ContinuityUndoCommand(EpisodeCommandBase):
    action: Literal["continuity.undo"]
    proposal_ref: EntityVersionRef
    created_at: str = Field(min_length=1, max_length=64)


class CandidateSelectCommand(EpisodeCommandBase):
    action: Literal["candidate.select"]
    target_shot_ref: EntityVersionRef
    candidate_ref: EntityVersionRef
    purpose: Literal["storyboard", "image", "video", "audio"]
    selection_entity_id: str = Field(pattern=SAFE_ID)
    selection_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)


class SelectionReviewCommand(EpisodeCommandBase):
    action: Literal["selection.review"]
    selection_ref: EntityVersionRef
    decision: Literal["approve", "reject"]
    selection_version_id: str = Field(pattern=SAFE_ID)
    decision_entity_id: str = Field(pattern=SAFE_ID)
    decision_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


class SelectionLockCommand(EpisodeCommandBase):
    action: Literal["selection.lock"]
    selection_ref: EntityVersionRef
    selection_version_id: str = Field(pattern=SAFE_ID)
    decision_entity_id: str = Field(pattern=SAFE_ID)
    decision_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


class SelectionUnlockCommand(SelectionLockCommand):
    action: Literal["selection.unlock"]


class SelectionRequestRevisionCommand(EpisodeCommandBase):
    action: Literal["selection.request_revision"]
    selection_ref: EntityVersionRef
    selection_version_id: str = Field(pattern=SAFE_ID)
    decision_entity_id: str = Field(pattern=SAFE_ID)
    decision_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)
    unlock_decision_entity_id: str | None = Field(default=None, pattern=SAFE_ID)
    unlock_decision_version_id: str | None = Field(default=None, pattern=SAFE_ID)


class SelectionRestoreCommand(EpisodeCommandBase):
    action: Literal["selection.restore"]
    selection_ref: EntityVersionRef
    historical_candidate_ref: EntityVersionRef
    selection_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)


class EpisodeArtifactAvailabilityProof(EpisodeCommandModel):
    artifact_ref: EpisodeSafeArtifactRef
    verification_id: str = Field(pattern=SAFE_ID)
    available: bool
    playable: bool = False


class DeliveryFreezeCommand(EpisodeCommandBase):
    action: Literal["delivery.freeze"]
    episode_ref: EntityVersionRef
    selection_refs: tuple[EntityVersionRef, ...] = Field(max_length=4096)
    missing_inventory_count: int = Field(ge=0, strict=True)
    preview_artifact_ref: EpisodeSafeArtifactRef | None = None
    export_artifact_refs: tuple[EpisodeSafeArtifactRef, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    artifact_proofs: tuple[EpisodeArtifactAvailabilityProof, ...] = Field(
        default_factory=tuple,
        max_length=65,
    )
    delivery_entity_id: str = Field(pattern=SAFE_ID)
    delivery_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)


class DeliveryUnlockCommand(EpisodeCommandBase):
    action: Literal["delivery.unlock"]
    delivery_ref: EntityVersionRef
    delivery_version_id: str = Field(pattern=SAFE_ID)
    decision_entity_id: str = Field(pattern=SAFE_ID)
    decision_version_id: str = Field(pattern=SAFE_ID)
    created_at: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


EpisodeCommandRequest = Annotated[
    Union[
        AuthoringCreateCommand,
        AuthoringReviseCommand,
        AuthoringReorderCommand,
        ShotReviseIntentCommand,
        ShotRestoreCommand,
        ShotReassignSceneCommand,
        ShotReviewCommand,
        ContinuityApplyCommand,
        ContinuityUndoCommand,
        CandidateSelectCommand,
        SelectionReviewCommand,
        SelectionLockCommand,
        SelectionUnlockCommand,
        SelectionRequestRevisionCommand,
        SelectionRestoreCommand,
        DeliveryFreezeCommand,
        DeliveryUnlockCommand,
    ],
    Field(discriminator="action"),
]


class EpisodeCommandResponse(EpisodeCommandModel):
    aggregate: EpisodeProductionProjectAggregate
    aggregate_version: int = Field(ge=2, strict=True)
    replayed: bool
    command_id: str = Field(
        pattern=SAFE_ID,
        description=(
            "The Idempotency-Key value. It is the sole durable identity of this command; "
            "there is no separate client-supplied command id."
        ),
    )


def register_runtime_episode_command_routes(
    app: FastAPI,
    store: RuntimeStore,
    auth: RuntimeAuthStore,
) -> None:
    aggregate_store = EpisodeDomainAggregateStore(store.root)

    @app.post(
        "/projects/{project_id}/episode-production-aggregate/commands",
        response_model=EpisodeCommandResponse,
    )
    def execute_episode_command(
        project_id: str,
        body: EpisodeCommandRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        payload_digest = _command_digest(scope, body)
        try:
            aggregate = aggregate_store.load(org_id=scope.org_id, project_id=project_id)
            if aggregate.scope != scope:
                _raise_command_error(
                    request,
                    project_id,
                    status_code=403,
                    error="episode_command_scope_mismatch",
                    message="Episode command scope does not match the project owner.",
                    stage="episode_command_scope",
                )
            if body.expected_aggregate_version != aggregate.aggregate_version:
                # The store checks a durable idempotency receipt before CAS. Passing
                # the current validated aggregate lets an exact retry replay after a
                # successful command advanced the version, while a new stale key
                # still fails closed at the store's version check.
                result = aggregate_store.save(
                    aggregate,
                    expected_aggregate_version=body.expected_aggregate_version,
                    idempotency_key=idempotency_key,
                    payload_digest=payload_digest,
                )
            else:
                changed = _execute_command(aggregate, scope=scope, command=body)
                # Safety is part of the mutation boundary, not only the response
                # projection. Reject unsafe creator text before the atomic store
                # can advance the aggregate or record an idempotency receipt.
                _safe_aggregate_payload(
                    changed,
                    request=request,
                    project_id=project_id,
                    status_code=422,
                    stage="episode_command_validation",
                )
                result = aggregate_store.save(
                    changed,
                    expected_aggregate_version=body.expected_aggregate_version,
                    idempotency_key=idempotency_key,
                    payload_digest=payload_digest,
                )
        except EpisodeDomainStoreError as exc:
            _raise_store_error(exc, request=request, project_id=project_id)
        except CreatorWorkflowScopeError as exc:
            _raise_service_error(request, project_id, exc, category="scope")
        except AuthoringScopeError as exc:
            _raise_service_error(request, project_id, exc, category="scope")
        except (
            AuthoringVersionConflictError,
            CreatorWorkflowVersionConflictError,
            ReviewDeliveryVersionConflictError,
        ) as exc:
            _raise_service_error(request, project_id, exc, category="version")
        except (
            AuthoringReferenceError,
            CreatorWorkflowReferenceError,
            ReviewDeliveryReferenceError,
        ) as exc:
            _raise_service_error(request, project_id, exc, category="reference")
        except (
            AuthoringStateError,
            CreatorWorkflowStateError,
            ReviewDeliveryStateError,
            ContinuityServiceError,
        ) as exc:
            _raise_service_error(request, project_id, exc, category="state")
        except ReviewDeliveryScopeError as exc:
            _raise_service_error(request, project_id, exc, category="scope")
        except DeliveryNotReadyError as exc:
            _raise_service_error(request, project_id, exc, category="delivery")
        except (ValidationError, ValueError) as exc:
            _raise_command_error(
                request,
                project_id,
                status_code=422,
                error="episode_command_invalid",
                message="Episode command data is invalid.",
                stage="episode_command_validation",
                cause=exc,
            )

        aggregate_payload = _safe_aggregate_payload(
            result.aggregate,
            request=request,
            project_id=project_id,
            status_code=500,
            stage="episode_command_projection",
        )
        return {
            "aggregate": aggregate_payload,
            "aggregate_version": result.aggregate.aggregate_version,
            "replayed": result.replayed,
            "command_id": idempotency_key,
        }


def _execute_command(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    command: EpisodeCommandRequest,
) -> ProductionProjectAggregate:
    common = {
        "scope": scope,
        "expected_aggregate_version": command.expected_aggregate_version,
    }
    if isinstance(command, AuthoringCreateCommand):
        attributes = _typed_model_values(command.entity, exclude={"entity_type"})
        return create_authoring_entity(
            aggregate,
            **common,
            entity_type=command.entity.entity_type,
            entity_id=command.entity_id,
            version_id=command.version_id,
            created_at=command.created_at,
            attributes=attributes,
        )
    if isinstance(command, AuthoringReviseCommand):
        if command.target_ref.entity_type != command.changes.entity_type:
            raise AuthoringReferenceError("revision target and typed changes disagree")
        changes = _typed_model_values(
            command.changes,
            exclude={"entity_type"},
            only_set=True,
        )
        return revise_authoring_entity(
            aggregate,
            **common,
            target_ref=command.target_ref,
            new_version_id=command.new_version_id,
            created_at=command.created_at,
            changes=changes,
        )
    if isinstance(command, AuthoringReorderCommand):
        return reorder_authoring_entities(
            aggregate,
            **common,
            ordered_refs=command.ordered_refs,
            new_version_ids=command.new_version_ids,
            created_at=command.created_at,
        )
    if isinstance(command, ShotReviseIntentCommand):
        changes = _typed_model_values(command.changes, only_set=True)
        return revise_shot_intent(
            aggregate,
            **common,
            shot_ref=command.shot_ref,
            new_version_id=command.new_version_id,
            created_at=command.created_at,
            proposed_changes=changes,
            preview_digest=command.preview_digest,
            confirmed_direct_refs=command.confirmed_direct_refs,
            confirmed_transitive_refs=command.confirmed_transitive_refs,
            confirmed_protected_refs=command.confirmed_protected_refs,
        )
    if isinstance(command, ShotRestoreCommand):
        return restore_shot_as_new(
            aggregate,
            **common,
            historical_ref=command.historical_ref,
            current_ref=command.current_ref,
            new_version_id=command.new_version_id,
            created_at=command.created_at,
            preview_digest=command.preview_digest,
            confirmed_direct_refs=command.confirmed_direct_refs,
            confirmed_transitive_refs=command.confirmed_transitive_refs,
            confirmed_protected_refs=command.confirmed_protected_refs,
        )
    if isinstance(command, ShotReassignSceneCommand):
        return reassign_shot_scene(
            aggregate,
            **common,
            shot_ref=command.shot_ref,
            scene_ref=command.scene_ref,
            new_version_id=command.new_version_id,
            created_at=command.created_at,
        )
    if isinstance(command, ShotReviewCommand):
        return review_shot_candidate(
            aggregate,
            **common,
            shot_ref=command.shot_ref,
            decision=command.decision,
            shot_version_id=command.shot_version_id,
            decision_entity_id=command.decision_entity_id,
            decision_version_id=command.decision_version_id,
            created_at=command.created_at,
            note=command.note,
        )
    if isinstance(command, ContinuityApplyCommand):
        plan = plan_change(
            aggregate,
            **common,
            old_continuity_ref=command.old_continuity_ref,
            new_version_id=command.new_version_id,
            proposal_entity_id=command.proposal_entity_id,
            created_at=command.planned_at,
            identity_baseline=command.identity_baseline,
            temporary_state=command.temporary_state,
            prohibited_changes=command.prohibited_changes,
        )
        return apply_change(
            aggregate,
            plan,
            **common,
            selected_shot_refs=command.selected_shot_refs,
            created_at=command.applied_at,
        )
    if isinstance(command, ContinuityUndoCommand):
        return undo_change(
            aggregate,
            **common,
            proposal_ref=command.proposal_ref,
            created_at=command.created_at,
        )
    if isinstance(command, CandidateSelectCommand):
        return select_shot_candidate_if_ready(
            aggregate,
            **common,
            target_shot_ref=command.target_shot_ref,
            candidate_ref=command.candidate_ref,
            purpose=command.purpose,
            selection_entity_id=command.selection_entity_id,
            selection_version_id=command.selection_version_id,
            created_at=command.created_at,
        )
    if isinstance(command, SelectionReviewCommand):
        return review_selection(
            aggregate,
            **common,
            selection_ref=command.selection_ref,
            decision=command.decision,
            selection_version_id=command.selection_version_id,
            decision_entity_id=command.decision_entity_id,
            decision_version_id=command.decision_version_id,
            created_at=command.created_at,
            note=command.note,
        )
    if isinstance(command, SelectionLockCommand) and not isinstance(
        command, SelectionUnlockCommand
    ):
        return lock_selection(
            aggregate,
            **common,
            selection_ref=command.selection_ref,
            selection_version_id=command.selection_version_id,
            decision_entity_id=command.decision_entity_id,
            decision_version_id=command.decision_version_id,
            created_at=command.created_at,
            note=command.note,
        )
    if isinstance(command, SelectionUnlockCommand):
        return unlock_selection(
            aggregate,
            **common,
            selection_ref=command.selection_ref,
            selection_version_id=command.selection_version_id,
            decision_entity_id=command.decision_entity_id,
            decision_version_id=command.decision_version_id,
            created_at=command.created_at,
            note=command.note,
        )
    if isinstance(command, SelectionRequestRevisionCommand):
        return request_selection_revision(
            aggregate,
            **common,
            selection_ref=command.selection_ref,
            selection_version_id=command.selection_version_id,
            decision_entity_id=command.decision_entity_id,
            decision_version_id=command.decision_version_id,
            created_at=command.created_at,
            note=command.note,
            unlock_decision_entity_id=command.unlock_decision_entity_id,
            unlock_decision_version_id=command.unlock_decision_version_id,
        )
    if isinstance(command, SelectionRestoreCommand):
        return restore_selection(
            aggregate,
            **common,
            selection_ref=command.selection_ref,
            historical_candidate_ref=command.historical_candidate_ref,
            selection_version_id=command.selection_version_id,
            created_at=command.created_at,
        )
    if isinstance(command, DeliveryFreezeCommand):
        preview_artifact_ref = (
            _contract_artifact(command.preview_artifact_ref)
            if command.preview_artifact_ref is not None
            else None
        )
        export_artifact_refs = tuple(
            _contract_artifact(ref) for ref in command.export_artifact_refs
        )
        return freeze_delivery(
            aggregate,
            **common,
            episode_ref=command.episode_ref,
            selection_refs=command.selection_refs,
            missing_inventory_count=command.missing_inventory_count,
            preview_artifact_ref=preview_artifact_ref,
            export_artifact_refs=export_artifact_refs,
            artifact_proofs=tuple(
                ArtifactAvailabilityProof(
                    artifact_ref=_contract_artifact(proof.artifact_ref),
                    verification_id=proof.verification_id,
                    available=proof.available,
                    playable=proof.playable,
                )
                for proof in command.artifact_proofs
            ),
            delivery_entity_id=command.delivery_entity_id,
            delivery_version_id=command.delivery_version_id,
            created_at=command.created_at,
        )
    if isinstance(command, DeliveryUnlockCommand):
        return unlock_delivery(
            aggregate,
            **common,
            delivery_ref=command.delivery_ref,
            delivery_version_id=command.delivery_version_id,
            decision_entity_id=command.decision_entity_id,
            decision_version_id=command.decision_version_id,
            created_at=command.created_at,
            note=command.note,
        )
    raise AssertionError("unsupported typed episode command")


def _contract_artifact(ref: EpisodeSafeArtifactRef) -> SafeArtifactRef:
    """Drop route-local schema identity before exact business-ref comparison."""

    return SafeArtifactRef.model_validate(ref.model_dump(mode="json"))


def _typed_model_values(
    model: BaseModel,
    *,
    exclude: set[str] | None = None,
    only_set: bool = False,
) -> dict[str, Any]:
    excluded = exclude or set()
    field_names = type(model).model_fields
    if only_set:
        field_names = {
            name: field
            for name, field in field_names.items()
            if name in model.model_fields_set
        }
    return {
        name: getattr(model, name)
        for name in field_names
        if name not in excluded
    }


def _command_digest(scope: TenantScope, command: EpisodeCommandRequest) -> str:
    canonical = json.dumps(
        {
            "operation": "execute_episode_command",
            "scope": scope.model_dump(mode="json"),
            "command": command.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _raise_service_error(
    request: Request,
    project_id: str,
    exc: Exception,
    *,
    category: Literal["scope", "version", "reference", "state", "delivery"],
) -> None:
    status_code = 403 if category == "scope" else 409
    messages = {
        "scope": ("episode_command_scope_mismatch", "Command scope is not allowed."),
        "version": (
            "episode_command_version_conflict",
            "Episode production state changed. Reload it before retrying.",
        ),
        "reference": (
            "episode_command_reference_conflict",
            "A referenced episode object is missing or no longer current.",
        ),
        "state": (
            "episode_command_state_conflict",
            "This action is not allowed in the current episode state.",
        ),
        "delivery": (
            "episode_delivery_not_ready",
            "The episode is not ready to freeze for delivery.",
        ),
    }
    error, message = messages[category]
    _raise_command_error(
        request,
        project_id,
        status_code=status_code,
        error=error,
        message=message,
        stage="episode_command_execute",
        retryable=category == "version",
        cause=exc,
    )


def _raise_command_error(
    request: Request,
    project_id: str,
    *,
    status_code: int,
    error: str,
    message: str,
    stage: str,
    retryable: bool = False,
    cause: Exception | None = None,
) -> None:
    try:
        _raise_api_error(
            request,
            project_id,
            status_code=status_code,
            error=error,
            message=message,
            stage=stage,
            retryable=retryable,
            cause=cause,
        )
    except HTTPException:
        raise


__all__ = (
    "EpisodeCommandRequest",
    "EpisodeCommandResponse",
    "ShotCreativePatch",
    "register_runtime_episode_command_routes",
)
