from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest
from pydantic import ValidationError

from apps.api.runtime_episode_continuity_service import (
    ContinuityServiceError,
    apply_change,
    plan_change,
    reject_change,
    undo_change,
)
from apps.api.runtime_episode_domain_contract import (
    ContinuityStateVersion,
    EntityVersionRef,
    EpisodeVersion,
    ProductionProjectAggregate,
    ProjectVersion,
    SceneVersion,
    SeriesVersion,
    ShotVersion,
    TenantScope,
)
from apps.api.runtime_episode_domain_store import EpisodeDomainAggregateStore


BASE_TIME = "2026-07-15T20:00:00+00:00"
PLAN_TIME = "2026-07-15T20:05:00+00:00"
APPLY_TIME = "2026-07-15T20:06:00+00:00"
P2_PLAN_TIME = "2026-07-15T20:07:00+00:00"
P2_APPLY_TIME = "2026-07-15T20:08:00+00:00"
UNDO_TIME = "2026-07-15T20:09:00+00:00"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ref(item: object) -> EntityVersionRef:
    return getattr(item, "as_ref")()


def common(scope: TenantScope, entity_id: str) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "version_id": f"{entity_id}.v1",
        "revision": 1,
        "lifecycle_state": "approved",
        "review_state": "approved",
        "content_digest": digest(entity_id),
        "scope": scope,
        "created_at": BASE_TIME,
    }


def build_episode() -> ProductionProjectAggregate:
    scope = TenantScope(
        org_id="org-team",
        project_id="episode-project",
        actor_id="creator-1",
    )
    project = ProjectVersion(**common(scope, scope.project_id), title="Episode project")
    series = SeriesVersion(
        **common(scope, "series-1"),
        project_ref=ref(project),
        title="Series",
    )
    episode = EpisodeVersion(
        **common(scope, "episode-1"),
        series_ref=ref(series),
        title="Episode",
    )
    scene = SceneVersion(
        **common(scope, "scene-1"),
        episode_ref=ref(episode),
        sequence=1,
        title="Archive",
    )
    lin = ContinuityStateVersion(
        **common(scope, "character-lin"),
        subject_type="character",
        subject_id="lin",
        identity_baseline=("blue-coat", "left-cheek-scar"),
        prohibited_changes=("scar-side",),
    )
    qiao = ContinuityStateVersion(
        **common(scope, "character-qiao"),
        subject_type="character",
        subject_id="qiao",
        identity_baseline=("red-scarf",),
    )
    shots = (
        ShotVersion(
            **common(scope, "shot-1"),
            scene_ref=ref(scene),
            sequence=1,
            duration_seconds=4,
            continuity_refs=(ref(lin), ref(qiao)),
        ),
        ShotVersion(
            **common(scope, "shot-2"),
            scene_ref=ref(scene),
            sequence=2,
            duration_seconds=5,
            continuity_refs=(ref(lin),),
        ),
        ShotVersion(
            **common(scope, "shot-3"),
            scene_ref=ref(scene),
            sequence=3,
            duration_seconds=6,
            continuity_refs=(ref(lin),),
        ),
        # Decoy: shares the scene and another character, not Lin's exact ref.
        ShotVersion(
            **common(scope, "shot-4"),
            scene_ref=ref(scene),
            sequence=4,
            duration_seconds=5,
            continuity_refs=(ref(qiao),),
        ),
    )
    return ProductionProjectAggregate(
        aggregate_version=1,
        evaluated_at=PLAN_TIME,
        scope=scope,
        projects=(project,),
        series=(series,),
        episodes=(episode,),
        scenes=(scene,),
        shots=shots,
        continuity_states=(lin, qiao),
    )


def make_plan(
    aggregate: ProductionProjectAggregate,
    *,
    old_ref: EntityVersionRef | None = None,
    version_id: str = "character-lin.v2",
    proposal_id: str = "proposal-lin-wardrobe",
    created_at: str = PLAN_TIME,
    identity_baseline: tuple[str, ...] = ("black-coat", "left-cheek-scar"),
):
    return plan_change(
        aggregate,
        scope=aggregate.scope,
        expected_aggregate_version=aggregate.aggregate_version,
        old_continuity_ref=old_ref or ref(aggregate.continuity_states[0]),
        new_version_id=version_id,
        proposal_entity_id=proposal_id,
        created_at=created_at,
        identity_baseline=identity_baseline,
    )


def apply_partial(
    aggregate: ProductionProjectAggregate,
    *,
    selected_count: int = 2,
) -> tuple[object, ProductionProjectAggregate]:
    plan = make_plan(aggregate)
    applied = apply_change(
        aggregate,
        plan,
        scope=aggregate.scope,
        expected_aggregate_version=aggregate.aggregate_version,
        selected_shot_refs=plan.affected_shot_refs[:selected_count],
        created_at=APPLY_TIME,
    )
    return plan, applied


def latest_shots(aggregate: ProductionProjectAggregate) -> dict[str, ShotVersion]:
    latest: dict[str, ShotVersion] = {}
    for shot in aggregate.shots:
        if shot.entity_id not in latest or shot.revision > latest[shot.entity_id].revision:
            latest[shot.entity_id] = shot
    return latest


def append_same_target_independent_shot(
    aggregate: ProductionProjectAggregate,
) -> ProductionProjectAggregate:
    target = aggregate.agent_proposals[0].target_ref
    template = aggregate.shots[3]
    independent = template.model_copy(
        update={
            "entity_id": "shot-independent",
            "version_id": "shot-independent.v1",
            "revision": 1,
            "parent_version_id": None,
            "sequence": 5,
            "continuity_refs": (target,),
            "source_proposal_ref": None,
            "content_digest": digest("shot-independent-same-target"),
            "created_at": P2_PLAN_TIME,
        }
    )
    return ProductionProjectAggregate(
        **{
            **aggregate.model_dump(),
            "aggregate_version": aggregate.aggregate_version + 1,
            "evaluated_at": P2_PLAN_TIME,
            "shots": (*aggregate.shots, independent),
        }
    )


def test_plan_derives_complete_exact_impact_and_unaffected_without_mutation() -> None:
    aggregate = build_episode()
    before = aggregate.model_dump_json()

    plan = make_plan(aggregate)

    assert [item.entity_id for item in plan.affected_shot_refs] == [
        "shot-1",
        "shot-2",
        "shot-3",
    ]
    assert [item.entity_id for item in plan.unaffected_shot_refs] == ["shot-4"]
    assert plan.proposed_continuity.parent_version_id == "character-lin.v1"
    assert plan.proposed_continuity.revision == 2
    assert plan.proposed_continuity.lifecycle_state == "candidate"
    assert plan.proposed_continuity.review_state == "needs_review"
    assert plan.proposed_continuity.approved_asset_selection_refs == ()
    assert aggregate.model_dump_json() == before


def test_apply_rederives_full_impact_and_rejects_caller_crop() -> None:
    aggregate = build_episode()
    plan = make_plan(aggregate)
    cropped = replace(
        plan,
        affected_shot_refs=plan.affected_shot_refs[:2],
        unaffected_shot_refs=(
            plan.affected_shot_refs[2],
            *plan.unaffected_shot_refs,
        ),
    )

    with pytest.raises(ContinuityServiceError, match="impact boundary"):
        apply_change(
            aggregate,
            cropped,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            selected_shot_refs=cropped.affected_shot_refs,
            created_at=APPLY_TIME,
        )

    old = aggregate.continuity_states[0]
    forged_noop = plan.proposed_continuity.model_copy(
        update={
            "identity_baseline": old.identity_baseline,
            "content_digest": digest("caller-recomputed-noop"),
        }
    )
    with pytest.raises(ContinuityServiceError, match="alter a semantic field"):
        apply_change(
            aggregate,
            replace(plan, proposed_continuity=forged_noop),
            scope=aggregate.scope,
            expected_aggregate_version=1,
            selected_shot_refs=plan.affected_shot_refs[:1],
            created_at=APPLY_TIME,
        )


def test_partial_apply_records_full_prediction_and_two_successors_separately() -> None:
    aggregate = build_episode()
    before = aggregate.model_dump_json()
    plan, applied = apply_partial(aggregate)
    proposal = applied.agent_proposals[-1]
    latest = latest_shots(applied)

    assert proposal.decision_state == "executed"
    assert proposal.impact_refs == plan.affected_shot_refs
    assert [item.entity_id for item in proposal.applied_refs] == ["shot-1", "shot-2"]
    assert all(
        latest[shot_id].source_proposal_ref == proposal.as_ref()
        for shot_id in ("shot-1", "shot-2")
    )
    assert all(
        plan.proposed_continuity.as_ref() in latest[shot_id].continuity_refs
        for shot_id in ("shot-1", "shot-2")
    )
    assert plan.old_continuity_ref in latest["shot-3"].continuity_refs
    assert latest["shot-4"] == aggregate.shots[3]
    assert all(
        latest[shot_id].lifecycle_state == "candidate"
        and latest[shot_id].review_state == "needs_review"
        for shot_id in ("shot-1", "shot-2")
    )
    assert aggregate.model_dump_json() == before


def test_full_apply_records_all_predicted_successors() -> None:
    aggregate = build_episode()
    plan = make_plan(aggregate)
    applied = apply_change(
        aggregate,
        plan,
        scope=aggregate.scope,
        expected_aggregate_version=1,
        selected_shot_refs=plan.affected_shot_refs,
        created_at=APPLY_TIME,
    )

    proposal = applied.agent_proposals[0]
    assert {item.entity_id for item in proposal.applied_refs} == {
        "shot-1",
        "shot-2",
        "shot-3",
    }
    assert proposal.impact_refs == plan.affected_shot_refs


def test_reject_returns_exact_same_aggregate_and_creates_no_operation() -> None:
    aggregate = build_episode()
    plan = make_plan(aggregate)

    rejected = reject_change(
        aggregate,
        plan,
        scope=aggregate.scope,
        expected_aggregate_version=1,
    )

    assert rejected is aggregate
    assert rejected.model_dump_json() == aggregate.model_dump_json()
    assert rejected.agent_proposals == ()


def test_honest_undo_uses_explicit_membership_and_appends_full_restoration() -> None:
    aggregate = build_episode()
    plan, applied = apply_partial(aggregate)
    executed = applied.agent_proposals[0]
    before = applied.model_dump_json()

    restored = undo_change(
        applied,
        scope=aggregate.scope,
        expected_aggregate_version=2,
        proposal_ref=executed.as_ref(),
        created_at=UNDO_TIME,
    )

    undo = restored.agent_proposals[-1]
    latest = latest_shots(restored)
    assert undo.entity_id == executed.entity_id
    assert undo.revision == 2
    assert undo.parent_version_id == executed.version_id
    assert undo.decision_state == "undone"
    assert undo.target_ref == plan.old_continuity_ref
    assert undo.impact_refs == executed.applied_refs
    assert {item.entity_id for item in undo.applied_refs} == {"shot-1", "shot-2"}
    assert all(
        latest[shot_id].source_proposal_ref == undo.as_ref()
        and plan.old_continuity_ref in latest[shot_id].continuity_refs
        for shot_id in ("shot-1", "shot-2")
    )
    assert latest["shot-3"] == aggregate.shots[2]
    assert latest["shot-4"] == aggregate.shots[3]
    assert applied.model_dump_json() == before


def test_same_target_independent_shot_is_not_captured_by_undo() -> None:
    aggregate = build_episode()
    plan, applied = apply_partial(aggregate, selected_count=1)
    with_independent = append_same_target_independent_shot(applied)
    independent = latest_shots(with_independent)["shot-independent"]

    restored = undo_change(
        with_independent,
        scope=aggregate.scope,
        expected_aggregate_version=3,
        proposal_ref=applied.agent_proposals[0].as_ref(),
        created_at=UNDO_TIME,
    )

    assert latest_shots(restored)["shot-independent"] == independent
    assert plan.proposed_continuity.as_ref() in independent.continuity_refs
    assert ref(independent) not in restored.agent_proposals[-1].impact_refs


def test_p2_advancing_one_owned_shot_blocks_all_of_p1_undo_atomically() -> None:
    aggregate = build_episode()
    _, p1 = apply_partial(aggregate)
    p1_executed = p1.agent_proposals[0]
    p2_plan = make_plan(
        p1,
        old_ref=p1_executed.target_ref,
        version_id="character-lin.v3",
        proposal_id="proposal-lin-raincoat",
        created_at=P2_PLAN_TIME,
        identity_baseline=("rain-coat", "left-cheek-scar"),
    )
    p2 = apply_change(
        p1,
        p2_plan,
        scope=p1.scope,
        expected_aggregate_version=2,
        selected_shot_refs=(p2_plan.affected_shot_refs[0],),
        created_at=P2_APPLY_TIME,
    )
    before = p2.model_dump_json()

    with pytest.raises(ContinuityServiceError, match="exact current shot"):
        undo_change(
            p2,
            scope=p2.scope,
            expected_aggregate_version=3,
            proposal_ref=p1_executed.as_ref(),
            created_at=UNDO_TIME,
        )

    assert p2.model_dump_json() == before
    assert latest_shots(p2)["shot-2"].as_ref() == p1_executed.applied_refs[1]


def test_corrupted_operation_membership_fails_closed_without_digest_authority() -> None:
    aggregate = build_episode()
    _, applied = apply_partial(aggregate, selected_count=1)
    with_independent = append_same_target_independent_shot(applied)
    proposal = with_independent.agent_proposals[0]
    independent_ref = latest_shots(with_independent)["shot-independent"].as_ref()
    forged = proposal.model_copy(
        update={"applied_refs": (*proposal.applied_refs, independent_ref)}
    )
    corrupted = with_independent.model_copy(
        update={
            "agent_proposals": (forged,),
            # A recomputed or arbitrary integrity digest must not authorize the
            # newly claimed explicit business membership.
        }
    )

    with pytest.raises(ContinuityServiceError, match="violates the episode fact contract"):
        undo_change(
            corrupted,
            scope=corrupted.scope,
            expected_aggregate_version=3,
            proposal_ref=forged.as_ref(),
            created_at=UNDO_TIME,
        )


def test_coordinated_membership_crop_is_invalid_json_and_rejected_by_service() -> None:
    aggregate = build_episode()
    _, applied = apply_partial(aggregate, selected_count=2)
    proposal = applied.agent_proposals[0]
    orphan_ref = proposal.applied_refs[1]

    payload = applied.model_dump(mode="json")
    payload["agent_proposals"][0]["applied_refs"] = payload["agent_proposals"][0][
        "applied_refs"
    ][:1]
    for shot in payload["shots"]:
        if (
            shot["entity_id"] == orphan_ref.entity_id
            and shot["version_id"] == orphan_ref.version_id
        ):
            shot["source_proposal_ref"] = None
            break

    with pytest.raises(
        ValidationError,
        match="continuity change requires an exact source proposal",
    ):
        ProductionProjectAggregate.model_validate_json(json.dumps(payload))

    cropped = proposal.model_copy(update={"applied_refs": proposal.applied_refs[:1]})
    corrupted_shots = tuple(
        shot.model_copy(update={"source_proposal_ref": None})
        if shot.as_ref() == orphan_ref
        else shot
        for shot in applied.shots
    )
    corrupted = applied.model_copy(
        update={
            "shots": corrupted_shots,
            "agent_proposals": (cropped,),
            # Keep the original digest: integrity evidence cannot repair or
            # authorize cropped explicit operation membership.
        }
    )

    with pytest.raises(
        ContinuityServiceError,
        match="violates the episode fact contract",
    ):
        undo_change(
            corrupted,
            scope=corrupted.scope,
            expected_aggregate_version=2,
            proposal_ref=cropped.as_ref(),
            created_at=UNDO_TIME,
        )


def test_scope_cas_stale_refs_and_non_continuity_refs_fail_closed() -> None:
    aggregate = build_episode()
    plan = make_plan(aggregate)
    foreign_actor = aggregate.scope.model_copy(update={"actor_id": "creator-2"})

    with pytest.raises(ContinuityServiceError, match="stale aggregate"):
        plan_change(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=2,
            old_continuity_ref=plan.old_continuity_ref,
            new_version_id="character-lin.v2",
            proposal_entity_id="proposal-stale",
            created_at=PLAN_TIME,
            identity_baseline=("black-coat",),
        )
    with pytest.raises(ContinuityServiceError, match="actor scope"):
        reject_change(
            aggregate,
            plan,
            scope=foreign_actor,
            expected_aggregate_version=1,
        )
    with pytest.raises(ContinuityServiceError, match="reference must target continuity_state"):
        plan_change(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            old_continuity_ref=ref(aggregate.shots[0]),
            new_version_id="character-lin.v2",
            proposal_entity_id="proposal-wrong-ref",
            created_at=PLAN_TIME,
            identity_baseline=("black-coat",),
        )
    stale = plan.old_continuity_ref.model_copy(update={"version_id": "character-lin.v0"})
    with pytest.raises(ContinuityServiceError, match="does not resolve"):
        plan_change(
            aggregate,
            scope=aggregate.scope,
            expected_aggregate_version=1,
            old_continuity_ref=stale,
            new_version_id="character-lin.v2",
            proposal_entity_id="proposal-stale-ref",
            created_at=PLAN_TIME,
            identity_baseline=("black-coat",),
        )


def test_apply_rejects_empty_duplicate_decoy_and_stale_selected_refs() -> None:
    aggregate = build_episode()
    plan = make_plan(aggregate)

    for selected, message in (
        ((), "at least one"),
        ((plan.affected_shot_refs[0], plan.affected_shot_refs[0]), "unique"),
        ((ref(aggregate.shots[3]),), "outside the derived impact"),
        (
            (
                plan.affected_shot_refs[0].model_copy(
                    update={"version_id": "shot-1.stale"}
                ),
            ),
            "outside the derived impact",
        ),
    ):
        with pytest.raises(ContinuityServiceError, match=message):
            apply_change(
                aggregate,
                plan,
                scope=aggregate.scope,
                expected_aggregate_version=1,
                selected_shot_refs=selected,
                created_at=APPLY_TIME,
            )


def test_duplicate_version_proposal_and_undo_requests_are_rejected() -> None:
    aggregate = build_episode()
    with pytest.raises(ContinuityServiceError, match="version id must be unused"):
        make_plan(aggregate, version_id="character-lin.v1")

    plan, applied = apply_partial(aggregate, selected_count=1)
    with pytest.raises(ContinuityServiceError, match="proposal entity id already exists"):
        make_plan(
            applied,
            old_ref=plan.proposed_continuity.as_ref(),
            version_id="character-lin.v3",
            proposal_id="proposal-lin-wardrobe",
            created_at=P2_PLAN_TIME,
            identity_baseline=("rain-coat",),
        )

    restored = undo_change(
        applied,
        scope=applied.scope,
        expected_aggregate_version=2,
        proposal_ref=applied.agent_proposals[0].as_ref(),
        created_at=UNDO_TIME,
    )
    with pytest.raises(ContinuityServiceError, match="exact current version"):
        undo_change(
            restored,
            scope=restored.scope,
            expected_aggregate_version=3,
            proposal_ref=applied.agent_proposals[0].as_ref(),
            created_at="2026-07-15T20:10:00+00:00",
        )


def test_apply_and_undo_are_deterministic_and_roundtrip_through_contract_and_store(
    tmp_path,
) -> None:
    aggregate = build_episode()
    plan = make_plan(aggregate)
    kwargs = {
        "scope": aggregate.scope,
        "expected_aggregate_version": 1,
        "selected_shot_refs": plan.affected_shot_refs[:2],
        "created_at": APPLY_TIME,
    }
    first = apply_change(aggregate, plan, **kwargs)
    second = apply_change(aggregate, plan, **kwargs)
    assert first.model_dump_json() == second.model_dump_json()

    restored = undo_change(
        first,
        scope=first.scope,
        expected_aggregate_version=2,
        proposal_ref=first.agent_proposals[0].as_ref(),
        created_at=UNDO_TIME,
    )
    decoded = ProductionProjectAggregate.model_validate_json(restored.model_dump_json())
    assert decoded == restored

    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        aggregate,
        expected_aggregate_version=0,
        idempotency_key="continuity-base",
        payload_digest=digest("continuity-base"),
    )
    store.save(
        first,
        expected_aggregate_version=1,
        idempotency_key="continuity-apply",
        payload_digest=digest("continuity-apply"),
    )
    store.save(
        restored,
        expected_aggregate_version=2,
        idempotency_key="continuity-undo",
        payload_digest=digest("continuity-undo"),
    )
    restarted = EpisodeDomainAggregateStore(tmp_path)
    assert restarted.load(
        org_id=restored.scope.org_id,
        project_id=restored.scope.project_id,
    ) == restored


def test_timestamps_and_noop_plan_fail_before_any_fact_changes() -> None:
    aggregate = build_episode()
    before = aggregate.model_dump_json()

    with pytest.raises(ContinuityServiceError, match="alter a semantic field"):
        make_plan(
            aggregate,
            identity_baseline=aggregate.continuity_states[0].identity_baseline,
        )
    with pytest.raises(ContinuityServiceError, match="later than its parent"):
        make_plan(
            aggregate,
            created_at=BASE_TIME,
        )
    assert aggregate.model_dump_json() == before
