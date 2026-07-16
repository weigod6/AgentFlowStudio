from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote

from pydantic import ValidationError

from apps.api.runtime_episode_creator_workflow_service import (
    EpisodeCreatorWorkflowError,
    derive_prior_shot_blockers,
)
from apps.api.runtime_episode_domain_contract import (
    AgentProposal,
    AssetCandidateVersion,
    ContinuityStateVersion,
    DeliveryVersion,
    EntityVersionRef,
    EpisodeVersion,
    ProductionProjectAggregate,
    ReviewDecision,
    SceneVersion,
    SelectedVersion,
    ShotVersion,
    VersionedFact,
)


EPISODE_WORKSPACE_PROJECTION_SCHEMA_VERSION = "afs_episode_workspace_projection.v0.1"
CREATOR_AUTHORING_PROJECTION_SCHEMA_VERSION = "afs_creator_authoring_workspace.v0.1"


class EpisodeWorkspaceProjectionError(RuntimeError):
    """The aggregate cannot be represented as one unambiguous safe workspace."""


class WorkspaceProjectionReferenceError(EpisodeWorkspaceProjectionError):
    pass


class WorkspaceProjectionStateError(EpisodeWorkspaceProjectionError):
    pass


def build_creator_authoring_projection(
    aggregate: ProductionProjectAggregate,
) -> dict[str, Any]:
    """Project-level authoring read model over the canonical Episode aggregate."""

    canonical = _validated_copy(aggregate)
    _require_exact_scope(canonical)
    project = max(canonical.projects, key=lambda item: item.revision)
    latest_series = _latest_by_entity(canonical.series)
    latest_bibles = _latest_by_entity(canonical.story_bibles)
    latest_arcs = tuple(
        sorted(_latest_by_entity(canonical.arcs), key=lambda item: (item.sequence, item.entity_id))
    )
    latest_episodes = tuple(
        sorted(
            _latest_by_entity(canonical.episodes),
            key=lambda item: (item.sequence, item.entity_id),
        )
    )
    latest_scenes = tuple(
        sorted(
            _latest_by_entity(canonical.scenes),
            key=lambda item: (item.episode_ref.entity_id, item.sequence, item.entity_id),
        )
    )
    latest_shots = tuple(
        sorted(
            _latest_by_entity(canonical.shots),
            key=lambda item: (item.scene_ref.entity_id, item.sequence, item.entity_id),
        )
    )
    latest_assets = _latest_by_entity(canonical.reference_assets)
    latest_sets = _latest_by_entity(canonical.reference_sets)

    for parent_id, records, label in (
        (series.entity_id, tuple(item for item in latest_arcs if item.series_ref.entity_id == series.entity_id), "arc")
        for series in latest_series
    ):
        del parent_id
        _require_unique_sequences(records, label)
    for series in latest_series:
        _require_unique_sequences(
            tuple(item for item in latest_episodes if item.series_ref.entity_id == series.entity_id),
            "episode",
        )
    for episode in latest_episodes:
        _require_unique_sequences(
            tuple(item for item in latest_scenes if item.episode_ref.entity_id == episode.entity_id),
            "scene",
        )
    for scene in latest_scenes:
        _require_unique_sequences(
            tuple(item for item in latest_shots if item.scene_ref.entity_id == scene.entity_id),
            "shot",
        )

    projection: dict[str, Any] = {
        "schema_version": CREATOR_AUTHORING_PROJECTION_SCHEMA_VERSION,
        "aggregate_version": canonical.aggregate_version,
        "project": {
            "ref": _ref(project.as_ref()),
            "title": project.title,
            "summary": project.summary,
            "creative_intent": project.creative_intent,
            "ip_profile": project.ip_profile,
            "privacy": "private" if project.data_policy.visibility == "private" else "shared",
            "trace": {"revision": project.revision, "content_digest": project.content_digest},
        },
        "story_bibles": [
            {
                "ref": _ref(item.as_ref()),
                "project_ref": _ref(item.project_ref),
                "title": item.title,
                "summary": item.summary,
                "world_rules": list(item.world_rules),
                "trace": {"revision": item.revision, "content_digest": item.content_digest},
            }
            for item in latest_bibles
        ],
        "series": [
            {
                "ref": _ref(item.as_ref()),
                "project_ref": _ref(item.project_ref),
                "title": item.title,
                "summary": item.summary,
                "creative_intent": item.creative_intent,
                "trace": {"revision": item.revision, "content_digest": item.content_digest},
            }
            for item in latest_series
        ],
        "arcs": [
            {
                "ref": _ref(item.as_ref()),
                "series_ref": _ref(item.series_ref),
                "story_bible_ref": _ref(item.story_bible_ref) if item.story_bible_ref else None,
                "sequence": item.sequence,
                "title": item.title,
                "summary": item.summary,
                "creative_intent": item.creative_intent,
                "trace": {"revision": item.revision, "content_digest": item.content_digest},
            }
            for item in latest_arcs
        ],
        "episodes": [
            {
                "ref": _ref(item.as_ref()),
                "series_ref": _ref(item.series_ref),
                "arc_ref": _ref(item.arc_ref) if item.arc_ref else None,
                "reference_set_ref": _ref(item.reference_set_ref) if item.reference_set_ref else None,
                "sequence": item.sequence,
                "title": item.title,
                "summary": item.summary,
                "creative_intent": item.creative_intent,
                "trace": {"revision": item.revision, "content_digest": item.content_digest},
            }
            for item in latest_episodes
        ],
        "scenes": [
            {
                "ref": _ref(item.as_ref()),
                "episode_ref": _ref(item.episode_ref),
                "reference_set_ref": _ref(item.reference_set_ref) if item.reference_set_ref else None,
                "sequence": item.sequence,
                "title": item.title,
                "summary": item.summary,
                "creative_intent": item.creative_intent,
                "trace": {"revision": item.revision, "content_digest": item.content_digest},
            }
            for item in latest_scenes
        ],
        "shots": [
            {
                "ref": _ref(item.as_ref()),
                "scene_ref": _ref(item.scene_ref),
                "reference_set_ref": _ref(item.reference_set_ref) if item.reference_set_ref else None,
                "sequence": item.sequence,
                "title": item.title,
                "summary": item.summary,
                "creative_intent": item.creative_intent,
                "duration_seconds": item.duration_seconds,
                "lifecycle_state": item.lifecycle_state,
                "review_state": item.review_state,
                "versions": [
                    {
                        "ref": _ref(version.as_ref()),
                        "revision": version.revision,
                        "parent_version_id": version.parent_version_id,
                        "title": version.title,
                        "summary": version.summary,
                        "creative_intent": version.creative_intent,
                        "duration_seconds": version.duration_seconds,
                        "reference_set_ref": (
                            _ref(version.reference_set_ref) if version.reference_set_ref else None
                        ),
                        "content_digest": version.content_digest,
                    }
                    for version in sorted(
                        (candidate for candidate in canonical.shots if candidate.entity_id == item.entity_id),
                        key=lambda candidate: candidate.revision,
                    )
                ],
                "trace": {"revision": item.revision, "content_digest": item.content_digest},
            }
            for item in latest_shots
        ],
        "reference_assets": [
            {
                "ref": _ref(item.as_ref()),
                "asset_kind": item.asset_kind,
                "label": item.label,
                "identity": item.identity,
                "confidence": item.confidence,
                "approval_state": item.approval_state,
                "human_confirmed": item.human_confirmed,
                "provenance": [
                    {
                        "source_id": source.source_id,
                        "source_type": source.source_type,
                        "rights_basis": source.rights_basis,
                    }
                    for source in item.source_refs
                ],
                "trace": {"revision": item.revision, "content_digest": item.content_digest},
            }
            for item in latest_assets
        ],
        "reference_sets": [
            {
                "ref": _ref(item.as_ref()),
                "title": item.title,
                "summary": item.summary,
                "scope_kind": item.scope_kind,
                "scope_refs": [_ref(ref) for ref in item.scope_refs],
                "asset_refs": [_ref(ref) for ref in item.asset_refs],
                "approval_state": item.approval_state,
                "human_confirmed": item.human_confirmed,
                "trace": {"revision": item.revision, "content_digest": item.content_digest},
            }
            for item in latest_sets
        ],
        "counts": {
            "episodes": len(latest_episodes),
            "scenes": len(latest_scenes),
            "shots": len(latest_shots),
            "reference_assets": len(latest_assets),
            "reference_sets": len(latest_sets),
        },
        "provider_dispatch_count": 0,
    }
    _require_safe_serialization(projection)
    return projection


_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)[^\s<>\"']+"
)
_SAFE_HTTP_URL_RE = re.compile(
    r"https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+",
    re.IGNORECASE,
)
_FORWARD_UNC_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:.~])//[^\s<>\"']+",
)
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:.~])/(?!/)(?=[^\s<>\"'])",
    re.IGNORECASE,
)
_FILE_URI_RE = re.compile(
    r"file:(?://|[\\/]|[A-Za-z]:[\\/])",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"api[-_]?key|apikey|access[-_]?token|authorization|auth[-_]?token|"
    r"awsaccesskeyid|client[-_]?secret|credential|googleaccessid|jwt|"
    r"key[-_]?pair[-_]?id|password|secret|sig|signature|signed[-_]?token|token|"
    r"x[-_]amz[-_](?:credential|security[-_]token|signature)|"
    r"x[-_]goog[-_](?:credential|security[-_]token|signature)|"
    r"x[-_]ms[-_](?:signature|token)"
    r")\s*=",
    re.IGNORECASE,
)


def build_episode_workspace_projection(
    aggregate: ProductionProjectAggregate,
    *,
    episode_ref: EntityVersionRef,
) -> dict[str, Any]:
    """Build a deterministic creator-safe read model from canonical aggregate facts.

    Missing assets are counted narrowly: one item for each latest candidate entity
    whose exact target belongs to the episode's current shot/continuity target set
    and whose latest candidate version has no ``artifact_ref``. The absence of a
    candidate is not evidence that an asset is missing. Dispatches are distinct,
    non-null job IDs on those same latest relevant candidate versions.

    This projection has no artifact-availability input. Therefore a preview ref on
    a current locked delivery is reported as present but never as playable.
    """

    canonical = _validated_copy(aggregate)
    _require_exact_scope(canonical)
    episode = _exact_latest_episode(canonical, episode_ref)
    scenes = _current_episode_scenes(canonical, episode)
    shots = _current_episode_shots(canonical, scenes)

    continuity_index = {item.as_ref(): item for item in canonical.continuity_states}
    continuity_for_shot: dict[EntityVersionRef, tuple[ContinuityStateVersion, ...]] = {}
    for shot in shots:
        resolved: list[ContinuityStateVersion] = []
        for ref in shot.continuity_refs:
            fact = continuity_index.get(ref)
            if fact is None:
                raise WorkspaceProjectionReferenceError(
                    "shot continuity reference does not resolve inside the aggregate"
                )
            resolved.append(fact)
        continuity_for_shot[shot.as_ref()] = tuple(resolved)

    episode_targets = {
        *(shot.as_ref() for shot in shots),
        *(ref for shot in shots for ref in shot.continuity_refs),
    }
    candidates = tuple(
        item
        for item in _latest_by_entity(canonical.asset_candidates)
        if item.target_ref in episode_targets
    )
    selections = tuple(
        item
        for item in _latest_by_entity(canonical.selections)
        if item.target_ref in episode_targets
    )
    proposals = tuple(
        item
        for item in _latest_by_entity(canonical.agent_proposals)
        if item.target_ref in episode_targets
        or any(
            ref in {shot.as_ref() for shot in shots}
            for ref in (*item.impact_refs, *item.applied_refs)
        )
    )

    candidate_by_target = _group_by_target(candidates)
    selection_by_target = _group_by_target(selections)
    blockers_by_shot: dict[EntityVersionRef, tuple[dict[str, Any], ...]] = {}
    for shot in shots:
        try:
            blockers = derive_prior_shot_blockers(
                canonical,
                scope=canonical.scope,
                target_shot_ref=shot.as_ref(),
            )
        except EpisodeCreatorWorkflowError as exc:
            raise WorkspaceProjectionStateError(
                "creator workflow could not derive an unambiguous episode order"
            ) from exc
        blockers_by_shot[shot.as_ref()] = tuple(
            {
                "shot_ref": _ref(item.shot_ref),
                "sequence": item.sequence,
                "reason": item.reason,
            }
            for item in blockers
        )

    review_notes = _review_notes_by_subject(canonical.review_decisions)
    shot_rows = [
        _shot_projection(
            shot,
            continuity=continuity_for_shot[shot.as_ref()],
            candidates=candidate_by_target.get(shot.as_ref(), ()),
            selections=selection_by_target.get(shot.as_ref(), ()),
            proposals=proposals,
            blockers=blockers_by_shot[shot.as_ref()],
            review_note=review_notes.get(shot.as_ref()),
        )
        for shot in shots
    ]

    missing_asset_count = sum(item.artifact_ref is None for item in candidates)
    dispatch_job_ids = tuple(
        sorted({item.job_id for item in candidates if item.job_id is not None})
    )
    delivery = _delivery_projection(canonical, episode, missing_asset_count)
    next_action = _next_action(shots, shot_rows)
    project = _project_for_episode(canonical, episode)
    series = _series_for_episode(canonical, episode)

    projection: dict[str, Any] = {
        "schema_version": EPISODE_WORKSPACE_PROJECTION_SCHEMA_VERSION,
        "aggregate": {
            "schema_version": canonical.schema_version,
            "aggregate_version": canonical.aggregate_version,
            "evaluated_at": canonical.evaluated_at,
            "scope": canonical.scope.model_dump(mode="json"),
            "projects": [
                _version_record(
                    project,
                    title=project.title,
                    data_policy=project.data_policy.model_dump(mode="json"),
                )
            ],
            "series": [_version_record(series, title=series.title)],
            "episodes": [_version_record(episode, title=episode.title)],
            "scenes": [
                _version_record(item, title=item.title, sequence=item.sequence)
                for item in scenes
            ],
            "shots": [
                _version_record(
                    item,
                    scene_ref=_ref(item.scene_ref),
                    sequence=item.sequence,
                    duration_seconds=item.duration_seconds,
                )
                for item in shots
            ],
        },
        "workspace": {
            "episode_ref": _ref(episode.as_ref()),
            "scenes": [
                {"ref": _ref(item.as_ref()), "sequence": item.sequence, "title": item.title}
                for item in scenes
            ],
            "shots": shot_rows,
            "next_action": next_action,
            "recovery": None,
            "truth": {
                "scene_count": len(scenes),
                "shot_count": len(shots),
                "duration_seconds": sum(item.duration_seconds for item in shots),
                "missing_asset_count": missing_asset_count,
                "generation_dispatch_count": len(dispatch_job_ids),
                "playable_preview_available": False,
            },
            "delivery": delivery,
            "evidence_environment": None,
        },
    }
    _require_safe_serialization(projection)
    return projection


def _validated_copy(aggregate: ProductionProjectAggregate) -> ProductionProjectAggregate:
    if not isinstance(aggregate, ProductionProjectAggregate):
        raise WorkspaceProjectionStateError("workspace source must be a production aggregate")
    try:
        return ProductionProjectAggregate.model_validate(aggregate.model_dump(mode="python"))
    except ValidationError as exc:
        raise WorkspaceProjectionStateError(
            "workspace source violates the production contract"
        ) from exc


def _require_exact_scope(aggregate: ProductionProjectAggregate) -> None:
    if any(item.scope != aggregate.scope for item in aggregate._records()):
        raise WorkspaceProjectionStateError(
            "workspace source contains a record outside the exact tenant, project, or actor scope"
        )


def _exact_latest_episode(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
) -> EpisodeVersion:
    if ref.entity_type != "episode":
        raise WorkspaceProjectionReferenceError("workspace reference must target an episode")
    exact = next((item for item in aggregate.episodes if item.as_ref() == ref), None)
    if exact is None:
        raise WorkspaceProjectionReferenceError("exact episode reference was not found")
    latest = _latest_by_entity(aggregate.episodes)
    current = next(item for item in latest if item.entity_id == exact.entity_id)
    if current.as_ref() != exact.as_ref():
        raise WorkspaceProjectionReferenceError("workspace episode reference is stale")
    return exact


def _current_episode_scenes(
    aggregate: ProductionProjectAggregate,
    episode: EpisodeVersion,
) -> tuple[SceneVersion, ...]:
    scenes = tuple(
        item
        for item in _latest_by_entity(aggregate.scenes)
        if item.episode_ref.entity_id == episode.entity_id
    )
    _require_unique_sequences(scenes, "scene")
    return tuple(sorted(scenes, key=lambda item: (item.sequence, item.entity_id, item.version_id)))


def _current_episode_shots(
    aggregate: ProductionProjectAggregate,
    scenes: tuple[SceneVersion, ...],
) -> tuple[ShotVersion, ...]:
    scene_entity_ids = {item.entity_id for item in scenes}
    shots = tuple(
        item
        for item in _latest_by_entity(aggregate.shots)
        if item.scene_ref.entity_id in scene_entity_ids
    )
    _require_unique_sequences(shots, "shot")
    return tuple(sorted(shots, key=lambda item: (item.sequence, item.entity_id, item.version_id)))


def _require_unique_sequences(records: Iterable[Any], label: str) -> None:
    seen: set[int] = set()
    for item in records:
        if item.sequence in seen:
            raise WorkspaceProjectionStateError(
                f"current {label} sequence is ambiguous inside the episode"
            )
        seen.add(item.sequence)


def _latest_by_entity(records: Iterable[Any]) -> tuple[Any, ...]:
    latest: dict[str, Any] = {}
    for item in records:
        current = latest.get(item.entity_id)
        if current is None or item.revision > current.revision:
            latest[item.entity_id] = item
    return tuple(sorted(latest.values(), key=lambda item: (item.entity_id, item.version_id)))


def _group_by_target(records: Iterable[Any]) -> dict[EntityVersionRef, tuple[Any, ...]]:
    grouped: dict[EntityVersionRef, list[Any]] = {}
    for item in records:
        grouped.setdefault(item.target_ref, []).append(item)
    return {
        target: tuple(
            sorted(
                items,
                key=lambda item: (item.created_at, item.entity_id, item.version_id),
            )
        )
        for target, items in grouped.items()
    }


def _review_notes_by_subject(
    decisions: Iterable[ReviewDecision],
) -> dict[EntityVersionRef, str]:
    grouped: dict[EntityVersionRef, list[ReviewDecision]] = {}
    for item in _latest_by_entity(decisions):
        if item.note:
            grouped.setdefault(item.subject_ref, []).append(item)
    return {
        subject: sorted(
            items,
            key=lambda item: (item.created_at, item.entity_id, item.version_id),
        )[-1].note
        for subject, items in grouped.items()
    }


def _shot_projection(
    shot: ShotVersion,
    *,
    continuity: tuple[ContinuityStateVersion, ...],
    candidates: tuple[AssetCandidateVersion, ...],
    selections: tuple[SelectedVersion, ...],
    proposals: tuple[AgentProposal, ...],
    blockers: tuple[dict[str, Any], ...],
    review_note: str | None,
) -> dict[str, Any]:
    relevant_proposals = tuple(
        item
        for item in proposals
        if item.target_ref == shot.as_ref()
        or shot.as_ref() in item.impact_refs
        or shot.as_ref() in item.applied_refs
        or item.target_ref in shot.continuity_refs
    )
    proposal_rows = [_proposal_projection(item) for item in relevant_proposals]
    selectable = any(
        item.artifact_ref is not None
        and item.lifecycle_state not in ("rejected", "retired")
        and item.job_state not in ("queued", "running", "paused", "failed", "cancelled")
        for item in candidates
    )
    mutation_blocked = bool(blockers) or shot.lifecycle_state in ("locked", "retired")
    active_selections = tuple(
        item for item in selections if item.lifecycle_state not in ("rejected", "retired")
    )
    facts = [fact for item in continuity for fact in _continuity_facts(item)]
    return {
        "ref": _ref(shot.as_ref()),
        "scene_ref": _ref(shot.scene_ref),
        "sequence": shot.sequence,
        "duration_seconds": shot.duration_seconds,
        "lifecycle_state": shot.lifecycle_state,
        "review_state": shot.review_state,
        "production_state": "rework" if shot.lifecycle_state == "rejected" else None,
        "selection_state": "selected" if active_selections else None,
        "selection_lifecycle_state": _selection_summary(active_selections),
        "ai_check_state": None,
        "delivery_invalid": False,
        "blocking": bool(blockers) or shot.review_state in ("needs_review", "rejected"),
        "script": None,
        "thumbnail_url": None,
        "review_note": review_note,
        "facts": facts,
        "continuity": [
            {
                "ref": _ref(item.as_ref()),
                "subject_type": item.subject_type,
                "identity_baseline": list(item.identity_baseline),
                "temporary_state": list(item.temporary_state),
                "prohibited_changes": list(item.prohibited_changes),
            }
            for item in continuity
        ],
        "continuity_issue": (
            {
                "summary": "前序镜头尚未完成审核，请先处理前置问题。",
                "declared_impact_count": max(
                    (item["declared_impact_count"] for item in proposal_rows), default=0
                ),
                "applied_count": max(
                    (item["applied_count"] for item in proposal_rows), default=0
                ),
            }
            if blockers
            else None
        ),
        "candidates": [
            {
                "ref": _ref(item.as_ref()),
                "label": f"候选 {index}",
                "status_label": _candidate_status(item),
                "summary": None,
                "artifact_present": item.artifact_ref is not None,
                "lifecycle_state": item.lifecycle_state,
                "review_state": item.review_state,
                "job_state": item.job_state,
                "selectable": (
                    item.artifact_ref is not None
                    and item.lifecycle_state not in ("rejected", "retired")
                    and item.job_state
                    not in ("queued", "running", "paused", "failed", "cancelled")
                ),
            }
            for index, item in enumerate(candidates, start=1)
        ],
        "selections": [
            {
                "ref": _ref(item.as_ref()),
                "candidate_ref": _ref(item.candidate_ref),
                "purpose": item.purpose,
                "lifecycle_state": item.lifecycle_state,
                "review_state": item.review_state,
            }
            for item in selections
        ],
        "agent_proposals": proposal_rows,
        "agent_proposal": proposal_rows[-1] if proposal_rows else None,
        "prior_shot_blockers": list(blockers),
        "allowed_actions": [
            {"action": "inspect", "enabled": True, "reason": "", "blocked_by": []},
            {
                "action": "review_shot",
                "enabled": (
                    not blockers
                    and shot.review_state == "needs_review"
                    and shot.lifecycle_state not in ("locked", "retired")
                ),
                "reason": (
                    "请先完成前序镜头审核。"
                    if blockers
                    else "当前镜头不需要审核。"
                ),
                "blocked_by": [item["shot_ref"] for item in blockers],
            },
            {
                "action": "reassign_scene",
                "enabled": shot.lifecycle_state not in ("locked", "retired"),
                "reason": (
                    "已锁定或已归档镜头不能更换场景。"
                    if shot.lifecycle_state in ("locked", "retired")
                    else ""
                ),
                "blocked_by": [],
            },
            {
                "action": "adopt_candidate",
                "enabled": selectable and not mutation_blocked and not active_selections,
                "reason": (
                    "请先完成前序镜头审核。"
                    if blockers
                    else "当前镜头已有有效选版，请先审核、锁定或重新打开该选版。"
                    if active_selections
                    else "当前没有带安全素材且状态可选的候选。"
                    if not selectable
                    else "当前镜头状态不允许采用候选。"
                    if mutation_blocked
                    else ""
                ),
                "blocked_by": [item["shot_ref"] for item in blockers],
            },
            {
                "action": "review_selection",
                "enabled": any(
                    item.lifecycle_state == "candidate"
                    and item.review_state == "needs_review"
                    for item in active_selections
                ),
                "reason": "当前没有待审核的选版。",
                "blocked_by": [],
            },
            {
                "action": "lock_selection",
                "enabled": any(
                    item.lifecycle_state == "approved"
                    and item.review_state == "approved"
                    for item in active_selections
                ),
                "reason": "请先批准当前选版。",
                "blocked_by": [],
            },
            {
                "action": "apply_continuity",
                "enabled": bool(continuity) and not mutation_blocked,
                "reason": (
                    "请先完成前序镜头审核。"
                    if blockers
                    else "当前镜头没有可修正的连续性事实。"
                    if not continuity
                    else "当前镜头状态不允许连续性修正。"
                    if mutation_blocked
                    else ""
                ),
                "blocked_by": [item["shot_ref"] for item in blockers],
            },
        ],
    }


def _continuity_facts(item: ContinuityStateVersion) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for value in item.identity_baseline:
        rows.append({"category": "identity", "label": "身份基线", "value": value})
    for value in item.temporary_state:
        rows.append({"category": "temporary", "label": "当前状态", "value": value})
    for value in item.prohibited_changes:
        rows.append({"category": "prohibited", "label": "禁止变化", "value": value})
    return rows


def _proposal_projection(item: AgentProposal) -> dict[str, Any]:
    return {
        "ref": _ref(item.as_ref()),
        "target_ref": _ref(item.target_ref),
        "title": item.action,
        "summary": None,
        "decision_state": item.decision_state,
        "declared_impact_count": len(item.impact_refs),
        "applied_count": len(item.applied_refs),
        "declared_impact_refs": [_ref(ref) for ref in item.impact_refs],
        "applied_refs": [_ref(ref) for ref in item.applied_refs],
    }


def _selection_summary(items: tuple[SelectedVersion, ...]) -> str | None:
    for state in ("locked", "approved", "candidate"):
        if any(item.lifecycle_state == state for item in items):
            return state
    return None


def _candidate_status(item: AssetCandidateVersion) -> str:
    if item.artifact_ref is None:
        return "素材缺失"
    if item.lifecycle_state == "locked":
        return "已锁定"
    if item.review_state == "approved":
        return "已审核"
    if item.review_state == "needs_review":
        return "待审核"
    return "候选"


def _next_action(
    shots: tuple[ShotVersion, ...],
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for shot, row in zip(shots, rows, strict=True):
        action = next(
            (
                item
                for item in row["allowed_actions"]
                if item["action"] == "review_shot"
            ),
            None,
        )
        if action is not None and action["enabled"] is True:
            return {
                "action": "review_shot",
                "label": f"审核镜头 {shot.sequence}",
                "subject_ref": _ref(shot.as_ref()),
            }
    for shot, row in zip(shots, rows, strict=True):
        action = next(
            (item for item in row["allowed_actions"] if item["action"] == "adopt_candidate"),
            None,
        )
        if action is not None and action["enabled"] is True:
            return {
                "action": "adopt_candidate",
                "label": f"为镜头 {shot.sequence} 采用可用候选",
                "subject_ref": _ref(shot.as_ref()),
            }
    for action_name, label in (
        ("review_selection", "审核镜头 {sequence} 的选版"),
        ("lock_selection", "锁定镜头 {sequence} 的选版"),
    ):
        for shot, row in zip(shots, rows, strict=True):
            action = next(
                (item for item in row["allowed_actions"] if item["action"] == action_name),
                None,
            )
            if action is not None and action["enabled"] is True:
                selection = next(
                    item
                    for item in reversed(row["selections"])
                    if (
                        action_name == "review_selection"
                        and item["lifecycle_state"] == "candidate"
                    )
                    or (
                        action_name == "lock_selection"
                        and item["lifecycle_state"] == "approved"
                    )
                )
                return {
                    "action": action_name,
                    "label": label.format(sequence=shot.sequence),
                    "subject_ref": selection["ref"],
                    "shot_ref": _ref(shot.as_ref()),
                }
    return None


def _delivery_projection(
    aggregate: ProductionProjectAggregate,
    episode: EpisodeVersion,
    missing_asset_count: int,
) -> dict[str, Any]:
    current = tuple(
        item
        for item in _latest_by_entity(aggregate.deliveries)
        if item.episode_ref == episode.as_ref()
        and item.lifecycle_state == "locked"
        and item.review_state == "approved"
    )
    if len(current) > 1:
        raise WorkspaceProjectionStateError("episode has ambiguous current locked deliveries")
    delivery: DeliveryVersion | None = current[0] if current else None
    preview_present = delivery is not None and delivery.preview_artifact_ref is not None
    blockers: list[str] = []
    if missing_asset_count:
        blockers.append("missing_assets")
    if delivery is None:
        blockers.append("delivery_not_frozen")
    elif preview_present:
        blockers.append("preview_availability_unverified")
    else:
        blockers.append("preview_missing")
    return {
        "current_ref": _ref(delivery.as_ref()) if delivery is not None else None,
        "status": "blocked" if blockers else "ready",
        "missing_asset_count": missing_asset_count,
        "preview_artifact_present": preview_present,
        "playable_preview_available": False,
        "blockers": blockers,
    }


def _project_for_episode(aggregate: ProductionProjectAggregate, episode: EpisodeVersion):
    series = _series_for_episode(aggregate, episode)
    exact = next((item for item in aggregate.projects if item.as_ref() == series.project_ref), None)
    if exact is None:
        raise WorkspaceProjectionReferenceError("episode project reference does not resolve")
    latest = next(
        item
        for item in _latest_by_entity(aggregate.projects)
        if item.entity_id == exact.entity_id
    )
    if latest.as_ref() != exact.as_ref() or exact.entity_id != aggregate.scope.project_id:
        raise WorkspaceProjectionReferenceError(
            "episode project reference is stale or out of scope"
        )
    return exact


def _series_for_episode(aggregate: ProductionProjectAggregate, episode: EpisodeVersion):
    exact = next((item for item in aggregate.series if item.as_ref() == episode.series_ref), None)
    if exact is None:
        raise WorkspaceProjectionReferenceError("episode series reference does not resolve")
    latest = next(
        item
        for item in _latest_by_entity(aggregate.series)
        if item.entity_id == exact.entity_id
    )
    if latest.as_ref() != exact.as_ref():
        raise WorkspaceProjectionReferenceError("episode series reference is stale")
    return exact


def _version_record(item: VersionedFact, **extra: Any) -> dict[str, Any]:
    return {
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "version_id": item.version_id,
        "revision": item.revision,
        **extra,
    }


def _ref(ref: EntityVersionRef) -> dict[str, str]:
    return ref.model_dump(mode="json")


def _require_safe_serialization(value: dict[str, Any]) -> None:
    # Exercise serialization itself so the read model cannot contain values that
    # FastAPI would only fail on after the route has started writing a response.
    json.dumps(value, ensure_ascii=False, sort_keys=True)
    try:
        _reject_unsafe_visible_value(value)
    except ValueError as exc:
        raise WorkspaceProjectionStateError(
            "workspace projection contains a private path or signed credential"
        ) from exc


def _reject_unsafe_visible_value(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _reject_unsafe_visible_value(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe_visible_value(item)
        return
    if not isinstance(value, str):
        return
    decoded = _decoded_string(value).replace("&amp;", "&").strip()
    # Credential-bearing URLs are rejected before safe URL spans are removed.
    # The remaining scanner therefore treats every slash-prefixed token as a
    # local absolute path without mistaking a proven credential-free http(s)
    # URL path for local filesystem data.
    without_safe_urls = _SAFE_HTTP_URL_RE.sub("", decoded)
    if (
        _CREDENTIAL_ASSIGNMENT_RE.search(decoded)
        or _FILE_URI_RE.search(decoded)
        or _WINDOWS_ABSOLUTE_PATH_RE.search(without_safe_urls)
        or _FORWARD_UNC_PATH_RE.search(without_safe_urls)
        or _POSIX_ABSOLUTE_PATH_RE.search(without_safe_urls)
    ):
        raise ValueError("unsafe workspace projection string")


def _decoded_string(value: str) -> str:
    decoded = value
    for _ in range(3):
        candidate = unquote(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    return decoded


__all__ = (
    "CREATOR_AUTHORING_PROJECTION_SCHEMA_VERSION",
    "EPISODE_WORKSPACE_PROJECTION_SCHEMA_VERSION",
    "EpisodeWorkspaceProjectionError",
    "WorkspaceProjectionReferenceError",
    "WorkspaceProjectionStateError",
    "build_creator_authoring_projection",
    "build_episode_workspace_projection",
)
