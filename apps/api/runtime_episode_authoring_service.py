from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from apps.api.runtime_episode_domain_contract import (
    ArcVersion,
    EntityVersionRef,
    EpisodeVersion,
    ProductionProjectAggregate,
    ProjectVersion,
    ReferenceAssetVersion,
    ReferenceSetVersion,
    SceneVersion,
    SeriesVersion,
    ShotVersion,
    StoryBibleVersion,
    TenantScope,
    VersionedFact,
)


AuthoringEntityType = Literal[
    "project",
    "series",
    "story_bible",
    "arc",
    "episode",
    "scene",
    "shot",
    "reference_asset",
    "reference_set",
]

_COLLECTIONS: dict[str, str] = {
    "project": "projects",
    "series": "series",
    "story_bible": "story_bibles",
    "arc": "arcs",
    "episode": "episodes",
    "scene": "scenes",
    "shot": "shots",
    "reference_asset": "reference_assets",
    "reference_set": "reference_sets",
}

_EDITABLE_FIELDS: dict[str, frozenset[str]] = {
    "project": frozenset({"title", "summary", "creative_intent", "ip_profile"}),
    "series": frozenset({"title", "summary", "creative_intent"}),
    "story_bible": frozenset({"title", "summary", "world_rules"}),
    "arc": frozenset({"title", "summary", "creative_intent"}),
    "episode": frozenset({"title", "summary", "creative_intent", "reference_set_ref"}),
    "scene": frozenset({"title", "summary", "creative_intent", "reference_set_ref"}),
    "reference_asset": frozenset(
        {"label", "identity", "confidence", "approval_state", "human_confirmed"}
    ),
    "reference_set": frozenset(
        {
            "title",
            "summary",
            "scope_kind",
            "scope_refs",
            "asset_refs",
            "approval_state",
            "human_confirmed",
        }
    ),
}


class EpisodeAuthoringError(RuntimeError):
    pass


class AuthoringScopeError(EpisodeAuthoringError):
    pass


class AuthoringVersionConflictError(EpisodeAuthoringError):
    pass


class AuthoringReferenceError(EpisodeAuthoringError):
    pass


class AuthoringStateError(EpisodeAuthoringError):
    pass


@dataclass(frozen=True)
class ShotImpactPreview:
    aggregate_version: int
    shot_ref: EntityVersionRef
    direct_affected_refs: tuple[EntityVersionRef, ...]
    transitive_affected_refs: tuple[EntityVersionRef, ...]
    protected_refs: tuple[EntityVersionRef, ...]
    stale_candidate_refs: tuple[EntityVersionRef, ...]
    stale_review_refs: tuple[EntityVersionRef, ...]
    estimated_follow_up: int
    proposed_changes: dict[str, Any]
    preview_digest: str


def create_authoring_entity(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    entity_type: AuthoringEntityType,
    entity_id: str,
    version_id: str,
    created_at: str,
    attributes: dict[str, Any],
) -> ProductionProjectAggregate:
    canonical = _require_mutation(aggregate, scope, expected_aggregate_version, created_at)
    if entity_type == "project":
        raise AuthoringStateError("project creation belongs to aggregate bootstrap")
    _require_unused_entity(canonical, entity_type, entity_id)
    _require_unused_ref(canonical, EntityVersionRef(entity_type=entity_type, entity_id=entity_id, version_id=version_id))

    record_type: type[VersionedFact]
    if entity_type == "series":
        project_ref = _exact_latest(canonical, attributes["project_ref"], "project")
        record_type = SeriesVersion
        values = {**attributes, "project_ref": project_ref.as_ref()}
    elif entity_type == "story_bible":
        project_ref = _exact_latest(canonical, attributes["project_ref"], "project")
        record_type = StoryBibleVersion
        values = {**attributes, "project_ref": project_ref.as_ref()}
    elif entity_type == "arc":
        series_ref = _exact_latest(canonical, attributes["series_ref"], "series")
        bible_ref = attributes.get("story_bible_ref")
        if bible_ref is not None:
            bible_ref = _exact_latest(canonical, bible_ref, "story_bible").as_ref()
        _require_unique_sequence(canonical.arcs, int(attributes["sequence"]), series_ref.entity_id)
        record_type = ArcVersion
        values = {**attributes, "series_ref": series_ref.as_ref(), "story_bible_ref": bible_ref}
    elif entity_type == "episode":
        series_ref = _exact_latest(canonical, attributes["series_ref"], "series")
        arc_ref = attributes.get("arc_ref")
        if arc_ref is not None:
            arc = _exact_latest(canonical, arc_ref, "arc")
            if arc.series_ref.entity_id != series_ref.entity_id:  # type: ignore[attr-defined]
                raise AuthoringReferenceError("episode arc must belong to the selected series")
            arc_ref = arc.as_ref()
        target_ref = EntityVersionRef(entity_type="episode", entity_id=entity_id, version_id=version_id)
        reference_set_ref = _approved_reference_set(
            canonical,
            attributes.get("reference_set_ref"),
            target_ref=target_ref,
            parent_ref=arc_ref or series_ref.as_ref(),
        )
        _require_unique_sequence(canonical.episodes, int(attributes["sequence"]), series_ref.entity_id)
        record_type = EpisodeVersion
        values = {
            **attributes,
            "series_ref": series_ref.as_ref(),
            "arc_ref": arc_ref,
            "reference_set_ref": reference_set_ref,
        }
    elif entity_type == "scene":
        episode_ref = _exact_latest(canonical, attributes["episode_ref"], "episode")
        target_ref = EntityVersionRef(entity_type="scene", entity_id=entity_id, version_id=version_id)
        reference_set_ref = _approved_reference_set(
            canonical, attributes.get("reference_set_ref"), target_ref=target_ref, parent_ref=episode_ref.as_ref()
        )
        _require_unique_sequence(canonical.scenes, int(attributes["sequence"]), episode_ref.entity_id)
        record_type = SceneVersion
        values = {
            **attributes,
            "episode_ref": episode_ref.as_ref(),
            "reference_set_ref": reference_set_ref,
        }
    elif entity_type == "shot":
        scene_ref = _exact_latest(canonical, attributes["scene_ref"], "scene")
        target_ref = EntityVersionRef(entity_type="shot", entity_id=entity_id, version_id=version_id)
        reference_set_ref = _approved_reference_set(
            canonical, attributes.get("reference_set_ref"), target_ref=target_ref, parent_ref=scene_ref.as_ref()
        )
        _require_unique_sequence(canonical.shots, int(attributes["sequence"]), scene_ref.entity_id)
        record_type = ShotVersion
        values = {
            **attributes,
            "scene_ref": scene_ref.as_ref(),
            "reference_set_ref": reference_set_ref,
        }
    elif entity_type == "reference_asset":
        project_ref = _exact_latest(canonical, attributes["project_ref"], "project")
        record_type = ReferenceAssetVersion
        values = {**attributes, "project_ref": project_ref.as_ref()}
    elif entity_type == "reference_set":
        project_ref = _exact_latest(canonical, attributes["project_ref"], "project")
        values = {
            **attributes,
            "project_ref": project_ref.as_ref(),
            "asset_refs": tuple(
                _exact_latest(canonical, ref, "reference_asset").as_ref()
                for ref in attributes.get("asset_refs", ())
            ),
            "scope_refs": tuple(
                _exact_latest(canonical, ref, str(attributes.get("scope_kind", "project"))).as_ref()
                for ref in attributes.get("scope_refs", ())
            ),
        }
        record_type = ReferenceSetVersion
    else:  # pragma: no cover - closed Literal and route union
        raise AuthoringStateError("unsupported authoring entity type")

    content = _business_payload(entity_type, values)
    record = record_type(
        entity_id=entity_id,
        version_id=version_id,
        revision=1,
        parent_version_id=None,
        lifecycle_state="draft",
        review_state="not_requested",
        content_digest=_digest(content),
        scope=scope,
        created_at=created_at,
        **values,
    )
    return _append(canonical, _COLLECTIONS[entity_type], (record,), created_at)


def revise_authoring_entity(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    target_ref: EntityVersionRef,
    new_version_id: str,
    created_at: str,
    changes: dict[str, Any],
) -> ProductionProjectAggregate:
    canonical = _require_mutation(aggregate, scope, expected_aggregate_version, created_at)
    if target_ref.entity_type == "shot":
        raise AuthoringStateError("shot creative changes require impact preview and shot.revise_intent")
    allowed = _EDITABLE_FIELDS.get(target_ref.entity_type)
    if allowed is None or not changes or not set(changes).issubset(allowed):
        raise AuthoringStateError("authoring revision contains unsupported fields")
    current = _exact_latest(canonical, target_ref, target_ref.entity_type)
    _require_editable(current)
    _require_unused_ref(
        canonical,
        EntityVersionRef(
            entity_type=target_ref.entity_type,
            entity_id=target_ref.entity_id,
            version_id=new_version_id,
        ),
    )
    normalized = dict(changes)
    if "reference_set_ref" in normalized:
        normalized["reference_set_ref"] = _approved_reference_set(
            canonical,
            normalized["reference_set_ref"],
            target_ref=current.as_ref(),
        )
    if target_ref.entity_type == "reference_set":
        if "asset_refs" in normalized:
            normalized["asset_refs"] = tuple(
                _exact_latest(canonical, ref, "reference_asset").as_ref()
                for ref in normalized["asset_refs"]
            )
        scope_kind = str(normalized.get("scope_kind", getattr(current, "scope_kind", "project")))
        if "scope_refs" in normalized:
            normalized["scope_refs"] = tuple(
                _exact_latest(canonical, ref, scope_kind).as_ref()
                for ref in normalized["scope_refs"]
            )
        if _reference_set_material_changed(current, normalized):
            normalized["approval_state"] = "pending_human"
            normalized["human_confirmed"] = False
    if target_ref.entity_type == "reference_asset" and _reference_asset_material_changed(
        current,
        normalized,
    ):
        normalized["approval_state"] = "pending_human"
        normalized["human_confirmed"] = False
    payload = current.model_dump(mode="python")
    payload.update(normalized)
    payload.update(
        {
            "version_id": new_version_id,
            "revision": current.revision + 1,
            "parent_version_id": current.version_id,
            "created_at": created_at,
            **_next_revision_state(current),
        }
    )
    payload["content_digest"] = _digest(
        _business_payload(target_ref.entity_type, payload)
    )
    successor = type(current).model_validate(payload)
    return _append(canonical, _COLLECTIONS[target_ref.entity_type], (successor,), created_at)


def reorder_authoring_entities(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    ordered_refs: tuple[EntityVersionRef, ...],
    new_version_ids: tuple[str, ...],
    created_at: str,
) -> ProductionProjectAggregate:
    canonical = _require_mutation(aggregate, scope, expected_aggregate_version, created_at)
    if not ordered_refs or len(ordered_refs) != len(new_version_ids):
        raise AuthoringStateError("reorder requires matching non-empty refs and version ids")
    entity_type = ordered_refs[0].entity_type
    if entity_type not in ("arc", "episode", "scene", "shot"):
        raise AuthoringStateError("only ordered authoring entities can be reordered")
    if any(ref.entity_type != entity_type for ref in ordered_refs):
        raise AuthoringReferenceError("reorder refs must share one entity type")
    current = tuple(_exact_latest(canonical, ref, entity_type) for ref in ordered_refs)
    if len({item.entity_id for item in current}) != len(current):
        raise AuthoringReferenceError("reorder refs must identify distinct entities")
    parent_ids = {_parent_entity_id(item) for item in current}
    if len(parent_ids) != 1:
        raise AuthoringReferenceError("reorder refs must belong to one parent")
    siblings = _latest_siblings(canonical, entity_type, next(iter(parent_ids)))
    if {item.entity_id for item in current} != {item.entity_id for item in siblings}:
        raise AuthoringStateError("reorder must include the complete current sibling set")
    if len(set(new_version_ids)) != len(new_version_ids):
        raise AuthoringReferenceError("reorder version ids must be unique")
    successors: list[VersionedFact] = []
    for sequence, (item, version_id) in enumerate(zip(current, new_version_ids), start=1):
        _require_editable(item)
        new_ref = EntityVersionRef(
            entity_type=entity_type,
            entity_id=item.entity_id,
            version_id=version_id,
        )
        _require_unused_ref(canonical, new_ref)
        payload = item.model_dump(mode="python")
        payload.update(
            {
                "version_id": version_id,
                "revision": item.revision + 1,
                "parent_version_id": item.version_id,
                "sequence": sequence,
                "created_at": created_at,
                **_next_revision_state(item),
            }
        )
        payload["content_digest"] = _digest(_business_payload(entity_type, payload))
        successors.append(type(item).model_validate(payload))
    return _append(canonical, _COLLECTIONS[entity_type], tuple(successors), created_at)


def preview_shot_revision(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    shot_ref: EntityVersionRef,
    proposed_changes: dict[str, Any],
) -> ShotImpactPreview:
    canonical = _require_read(aggregate, scope, expected_aggregate_version)
    shot = _exact_latest(canonical, shot_ref, "shot")
    normalized = _normalize_shot_changes(canonical, shot, proposed_changes)
    direct = (shot.as_ref(),)
    candidates = tuple(
        item.as_ref() for item in canonical.asset_candidates if item.target_ref == shot.as_ref()
    )
    candidate_set = set(candidates)
    selections = tuple(
        item.as_ref()
        for item in canonical.selections
        if item.target_ref == shot.as_ref() or item.candidate_ref in candidate_set
    )
    affected_subjects = {shot.as_ref(), *candidates, *selections}
    reviews = tuple(
        item.as_ref()
        for item in canonical.review_decisions
        if item.subject_ref in affected_subjects
    )
    proposals = tuple(
        item.as_ref()
        for item in canonical.agent_proposals
        if shot.as_ref() in item.impact_refs or shot.as_ref() in item.applied_refs
    )
    downstream_refs = {*selections, *reviews}
    deliveries = tuple(
        item.as_ref()
        for item in canonical.deliveries
        if downstream_refs.intersection((*item.selection_refs, *item.review_decision_refs))
    )
    transitive = _unique_refs((*candidates, *selections, *reviews, *proposals, *deliveries))
    affected = {shot.as_ref(), *transitive}
    protected = tuple(
        item.as_ref()
        for item in _latest_authoring_records(canonical)
        if item.as_ref() not in affected
    )
    payload = {
        "aggregate_version": canonical.aggregate_version,
        "shot_ref": shot.as_ref().model_dump(mode="json"),
        "direct_affected_refs": [ref.model_dump(mode="json") for ref in direct],
        "transitive_affected_refs": [ref.model_dump(mode="json") for ref in transitive],
        "protected_refs": [ref.model_dump(mode="json") for ref in protected],
        "proposed_changes": _jsonable(normalized),
    }
    return ShotImpactPreview(
        aggregate_version=canonical.aggregate_version,
        shot_ref=shot.as_ref(),
        direct_affected_refs=direct,
        transitive_affected_refs=transitive,
        protected_refs=protected,
        stale_candidate_refs=candidates,
        stale_review_refs=reviews,
        estimated_follow_up=1 + len(transitive),
        proposed_changes=normalized,
        preview_digest=_digest(payload),
    )


def revise_shot_intent(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    shot_ref: EntityVersionRef,
    new_version_id: str,
    created_at: str,
    proposed_changes: dict[str, Any],
    preview_digest: str,
    confirmed_direct_refs: tuple[EntityVersionRef, ...],
    confirmed_transitive_refs: tuple[EntityVersionRef, ...],
    confirmed_protected_refs: tuple[EntityVersionRef, ...],
) -> ProductionProjectAggregate:
    preview = preview_shot_revision(
        aggregate,
        scope=scope,
        expected_aggregate_version=expected_aggregate_version,
        shot_ref=shot_ref,
        proposed_changes=proposed_changes,
    )
    _require_exact_preview_confirmation(
        preview,
        preview_digest=preview_digest,
        direct_refs=confirmed_direct_refs,
        transitive_refs=confirmed_transitive_refs,
        protected_refs=confirmed_protected_refs,
    )
    canonical = _require_mutation(aggregate, scope, expected_aggregate_version, created_at)
    current = _exact_latest(canonical, shot_ref, "shot")
    _require_editable(current)
    _require_unused_ref(
        canonical,
        EntityVersionRef(entity_type="shot", entity_id=current.entity_id, version_id=new_version_id),
    )
    payload = current.model_dump(mode="python")
    payload.update(preview.proposed_changes)
    payload.update(
        {
            "version_id": new_version_id,
            "revision": current.revision + 1,
            "parent_version_id": current.version_id,
            "created_at": created_at,
            "lifecycle_state": "candidate",
            "review_state": "needs_review",
            "source_proposal_ref": None,
        }
    )
    payload["content_digest"] = _digest(_business_payload("shot", payload))
    successor = ShotVersion.model_validate(payload)
    return _append(canonical, "shots", (successor,), created_at)


def restore_shot_as_new(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    historical_ref: EntityVersionRef,
    current_ref: EntityVersionRef,
    new_version_id: str,
    created_at: str,
    preview_digest: str,
    confirmed_direct_refs: tuple[EntityVersionRef, ...],
    confirmed_transitive_refs: tuple[EntityVersionRef, ...],
    confirmed_protected_refs: tuple[EntityVersionRef, ...],
) -> ProductionProjectAggregate:
    preview = preview_shot_restore(
        aggregate,
        scope=scope,
        expected_aggregate_version=expected_aggregate_version,
        historical_ref=historical_ref,
        current_ref=current_ref,
    )
    return revise_shot_intent(
        aggregate,
        scope=scope,
        expected_aggregate_version=expected_aggregate_version,
        shot_ref=preview.shot_ref,
        new_version_id=new_version_id,
        created_at=created_at,
        proposed_changes=preview.proposed_changes,
        preview_digest=preview_digest,
        confirmed_direct_refs=confirmed_direct_refs,
        confirmed_transitive_refs=confirmed_transitive_refs,
        confirmed_protected_refs=confirmed_protected_refs,
    )


def preview_shot_restore(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    historical_ref: EntityVersionRef,
    current_ref: EntityVersionRef,
) -> ShotImpactPreview:
    canonical = _require_read(aggregate, scope, expected_aggregate_version)
    historical = _exact(canonical, historical_ref, "shot")
    current = _exact_latest(canonical, current_ref, "shot")
    if historical.entity_id != current.entity_id:
        raise AuthoringReferenceError("shot restore refs must share one stable entity")
    changes = {
        "title": historical.title,
        "summary": historical.summary,
        "creative_intent": historical.creative_intent,
        "duration_seconds": historical.duration_seconds,
        "reference_set_ref": historical.reference_set_ref,
    }
    return preview_shot_revision(
        canonical,
        scope=scope,
        expected_aggregate_version=expected_aggregate_version,
        shot_ref=current.as_ref(),
        proposed_changes=changes,
    )


def diff_shot_versions(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    left_ref: EntityVersionRef,
    right_ref: EntityVersionRef,
) -> dict[str, dict[str, Any]]:
    canonical = _require_scope(aggregate, scope)
    left = _exact(canonical, left_ref, "shot")
    right = _exact(canonical, right_ref, "shot")
    if left.entity_id != right.entity_id:
        raise AuthoringReferenceError("shot diff refs must share one stable entity")
    fields = ("title", "summary", "creative_intent", "duration_seconds", "reference_set_ref")
    return {
        field: {"before": _jsonable(getattr(left, field)), "after": _jsonable(getattr(right, field))}
        for field in fields
        if getattr(left, field) != getattr(right, field)
    }


def _require_exact_preview_confirmation(
    preview: ShotImpactPreview,
    *,
    preview_digest: str,
    direct_refs: tuple[EntityVersionRef, ...],
    transitive_refs: tuple[EntityVersionRef, ...],
    protected_refs: tuple[EntityVersionRef, ...],
) -> None:
    if preview.preview_digest != preview_digest:
        raise AuthoringVersionConflictError("impact preview digest is stale or mismatched")
    if direct_refs != preview.direct_affected_refs:
        raise AuthoringStateError("confirmed direct impact set does not match preview")
    if transitive_refs != preview.transitive_affected_refs:
        raise AuthoringStateError("confirmed transitive impact set does not match preview")
    if protected_refs != preview.protected_refs:
        raise AuthoringStateError("confirmed protected set does not match preview")


def _normalize_shot_changes(
    aggregate: ProductionProjectAggregate,
    current: ShotVersion,
    proposed_changes: dict[str, Any],
) -> dict[str, Any]:
    allowed = {"title", "summary", "creative_intent", "duration_seconds", "reference_set_ref"}
    if not proposed_changes or not set(proposed_changes).issubset(allowed):
        raise AuthoringStateError("shot revision contains unsupported or empty creative changes")
    normalized = dict(proposed_changes)
    if "reference_set_ref" in normalized:
        normalized["reference_set_ref"] = _approved_reference_set(
            aggregate,
            normalized["reference_set_ref"],
            target_ref=current.as_ref(),
        )
    candidate = current.model_copy(update=normalized)
    if all(getattr(candidate, field) == getattr(current, field) for field in allowed):
        raise AuthoringStateError("shot revision must change at least one creative field")
    # Validate field limits without constructing a successor revision yet.
    ShotVersion.model_validate(candidate.model_dump(mode="python"))
    return normalized


def _reference_asset_material_changed(
    current: VersionedFact,
    normalized: dict[str, Any],
) -> bool:
    for field in ("identity", "confidence", "source_refs"):
        if field in normalized and normalized[field] != getattr(current, field):
            return True
    return False


def _reference_set_material_changed(
    current: VersionedFact,
    normalized: dict[str, Any],
) -> bool:
    if "scope_kind" in normalized and normalized["scope_kind"] != getattr(current, "scope_kind"):
        return True
    for field in ("asset_refs", "scope_refs", "source_refs"):
        if field in normalized and set(normalized[field]) != set(getattr(current, field)):
            return True
    return False


def _approved_reference_set(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef | None,
    *,
    target_ref: EntityVersionRef,
    parent_ref: EntityVersionRef | None = None,
) -> EntityVersionRef | None:
    if ref is None:
        return None
    reference_set = _exact_latest(aggregate, ref, "reference_set")
    if reference_set.approval_state != "approved":  # type: ignore[attr-defined]
        raise AuthoringStateError("reference set binding requires human-approved exact version")
    allowed = _scope_chain(aggregate, target_ref, parent_ref=parent_ref)
    if not any(
        (scope_ref.entity_type, scope_ref.entity_id) in allowed
        for scope_ref in reference_set.scope_refs  # type: ignore[attr-defined]
    ):
        raise AuthoringStateError("reference set scope does not include the binding target")
    return reference_set.as_ref()


def _scope_chain(
    aggregate: ProductionProjectAggregate,
    target_ref: EntityVersionRef,
    *,
    parent_ref: EntityVersionRef | None = None,
) -> set[tuple[str, str]]:
    allowed = {("project", aggregate.scope.project_id), (target_ref.entity_type, target_ref.entity_id)}
    current = parent_ref or target_ref
    while current.entity_type != "project":
        allowed.add((current.entity_type, current.entity_id))
        record = _exact(aggregate, current, current.entity_type)
        if isinstance(record, SeriesVersion) or isinstance(record, StoryBibleVersion) or isinstance(record, ReferenceSetVersion) or isinstance(record, ReferenceAssetVersion):
            current = record.project_ref
        elif isinstance(record, ArcVersion):
            current = record.series_ref
        elif isinstance(record, EpisodeVersion):
            current = record.arc_ref or record.series_ref
        elif isinstance(record, SceneVersion):
            current = record.episode_ref
        elif isinstance(record, ShotVersion):
            current = record.scene_ref
        else:
            break
    allowed.add(("project", aggregate.scope.project_id))
    return allowed


def _require_mutation(
    aggregate: ProductionProjectAggregate,
    scope: TenantScope,
    expected_aggregate_version: int,
    created_at: str,
) -> ProductionProjectAggregate:
    canonical = _require_read(aggregate, scope, expected_aggregate_version)
    if datetime.fromisoformat(created_at) <= datetime.fromisoformat(canonical.evaluated_at):
        raise AuthoringStateError("authoring mutation timestamp must advance aggregate time")
    return canonical


def _require_read(
    aggregate: ProductionProjectAggregate,
    scope: TenantScope,
    expected_aggregate_version: int,
) -> ProductionProjectAggregate:
    canonical = _require_scope(aggregate, scope)
    if expected_aggregate_version != canonical.aggregate_version:
        raise AuthoringVersionConflictError(
            f"aggregate version conflict: expected {expected_aggregate_version}, current {canonical.aggregate_version}"
        )
    return canonical


def _require_scope(
    aggregate: ProductionProjectAggregate,
    scope: TenantScope,
) -> ProductionProjectAggregate:
    canonical = ProductionProjectAggregate.model_validate(aggregate)
    if canonical.scope != scope:
        raise AuthoringScopeError("authoring command scope does not match aggregate scope")
    return canonical


def _exact(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
    expected_type: str,
) -> VersionedFact:
    if ref.entity_type != expected_type:
        raise AuthoringReferenceError(f"reference must target {expected_type}")
    for item in _records_for_type(aggregate, expected_type):
        if item.as_ref() == ref:
            return item
    raise AuthoringReferenceError("exact authoring reference was not found")


def _exact_latest(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
    expected_type: str,
) -> VersionedFact:
    item = _exact(aggregate, ref, expected_type)
    latest = max(
        (candidate for candidate in _records_for_type(aggregate, expected_type) if candidate.entity_id == item.entity_id),
        key=lambda candidate: candidate.revision,
    )
    if latest.as_ref() != ref:
        raise AuthoringReferenceError("authoring reference is stale")
    return latest


def _records_for_type(
    aggregate: ProductionProjectAggregate,
    entity_type: str,
) -> tuple[VersionedFact, ...]:
    collection = _COLLECTIONS.get(entity_type)
    if collection is None:
        raise AuthoringReferenceError("unsupported authoring reference type")
    return tuple(getattr(aggregate, collection))


def _latest_authoring_records(
    aggregate: ProductionProjectAggregate,
) -> tuple[VersionedFact, ...]:
    result: list[VersionedFact] = []
    for collection in _COLLECTIONS.values():
        by_entity: dict[str, VersionedFact] = {}
        for item in getattr(aggregate, collection):
            current = by_entity.get(item.entity_id)
            if current is None or item.revision > current.revision:
                by_entity[item.entity_id] = item
        result.extend(by_entity.values())
    return tuple(sorted(result, key=lambda item: (item.entity_type, item.entity_id)))


def _latest_siblings(
    aggregate: ProductionProjectAggregate,
    entity_type: str,
    parent_entity_id: str,
) -> tuple[VersionedFact, ...]:
    latest = {
        item.entity_id: item
        for item in _records_for_type(aggregate, entity_type)
        if _parent_entity_id(item) == parent_entity_id
    }
    for item in _records_for_type(aggregate, entity_type):
        if _parent_entity_id(item) != parent_entity_id:
            continue
        previous = latest.get(item.entity_id)
        if previous is None or item.revision > previous.revision:
            latest[item.entity_id] = item
    return tuple(sorted(latest.values(), key=lambda item: (getattr(item, "sequence"), item.entity_id)))


def _parent_entity_id(item: VersionedFact) -> str:
    if isinstance(item, ArcVersion):
        return item.series_ref.entity_id
    if isinstance(item, EpisodeVersion):
        return item.series_ref.entity_id
    if isinstance(item, SceneVersion):
        return item.episode_ref.entity_id
    if isinstance(item, ShotVersion):
        return item.scene_ref.entity_id
    raise AuthoringReferenceError("entity does not belong to an ordered authoring collection")


def _require_unique_sequence(
    records: tuple[VersionedFact, ...],
    sequence: int,
    parent_entity_id: str,
) -> None:
    latest: dict[str, VersionedFact] = {}
    for item in records:
        if _parent_entity_id(item) != parent_entity_id:
            continue
        current = latest.get(item.entity_id)
        if current is None or item.revision > current.revision:
            latest[item.entity_id] = item
    if any(getattr(item, "sequence") == sequence for item in latest.values()):
        raise AuthoringStateError("sibling sequence is already in use")


def _require_unused_entity(
    aggregate: ProductionProjectAggregate,
    entity_type: str,
    entity_id: str,
) -> None:
    if any(item.entity_id == entity_id for item in _records_for_type(aggregate, entity_type)):
        raise AuthoringReferenceError("stable authoring entity id is already in use")


def _require_unused_ref(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
) -> None:
    if any(item.as_ref() == ref for item in _records_for_type(aggregate, ref.entity_type)):
        raise AuthoringReferenceError("authoring version ref is already in use")


def _require_editable(item: VersionedFact) -> None:
    if item.lifecycle_state in ("locked", "retired"):
        raise AuthoringStateError("locked or retired authoring facts cannot be edited")


def _next_revision_state(item: VersionedFact) -> dict[str, str]:
    if item.lifecycle_state == "draft":
        return {"lifecycle_state": "draft", "review_state": "not_requested"}
    return {"lifecycle_state": "candidate", "review_state": "needs_review"}


def _append(
    aggregate: ProductionProjectAggregate,
    collection: str,
    records: tuple[VersionedFact, ...],
    created_at: str,
) -> ProductionProjectAggregate:
    payload = aggregate.model_dump(mode="python")
    payload[collection] = (*getattr(aggregate, collection), *records)
    payload["aggregate_version"] = aggregate.aggregate_version + 1
    payload["evaluated_at"] = created_at
    return ProductionProjectAggregate.model_validate(payload)


def _business_payload(entity_type: str, values: dict[str, Any]) -> dict[str, Any]:
    ignored = {
        "entity_type",
        "entity_id",
        "version_id",
        "revision",
        "parent_version_id",
        "lifecycle_state",
        "review_state",
        "content_digest",
        "scope",
        "created_at",
        "source_refs",
    }
    return {
        "entity_type": entity_type,
        **{key: _jsonable(value) for key, value in values.items() if key not in ignored},
    }


def _unique_refs(refs: tuple[EntityVersionRef, ...]) -> tuple[EntityVersionRef, ...]:
    seen: set[EntityVersionRef] = set()
    result: list[EntityVersionRef] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            result.append(ref)
    return tuple(result)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _digest(value: object) -> str:
    canonical = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = (
    "AuthoringReferenceError",
    "AuthoringScopeError",
    "AuthoringStateError",
    "AuthoringVersionConflictError",
    "EpisodeAuthoringError",
    "ShotImpactPreview",
    "create_authoring_entity",
    "diff_shot_versions",
    "preview_shot_revision",
    "preview_shot_restore",
    "reorder_authoring_entities",
    "restore_shot_as_new",
    "revise_authoring_entity",
    "revise_shot_intent",
)
