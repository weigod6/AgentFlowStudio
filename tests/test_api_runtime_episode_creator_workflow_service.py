from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from apps.api.runtime_episode_creator_workflow_service import (
    CreatorWorkflowReferenceError,
    CreatorWorkflowScopeError,
    CreatorWorkflowStateError,
    CreatorWorkflowVersionConflictError,
    derive_prior_shot_blockers,
    reassign_shot_scene,
    review_shot_candidate,
    select_shot_candidate_if_ready,
)
from apps.api.runtime_episode_domain_contract import (
    AssetCandidateVersion,
    EntityVersionRef,
    EpisodeVersion,
    ProductionProjectAggregate,
    ProjectDataPolicy,
    ProjectVersion,
    SafeArtifactRef,
    SceneVersion,
    SeriesVersion,
    ShotVersion,
    TenantScope,
)
from apps.api.runtime_episode_domain_store import EpisodeDomainAggregateStore


BASE_TIME = "2026-07-15T08:00:00+00:00"
MOVE_TIME = "2026-07-15T08:01:00+00:00"
SHOT6_APPROVAL_TIME = "2026-07-15T08:02:00+00:00"
SHOT7_APPROVAL_TIME = "2026-07-15T08:03:00+00:00"
SELECTION_TIME = "2026-07-15T08:04:00+00:00"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def ref(value) -> EntityVersionRef:
    return value.as_ref()


def common(
    scope: TenantScope,
    entity_id: str,
    *,
    lifecycle_state: str = "approved",
    review_state: str = "approved",
    created_at: str = BASE_TIME,
) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "version_id": f"{entity_id}.v1",
        "revision": 1,
        "lifecycle_state": lifecycle_state,
        "review_state": review_state,
        "content_digest": digest(entity_id),
        "scope": scope,
        "created_at": created_at,
    }


def build_episode(
    *,
    shot_count: int | None = None,
) -> ProductionProjectAggregate:
    scope = TenantScope(org_id="org-1", project_id="project-1", actor_id="creator-1")
    project = ProjectVersion(
        **common(scope, "project-1"),
        title="雨夜追光",
        data_policy=ProjectDataPolicy(),
    )
    series = SeriesVersion(
        **common(scope, "series-1"), project_ref=ref(project), title="第一季"
    )
    episode = EpisodeVersion(
        **common(scope, "episode-1"), series_ref=ref(series), title="第一集"
    )
    episode_2 = EpisodeVersion(
        **common(scope, "episode-2"), series_ref=ref(series), title="第二集"
    )
    scene_a = SceneVersion(
        **common(scope, "scene-a"), episode_ref=ref(episode), sequence=1, title="巷口"
    )
    scene_b = SceneVersion(
        **common(scope, "scene-b"), episode_ref=ref(episode), sequence=2, title="桥下"
    )
    scene_foreign = SceneVersion(
        **common(scope, "scene-foreign"),
        episode_ref=ref(episode_2),
        sequence=1,
        title="另一集",
    )

    if shot_count is None:
        states = {
            6: ("candidate", "needs_review"),
            7: ("candidate", "needs_review"),
            8: ("approved", "approved"),
            11: ("candidate", "needs_review"),
        }
    else:
        states = {
            sequence: (
                ("candidate", "needs_review")
                if sequence < shot_count and sequence % 7 == 0
                else ("approved", "approved")
            )
            for sequence in range(1, shot_count + 1)
        }
        states[shot_count] = ("candidate", "needs_review")

    shots = tuple(
        ShotVersion(
            **common(
                scope,
                f"shot-{sequence}",
                lifecycle_state=lifecycle,
                review_state=review,
            ),
            scene_ref=ref(scene_a if sequence <= 7 else scene_b),
            sequence=sequence,
            duration_seconds=3,
        )
        for sequence, (lifecycle, review) in states.items()
    )
    target = max(shots, key=lambda item: item.sequence)
    candidate = AssetCandidateVersion(
        **common(
            scope,
            "candidate-target",
            lifecycle_state="candidate",
            review_state="needs_review",
        ),
        target_ref=ref(target),
        artifact_ref=SafeArtifactRef(
            artifact_id="artifact-target",
            artifact_type="image",
            content_digest=digest("artifact-target"),
        ),
        job_id="job-target",
        job_state="succeeded",
    )
    return ProductionProjectAggregate(
        aggregate_version=1,
        evaluated_at=BASE_TIME,
        scope=scope,
        projects=(project,),
        series=(series,),
        episodes=(episode, episode_2),
        scenes=(scene_a, scene_b, scene_foreign),
        shots=shots,
        asset_candidates=(candidate,),
    )


def latest_shot(aggregate: ProductionProjectAggregate, entity_id: str) -> ShotVersion:
    return max(
        (shot for shot in aggregate.shots if shot.entity_id == entity_id),
        key=lambda item: item.revision,
    )


def approve_shot(
    aggregate: ProductionProjectAggregate,
    entity_id: str,
    created_at: str,
) -> ProductionProjectAggregate:
    shot = latest_shot(aggregate, entity_id)
    return review_shot_candidate(
        aggregate,
        scope=aggregate.scope,
        expected_aggregate_version=aggregate.aggregate_version,
        shot_ref=ref(shot),
        decision="approve",
        shot_version_id=f"{entity_id}.v{shot.revision + 1}",
        decision_entity_id=f"decision-{entity_id}",
        decision_version_id=f"decision-{entity_id}.v1",
        created_at=created_at,
        note="创作者确认",
    )


def test_reassign_appends_canonical_candidate_and_preserves_unrelated_facts() -> None:
    aggregate = build_episode()
    shot_8 = latest_shot(aggregate, "shot-8")
    scene_a, scene_b = aggregate.scenes[:2]
    before = aggregate.model_dump_json()

    moved = reassign_shot_scene(
        aggregate,
        scope=aggregate.scope,
        expected_aggregate_version=1,
        shot_ref=ref(shot_8),
        scene_ref=ref(scene_a),
        new_version_id="shot-8.v2",
        created_at=MOVE_TIME,
    )
    successor = latest_shot(moved, "shot-8")

    assert aggregate.model_dump_json() == before
    assert successor.parent_version_id == shot_8.version_id
    assert successor.scene_ref == ref(scene_a)
    assert successor.lifecycle_state == "candidate"
    assert successor.review_state == "needs_review"
    assert successor.source_proposal_ref is None
    assert successor.continuity_refs == shot_8.continuity_refs
    assert successor.sequence == shot_8.sequence
    assert successor.duration_seconds == shot_8.duration_seconds
    assert successor.content_digest != shot_8.content_digest
    assert latest_shot(moved, "shot-6") == latest_shot(aggregate, "shot-6")
    assert scene_b == moved.scenes[1]


def test_reassign_rejects_noop_cross_episode_stale_locked_retired_and_duplicate() -> None:
    aggregate = build_episode()
    shot = latest_shot(aggregate, "shot-8")
    current_scene = next(scene for scene in aggregate.scenes if ref(scene) == shot.scene_ref)
    foreign_scene = aggregate.scenes[2]

    with pytest.raises(CreatorWorkflowStateError, match="already assigned"):
        reassign_shot_scene(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            shot_ref=ref(shot),
            scene_ref=ref(current_scene),
            new_version_id="shot-8.v2",
            created_at=MOVE_TIME,
        )
    with pytest.raises(CreatorWorkflowScopeError, match="cross an episode"):
        reassign_shot_scene(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            shot_ref=ref(shot),
            scene_ref=ref(foreign_scene),
            new_version_id="shot-8.v2",
            created_at=MOVE_TIME,
        )

    moved = reassign_shot_scene(
        aggregate,
        scope=aggregate.scope,
        expected_aggregate_version=1,
        shot_ref=ref(shot),
        scene_ref=ref(aggregate.scenes[0]),
        new_version_id="shot-8.v2",
        created_at=MOVE_TIME,
    )
    with pytest.raises(CreatorWorkflowVersionConflictError, match="latest exact"):
        reassign_shot_scene(
            moved,
            scope=moved.scope,
            expected_aggregate_version=2,
            shot_ref=ref(shot),
            scene_ref=ref(aggregate.scenes[1]),
            new_version_id="shot-8.v3",
            created_at=SHOT6_APPROVAL_TIME,
        )
    with pytest.raises(CreatorWorkflowStateError, match="unused"):
        reassign_shot_scene(
            moved,
            scope=moved.scope,
            expected_aggregate_version=2,
            shot_ref=ref(latest_shot(moved, "shot-8")),
            scene_ref=ref(aggregate.scenes[1]),
            new_version_id="shot-8.v1",
            created_at=SHOT6_APPROVAL_TIME,
        )

    for state in ("locked", "retired"):
        review_state = "approved" if state == "locked" else "approved"
        blocked_shot = shot.model_copy(
            update={"lifecycle_state": state, "review_state": review_state}
        )
        blocked = aggregate.model_copy(
            update={
                "shots": tuple(
                    blocked_shot if item.as_ref() == shot.as_ref() else item
                    for item in aggregate.shots
                )
            }
        )
        with pytest.raises(CreatorWorkflowStateError, match="locked or retired"):
            reassign_shot_scene(
                blocked,
                scope=blocked.scope,
                expected_aggregate_version=1,
                shot_ref=ref(blocked_shot),
                scene_ref=ref(aggregate.scenes[0]),
                new_version_id="shot-8.v2",
                created_at=MOVE_TIME,
            )


def test_review_candidate_appends_successor_and_exact_decision_without_mutation() -> None:
    aggregate = build_episode()
    before = aggregate.model_dump_json()
    reviewed = approve_shot(aggregate, "shot-6", SHOT6_APPROVAL_TIME)
    approved = latest_shot(reviewed, "shot-6")
    decision = reviewed.review_decisions[-1]

    assert aggregate.model_dump_json() == before
    assert approved.parent_version_id == "shot-6.v1"
    assert approved.lifecycle_state == "approved"
    assert approved.review_state == "approved"
    assert decision.subject_ref == approved.as_ref()
    assert decision.decision == "approve"
    assert decision.scope == reviewed.scope

    rejected = review_shot_candidate(
        aggregate,
        scope=aggregate.scope,
        expected_aggregate_version=1,
        shot_ref=ref(latest_shot(aggregate, "shot-7")),
        decision="reject",
        shot_version_id="shot-7.v2",
        decision_entity_id="decision-shot-7-reject",
        decision_version_id="decision-shot-7-reject.v1",
        created_at=SHOT6_APPROVAL_TIME,
    )
    assert latest_shot(rejected, "shot-7").lifecycle_state == "rejected"
    assert rejected.review_decisions[-1].decision == "reject"


def test_shot11_selection_is_blocked_until_shot6_and_shot7_exact_approvals() -> None:
    aggregate = build_episode()
    target = latest_shot(aggregate, "shot-11")
    candidate = aggregate.asset_candidates[0]
    blockers = derive_prior_shot_blockers(
        aggregate, scope=aggregate.scope, target_shot_ref=ref(target)
    )
    assert [item.shot_ref.entity_id for item in blockers] == ["shot-6", "shot-7"]

    with pytest.raises(CreatorWorkflowStateError, match="shot-6.*shot-7"):
        select_shot_candidate_if_ready(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            target_shot_ref=ref(target),
            candidate_ref=ref(candidate),
            purpose="storyboard",
            selection_entity_id="selection-shot-11",
            selection_version_id="selection-shot-11.v1",
            created_at=SELECTION_TIME,
        )

    shot6_done = approve_shot(aggregate, "shot-6", SHOT6_APPROVAL_TIME)
    blockers = derive_prior_shot_blockers(
        shot6_done, scope=shot6_done.scope, target_shot_ref=ref(target)
    )
    assert [item.shot_ref.entity_id for item in blockers] == ["shot-7"]

    with pytest.raises(CreatorWorkflowStateError, match="shot-7"):
        select_shot_candidate_if_ready(
            shot6_done,
            scope=shot6_done.scope,
            expected_aggregate_version=2,
            target_shot_ref=ref(target),
            candidate_ref=ref(candidate),
            purpose="storyboard",
            selection_entity_id="selection-shot-11",
            selection_version_id="selection-shot-11.v1",
            created_at=SELECTION_TIME,
        )

    ready = approve_shot(shot6_done, "shot-7", SHOT7_APPROVAL_TIME)
    selected = select_shot_candidate_if_ready(
        ready,
        scope=ready.scope,
        expected_aggregate_version=3,
        target_shot_ref=ref(target),
        candidate_ref=ref(candidate),
        purpose="storyboard",
        selection_entity_id="selection-shot-11",
        selection_version_id="selection-shot-11.v1",
        created_at=SELECTION_TIME,
    )
    assert selected.aggregate_version == 4
    assert selected.selections[-1].target_ref == ref(target)
    assert selected.selections[-1].candidate_ref == ref(candidate)
    assert latest_shot(selected, "shot-8") == latest_shot(aggregate, "shot-8")


def test_stale_scope_cross_target_malformed_and_time_guards_fail_closed() -> None:
    aggregate = build_episode()
    target = latest_shot(aggregate, "shot-11")
    foreign_actor = aggregate.scope.model_copy(update={"actor_id": "creator-2"})

    with pytest.raises(CreatorWorkflowVersionConflictError, match="aggregate version"):
        review_shot_candidate(
            aggregate.model_copy(update={"aggregate_version": 2}),
            scope=aggregate.scope,
            expected_aggregate_version=1,
            shot_ref=ref(latest_shot(aggregate, "shot-6")),
            decision="approve",
            shot_version_id="shot-6.v2",
            decision_entity_id="decision-stale-cas",
            decision_version_id="decision-stale-cas.v1",
            created_at=SHOT6_APPROVAL_TIME,
        )
    with pytest.raises(CreatorWorkflowScopeError, match="exactly match"):
        derive_prior_shot_blockers(
            aggregate, scope=foreign_actor, target_shot_ref=ref(target)
        )
    with pytest.raises(CreatorWorkflowReferenceError, match="target a shot"):
        derive_prior_shot_blockers(
            aggregate,
            scope=aggregate.scope,
            target_shot_ref=ref(aggregate.scenes[0]),
        )
    with pytest.raises(CreatorWorkflowStateError, match="later"):
        review_shot_candidate(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            shot_ref=ref(latest_shot(aggregate, "shot-6")),
            decision="approve",
            shot_version_id="shot-6.v2",
            decision_entity_id="decision-shot-6",
            decision_version_id="decision-shot-6.v1",
            created_at=BASE_TIME,
        )

    bad = aggregate.model_copy(
        update={
            "shots": (
                aggregate.shots[0].model_copy(
                    update={"scene_ref": ref(aggregate.scenes[2])}
                ),
                *aggregate.shots[1:],
            )
        }
    )
    # The model itself still resolves, but the workflow gate never infers
    # dependencies from another episode or from caller-provided local state.
    blockers = derive_prior_shot_blockers(
        bad, scope=bad.scope, target_shot_ref=ref(target)
    )
    assert [item.shot_ref.entity_id for item in blockers] == ["shot-7"]

    malformed = aggregate.model_copy(
        update={"shots": (aggregate.shots[0].model_copy(update={"sequence": 0}), *aggregate.shots[1:])}
    )
    with pytest.raises(CreatorWorkflowStateError, match="violates"):
        derive_prior_shot_blockers(
            malformed, scope=malformed.scope, target_shot_ref=ref(target)
        )


def test_candidate_wrapper_rejects_stale_and_cross_target_candidate() -> None:
    aggregate = build_episode()
    ready = approve_shot(aggregate, "shot-6", SHOT6_APPROVAL_TIME)
    ready = approve_shot(ready, "shot-7", SHOT7_APPROVAL_TIME)
    target = latest_shot(ready, "shot-11")
    candidate = ready.asset_candidates[0]

    with pytest.raises(CreatorWorkflowVersionConflictError, match="aggregate version"):
        select_shot_candidate_if_ready(
            ready,
            scope=ready.scope,
            expected_aggregate_version=2,
            target_shot_ref=ref(target),
            candidate_ref=ref(candidate),
            purpose="image",
            selection_entity_id="selection-stale",
            selection_version_id="selection-stale.v1",
            created_at=SELECTION_TIME,
        )
    with pytest.raises(CreatorWorkflowReferenceError, match="exact latest shot"):
        select_shot_candidate_if_ready(
            ready,
            scope=ready.scope,
            expected_aggregate_version=3,
            target_shot_ref=ref(latest_shot(ready, "shot-8")),
            candidate_ref=ref(candidate),
            purpose="image",
            selection_entity_id="selection-cross",
            selection_version_id="selection-cross.v1",
            created_at=SELECTION_TIME,
        )


def test_sixty_shot_blocker_order_is_deterministic() -> None:
    aggregate = build_episode(shot_count=60)
    target = latest_shot(aggregate, "shot-60")
    first = derive_prior_shot_blockers(
        aggregate, scope=aggregate.scope, target_shot_ref=ref(target)
    )
    second = derive_prior_shot_blockers(
        ProductionProjectAggregate.model_validate_json(aggregate.model_dump_json()),
        scope=aggregate.scope,
        target_shot_ref=ref(target),
    )
    assert [item.sequence for item in first] == [7, 14, 21, 28, 35, 42, 49, 56]
    assert first == second


def test_duplicate_later_sequence_blocks_derivation_for_entire_target_episode() -> None:
    aggregate = build_episode()
    template = latest_shot(aggregate, "shot-11")
    duplicate_a = template.model_copy(
        update={
            "entity_id": "shot-12-a",
            "version_id": "shot-12-a.v1",
            "revision": 1,
            "parent_version_id": None,
            "sequence": 12,
            "lifecycle_state": "approved",
            "review_state": "approved",
            "content_digest": digest("shot-12-a"),
        }
    )
    duplicate_b = duplicate_a.model_copy(
        update={
            "entity_id": "shot-12-b",
            "version_id": "shot-12-b.v1",
            "content_digest": digest("shot-12-b"),
        }
    )
    adversarial = ProductionProjectAggregate.model_validate(
        {
            **aggregate.model_dump(mode="python"),
            "shots": (*aggregate.shots, duplicate_a, duplicate_b),
        }
    )
    target = latest_shot(adversarial, "shot-11")

    with pytest.raises(CreatorWorkflowStateError, match="unique sequence.*12"):
        derive_prior_shot_blockers(
            adversarial,
            scope=adversarial.scope,
            target_shot_ref=ref(target),
        )


def test_duplicate_lower_sequence_blocks_selection_without_appending_fact() -> None:
    aggregate = build_episode()
    approved = approve_shot(aggregate, "shot-6", SHOT6_APPROVAL_TIME)
    approved = approve_shot(approved, "shot-7", SHOT7_APPROVAL_TIME)
    shot_8 = latest_shot(approved, "shot-8")
    duplicate_lower = shot_8.model_copy(
        update={
            "entity_id": "shot-7-shadow",
            "version_id": "shot-7-shadow.v1",
            "revision": 1,
            "parent_version_id": None,
            "sequence": 7,
            "content_digest": digest("shot-7-shadow"),
        }
    )
    adversarial = ProductionProjectAggregate.model_validate(
        {
            **approved.model_dump(mode="python"),
            "shots": (*approved.shots, duplicate_lower),
        }
    )
    before = adversarial.model_dump_json()
    before_selections = adversarial.selections

    with pytest.raises(CreatorWorkflowStateError, match="unique sequence.*7"):
        select_shot_candidate_if_ready(
            adversarial,
            scope=adversarial.scope,
            expected_aggregate_version=3,
            target_shot_ref=ref(latest_shot(adversarial, "shot-11")),
            candidate_ref=ref(adversarial.asset_candidates[0]),
            purpose="image",
            selection_entity_id="selection-should-not-exist",
            selection_version_id="selection-should-not-exist.v1",
            created_at=SELECTION_TIME,
        )

    assert adversarial.model_dump_json() == before
    assert adversarial.selections == before_selections == ()


def test_refresh_and_store_reload_preserve_workflow_facts(tmp_path) -> None:
    aggregate = build_episode()
    approved = approve_shot(aggregate, "shot-6", SHOT6_APPROVAL_TIME)
    approved = approve_shot(approved, "shot-7", SHOT7_APPROVAL_TIME)
    target = latest_shot(approved, "shot-11")
    selected = select_shot_candidate_if_ready(
        approved,
        scope=approved.scope,
        expected_aggregate_version=3,
        target_shot_ref=ref(target),
        candidate_ref=ref(approved.asset_candidates[0]),
        purpose="image",
        selection_entity_id="selection-shot-11",
        selection_version_id="selection-shot-11.v1",
        created_at=SELECTION_TIME,
    )

    decoded = ProductionProjectAggregate.model_validate_json(selected.model_dump_json())
    assert derive_prior_shot_blockers(
        decoded, scope=decoded.scope, target_shot_ref=ref(target)
    ) == ()
    assert decoded.selections[-1] == selected.selections[-1]

    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        aggregate,
        expected_aggregate_version=0,
        idempotency_key="workflow-base",
        payload_digest=digest("workflow-base"),
    )
    store.save(
        approve_shot(aggregate, "shot-6", SHOT6_APPROVAL_TIME),
        expected_aggregate_version=1,
        idempotency_key="workflow-shot-6",
        payload_digest=digest("workflow-shot-6"),
    )
    store.save(
        approved,
        expected_aggregate_version=2,
        idempotency_key="workflow-shot-7",
        payload_digest=digest("workflow-shot-7"),
    )
    store.save(
        selected,
        expected_aggregate_version=3,
        idempotency_key="workflow-selection",
        payload_digest=digest("workflow-selection"),
    )
    restarted = EpisodeDomainAggregateStore(tmp_path)
    loaded = restarted.load(org_id="org-1", project_id="project-1")
    assert loaded == selected
    assert derive_prior_shot_blockers(
        loaded, scope=loaded.scope, target_shot_ref=ref(target)
    ) == ()


def test_review_rejects_stale_non_candidate_duplicate_and_foreign_actor() -> None:
    aggregate = build_episode()
    approved = approve_shot(aggregate, "shot-6", SHOT6_APPROVAL_TIME)
    old = latest_shot(aggregate, "shot-6")
    latest = latest_shot(approved, "shot-6")
    foreign_actor = approved.scope.model_copy(update={"actor_id": "creator-2"})

    with pytest.raises(CreatorWorkflowVersionConflictError, match="latest exact"):
        review_shot_candidate(
            approved,
            scope=approved.scope,
            expected_aggregate_version=2,
            shot_ref=ref(old),
            decision="approve",
            shot_version_id="shot-6.v3",
            decision_entity_id="decision-stale",
            decision_version_id="decision-stale.v1",
            created_at=SHOT7_APPROVAL_TIME,
        )
    with pytest.raises(CreatorWorkflowStateError, match="only an exact latest"):
        review_shot_candidate(
            approved,
            scope=approved.scope,
            expected_aggregate_version=2,
            shot_ref=ref(latest),
            decision="approve",
            shot_version_id="shot-6.v3",
            decision_entity_id="decision-duplicate-review",
            decision_version_id="decision-duplicate-review.v1",
            created_at=SHOT7_APPROVAL_TIME,
        )
    with pytest.raises(CreatorWorkflowScopeError, match="exactly match"):
        review_shot_candidate(
            aggregate,
            scope=foreign_actor,
            expected_aggregate_version=1,
            shot_ref=ref(old),
            decision="approve",
            shot_version_id="shot-6.v2",
            decision_entity_id="decision-foreign",
            decision_version_id="decision-foreign.v1",
            created_at=SHOT6_APPROVAL_TIME,
        )
    with pytest.raises(CreatorWorkflowStateError, match="unused"):
        review_shot_candidate(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            shot_ref=ref(old),
            decision="approve",
            shot_version_id="shot-6.v1",
            decision_entity_id="decision-dup-version",
            decision_version_id="decision-dup-version.v1",
            created_at=SHOT6_APPROVAL_TIME,
        )
    with pytest.raises(CreatorWorkflowStateError, match="approve or reject"):
        review_shot_candidate(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            shot_ref=ref(old),
            decision="skip",  # type: ignore[arg-type]
            shot_version_id="shot-6.v2",
            decision_entity_id="decision-invalid",
            decision_version_id="decision-invalid.v1",
            created_at=SHOT6_APPROVAL_TIME,
        )


def test_selection_rejects_runtime_type_bypass_for_purpose() -> None:
    aggregate = build_episode()
    ready = approve_shot(aggregate, "shot-6", SHOT6_APPROVAL_TIME)
    ready = approve_shot(ready, "shot-7", SHOT7_APPROVAL_TIME)
    with pytest.raises(CreatorWorkflowStateError, match="purpose"):
        select_shot_candidate_if_ready(
            ready,
            scope=ready.scope,
            expected_aggregate_version=3,
            target_shot_ref=ref(latest_shot(ready, "shot-11")),
            candidate_ref=ref(ready.asset_candidates[0]),
            purpose="character_reference",  # type: ignore[arg-type]
            selection_entity_id="selection-invalid-purpose",
            selection_version_id="selection-invalid-purpose.v1",
            created_at=SELECTION_TIME,
        )


def test_builder_rejects_true_cross_project_contract_corruption() -> None:
    aggregate = build_episode()
    corrupt_scope = aggregate.scope.model_copy(update={"project_id": "project-2"})
    payload = aggregate.model_dump(mode="python")
    payload["shots"][0]["scope"] = corrupt_scope
    with pytest.raises(ValidationError):
        ProductionProjectAggregate.model_validate(payload)
