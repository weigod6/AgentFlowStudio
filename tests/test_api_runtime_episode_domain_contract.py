from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.runtime_episode_domain_contract import (
    AgentProposal,
    AssetCandidateVersion,
    ConsentRecord,
    ContinuityStateVersion,
    DeliveryVersion,
    EntityVersionRef,
    EPISODE_PRODUCTION_CONTRACT_REVISION,
    EpisodeVersion,
    ProductionProjectAggregate,
    ProjectVersion,
    ProjectDataPolicy,
    ReviewDecision,
    SafeArtifactRef,
    SceneVersion,
    SelectedVersion,
    SourceEvidenceRef,
    SeriesVersion,
    ShotVersion,
    TenantScope,
    is_lifecycle_transition_allowed,
)
from apps.api.runtime_episode_domain_store import (
    AggregateNotFoundError,
    EpisodeDomainAggregateStore,
)


NOW = "2026-07-15T20:30:00+00:00"
LATER = "2026-07-15T20:31:00+00:00"
EVALUATED_AT = "2026-07-15T20:35:00+00:00"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ref(item):
    return EntityVersionRef(
        entity_type=item.entity_type,
        entity_id=item.entity_id,
        version_id=item.version_id,
    )


def common(
    scope: TenantScope,
    name: str,
    *,
    lifecycle="approved",
    review="approved",
    created_at=NOW,
) -> dict:
    return {
        "entity_id": name,
        "version_id": f"{name}.v1",
        "revision": 1,
        "lifecycle_state": lifecycle,
        "review_state": review,
        "content_digest": digest(name),
        "scope": scope,
        "created_at": created_at,
    }


def build_aggregate() -> ProductionProjectAggregate:
    scope = TenantScope(org_id="org-small-team", project_id="project-short-episode", actor_id="creator-1")
    project = ProjectVersion(**common(scope, scope.project_id), title="短篇项目")
    series = SeriesVersion(
        **common(scope, "series-1"), project_ref=ref(project), title="第一季"
    )


    episode = EpisodeVersion(
        **common(scope, "episode-1"), series_ref=ref(series), title="试播集"
    )
    scene = SceneVersion(
        **common(scope, "scene-1"), episode_ref=ref(episode), sequence=1, title="雨夜车站"
    )
    character = ContinuityStateVersion(
        **common(scope, "character-lin"),
        subject_type="character",
        subject_id="lin",
        identity_baseline=("left-cheek-scar", "blue-coat"),
        prohibited_changes=("scar-side",),
    )
    shot_1 = ShotVersion(
        **common(scope, "shot-1"),
        scene_ref=ref(scene),
        sequence=1,
        duration_seconds=4.5,
        continuity_refs=(ref(character),),
    )
    shot_2 = ShotVersion(
        **common(scope, "shot-2"),
        scene_ref=ref(scene),
        sequence=2,
        duration_seconds=5.0,
        continuity_refs=(ref(character),),
    )
    artifact = SafeArtifactRef(
        artifact_id="artifact-shot-1",
        artifact_type="image",
        content_digest=digest("artifact-shot-1"),
    )
    candidate = AssetCandidateVersion(
        **common(scope, "candidate-shot-1"),
        target_ref=ref(shot_1),
        artifact_ref=artifact,
    )
    selection = SelectedVersion(
        **common(scope, "selection-shot-1", lifecycle="locked", review="approved"),
        target_ref=ref(shot_1),
        purpose="storyboard",
        candidate_ref=ref(candidate),
    )
    decision = ReviewDecision(
        **common(scope, "review-selection-1", lifecycle="locked", review="approved"),
        subject_ref=ref(selection),
        decision="approve",
    )
    preview = SafeArtifactRef(
        artifact_id="preview-episode-1",
        artifact_type="video",
        content_digest=digest("preview-episode-1"),
    )
    delivery = DeliveryVersion(
        **common(scope, "delivery-episode-1", lifecycle="locked", review="approved"),
        episode_ref=ref(episode),
        selection_refs=(ref(selection),),
        review_decision_refs=(ref(decision),),
        preview_artifact_ref=preview,
    )
    return ProductionProjectAggregate(
        aggregate_version=1,
        evaluated_at=EVALUATED_AT,
        scope=scope,
        projects=(project,),
        series=(series,),
        episodes=(episode,),
        scenes=(scene,),
        shots=(shot_1, shot_2),
        continuity_states=(character,),
        asset_candidates=(candidate,),
        selections=(selection,),
        review_decisions=(decision,),
        deliveries=(delivery,),
    )


def build_applied_continuity_operation(
    *,
    selected_shot_ids: tuple[str, ...] = ("shot-1",),
) -> ProductionProjectAggregate:
    aggregate = build_aggregate()
    old_continuity = aggregate.continuity_states[0]
    target = old_continuity.model_copy(
        update={
            "version_id": "character-lin.v2",
            "revision": 2,
            "parent_version_id": old_continuity.version_id,
            "lifecycle_state": "candidate",
            "review_state": "needs_review",
            "content_digest": digest("character-lin-black-coat"),
            "created_at": LATER,
            "identity_baseline": ("left-cheek-scar", "black-coat"),
        }
    )
    proposal = AgentProposal(
        **common(
            aggregate.scope,
            "proposal-lin-wardrobe",
            lifecycle="draft",
            review="not_requested",
            created_at=LATER,
        ),
        target_ref=ref(target),
        impact_refs=tuple(ref(shot) for shot in aggregate.shots),
        action="replace_continuity_ref",
        decision_state="executed",
    )
    selected = [shot for shot in aggregate.shots if shot.entity_id in selected_shot_ids]
    successors = tuple(
        shot.model_copy(
            update={
                "version_id": f"{shot.entity_id}.v2",
                "revision": 2,
                "parent_version_id": shot.version_id,
                "lifecycle_state": "candidate",
                "review_state": "needs_review",
                "content_digest": digest(f"{shot.entity_id}-black-coat"),
                "created_at": LATER,
                "continuity_refs": (ref(target),),
                "source_proposal_ref": ref(proposal),
            }
        )
        for shot in selected
    )
    proposal = proposal.model_copy(update={"applied_refs": tuple(ref(shot) for shot in successors)})
    return ProductionProjectAggregate(
        **{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots, *successors),
            "continuity_states": (*aggregate.continuity_states, target),
            "agent_proposals": (proposal,),
        }
    )


def append_full_undo(aggregate: ProductionProjectAggregate) -> ProductionProjectAggregate:
    executed = aggregate.agent_proposals[0]
    old_continuity = aggregate.continuity_states[0]
    applied_shots = {
        shot.as_ref(): shot for shot in aggregate.shots if shot.as_ref() in executed.applied_refs
    }
    undo = executed.model_copy(
        update={
            "version_id": "proposal-lin-wardrobe.v2",
            "revision": 2,
            "parent_version_id": executed.version_id,
            "target_ref": ref(old_continuity),
            "impact_refs": executed.applied_refs,
            "applied_refs": (),
            "decision_state": "undone",
            "content_digest": digest("proposal-lin-wardrobe-undo"),
            "created_at": "2026-07-15T20:32:00+00:00",
        }
    )
    restored = tuple(
        shot.model_copy(
            update={
                "version_id": f"{shot.entity_id}.v3",
                "revision": 3,
                "parent_version_id": shot.version_id,
                "content_digest": digest(f"{shot.entity_id}-restore-blue-coat"),
                "created_at": "2026-07-15T20:32:00+00:00",
                "continuity_refs": (ref(old_continuity),),
                "source_proposal_ref": ref(undo),
            }
        )
        for shot in applied_shots.values()
    )
    undo = undo.model_copy(update={"applied_refs": tuple(ref(shot) for shot in restored)})
    return ProductionProjectAggregate(
        **{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots, *restored),
            "agent_proposals": (*aggregate.agent_proposals, undo),
        }
    )


def test_generic_aggregate_forms_one_resolvable_fact_chain() -> None:
    aggregate = build_aggregate()

    assert len(aggregate.shots) == 2
    assert aggregate.shots[0].continuity_refs == aggregate.shots[1].continuity_refs
    assert aggregate.deliveries[0].lifecycle_state == "locked"


def test_new_project_defaults_private_and_denies_training() -> None:
    project = build_aggregate().projects[0]

    assert project.data_policy.visibility == "private"
    assert project.data_policy.training_use == "denied_by_default"
    assert project.data_policy.product_improvement_use == "denied_by_default"


def test_runtime_job_state_cannot_be_used_as_content_lifecycle() -> None:
    scope = TenantScope(org_id="org-1", project_id="project-1", actor_id="creator-1")

    with pytest.raises(ValidationError, match="lifecycle_state"):
        ProjectVersion(**common(scope, scope.project_id, lifecycle="running"), title="错误状态")


def test_locked_content_requires_approved_review() -> None:
    scope = TenantScope(org_id="org-1", project_id="project-1", actor_id="creator-1")

    with pytest.raises(ValidationError, match="locked content requires approved review state"):
        ProjectVersion(
            **common(scope, scope.project_id, lifecycle="locked", review="needs_review"),
            title="尚未审核",
        )


def test_cross_project_record_is_rejected() -> None:
    aggregate = build_aggregate()
    foreign_scope = TenantScope(org_id="org-small-team", project_id="other-project", actor_id="creator-1")
    foreign_shot = aggregate.shots[0].model_copy(update={"scope": foreign_scope})

    with pytest.raises(ValidationError, match="aggregate tenant and project"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (foreign_shot, aggregate.shots[1]),
        })


def test_selection_cannot_point_to_candidate_for_another_target() -> None:
    aggregate = build_aggregate()
    candidate = aggregate.asset_candidates[0].model_copy(update={"target_ref": ref(aggregate.shots[1])})

    with pytest.raises(ValidationError, match="candidate must belong"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "asset_candidates": (candidate,),
        })


def test_locked_delivery_requires_locked_selection_and_approval() -> None:
    aggregate = build_aggregate()
    unlocked = aggregate.selections[0].model_copy(
        update={"lifecycle_state": "approved", "review_state": "approved"}
    )

    with pytest.raises(ValidationError, match="locked selections"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "selections": (unlocked,),
        })


def test_duplicate_version_identity_is_rejected() -> None:
    aggregate = build_aggregate()

    with pytest.raises(ValidationError, match="must be unique"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots, aggregate.shots[0]),
        })


def test_lifecycle_transitions_require_explicit_new_version_or_unlock() -> None:
    assert is_lifecycle_transition_allowed("draft", "candidate")
    assert is_lifecycle_transition_allowed("locked", "approved")
    assert not is_lifecycle_transition_allowed("locked", "draft")
    assert not is_lifecycle_transition_allowed("retired", "approved")


def test_contract_does_not_encode_representative_episode_counts() -> None:
    aggregate = build_aggregate()

    assert len(aggregate.projects) == 1
    assert len(aggregate.episodes) == 1
    assert len(aggregate.scenes) == 1
    assert len(aggregate.shots) == 2


def test_training_policy_requires_a_separate_granted_consent_record() -> None:
    aggregate = build_aggregate()
    project = aggregate.projects[0].model_copy(
        update={
            "data_policy": ProjectDataPolicy(
                training_use="consented",
                product_improvement_use="denied_by_default",
            )
        }
    )

    with pytest.raises(ValidationError, match="granted training consent"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "projects": (project,),
        })


def test_source_training_use_cannot_bypass_project_policy_and_consent() -> None:
    aggregate = build_aggregate()
    source = SourceEvidenceRef(
        source_id="source-training",
        scope=aggregate.scope,
        source_type="upload",
        uploaded_by="creator-1",
        rights_basis="creator_owned",
        allowed_uses=("production", "training"),
        training_status="consented",
    )
    project = aggregate.projects[0].model_copy(update={"source_refs": (source,)})

    with pytest.raises(ValidationError, match="source training use requires project policy"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "projects": (project,),
        })


def test_draft_approval_decision_cannot_authorize_locked_delivery() -> None:
    aggregate = build_aggregate()
    draft_decision = aggregate.review_decisions[0].model_copy(
        update={"lifecycle_state": "draft", "review_state": "not_requested"}
    )

    with pytest.raises(ValidationError, match="approval decision for every selection"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "review_decisions": (draft_decision,),
        })


def test_duplicate_revision_with_another_version_id_is_rejected() -> None:
    aggregate = build_aggregate()
    duplicate_revision = aggregate.shots[0].model_copy(update={"version_id": "shot-1-alias.v1"})

    with pytest.raises(ValidationError, match="entity revisions must be unique"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots, duplicate_revision),
        })


def test_project_history_is_contiguous_and_latest_revision_controls_policy() -> None:
    aggregate = build_aggregate()
    project_v1 = aggregate.projects[0]
    project_v2 = project_v1.model_copy(
        update={
            "version_id": "project-short-episode.v2",
            "revision": 2,
            "parent_version_id": project_v1.version_id,
            "content_digest": digest("project-short-episode-v2"),
            "data_policy": ProjectDataPolicy(visibility="project_members"),
            "created_at": LATER,
        }
    )
    approval = ReviewDecision(
        **common(aggregate.scope, "review-project-v2", created_at=LATER),
        subject_ref=ref(project_v2),
        decision="approve",
    )

    restored = ProductionProjectAggregate(**{
        **aggregate.model_dump(),
        "projects": (project_v1, project_v2),
        "review_decisions": (*aggregate.review_decisions, approval),
    })

    assert restored.projects[-1].revision == 2
    assert restored.projects[-1].data_policy.visibility == "project_members"


def test_project_history_rejects_revision_gaps() -> None:
    aggregate = build_aggregate()
    project_v3 = aggregate.projects[0].model_copy(
        update={
            "version_id": "project-short-episode.v3",
            "revision": 3,
            "parent_version_id": aggregate.projects[0].version_id,
            "content_digest": digest("project-short-episode-v3"),
            "created_at": LATER,
        }
    )

    with pytest.raises(ValidationError, match="complete and contiguous"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "projects": (*aggregate.projects, project_v3),
        })


def test_explicit_training_policy_and_matching_consent_allow_source_use() -> None:
    aggregate = build_aggregate()
    source = SourceEvidenceRef(
        source_id="source-consented-training",
        scope=aggregate.scope,
        source_type="upload",
        uploaded_by="creator-1",
        rights_basis="creator_owned",
        allowed_uses=("production", "training"),
        training_status="consented",
    )
    project = aggregate.projects[0].model_copy(
        update={
            "source_refs": (source,),
            "data_policy": ProjectDataPolicy(training_use="consented"),
        }
    )
    consent = ConsentRecord(
        consent_id="consent-training-1",
        scope=aggregate.scope,
        purpose="training",
        data_classes=("uploaded_reference",),
        policy_version="policy-v1",
        status="granted",
        granted_at=NOW,
    )

    restored = ProductionProjectAggregate(**{
        **aggregate.model_dump(),
        "projects": (project,),
        "consent_records": (consent,),
    })

    assert restored.projects[0].data_policy.training_use == "consented"


def test_expired_consent_is_not_active_at_aggregate_evaluation_time() -> None:
    aggregate = build_aggregate()
    source = SourceEvidenceRef(
        source_id="source-expired-training",
        scope=aggregate.scope,
        source_type="upload",
        uploaded_by="creator-1",
        rights_basis="creator_owned",
        allowed_uses=("production", "training"),
        training_status="consented",
    )
    project = aggregate.projects[0].model_copy(
        update={
            "source_refs": (source,),
            "data_policy": ProjectDataPolicy(training_use="consented"),
        }
    )
    expired = ConsentRecord(
        consent_id="consent-expired-training",
        scope=aggregate.scope,
        purpose="training",
        data_classes=("uploaded_reference",),
        policy_version="policy-v1",
        status="granted",
        granted_at="2019-01-01T00:00:00+00:00",
        expires_at="2020-01-01T00:00:00+00:00",
    )

    with pytest.raises(ValidationError, match="granted training consent"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "projects": (project,),
            "consent_records": (expired,),
        })


def test_continuity_impact_has_no_second_applies_to_authority() -> None:
    aggregate = build_aggregate()
    payload = aggregate.continuity_states[0].model_dump()
    payload["applies_to_refs"] = [ref(aggregate.shots[0]).model_dump()]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ContinuityStateVersion(**payload)


def test_continuity_assets_use_the_same_candidate_selection_chain() -> None:
    aggregate = build_aggregate()
    continuity = aggregate.continuity_states[0]
    artifact = SafeArtifactRef(
        artifact_id="artifact-character-lin",
        artifact_type="image",
        content_digest=digest("artifact-character-lin"),
    )
    candidate = AssetCandidateVersion(
        **common(aggregate.scope, "candidate-character-lin"),
        target_ref=ref(continuity),
        artifact_ref=artifact,
    )
    selection = SelectedVersion(
        **common(aggregate.scope, "selection-character-lin"),
        target_ref=ref(continuity),
        purpose="character_reference",
        candidate_ref=ref(candidate),
    )
    continuity_with_asset = continuity.model_copy(
        update={"approved_asset_selection_refs": (ref(selection),)}
    )

    restored = ProductionProjectAggregate(**{
        **aggregate.model_dump(),
        "continuity_states": (continuity_with_asset,),
        "asset_candidates": (*aggregate.asset_candidates, candidate),
        "selections": (*aggregate.selections, selection),
    })

    assert restored.continuity_states[0].approved_asset_selection_refs == (ref(selection),)


def test_rejected_candidate_cannot_support_locked_selection_or_delivery() -> None:
    aggregate = build_aggregate()
    rejected_candidate = aggregate.asset_candidates[0].model_copy(
        update={"lifecycle_state": "rejected", "review_state": "rejected"}
    )

    with pytest.raises(ValidationError, match="valid approved candidate"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "asset_candidates": (rejected_candidate,),
        })


def test_locked_fact_cannot_create_draft_successor_without_unlock() -> None:
    aggregate = build_aggregate()
    locked = aggregate.shots[0].model_copy(
        update={"lifecycle_state": "locked", "review_state": "approved"}
    )
    draft_successor = locked.model_copy(
        update={
            "version_id": "shot-1.v2",
            "revision": 2,
            "parent_version_id": locked.version_id,
            "lifecycle_state": "draft",
            "review_state": "not_requested",
            "content_digest": digest("changed-locked-shot"),
            "created_at": LATER,
        }
    )

    with pytest.raises(ValidationError, match="invalid lifecycle transition"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (locked, draft_successor, aggregate.shots[1]),
        })


def test_locked_fact_unlock_requires_exact_finalized_decision_and_preserves_content() -> None:
    aggregate = build_aggregate()
    locked = aggregate.shots[0].model_copy(
        update={"lifecycle_state": "locked", "review_state": "approved"}
    )
    unlocked = locked.model_copy(
        update={
            "version_id": "shot-1.v2",
            "revision": 2,
            "parent_version_id": locked.version_id,
            "lifecycle_state": "approved",
            "created_at": LATER,
        }
    )

    with pytest.raises(ValidationError, match="exact finalized unlock"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (locked, unlocked, aggregate.shots[1]),
        })

    unlock = ReviewDecision(
        **common(aggregate.scope, "review-unlock-shot-1"),
        subject_ref=ref(locked),
        decision="unlock",
    )
    restored = ProductionProjectAggregate(**{
        **aggregate.model_dump(),
        "shots": (locked, unlocked, aggregate.shots[1]),
        "review_decisions": (*aggregate.review_decisions, unlock),
    })

    assert restored.shots[1].parent_version_id == locked.version_id


def test_retired_fact_is_terminal_even_with_same_state_successor() -> None:
    aggregate = build_aggregate()
    retired = aggregate.shots[0].model_copy(
        update={"lifecycle_state": "retired", "review_state": "approved"}
    )
    successor = retired.model_copy(
        update={
            "version_id": "shot-1.v2",
            "revision": 2,
            "parent_version_id": retired.version_id,
            "created_at": LATER,
        }
    )

    with pytest.raises(ValidationError, match="invalid lifecycle transition"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (retired, successor, aggregate.shots[1]),
        })


def test_future_or_late_decision_cannot_authorize_delivery_or_unlock() -> None:
    aggregate = build_aggregate()
    late_approval = aggregate.review_decisions[0].model_copy(update={"created_at": LATER})

    with pytest.raises(ValidationError, match="approval decision for every selection"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "review_decisions": (late_approval,),
        })

    locked = aggregate.shots[0].model_copy(
        update={"lifecycle_state": "locked", "review_state": "approved"}
    )
    unlocked = locked.model_copy(
        update={
            "version_id": "shot-1.v2",
            "revision": 2,
            "parent_version_id": locked.version_id,
            "lifecycle_state": "approved",
            "created_at": LATER,
        }
    )
    unlock_after_successor = ReviewDecision(
        **common(
            aggregate.scope,
            "review-unlock-after-successor",
            created_at="2026-07-15T20:32:00+00:00",
        ),
        subject_ref=ref(locked),
        decision="unlock",
    )

    with pytest.raises(ValidationError, match="exact finalized unlock"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (locked, unlocked, aggregate.shots[1]),
            "review_decisions": (*aggregate.review_decisions, unlock_after_successor),
        })


def test_changed_approved_candidate_requires_new_exact_approval() -> None:
    aggregate = build_aggregate()
    candidate_v1 = aggregate.asset_candidates[0]
    candidate_v2 = candidate_v1.model_copy(
        update={
            "version_id": "candidate-shot-1.v2",
            "revision": 2,
            "parent_version_id": candidate_v1.version_id,
            "content_digest": digest("changed-candidate-shot-1"),
            "created_at": LATER,
        }
    )

    with pytest.raises(ValidationError, match="new exact finalized approval"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "asset_candidates": (candidate_v1, candidate_v2),
        })


def test_continuity_version_cannot_claim_selection_targeting_an_older_version() -> None:
    aggregate = build_aggregate()
    continuity_v1 = aggregate.continuity_states[0]
    artifact = SafeArtifactRef(
        artifact_id="artifact-character-old-version",
        artifact_type="image",
        content_digest=digest("artifact-character-old-version"),
    )
    candidate = AssetCandidateVersion(
        **common(aggregate.scope, "candidate-character-old-version"),
        target_ref=ref(continuity_v1),
        artifact_ref=artifact,
    )
    selection = SelectedVersion(
        **common(aggregate.scope, "selection-character-old-version"),
        target_ref=ref(continuity_v1),
        purpose="character_reference",
        candidate_ref=ref(candidate),
    )
    continuity_v2 = continuity_v1.model_copy(
        update={
            "version_id": "character-lin.v2",
            "revision": 2,
            "parent_version_id": continuity_v1.version_id,
            "created_at": LATER,
            "approved_asset_selection_refs": (ref(selection),),
        }
    )

    with pytest.raises(ValidationError, match="exact continuity version"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "continuity_states": (continuity_v1, continuity_v2),
            "asset_candidates": (*aggregate.asset_candidates, candidate),
            "selections": (*aggregate.selections, selection),
        })


def test_delivery_cannot_include_a_selection_from_another_episode() -> None:
    aggregate = build_aggregate()
    other_episode = aggregate.episodes[0].model_copy(
        update={
            "entity_id": "episode-2",
            "version_id": "episode-2.v1",
            "content_digest": digest("episode-2"),
        }
    )
    delivery = aggregate.deliveries[0].model_copy(update={"episode_ref": ref(other_episode)})

    with pytest.raises(ValidationError, match="selection must belong"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "episodes": (*aggregate.episodes, other_episode),
            "deliveries": (delivery,),
        })


def test_continuity_operation_records_predicted_and_partial_applied_scope_separately() -> None:
    aggregate = build_applied_continuity_operation(selected_shot_ids=("shot-1",))
    proposal = aggregate.agent_proposals[0]

    assert proposal.decision_state == "executed"
    assert proposal.impact_refs == tuple(ref(shot) for shot in aggregate.shots[:2])
    assert [item.entity_id for item in proposal.applied_refs] == ["shot-1"]
    assert aggregate.shots[-1].source_proposal_ref == ref(proposal)


def test_continuity_operation_can_apply_full_predicted_scope() -> None:
    aggregate = build_applied_continuity_operation(selected_shot_ids=("shot-1", "shot-2"))

    assert {item.entity_id for item in aggregate.agent_proposals[0].applied_refs} == {
        "shot-1",
        "shot-2",
    }


@pytest.mark.parametrize("decision_state", ["pending", "accepted", "partially_accepted", "rejected"])
def test_non_executed_proposal_state_cannot_claim_applied_refs(decision_state: str) -> None:
    aggregate = build_applied_continuity_operation()
    proposal = aggregate.agent_proposals[0].model_copy(update={"decision_state": decision_state})

    with pytest.raises(ValidationError, match="decision state cannot claim"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "agent_proposals": (proposal,),
        })


def test_application_rejects_cropped_added_and_duplicate_membership() -> None:
    aggregate = build_applied_continuity_operation(selected_shot_ids=("shot-1", "shot-2"))
    proposal = aggregate.agent_proposals[0]

    cropped = proposal.model_copy(update={"applied_refs": proposal.applied_refs[:1]})
    with pytest.raises(ValidationError, match="bidirectionally exact"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "agent_proposals": (cropped,),
        })

    added = proposal.model_copy(update={"applied_refs": (*proposal.applied_refs, ref(aggregate.shots[0]))})
    with pytest.raises(ValidationError, match="distinct shot entities"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "agent_proposals": (added,),
        })

    duplicate = proposal.model_copy(update={"impact_refs": (*proposal.impact_refs, proposal.impact_refs[0])})
    with pytest.raises(ValidationError, match="impact refs must be unique"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "agent_proposals": (duplicate,),
        })


def test_continuity_changed_successor_cannot_be_orphaned_by_coordinated_membership_crop() -> None:
    aggregate = build_applied_continuity_operation(
        selected_shot_ids=("shot-1", "shot-2")
    )
    proposal = aggregate.agent_proposals[0]
    cropped = proposal.model_copy(update={"applied_refs": proposal.applied_refs[:1]})
    orphan = aggregate.shots[-1].model_copy(update={"source_proposal_ref": None})

    with pytest.raises(
        ValidationError,
        match="continuity change requires an exact source proposal",
    ):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots[:-1], orphan),
            "agent_proposals": (cropped,),
        })


def test_orphaned_continuity_change_fails_json_and_store_before_persist(tmp_path: Path) -> None:
    aggregate = build_applied_continuity_operation(
        selected_shot_ids=("shot-1", "shot-2")
    )
    payload = aggregate.model_dump(mode="json")
    payload["agent_proposals"][0]["applied_refs"] = payload["agent_proposals"][0][
        "applied_refs"
    ][:1]
    payload["shots"][-1]["source_proposal_ref"] = None

    with pytest.raises(
        ValidationError,
        match="continuity change requires an exact source proposal",
    ):
        ProductionProjectAggregate.model_validate_json(json.dumps(payload))

    store = EpisodeDomainAggregateStore(tmp_path)
    snapshot = store.snapshot_path(
        org_id=aggregate.scope.org_id,
        project_id=aggregate.scope.project_id,
    )
    with pytest.raises(
        ValidationError,
        match="continuity change requires an exact source proposal",
    ):
        store.save(  # type: ignore[arg-type]
            payload,
            expected_aggregate_version=0,
            idempotency_key="orphaned-continuity-change",
            payload_digest=digest("orphaned-continuity-change"),
        )
    assert not snapshot.exists()

    restarted = EpisodeDomainAggregateStore(tmp_path)
    with pytest.raises(AggregateNotFoundError, match="does not exist"):
        restarted.load(
            org_id=aggregate.scope.org_id,
            project_id=aggregate.scope.project_id,
        )


def test_source_proposal_backlink_is_rejected_when_continuity_is_unchanged() -> None:
    aggregate = build_aggregate()
    parent = aggregate.shots[0]
    proposal = AgentProposal(
        **common(
            aggregate.scope,
            "proposal-noop",
            lifecycle="draft",
            review="not_requested",
            created_at=LATER,
        ),
        target_ref=ref(aggregate.continuity_states[0]),
        impact_refs=(ref(parent),),
        action="inspect_continuity",
        decision_state="accepted",
    )
    ordinary_revision = parent.model_copy(
        update={
            "version_id": "shot-1.v2",
            "revision": 2,
            "parent_version_id": parent.version_id,
            "lifecycle_state": "candidate",
            "review_state": "needs_review",
            "created_at": LATER,
            "content_digest": digest("shot-1-ordinary-revision"),
            "source_proposal_ref": ref(proposal),
        }
    )

    with pytest.raises(
        ValidationError,
        match="unchanged continuity cannot claim a source proposal",
    ):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots, ordinary_revision),
            "agent_proposals": (proposal,),
        })


def test_application_rejects_source_scope_and_non_continuity_fact_mismatch() -> None:
    aggregate = build_applied_continuity_operation()
    successor = aggregate.shots[-1]
    without_source = successor.model_copy(update={"source_proposal_ref": None})
    with pytest.raises(ValidationError, match="source proposal"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots[:-1], without_source),
        })

    foreign_actor = aggregate.scope.model_copy(update={"actor_id": "creator-2"})
    foreign_proposal = aggregate.agent_proposals[0].model_copy(update={"scope": foreign_actor})
    with pytest.raises(ValidationError, match="exact org, project, and actor"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "agent_proposals": (foreign_proposal,),
        })

    changed_duration = successor.model_copy(update={"duration_seconds": 9.0})
    with pytest.raises(ValidationError, match="non-continuity shot facts"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots[:-1], changed_duration),
        })

    skipped_review = successor.model_copy(update={"review_state": "not_requested"})
    with pytest.raises(ValidationError, match="require creator review"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots[:-1], skipped_review),
        })

    additive_continuity = successor.model_copy(
        update={
            "continuity_refs": (
                ref(aggregate.continuity_states[0]),
                ref(aggregate.continuity_states[-1]),
            )
        }
    )
    with pytest.raises(ValidationError, match="only replace"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (*aggregate.shots[:-1], additive_continuity),
        })


def test_every_predicted_impact_must_contain_exact_parent_continuity_ref() -> None:
    aggregate = build_applied_continuity_operation()
    unrelated = aggregate.continuity_states[0].model_copy(
        update={
            "entity_id": "character-qiao",
            "version_id": "character-qiao.v1",
            "subject_id": "qiao",
            "parent_version_id": None,
            "revision": 1,
            "content_digest": digest("character-qiao"),
        }
    )
    unaffected_parent = aggregate.shots[1].model_copy(update={"continuity_refs": (ref(unrelated),)})

    with pytest.raises(ValidationError, match="every predicted impact shot"):
        ProductionProjectAggregate(**{
            **aggregate.model_dump(),
            "shots": (aggregate.shots[0], unaffected_parent, *aggregate.shots[2:]),
            "continuity_states": (*aggregate.continuity_states, unrelated),
        })


def test_same_target_independent_shot_is_not_claimed_by_operation_membership() -> None:
    aggregate = build_applied_continuity_operation()
    independent = aggregate.shots[0].model_copy(
        update={
            "entity_id": "shot-independent",
            "version_id": "shot-independent.v1",
            "revision": 1,
            "parent_version_id": None,
            "sequence": 3,
            "continuity_refs": (ref(aggregate.continuity_states[-1]),),
            "source_proposal_ref": None,
            "content_digest": digest("shot-independent"),
        }
    )
    restored = ProductionProjectAggregate(**{
        **aggregate.model_dump(),
        "shots": (*aggregate.shots, independent),
    })

    assert ref(independent) not in restored.agent_proposals[0].applied_refs


def test_later_proposal_keeps_operation_membership_separate() -> None:
    aggregate = build_applied_continuity_operation()
    first_proposal = aggregate.agent_proposals[0]
    first_successor = aggregate.shots[-1]
    first_target = aggregate.continuity_states[-1]
    second_target = first_target.model_copy(
        update={
            "version_id": "character-lin.v3",
            "revision": 3,
            "parent_version_id": first_target.version_id,
            "content_digest": digest("character-lin-rain-coat"),
            "created_at": "2026-07-15T20:32:00+00:00",
        }
    )
    second_proposal = first_proposal.model_copy(
        update={
            "entity_id": "proposal-lin-rain-coat",
            "version_id": "proposal-lin-rain-coat.v1",
            "revision": 1,
            "parent_version_id": None,
            "target_ref": ref(second_target),
            "impact_refs": (ref(first_successor),),
            "applied_refs": (),
            "content_digest": digest("proposal-lin-rain-coat"),
            "created_at": "2026-07-15T20:32:00+00:00",
        }
    )
    second_successor = first_successor.model_copy(
        update={
            "version_id": "shot-1.v3",
            "revision": 3,
            "parent_version_id": first_successor.version_id,
            "continuity_refs": (ref(second_target),),
            "source_proposal_ref": ref(second_proposal),
            "content_digest": digest("shot-1-rain-coat"),
            "created_at": "2026-07-15T20:32:00+00:00",
        }
    )
    second_proposal = second_proposal.model_copy(update={"applied_refs": (ref(second_successor),)})
    restored = ProductionProjectAggregate(**{
        **aggregate.model_dump(),
        "shots": (*aggregate.shots, second_successor),
        "continuity_states": (*aggregate.continuity_states, second_target),
        "agent_proposals": (*aggregate.agent_proposals, second_proposal),
    })

    assert restored.agent_proposals[0].applied_refs == (ref(first_successor),)
    assert restored.agent_proposals[1].applied_refs == (ref(second_successor),)


def test_undo_requires_and_records_full_restoration_operation() -> None:
    applied = build_applied_continuity_operation(selected_shot_ids=("shot-1", "shot-2"))
    restored = append_full_undo(applied)
    undo = restored.agent_proposals[-1]

    assert set(undo.impact_refs) == set(applied.agent_proposals[0].applied_refs)
    assert {item.entity_id for item in undo.applied_refs} == {"shot-1", "shot-2"}
    assert all(
        restored.continuity_states[0].as_ref() in shot.continuity_refs
        for shot in restored.shots[-2:]
    )


def test_undo_rejects_partial_restoration_and_wrong_parent_scope() -> None:
    applied = build_applied_continuity_operation(selected_shot_ids=("shot-1", "shot-2"))
    restored = append_full_undo(applied)
    undo = restored.agent_proposals[-1]
    partial = undo.model_copy(update={"applied_refs": undo.applied_refs[:1]})
    with pytest.raises(ValidationError, match="restore the full parent applied scope"):
        ProductionProjectAggregate(**{
            **restored.model_dump(),
            "agent_proposals": (restored.agent_proposals[0], partial),
        })

    wrong_impact = undo.model_copy(update={"impact_refs": tuple(reversed(undo.impact_refs[:-1]))})
    with pytest.raises(ValidationError, match="exactly equal"):
        ProductionProjectAggregate(**{
            **restored.model_dump(),
            "agent_proposals": (restored.agent_proposals[0], wrong_impact),
        })


def test_operation_facts_survive_json_and_store_roundtrip(tmp_path: Path) -> None:
    aggregate = build_applied_continuity_operation()
    decoded = ProductionProjectAggregate.model_validate_json(aggregate.model_dump_json())
    assert decoded == aggregate

    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        aggregate,
        expected_aggregate_version=0,
        idempotency_key="contract-v011-roundtrip",
        payload_digest=digest("contract-v011-roundtrip"),
    )
    assert store.load(org_id=aggregate.scope.org_id, project_id=aggregate.scope.project_id) == aggregate


def test_old_v01_payload_without_additive_fields_keeps_defaults() -> None:
    aggregate = build_aggregate()
    proposal = AgentProposal(
        **common(
            aggregate.scope,
            "proposal-old-v01",
            lifecycle="draft",
            review="not_requested",
        ),
        target_ref=ref(aggregate.continuity_states[0]),
        impact_refs=tuple(ref(shot) for shot in aggregate.shots),
        action="inspect_continuity",
        decision_state="accepted",
    )
    payload = {
        **aggregate.model_dump(mode="json"),
        "agent_proposals": [proposal.model_dump(mode="json")],
    }
    for shot in payload["shots"]:
        shot.pop("source_proposal_ref")
    payload["agent_proposals"][0].pop("applied_refs")

    decoded = ProductionProjectAggregate.model_validate(payload)

    assert decoded.schema_version == "afs_episode_production_aggregate.v0.1"
    assert EPISODE_PRODUCTION_CONTRACT_REVISION == "v0.1.1"
    assert decoded.agent_proposals[0].applied_refs == ()
    assert all(shot.source_proposal_ref is None for shot in decoded.shots)
