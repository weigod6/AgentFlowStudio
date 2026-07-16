from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from apps.api.runtime_episode_domain_contract import (
    AssetCandidateVersion,
    ContinuityStateVersion,
    EntityVersionRef,
    EpisodeVersion,
    ProductionProjectAggregate,
    ProjectVersion,
    ReviewDecision,
    SafeArtifactRef,
    SceneVersion,
    SeriesVersion,
    ShotVersion,
    TenantScope,
)
from apps.api.runtime_episode_review_delivery_service import (
    ArtifactAvailabilityProof,
    DeliveryNotReadyError,
    ReviewDeliveryReferenceError,
    ReviewDeliveryScopeError,
    ReviewDeliveryStateError,
    ReviewDeliveryVersionConflictError,
    assess_current_delivery,
    assess_delivery_readiness,
    compare_candidate_versions,
    freeze_delivery,
    lock_selection,
    request_selection_revision,
    retire_selection,
    restore_selection,
    review_selection,
    select_candidate,
    unlock_delivery,
    unlock_selection,
)


SCOPE = TenantScope(org_id="small-studio", project_id="rainlight", actor_id="creator-1")
FOREIGN_ACTOR_SCOPE = TenantScope(
    org_id=SCOPE.org_id,
    project_id=SCOPE.project_id,
    actor_id="creator-2",
)
TIMES = tuple(f"2026-07-15T08:{minute:02d}:00+00:00" for minute in range(30))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ref(item) -> EntityVersionRef:
    return item.as_ref()


def _fact(
    entity_id: str,
    *,
    created_at: str = TIMES[0],
    lifecycle_state: str = "approved",
    review_state: str = "approved",
) -> dict:
    return {
        "entity_id": entity_id,
        "version_id": f"{entity_id}.v1",
        "revision": 1,
        "lifecycle_state": lifecycle_state,
        "review_state": review_state,
        "content_digest": _digest(entity_id),
        "scope": SCOPE,
        "created_at": created_at,
    }


def _aggregate() -> ProductionProjectAggregate:
    project = ProjectVersion(**_fact(SCOPE.project_id), title="Rainlight")
    series = SeriesVersion(**_fact("series-1"), project_ref=_ref(project), title="Series")
    episode = EpisodeVersion(**_fact("episode-1"), series_ref=_ref(series), title="Episode")
    scene = SceneVersion(
        **_fact("scene-1"), episode_ref=_ref(episode), sequence=1, title="Archive Tower"
    )
    shot = ShotVersion(
        **_fact("shot-1"), scene_ref=_ref(scene), sequence=1, duration_seconds=9
    )
    continuity = ContinuityStateVersion(
        **_fact("character-lin"),
        subject_type="character",
        subject_id="lin",
    )
    candidate_v1 = AssetCandidateVersion(
        **_fact("candidate-shot-1", created_at=TIMES[1]),
        target_ref=_ref(shot),
        artifact_ref=_artifact("artifact-shot-v1"),
    )
    candidate_v2 = candidate_v1.model_copy(
        update={
            "version_id": "candidate-shot-1.v2",
            "revision": 2,
            "parent_version_id": candidate_v1.version_id,
            "content_digest": _digest("candidate-shot-v2"),
            "artifact_ref": _artifact("artifact-shot-v2"),
            "created_at": TIMES[2],
        }
    )
    candidate_v2_approval = ReviewDecision(
        **_fact("review-candidate-v2", created_at=TIMES[2]),
        subject_ref=_ref(candidate_v2),
        decision="approve",
    )
    continuity_candidate = AssetCandidateVersion(
        **_fact("candidate-character", created_at=TIMES[1]),
        target_ref=_ref(continuity),
        artifact_ref=_artifact("artifact-character"),
    )
    return ProductionProjectAggregate(
        aggregate_version=1,
        evaluated_at=TIMES[3],
        scope=SCOPE,
        projects=(project,),
        series=(series,),
        episodes=(episode,),
        scenes=(scene,),
        shots=(shot,),
        continuity_states=(continuity,),
        asset_candidates=(candidate_v1, candidate_v2, continuity_candidate),
        review_decisions=(candidate_v2_approval,),
    )


def _artifact(name: str, artifact_type: str = "image") -> SafeArtifactRef:
    return SafeArtifactRef(
        artifact_id=name,
        artifact_type=artifact_type,
        content_digest=_digest(name),
    )


def _proof(
    artifact_ref: SafeArtifactRef,
    *,
    playable: bool = False,
    available: bool = True,
) -> ArtifactAvailabilityProof:
    return ArtifactAvailabilityProof(
        artifact_ref=artifact_ref,
        verification_id=f"verify-{artifact_ref.artifact_id}",
        available=available,
        playable=playable,
    )


def _delivery_proofs(
    aggregate: ProductionProjectAggregate,
    preview: SafeArtifactRef,
    exports: tuple[SafeArtifactRef, ...] = (),
) -> tuple[ArtifactAvailabilityProof, ...]:
    selected_artifacts = tuple(
        candidate.artifact_ref
        for selection in aggregate.selections
        for candidate in aggregate.asset_candidates
        if selection == aggregate.selections[-1]
        and candidate.as_ref() == selection.candidate_ref
        and candidate.artifact_ref is not None
    )
    return (
        *(_proof(ref) for ref in selected_artifacts),
        _proof(preview, playable=True),
        *(_proof(ref) for ref in exports),
    )


def _selected_v2(aggregate: ProductionProjectAggregate) -> ProductionProjectAggregate:
    return select_candidate(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        candidate_ref=_ref(aggregate.asset_candidates[1]),
        purpose="storyboard",
        selection_entity_id="selection-shot-1",
        selection_version_id="selection-shot-1.v1",
        created_at=TIMES[4],
    )


def _approved(aggregate: ProductionProjectAggregate) -> ProductionProjectAggregate:
    return review_selection(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        selection_ref=_ref(aggregate.selections[-1]),
        decision="approve",
        selection_version_id="selection-shot-1.v2",
        decision_entity_id="review-selection-v2",
        decision_version_id="review-selection-v2.v1",
        created_at=TIMES[5],
    )


def _locked(aggregate: ProductionProjectAggregate) -> ProductionProjectAggregate:
    return lock_selection(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        selection_ref=_ref(aggregate.selections[-1]),
        selection_version_id="selection-shot-1.v3",
        decision_entity_id="lock-selection-v3",
        decision_version_id="lock-selection-v3.v1",
        created_at=TIMES[6],
    )


def _frozen(aggregate: ProductionProjectAggregate) -> ProductionProjectAggregate:
    preview = _artifact("preview-episode", "video")
    exports = (_artifact("export-episode", "video"),)
    return freeze_delivery(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        episode_ref=_ref(aggregate.episodes[0]),
        selection_refs=(_ref(aggregate.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=preview,
        export_artifact_refs=exports,
        artifact_proofs=_delivery_proofs(aggregate, preview, exports),
        delivery_entity_id="delivery-episode-1",
        delivery_version_id="delivery-episode-1.v1",
        created_at=TIMES[7],
    )


def _duplicate_exact_approval(
    aggregate: ProductionProjectAggregate,
    *,
    selection_ref: EntityVersionRef,
    entity_id: str,
    created_at: str,
) -> ProductionProjectAggregate:
    duplicate = ReviewDecision(
        **_fact(entity_id, created_at=created_at),
        subject_ref=selection_ref,
        decision="approve",
    )
    payload = aggregate.model_dump(mode="python")
    payload["review_decisions"] = (*aggregate.review_decisions, duplicate)
    return ProductionProjectAggregate.model_validate(payload)


def _replace_approval_actor(
    aggregate: ProductionProjectAggregate,
    *,
    approval_ref: EntityVersionRef,
    scope: TenantScope,
) -> ProductionProjectAggregate:
    payload = aggregate.model_dump(mode="python")
    payload["review_decisions"] = tuple(
        decision.model_copy(update={"scope": scope})
        if decision.as_ref() == approval_ref
        else decision
        for decision in aggregate.review_decisions
    )
    return ProductionProjectAggregate.model_validate(payload)


def test_compare_v1_v2_and_reject_cross_target_versions() -> None:
    aggregate = _aggregate()
    comparison = compare_candidate_versions(
        aggregate,
        scope=SCOPE,
        target_ref=_ref(aggregate.shots[0]),
        candidate_refs=(_ref(aggregate.asset_candidates[1]), _ref(aggregate.asset_candidates[0])),
    )

    assert [item.version_id for item in comparison.candidates] == [
        "candidate-shot-1.v1",
        "candidate-shot-1.v2",
    ]
    with pytest.raises(ReviewDeliveryReferenceError, match="cannot cross"):
        compare_candidate_versions(
            aggregate,
            scope=SCOPE,
            target_ref=_ref(aggregate.shots[0]),
            candidate_refs=(
                _ref(aggregate.asset_candidates[0]),
                _ref(aggregate.asset_candidates[2]),
            ),
        )


def test_new_selection_rejects_stale_candidate_version_and_parallel_authority() -> None:
    aggregate = _aggregate()
    with pytest.raises(ReviewDeliveryVersionConflictError, match="not the latest"):
        select_candidate(
            aggregate,
            scope=SCOPE,
            expected_aggregate_version=aggregate.aggregate_version,
            candidate_ref=_ref(aggregate.asset_candidates[0]),
            purpose="storyboard",
            selection_entity_id="selection-stale",
            selection_version_id="selection-stale.v1",
            created_at=TIMES[4],
        )

    candidate_v2 = aggregate.asset_candidates[1]
    candidate_v3 = candidate_v2.model_copy(
        update={
            "version_id": "candidate-shot-1.v3",
            "revision": 3,
            "parent_version_id": candidate_v2.version_id,
            "lifecycle_state": "candidate",
            "review_state": "needs_review",
            "content_digest": _digest("candidate-shot-v3"),
            "artifact_ref": _artifact("artifact-shot-v3"),
            "created_at": TIMES[3],
        }
    )
    payload = aggregate.model_dump(mode="python")
    payload["asset_candidates"] = (*aggregate.asset_candidates, candidate_v3)
    with_v3 = ProductionProjectAggregate.model_validate(payload)
    with pytest.raises(ReviewDeliveryVersionConflictError, match="not the latest"):
        select_candidate(
            with_v3,
            scope=SCOPE,
            expected_aggregate_version=with_v3.aggregate_version,
            candidate_ref=_ref(candidate_v2),
            purpose="storyboard",
            selection_entity_id="selection-stale-v2",
            selection_version_id="selection-stale-v2.v1",
            created_at=TIMES[4],
        )

    selected = _selected_v2(aggregate)
    with pytest.raises(ReviewDeliveryStateError, match="active selection authority"):
        select_candidate(
            selected,
            scope=SCOPE,
            expected_aggregate_version=selected.aggregate_version,
            candidate_ref=_ref(selected.asset_candidates[1]),
            purpose="storyboard",
            selection_entity_id="selection-parallel",
            selection_version_id="selection-parallel.v1",
            created_at=TIMES[5],
        )


def test_select_v2_approve_lock_and_freeze_append_exact_history() -> None:
    original = _aggregate()
    selected = _selected_v2(original)
    approved = _approved(selected)
    locked = _locked(approved)
    frozen = _frozen(locked)

    assert original.selections == ()
    assert [item.lifecycle_state for item in frozen.selections] == [
        "candidate",
        "approved",
        "locked",
    ]
    assert frozen.selections[-1].candidate_ref == _ref(original.asset_candidates[1])
    assert frozen.deliveries[-1].selection_refs == (_ref(frozen.selections[-1]),)
    assert frozen.deliveries[-1].preview_artifact_ref == _artifact("preview-episode", "video")
    assert frozen.deliveries[-1].export_artifact_refs == (
        _artifact("export-episode", "video"),
    )
    assert frozen.aggregate_version == 5
    assert ProductionProjectAggregate.model_validate(frozen.model_dump()) == frozen


def test_duplicate_approve_fails_without_appending_another_revision() -> None:
    approved = _approved(_selected_v2(_aggregate()))
    before = approved.model_dump(mode="python")

    with pytest.raises(ReviewDeliveryStateError, match="duplicate approve"):
        review_selection(
            approved,
            scope=SCOPE,
            expected_aggregate_version=approved.aggregate_version,
            selection_ref=_ref(approved.selections[-1]),
            decision="approve",
            selection_version_id="selection-shot-1.v3",
            decision_entity_id="duplicate-approve",
            decision_version_id="duplicate-approve.v1",
            created_at=TIMES[6],
        )

    assert approved.model_dump(mode="python") == before


def test_foreign_actor_approval_cannot_authorize_lock_readiness_or_freeze() -> None:
    approved = _approved(_selected_v2(_aggregate()))
    foreign_approved = _replace_approval_actor(
        approved,
        approval_ref=_ref(approved.review_decisions[-1]),
        scope=FOREIGN_ACTOR_SCOPE,
    )
    with pytest.raises(ReviewDeliveryStateError, match="no exact approval"):
        lock_selection(
            foreign_approved,
            scope=SCOPE,
            expected_aggregate_version=foreign_approved.aggregate_version,
            selection_ref=_ref(foreign_approved.selections[-1]),
            selection_version_id="selection-shot-1.v3",
            decision_entity_id="lock-with-foreign-approval",
            decision_version_id="lock-with-foreign-approval.v1",
            created_at=TIMES[6],
        )

    locked = _locked(approved)
    foreign_locked = _replace_approval_actor(
        locked,
        approval_ref=_ref(locked.review_decisions[-1]),
        scope=FOREIGN_ACTOR_SCOPE,
    )
    preview = _artifact("preview-foreign-approval", "video")
    proofs = _delivery_proofs(foreign_locked, preview)
    readiness = assess_delivery_readiness(
        foreign_locked,
        scope=SCOPE,
        episode_ref=_ref(foreign_locked.episodes[0]),
        selection_refs=(_ref(foreign_locked.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=preview,
        artifact_proofs=proofs,
    )
    assert (
        "selection_approval_missing:selection-shot-1:selection-shot-1.v3"
        in readiness.blockers
    )
    with pytest.raises(DeliveryNotReadyError) as exc_info:
        freeze_delivery(
            foreign_locked,
            scope=SCOPE,
            expected_aggregate_version=foreign_locked.aggregate_version,
            episode_ref=_ref(foreign_locked.episodes[0]),
            selection_refs=(_ref(foreign_locked.selections[-1]),),
            missing_inventory_count=0,
            preview_artifact_ref=preview,
            export_artifact_refs=(),
            artifact_proofs=proofs,
            delivery_entity_id="delivery-foreign-approval",
            delivery_version_id="delivery-foreign-approval.v1",
            created_at=TIMES[7],
        )
    assert readiness.blockers == exc_info.value.readiness.blockers


def test_duplicate_exact_approvals_fail_closed_for_lock_readiness_and_freeze() -> None:
    approved = _approved(_selected_v2(_aggregate()))
    duplicate_approved = _duplicate_exact_approval(
        approved,
        selection_ref=_ref(approved.selections[-1]),
        entity_id="duplicate-selection-v2-approval",
        created_at=TIMES[5],
    )
    with pytest.raises(ReviewDeliveryStateError, match="multiple exact approval"):
        lock_selection(
            duplicate_approved,
            scope=SCOPE,
            expected_aggregate_version=duplicate_approved.aggregate_version,
            selection_ref=_ref(duplicate_approved.selections[-1]),
            selection_version_id="selection-shot-1.v3",
            decision_entity_id="lock-with-duplicate-approval",
            decision_version_id="lock-with-duplicate-approval.v1",
            created_at=TIMES[6],
        )
    with pytest.raises(ReviewDeliveryStateError, match="duplicate approve"):
        review_selection(
            duplicate_approved,
            scope=SCOPE,
            expected_aggregate_version=duplicate_approved.aggregate_version,
            selection_ref=_ref(duplicate_approved.selections[-1]),
            decision="approve",
            selection_version_id="selection-shot-1.v3",
            decision_entity_id="review-with-duplicate-approval",
            decision_version_id="review-with-duplicate-approval.v1",
            created_at=TIMES[6],
        )

    locked = _locked(approved)
    duplicate_locked = _duplicate_exact_approval(
        locked,
        selection_ref=_ref(locked.selections[-1]),
        entity_id="duplicate-selection-v3-approval",
        created_at=TIMES[6],
    )
    preview = _artifact("preview-duplicate-approval", "video")
    proofs = _delivery_proofs(duplicate_locked, preview)
    readiness = assess_delivery_readiness(
        duplicate_locked,
        scope=SCOPE,
        episode_ref=_ref(duplicate_locked.episodes[0]),
        selection_refs=(_ref(duplicate_locked.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=preview,
        artifact_proofs=proofs,
    )
    assert (
        "selection_approval_duplicate:selection-shot-1:selection-shot-1.v3"
        in readiness.blockers
    )
    with pytest.raises(DeliveryNotReadyError) as exc_info:
        freeze_delivery(
            duplicate_locked,
            scope=SCOPE,
            expected_aggregate_version=duplicate_locked.aggregate_version,
            episode_ref=_ref(duplicate_locked.episodes[0]),
            selection_refs=(_ref(duplicate_locked.selections[-1]),),
            missing_inventory_count=0,
            preview_artifact_ref=preview,
            export_artifact_refs=(),
            artifact_proofs=proofs,
            delivery_entity_id="delivery-duplicate-approval",
            delivery_version_id="delivery-duplicate-approval.v1",
            created_at=TIMES[7],
        )
    assert readiness.blockers == exc_info.value.readiness.blockers


def test_parallel_selection_authorities_block_lock_and_freeze() -> None:
    locked = _locked(_approved(_selected_v2(_aggregate())))
    parallel = locked.selections[-1].model_copy(
        update={
            "entity_id": "selection-parallel",
            "version_id": "selection-parallel.v1",
            "revision": 1,
            "parent_version_id": None,
        }
    )
    parallel_approval = ReviewDecision(
        **_fact("approve-selection-parallel", created_at=TIMES[6]),
        subject_ref=_ref(parallel),
        decision="approve",
    )
    payload = locked.model_dump(mode="python")
    payload.update(
        {
            "selections": (*locked.selections, parallel),
            "review_decisions": (*locked.review_decisions, parallel_approval),
        }
    )
    duplicated = ProductionProjectAggregate.model_validate(payload)
    preview = _artifact("preview-duplicate", "video")
    proofs = _delivery_proofs(duplicated, preview)

    readiness = assess_delivery_readiness(
        duplicated,
        scope=SCOPE,
        episode_ref=_ref(duplicated.episodes[0]),
        selection_refs=(_ref(duplicated.selections[-2]), _ref(parallel)),
        missing_inventory_count=0,
        preview_artifact_ref=preview,
        artifact_proofs=proofs,
    )
    assert any(
        blocker.startswith("selection_authority_duplicate:")
        for blocker in readiness.blockers
    )
    with pytest.raises(ReviewDeliveryStateError, match="active selection authority"):
        lock_selection(
            duplicated,
            scope=SCOPE,
            expected_aggregate_version=duplicated.aggregate_version,
            selection_ref=_ref(duplicated.selections[-2]),
            selection_version_id="selection-shot-1.v4",
            decision_entity_id="lock-duplicate",
            decision_version_id="lock-duplicate.v1",
            created_at=TIMES[7],
        )
    with pytest.raises(DeliveryNotReadyError):
        freeze_delivery(
            duplicated,
            scope=SCOPE,
            expected_aggregate_version=duplicated.aggregate_version,
            episode_ref=_ref(duplicated.episodes[0]),
            selection_refs=(_ref(duplicated.selections[-2]), _ref(parallel)),
            missing_inventory_count=0,
            preview_artifact_ref=preview,
            export_artifact_refs=(),
            artifact_proofs=proofs,
            delivery_entity_id="delivery-duplicate",
            delivery_version_id="delivery-duplicate.v1",
            created_at=TIMES[7],
        )


def test_reject_appends_history_without_deleting_candidate_or_selection() -> None:
    selected = _selected_v2(_aggregate())
    rejected = review_selection(
        selected,
        scope=SCOPE,
        expected_aggregate_version=selected.aggregate_version,
        selection_ref=_ref(selected.selections[-1]),
        decision="reject",
        selection_version_id="selection-shot-1.v2",
        decision_entity_id="reject-selection-v2",
        decision_version_id="reject-selection-v2.v1",
        created_at=TIMES[5],
        note="continuity mismatch",
    )

    assert len(rejected.asset_candidates) == 3
    assert [item.lifecycle_state for item in rejected.selections] == ["candidate", "rejected"]
    assert rejected.review_decisions[-1].decision == "reject"
    assert rejected.review_decisions[-1].subject_ref == _ref(rejected.selections[-1])


def test_unlock_appends_decision_and_makes_old_lock_stale() -> None:
    locked = _locked(_approved(_selected_v2(_aggregate())))
    unlocked = unlock_selection(
        locked,
        scope=SCOPE,
        expected_aggregate_version=locked.aggregate_version,
        selection_ref=_ref(locked.selections[-1]),
        selection_version_id="selection-shot-1.v4",
        decision_entity_id="unlock-selection-v3",
        decision_version_id="unlock-selection-v3.v1",
        created_at=TIMES[7],
    )

    assert unlocked.selections[-2].lifecycle_state == "locked"
    assert unlocked.selections[-1].lifecycle_state == "approved"
    assert unlocked.review_decisions[-1].decision == "unlock"
    readiness = assess_delivery_readiness(
        unlocked,
        scope=SCOPE,
        episode_ref=_ref(unlocked.episodes[0]),
        selection_refs=(_ref(unlocked.selections[-2]),),
        missing_inventory_count=0,
        preview_artifact_ref=_artifact("preview-episode", "video"),
    )
    assert "selection_not_latest:selection-shot-1:selection-shot-1.v3" in readiness.blockers


def test_restore_v1_creates_new_selection_version_and_requires_new_exact_review() -> None:
    approved = _approved(_selected_v2(_aggregate()))
    restored = restore_selection(
        approved,
        scope=SCOPE,
        expected_aggregate_version=approved.aggregate_version,
        selection_ref=_ref(approved.selections[-1]),
        historical_candidate_ref=_ref(approved.asset_candidates[0]),
        selection_version_id="selection-shot-1.v3",
        created_at=TIMES[6],
    )

    assert restored.selections[-1].candidate_ref.version_id == "candidate-shot-1.v1"
    assert restored.selections[-1].lifecycle_state == "candidate"
    assert restored.selections[-1].review_state == "needs_review"
    readiness = assess_delivery_readiness(
        restored,
        scope=SCOPE,
        episode_ref=_ref(restored.episodes[0]),
        selection_refs=(_ref(restored.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=_artifact("preview-episode", "video"),
    )
    assert "selection_approval_missing:selection-shot-1:selection-shot-1.v3" in readiness.blockers
    assert "selection_not_locked:selection-shot-1:selection-shot-1.v3" in readiness.blockers


def test_changed_candidate_selection_cannot_reuse_old_approval() -> None:
    v2_approved = _approved(_selected_v2(_aggregate()))
    changed = restore_selection(
        v2_approved,
        scope=SCOPE,
        expected_aggregate_version=v2_approved.aggregate_version,
        selection_ref=_ref(v2_approved.selections[-1]),
        historical_candidate_ref=_ref(v2_approved.asset_candidates[0]),
        selection_version_id="selection-shot-1.v3",
        created_at=TIMES[6],
    )

    exact_old_approvals = [
        item
        for item in changed.review_decisions
        if item.decision == "approve" and item.subject_ref == _ref(changed.selections[-1])
    ]
    assert exact_old_approvals == []
    with pytest.raises(ReviewDeliveryStateError, match="only an approved selection"):
        lock_selection(
            changed,
            scope=SCOPE,
            expected_aggregate_version=changed.aggregate_version,
            selection_ref=_ref(changed.selections[-1]),
            selection_version_id="selection-shot-1.v4",
            decision_entity_id="lock-selection-v4",
            decision_version_id="lock-selection-v4.v1",
            created_at=TIMES[7],
        )


def test_cross_scope_cross_target_and_stale_cas_are_rejected() -> None:
    aggregate = _aggregate()
    foreign_scope = TenantScope(
        org_id=SCOPE.org_id,
        project_id="another-project",
        actor_id=SCOPE.actor_id,
    )
    with pytest.raises(ReviewDeliveryScopeError, match="exactly match"):
        compare_candidate_versions(
            aggregate,
            scope=foreign_scope,
            target_ref=_ref(aggregate.shots[0]),
        )
    foreign_actor = TenantScope(
        org_id=SCOPE.org_id,
        project_id=SCOPE.project_id,
        actor_id="creator-2",
    )
    with pytest.raises(ReviewDeliveryScopeError, match="actor"):
        compare_candidate_versions(
            aggregate,
            scope=foreign_actor,
            target_ref=_ref(aggregate.shots[0]),
        )
    with pytest.raises(ReviewDeliveryVersionConflictError, match="expected 0, current 1"):
        select_candidate(
            aggregate,
            scope=SCOPE,
            expected_aggregate_version=0,
            candidate_ref=_ref(aggregate.asset_candidates[1]),
            purpose="storyboard",
            selection_entity_id="selection-shot-1",
            selection_version_id="selection-shot-1.v1",
            created_at=TIMES[4],
        )
    selected = _selected_v2(aggregate)
    approved = _approved(selected)
    with pytest.raises(ReviewDeliveryVersionConflictError, match="not the latest"):
        review_selection(
            approved,
            scope=SCOPE,
            expected_aggregate_version=approved.aggregate_version,
            selection_ref=_ref(approved.selections[0]),
            decision="approve",
            selection_version_id="selection-shot-1.v3",
            decision_entity_id="review-stale-selection",
            decision_version_id="review-stale-selection.v1",
            created_at=TIMES[6],
        )
    with pytest.raises(ReviewDeliveryReferenceError, match="exact selection target"):
        restore_selection(
            selected,
            scope=SCOPE,
            expected_aggregate_version=selected.aggregate_version,
            selection_ref=_ref(selected.selections[-1]),
            historical_candidate_ref=_ref(selected.asset_candidates[2]),
            selection_version_id="selection-shot-1.v2",
            created_at=TIMES[5],
        )


@pytest.mark.parametrize("job_state", ["running", "failed"])
def test_running_or_failed_candidate_cannot_be_selected(job_state: str) -> None:
    aggregate = _aggregate()
    candidate = aggregate.asset_candidates[0].model_copy(
        update={
            "lifecycle_state": "candidate",
            "review_state": "needs_review",
            "artifact_ref": None,
            "job_id": "job-1",
            "job_state": job_state,
        }
    )
    payload = aggregate.model_dump(mode="python")
    payload.update(
        {
            "asset_candidates": (candidate, aggregate.asset_candidates[2]),
            "review_decisions": (),
        }
    )
    isolated = ProductionProjectAggregate.model_validate(payload)

    with pytest.raises(ReviewDeliveryStateError, match=f"{job_state} state"):
        select_candidate(
            isolated,
            scope=SCOPE,
            expected_aggregate_version=isolated.aggregate_version,
            candidate_ref=_ref(candidate),
            purpose="storyboard",
            selection_entity_id="selection-shot-1",
            selection_version_id="selection-shot-1.v1",
            created_at=TIMES[4],
        )


def test_candidate_without_artifact_cannot_be_selected() -> None:
    aggregate = _aggregate()
    candidate = aggregate.asset_candidates[0].model_copy(
        update={
            "lifecycle_state": "candidate",
            "review_state": "needs_review",
            "artifact_ref": None,
        }
    )
    payload = aggregate.model_dump(mode="python")
    payload.update(
        {
            "asset_candidates": (candidate, aggregate.asset_candidates[2]),
            "review_decisions": (),
        }
    )
    isolated = ProductionProjectAggregate.model_validate(payload)

    with pytest.raises(ReviewDeliveryStateError, match="without a safe artifact"):
        select_candidate(
            isolated,
            scope=SCOPE,
            expected_aggregate_version=isolated.aggregate_version,
            candidate_ref=_ref(candidate),
            purpose="storyboard",
            selection_entity_id="selection-shot-1",
            selection_version_id="selection-shot-1.v1",
            created_at=TIMES[4],
        )


def test_twenty_five_missing_inventory_blocks_delivery_without_fake_preview() -> None:
    aggregate = _aggregate()
    readiness = assess_delivery_readiness(
        aggregate,
        scope=SCOPE,
        episode_ref=_ref(aggregate.episodes[0]),
        selection_refs=(),
        missing_inventory_count=25,
        preview_artifact_ref=None,
    )

    assert readiness.ready is False
    assert readiness.missing_inventory_count == 25
    assert readiness.preview_artifact_ref is None
    assert "missing_inventory:25" in readiness.blockers
    assert "playable_preview_missing" in readiness.blockers
    with pytest.raises(DeliveryNotReadyError) as exc_info:
        freeze_delivery(
            aggregate,
            scope=SCOPE,
            expected_aggregate_version=aggregate.aggregate_version,
            episode_ref=_ref(aggregate.episodes[0]),
            selection_refs=(),
            missing_inventory_count=25,
            preview_artifact_ref=None,
            export_artifact_refs=(),
            artifact_proofs=(),
            delivery_entity_id="delivery-blocked",
            delivery_version_id="delivery-blocked.v1",
            created_at=TIMES[4],
        )
    assert exc_info.value.readiness.preview_artifact_ref is None
    assert aggregate.deliveries == ()


def test_delivery_blocks_when_episode_continuity_exact_selection_is_open() -> None:
    aggregate = _aggregate()
    shot_with_continuity = aggregate.shots[0].model_copy(
        update={"continuity_refs": (_ref(aggregate.continuity_states[0]),)}
    )
    payload = aggregate.model_dump(mode="python")
    payload["shots"] = (shot_with_continuity,)
    with_continuity = ProductionProjectAggregate.model_validate(payload)
    locked = _locked(_approved(_selected_v2(with_continuity)))

    readiness = assess_delivery_readiness(
        locked,
        scope=SCOPE,
        episode_ref=_ref(locked.episodes[0]),
        selection_refs=(_ref(locked.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=_artifact("preview-episode", "video"),
    )

    assert "continuity_selection_missing:character-lin:character-lin.v1" in readiness.blockers
    with pytest.raises(DeliveryNotReadyError):
        freeze_delivery(
            locked,
            scope=SCOPE,
            expected_aggregate_version=locked.aggregate_version,
            episode_ref=_ref(locked.episodes[0]),
            selection_refs=(_ref(locked.selections[-1]),),
            missing_inventory_count=0,
            preview_artifact_ref=_artifact("preview-episode", "video"),
            export_artifact_refs=(),
            artifact_proofs=(),
            delivery_entity_id="delivery-open-continuity",
            delivery_version_id="delivery-open-continuity.v1",
            created_at=TIMES[7],
        )


def test_delivery_unlock_is_append_only_and_serializes_roundtrip() -> None:
    frozen = _frozen(_locked(_approved(_selected_v2(_aggregate()))))
    delivery = frozen.deliveries[-1]
    proofs = _delivery_proofs(
        frozen,
        delivery.preview_artifact_ref,
        delivery.export_artifact_refs,
    )
    assert assess_current_delivery(
        frozen,
        scope=SCOPE,
        delivery_ref=_ref(delivery),
        artifact_proofs=proofs,
    ).current_valid
    with pytest.raises(ReviewDeliveryStateError, match="current locked delivery"):
        freeze_delivery(
            frozen,
            scope=SCOPE,
            expected_aggregate_version=frozen.aggregate_version,
            episode_ref=delivery.episode_ref,
            selection_refs=delivery.selection_refs,
            missing_inventory_count=0,
            preview_artifact_ref=delivery.preview_artifact_ref,
            export_artifact_refs=delivery.export_artifact_refs,
            artifact_proofs=proofs,
            delivery_entity_id="delivery-parallel-current",
            delivery_version_id="delivery-parallel-current.v1",
            created_at=TIMES[8],
        )
    unlocked = unlock_delivery(
        frozen,
        scope=SCOPE,
        expected_aggregate_version=frozen.aggregate_version,
        delivery_ref=_ref(frozen.deliveries[-1]),
        delivery_version_id="delivery-episode-1.v2",
        decision_entity_id="unlock-delivery-v1",
        decision_version_id="unlock-delivery-v1.v1",
        created_at=TIMES[8],
    )
    restored = ProductionProjectAggregate.model_validate_json(unlocked.model_dump_json())

    assert [item.lifecycle_state for item in restored.deliveries] == ["locked", "approved"]
    assert restored.review_decisions[-1].decision == "unlock"
    assert restored.review_decisions[-1].subject_ref == _ref(restored.deliveries[0])
    assert restored == unlocked
    validity = assess_current_delivery(
        restored,
        scope=SCOPE,
        delivery_ref=_ref(restored.deliveries[0]),
        artifact_proofs=proofs,
    )
    assert validity.current_valid is False
    assert validity.delivery == restored.deliveries[0]
    assert any(blocker.startswith("delivery_not_latest:") for blocker in validity.blockers)
    assert any("delivery_invalidated:unlock:" in blocker for blocker in validity.blockers)


@pytest.mark.parametrize("approval_corruption", ["foreign_actor", "duplicate"])
def test_current_delivery_fails_closed_when_exact_approval_authority_is_corrupt(
    approval_corruption: str,
) -> None:
    frozen = _frozen(_locked(_approved(_selected_v2(_aggregate()))))
    selection = frozen.selections[-1]
    if approval_corruption == "foreign_actor":
        corrupted = _replace_approval_actor(
            frozen,
            approval_ref=frozen.deliveries[-1].review_decision_refs[0],
            scope=FOREIGN_ACTOR_SCOPE,
        )
        expected_approval_blocker = (
            "selection_approval_missing:selection-shot-1:selection-shot-1.v3"
        )
    else:
        corrupted = _duplicate_exact_approval(
            frozen,
            selection_ref=_ref(selection),
            entity_id="duplicate-current-delivery-approval",
            created_at=TIMES[7],
        )
        expected_approval_blocker = (
            "selection_approval_duplicate:selection-shot-1:selection-shot-1.v3"
        )

    delivery = corrupted.deliveries[-1]
    validity = assess_current_delivery(
        corrupted,
        scope=SCOPE,
        delivery_ref=_ref(delivery),
        artifact_proofs=_delivery_proofs(
            corrupted,
            delivery.preview_artifact_ref,
            delivery.export_artifact_refs,
        ),
    )
    assert validity.current_valid is False
    assert expected_approval_blocker in validity.blockers
    assert (
        f"delivery_approval_authority_stale:{delivery.entity_id}:{delivery.version_id}"
        in validity.blockers
    )
    assert (
        f"delivery_not_current_authority:{delivery.entity_id}:{delivery.version_id}"
        in validity.blockers
    )


def test_selection_unlock_revision_request_and_retire_invalidate_current_delivery() -> None:
    frozen = _frozen(_locked(_approved(_selected_v2(_aggregate()))))
    delivery = frozen.deliveries[-1]
    proofs = _delivery_proofs(
        frozen,
        delivery.preview_artifact_ref,
        delivery.export_artifact_refs,
    )

    unlocked = unlock_selection(
        frozen,
        scope=SCOPE,
        expected_aggregate_version=frozen.aggregate_version,
        selection_ref=_ref(frozen.selections[-1]),
        selection_version_id="selection-shot-1.v4",
        decision_entity_id="unlock-current-selection",
        decision_version_id="unlock-current-selection.v1",
        created_at=TIMES[8],
    )
    unlock_validity = assess_current_delivery(
        unlocked,
        scope=SCOPE,
        delivery_ref=_ref(delivery),
        artifact_proofs=proofs,
    )
    assert unlock_validity.current_valid is False
    assert delivery in unlocked.deliveries
    assert any("delivery_invalidated:unlock:" in item for item in unlock_validity.blockers)

    revision_requested = request_selection_revision(
        frozen,
        scope=SCOPE,
        expected_aggregate_version=frozen.aggregate_version,
        selection_ref=_ref(frozen.selections[-1]),
        selection_version_id="selection-shot-1.v4",
        decision_entity_id="revise-current-selection",
        decision_version_id="revise-current-selection.v1",
        unlock_decision_entity_id="unlock-for-revision",
        unlock_decision_version_id="unlock-for-revision.v1",
        created_at=TIMES[8],
        note="continuity repair required",
    )
    revision_validity = assess_current_delivery(
        revision_requested,
        scope=SCOPE,
        delivery_ref=_ref(delivery),
        artifact_proofs=proofs,
    )
    assert revision_requested.selections[-1].review_state == "needs_review"
    assert revision_validity.current_valid is False
    assert any(
        "delivery_invalidated:request_revision:" in item
        for item in revision_validity.blockers
    )

    retired = retire_selection(
        frozen,
        scope=SCOPE,
        expected_aggregate_version=frozen.aggregate_version,
        selection_ref=_ref(frozen.selections[-1]),
        selection_version_id="selection-shot-1.v4",
        decision_entity_id="retire-current-selection",
        decision_version_id="retire-current-selection.v1",
        created_at=TIMES[8],
        note="candidate withdrawn",
    )
    retire_validity = assess_current_delivery(
        retired,
        scope=SCOPE,
        delivery_ref=_ref(delivery),
        artifact_proofs=proofs,
    )
    assert retired.selections[-1].lifecycle_state == "retired"
    assert retire_validity.current_valid is False
    assert any("delivery_invalidated:retire:" in item for item in retire_validity.blockers)


def test_unsafe_preview_or_export_ref_fails_contract_validation() -> None:
    locked = _locked(_approved(_selected_v2(_aggregate())))
    with pytest.raises(ValidationError):
        SafeArtifactRef(
            artifact_id="../private-preview",
            artifact_type="video",
            content_digest=_digest("unsafe"),
        )
    with pytest.raises(ValidationError):
        assess_delivery_readiness(
            locked,
            scope=SCOPE,
            episode_ref=_ref(locked.episodes[0]),
            selection_refs=(_ref(locked.selections[-1]),),
            missing_inventory_count=0,
            preview_artifact_ref={  # type: ignore[arg-type]
                "artifact_id": "../private-preview",
                "artifact_type": "video",
                "content_digest": _digest("unsafe"),
            },
        )
    preview = _artifact("safe-preview", "video")
    export = _artifact("safe-export", "video")
    unverified = assess_delivery_readiness(
        locked,
        scope=SCOPE,
        episode_ref=_ref(locked.episodes[0]),
        selection_refs=(_ref(locked.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=preview,
        export_artifact_refs=(export,),
    )
    assert unverified.ready is False
    assert f"preview_artifact_unverified:{preview.artifact_id}" in unverified.blockers
    assert f"export_artifact_unverified:{export.artifact_id}" in unverified.blockers
    assert any(
        blocker.startswith("candidate_artifact_unverified:")
        for blocker in unverified.blockers
    )
    with pytest.raises(DeliveryNotReadyError) as unverified_freeze:
        freeze_delivery(
            locked,
            scope=SCOPE,
            expected_aggregate_version=locked.aggregate_version,
            episode_ref=_ref(locked.episodes[0]),
            selection_refs=(_ref(locked.selections[-1]),),
            missing_inventory_count=0,
            preview_artifact_ref=preview,
            export_artifact_refs=(export,),
            delivery_entity_id="delivery-unverified-artifacts",
            delivery_version_id="delivery-unverified-artifacts.v1",
            created_at=TIMES[7],
        )
    assert f"preview_artifact_unverified:{preview.artifact_id}" in (
        unverified_freeze.value.readiness.blockers
    )
    assert f"export_artifact_unverified:{export.artifact_id}" in (
        unverified_freeze.value.readiness.blockers
    )
    verified = assess_delivery_readiness(
        locked,
        scope=SCOPE,
        episode_ref=_ref(locked.episodes[0]),
        selection_refs=(_ref(locked.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=preview,
        export_artifact_refs=(export,),
        artifact_proofs=_delivery_proofs(locked, preview, (export,)),
    )
    assert verified.ready

    unavailable = assess_delivery_readiness(
        locked,
        scope=SCOPE,
        episode_ref=_ref(locked.episodes[0]),
        selection_refs=(_ref(locked.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=preview,
        export_artifact_refs=(export,),
        artifact_proofs=(
            *_delivery_proofs(locked, preview),
            _proof(export, available=False),
        ),
    )
    assert unavailable.ready is False
    assert f"export_artifact_unavailable:{export.artifact_id}" in unavailable.blockers

    not_playable_proofs = tuple(
        _proof(item.artifact_ref, playable=False)
        if item.artifact_ref == preview
        else item
        for item in _delivery_proofs(locked, preview, (export,))
    )
    not_playable = assess_delivery_readiness(
        locked,
        scope=SCOPE,
        episode_ref=_ref(locked.episodes[0]),
        selection_refs=(_ref(locked.selections[-1]),),
        missing_inventory_count=0,
        preview_artifact_ref=preview,
        export_artifact_refs=(export,),
        artifact_proofs=not_playable_proofs,
    )
    assert not_playable.ready is False
    assert f"preview_artifact_not_playable:{preview.artifact_id}" in not_playable.blockers
