from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from apps.api.runtime_episode_domain_contract import (
    EntityVersionRef,
    ProductionProjectAggregate,
    ReviewDecision,
    SceneVersion,
    ShotVersion,
    TenantScope,
)
from apps.api.runtime_episode_review_delivery_service import select_candidate


class EpisodeCreatorWorkflowError(RuntimeError):
    """Base error for deterministic creator workflow operations."""


class CreatorWorkflowScopeError(EpisodeCreatorWorkflowError):
    pass


class CreatorWorkflowVersionConflictError(EpisodeCreatorWorkflowError):
    pass


class CreatorWorkflowReferenceError(EpisodeCreatorWorkflowError):
    pass


class CreatorWorkflowStateError(EpisodeCreatorWorkflowError):
    pass


@dataclass(frozen=True)
class PriorShotBlocker:
    shot_ref: EntityVersionRef
    scene_ref: EntityVersionRef
    sequence: int
    lifecycle_state: str
    review_state: str
    reason: Literal["requires_approved_review"] = "requires_approved_review"


def reassign_shot_scene(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    shot_ref: EntityVersionRef,
    scene_ref: EntityVersionRef,
    new_version_id: str,
    created_at: str,
) -> ProductionProjectAggregate:
    """Append a candidate Shot successor assigned to an exact latest scene."""

    aggregate = _require_mutation(aggregate, scope, expected_aggregate_version)
    shot = _latest_shot(aggregate, shot_ref)
    scene = _latest_scene(aggregate, scene_ref)
    current_scene = _scene(aggregate, shot.scene_ref)
    if shot.lifecycle_state in ("locked", "retired"):
        raise CreatorWorkflowStateError("locked or retired shot cannot be reassigned")
    if scene.episode_ref != current_scene.episode_ref:
        raise CreatorWorkflowScopeError("shot scene reassignment cannot cross an episode")
    if scene.as_ref() == shot.scene_ref:
        raise CreatorWorkflowStateError("shot is already assigned to the exact scene")
    _require_unused_ref(
        aggregate,
        EntityVersionRef(
            entity_type="shot",
            entity_id=shot.entity_id,
            version_id=new_version_id,
        ),
    )
    _require_later(created_at, aggregate.evaluated_at, "workflow mutation")

    successor = shot.model_copy(
        update={
            "version_id": new_version_id,
            "revision": shot.revision + 1,
            "parent_version_id": shot.version_id,
            "lifecycle_state": "candidate",
            "review_state": "needs_review",
            "content_digest": _digest(
                {
                    "scene_ref": scene.as_ref().model_dump(mode="json"),
                    "sequence": shot.sequence,
                    "duration_seconds": shot.duration_seconds,
                    "continuity_refs": [
                        ref.model_dump(mode="json") for ref in shot.continuity_refs
                    ],
                }
            ),
            "scene_ref": scene.as_ref(),
            "source_proposal_ref": None,
            "created_at": created_at,
        }
    )
    return _append(aggregate, evaluated_at=created_at, shots=(successor,))


def review_shot_candidate(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    shot_ref: EntityVersionRef,
    decision: Literal["approve", "reject"],
    shot_version_id: str,
    decision_entity_id: str,
    decision_version_id: str,
    created_at: str,
    note: str = "",
) -> ProductionProjectAggregate:
    """Append an exact reviewed Shot successor and its exact ReviewDecision."""

    aggregate = _require_mutation(aggregate, scope, expected_aggregate_version)
    if decision not in ("approve", "reject"):
        raise CreatorWorkflowStateError("shot review decision must be approve or reject")
    shot = _latest_shot(aggregate, shot_ref)
    if shot.lifecycle_state != "candidate" or shot.review_state != "needs_review":
        raise CreatorWorkflowStateError("only an exact latest shot candidate can be reviewed")
    _require_unused_ref(
        aggregate,
        EntityVersionRef(
            entity_type="shot",
            entity_id=shot.entity_id,
            version_id=shot_version_id,
        ),
    )
    if any(item.entity_id == decision_entity_id for item in aggregate.review_decisions):
        raise CreatorWorkflowStateError("review decision entity id must be unused")
    _require_later(created_at, aggregate.evaluated_at, "workflow mutation")

    lifecycle_state = "approved" if decision == "approve" else "rejected"
    review_state = "approved" if decision == "approve" else "rejected"
    successor = shot.model_copy(
        update={
            "version_id": shot_version_id,
            "revision": shot.revision + 1,
            "parent_version_id": shot.version_id,
            "lifecycle_state": lifecycle_state,
            "review_state": review_state,
            "source_proposal_ref": None,
            "created_at": created_at,
        }
    )
    decision_fact = ReviewDecision(
        entity_id=decision_entity_id,
        version_id=decision_version_id,
        revision=1,
        lifecycle_state="approved",
        review_state="approved",
        content_digest=_digest(
            {
                "subject_ref": successor.as_ref().model_dump(mode="json"),
                "decision": decision,
                "note": note,
            }
        ),
        scope=scope,
        created_at=created_at,
        subject_ref=successor.as_ref(),
        decision=decision,
        note=note,
    )
    _require_unused_ref(aggregate, decision_fact.as_ref())
    return _append(
        aggregate,
        evaluated_at=created_at,
        shots=(successor,),
        review_decisions=(decision_fact,),
    )


def derive_prior_shot_blockers(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    target_shot_ref: EntityVersionRef,
) -> tuple[PriorShotBlocker, ...]:
    """Derive deterministic prior-shot blockers from latest aggregate facts."""

    aggregate = _validated_aggregate(aggregate)
    _require_scope(aggregate, scope)
    target = _latest_shot(aggregate, target_shot_ref)
    target_scene = _scene(aggregate, target.scene_ref)
    episode_shots = _latest_episode_shots(
        aggregate,
        episode_ref=target_scene.episode_ref,
    )
    _require_unique_episode_sequences(episode_shots)
    blockers: list[PriorShotBlocker] = []
    for shot in episode_shots:
        if shot.sequence >= target.sequence:
            continue
        if (
            shot.lifecycle_state in ("approved", "locked")
            and shot.review_state == "approved"
        ):
            continue
        blockers.append(
            PriorShotBlocker(
                shot_ref=shot.as_ref(),
                scene_ref=shot.scene_ref,
                sequence=shot.sequence,
                lifecycle_state=shot.lifecycle_state,
                review_state=shot.review_state,
            )
        )
    return tuple(
        sorted(
            blockers,
            key=lambda item: (
                item.sequence,
                item.shot_ref.entity_id,
                item.shot_ref.version_id,
            ),
        )
    )


def select_shot_candidate_if_ready(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    target_shot_ref: EntityVersionRef,
    candidate_ref: EntityVersionRef,
    purpose: Literal["storyboard", "image", "video", "audio"],
    selection_entity_id: str,
    selection_version_id: str,
    created_at: str,
) -> ProductionProjectAggregate:
    """Use the review service only after all exact prior-shot facts are approved."""

    aggregate = _require_mutation(aggregate, scope, expected_aggregate_version)
    if purpose not in ("storyboard", "image", "video", "audio"):
        raise CreatorWorkflowStateError("shot candidate purpose is not supported")
    target = _latest_shot(aggregate, target_shot_ref)
    blockers = derive_prior_shot_blockers(
        aggregate,
        scope=scope,
        target_shot_ref=target.as_ref(),
    )
    if blockers:
        refs = ", ".join(
            f"{item.shot_ref.entity_id}:{item.shot_ref.version_id}" for item in blockers
        )
        raise CreatorWorkflowStateError(f"prior shots require approved review: {refs}")
    candidate = next(
        (item for item in aggregate.asset_candidates if item.as_ref() == candidate_ref),
        None,
    )
    if candidate is None:
        raise CreatorWorkflowReferenceError("exact asset candidate reference was not found")
    if candidate.target_ref != target.as_ref():
        raise CreatorWorkflowReferenceError(
            "candidate must target the exact latest shot selected by the workflow"
        )
    _require_later(created_at, aggregate.evaluated_at, "workflow mutation")
    return select_candidate(
        aggregate,
        scope=scope,
        expected_aggregate_version=expected_aggregate_version,
        candidate_ref=candidate_ref,
        purpose=purpose,
        selection_entity_id=selection_entity_id,
        selection_version_id=selection_version_id,
        created_at=created_at,
    )


def _require_mutation(
    aggregate: ProductionProjectAggregate,
    scope: TenantScope,
    expected_aggregate_version: int,
) -> ProductionProjectAggregate:
    aggregate = _validated_aggregate(aggregate)
    _require_scope(aggregate, scope)
    if expected_aggregate_version != aggregate.aggregate_version:
        raise CreatorWorkflowVersionConflictError(
            f"aggregate version conflict: expected {expected_aggregate_version}, "
            f"current {aggregate.aggregate_version}"
        )
    return aggregate


def _validated_aggregate(
    aggregate: ProductionProjectAggregate,
) -> ProductionProjectAggregate:
    try:
        return ProductionProjectAggregate.model_validate(
            aggregate.model_dump(mode="python")
        )
    except (AttributeError, ValidationError, ValueError, TypeError) as exc:
        raise CreatorWorkflowStateError(
            "aggregate violates the episode production fact contract"
        ) from exc


def _require_scope(aggregate: ProductionProjectAggregate, scope: TenantScope) -> None:
    if scope != aggregate.scope:
        raise CreatorWorkflowScopeError(
            "creator workflow scope must exactly match org, project, and actor"
        )


def _latest_shot(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
) -> ShotVersion:
    shot = _shot(aggregate, ref)
    latest = max(
        (item for item in aggregate.shots if item.entity_id == shot.entity_id),
        key=lambda item: item.revision,
    )
    if latest.as_ref() != ref:
        raise CreatorWorkflowVersionConflictError(
            "shot ref is not the latest exact version"
        )
    return shot


def _latest_scene(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
) -> SceneVersion:
    scene = _scene(aggregate, ref)
    latest = max(
        (item for item in aggregate.scenes if item.entity_id == scene.entity_id),
        key=lambda item: item.revision,
    )
    if latest.as_ref() != ref:
        raise CreatorWorkflowVersionConflictError(
            "scene ref is not the latest exact version"
        )
    return scene


def _shot(aggregate: ProductionProjectAggregate, ref: EntityVersionRef) -> ShotVersion:
    if ref.entity_type != "shot":
        raise CreatorWorkflowReferenceError("reference must target a shot")
    item = next((item for item in aggregate.shots if item.as_ref() == ref), None)
    if item is None:
        raise CreatorWorkflowReferenceError("exact shot reference was not found")
    return item


def _scene(aggregate: ProductionProjectAggregate, ref: EntityVersionRef) -> SceneVersion:
    if ref.entity_type != "scene":
        raise CreatorWorkflowReferenceError("reference must target a scene")
    item = next((item for item in aggregate.scenes if item.as_ref() == ref), None)
    if item is None:
        raise CreatorWorkflowReferenceError("exact scene reference was not found")
    return item


def _latest_shots(
    aggregate: ProductionProjectAggregate,
) -> tuple[ShotVersion, ...]:
    latest: dict[str, ShotVersion] = {}
    for shot in aggregate.shots:
        current = latest.get(shot.entity_id)
        if current is None or shot.revision > current.revision:
            latest[shot.entity_id] = shot
    return tuple(latest.values())


def _latest_episode_shots(
    aggregate: ProductionProjectAggregate,
    *,
    episode_ref: EntityVersionRef,
) -> tuple[ShotVersion, ...]:
    return tuple(
        shot
        for shot in _latest_shots(aggregate)
        if _scene(aggregate, shot.scene_ref).episode_ref == episode_ref
    )


def _require_unique_episode_sequences(shots: tuple[ShotVersion, ...]) -> None:
    seen: dict[int, EntityVersionRef] = {}
    for shot in shots:
        existing = seen.get(shot.sequence)
        if existing is not None:
            raise CreatorWorkflowStateError(
                "latest shots in one episode must have unique sequence values; "
                f"sequence {shot.sequence} is shared by "
                f"{existing.entity_id}:{existing.version_id} and "
                f"{shot.entity_id}:{shot.version_id}"
            )
        seen[shot.sequence] = shot.as_ref()


def _require_unused_ref(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
) -> None:
    if any(item.as_ref() == ref for item in aggregate._records()):
        raise CreatorWorkflowStateError("new entity version reference must be unused")


def _append(
    aggregate: ProductionProjectAggregate,
    *,
    evaluated_at: str,
    shots: tuple[ShotVersion, ...] = (),
    review_decisions: tuple[ReviewDecision, ...] = (),
) -> ProductionProjectAggregate:
    payload = aggregate.model_dump(mode="python")
    payload.update(
        {
            "aggregate_version": aggregate.aggregate_version + 1,
            "evaluated_at": evaluated_at,
            "shots": (*aggregate.shots, *shots),
            "review_decisions": (*aggregate.review_decisions, *review_decisions),
        }
    )
    try:
        return ProductionProjectAggregate.model_validate(payload)
    except ValidationError as exc:
        raise CreatorWorkflowStateError(
            "workflow result violates the episode production fact contract"
        ) from exc


def _require_later(value: str, previous: str, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CreatorWorkflowStateError(f"{label} timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CreatorWorkflowStateError(f"{label} timestamp must include a timezone")
    if parsed <= datetime.fromisoformat(previous):
        raise CreatorWorkflowStateError(
            f"{label} timestamp must be later than aggregate evaluated_at"
        )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CreatorWorkflowReferenceError",
    "CreatorWorkflowScopeError",
    "CreatorWorkflowStateError",
    "CreatorWorkflowVersionConflictError",
    "EpisodeCreatorWorkflowError",
    "PriorShotBlocker",
    "derive_prior_shot_blockers",
    "reassign_shot_scene",
    "review_shot_candidate",
    "select_shot_candidate_if_ready",
]
