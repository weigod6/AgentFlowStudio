from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from apps.api.runtime_episode_authoring_service import (
    AuthoringReferenceError,
    AuthoringStateError,
    AuthoringVersionConflictError,
    create_authoring_entity,
    diff_shot_versions,
    preview_shot_revision,
    reorder_authoring_entities,
    restore_shot_as_new,
    revise_authoring_entity,
    revise_shot_intent,
)
from apps.api.runtime_episode_domain_contract import (
    EntityVersionRef,
    ProductionProjectAggregate,
    ProjectVersion,
    TenantScope,
)


SCOPE = TenantScope(org_id="org-author", project_id="project-author", actor_id="creator-1")
START = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _empty_aggregate() -> ProductionProjectAggregate:
    project = ProjectVersion(
        entity_id=SCOPE.project_id,
        version_id="project-v1",
        revision=1,
        parent_version_id=None,
        lifecycle_state="draft",
        review_state="not_requested",
        content_digest=_digest("project-v1"),
        scope=SCOPE,
        created_at=START.isoformat(),
        title="长篇新作",
        summary="",
        creative_intent="",
        ip_profile="",
    )
    return ProductionProjectAggregate(
        aggregate_version=1,
        evaluated_at=START.isoformat(),
        scope=SCOPE,
        projects=(project,),
    )


def _time(step: int) -> str:
    return (START + timedelta(seconds=step)).isoformat()


def _create(
    aggregate: ProductionProjectAggregate,
    *,
    step: int,
    entity_type: str,
    entity_id: str,
    attributes: dict[str, object],
) -> ProductionProjectAggregate:
    return create_authoring_entity(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        entity_type=entity_type,  # type: ignore[arg-type]
        entity_id=entity_id,
        version_id=f"{entity_id}-v1",
        created_at=_time(step),
        attributes=attributes,
    )


def _latest(aggregate: ProductionProjectAggregate, collection: str, entity_id: str):
    return max(
        (item for item in getattr(aggregate, collection) if item.entity_id == entity_id),
        key=lambda item: item.revision,
    )


def _long_form_aggregate() -> ProductionProjectAggregate:
    aggregate = _empty_aggregate()
    project_ref = aggregate.projects[0].as_ref()
    aggregate = _create(
        aggregate,
        step=1,
        entity_type="story_bible",
        entity_id="bible-main",
        attributes={
            "project_ref": project_ref,
            "title": "世界设定",
            "summary": "城市与荒野共存。",
            "world_rules": ("记忆不可复制", "夜雨会放大旧伤"),
        },
    )
    aggregate = _create(
        aggregate,
        step=2,
        entity_type="series",
        entity_id="series-main",
        attributes={
            "project_ref": project_ref,
            "title": "雨城纪事",
            "summary": "两代人的选择。",
            "creative_intent": "克制而温暖。",
        },
    )
    aggregate = _create(
        aggregate,
        step=3,
        entity_type="arc",
        entity_id="arc-main",
        attributes={
            "series_ref": aggregate.series[0].as_ref(),
            "story_bible_ref": aggregate.story_bibles[0].as_ref(),
            "sequence": 1,
            "title": "归城",
            "summary": "主人公重新面对故乡。",
            "creative_intent": "让悬念来自人物关系。",
        },
    )
    aggregate = _create(
        aggregate,
        step=4,
        entity_type="reference_asset",
        entity_id="asset-hero",
        attributes={
            "project_ref": project_ref,
            "asset_kind": "human",
            "label": "主角林澈",
            "identity": "二十七岁，短发，左眉有浅疤。",
            "confidence": 0.55,
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    aggregate = _create(
        aggregate,
        step=5,
        entity_type="reference_set",
        entity_id="refset-main",
        attributes={
            "project_ref": project_ref,
            "title": "主角基准",
            "summary": "用于前两集的主角外观与气质。",
            "scope_kind": "project",
            "scope_refs": (project_ref,),
            "asset_refs": (aggregate.reference_assets[0].as_ref(),),
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    series_ref = aggregate.series[0].as_ref()
    arc_ref = aggregate.arcs[0].as_ref()
    refset_ref = aggregate.reference_sets[0].as_ref()
    step = 6
    for episode_index in range(1, 3):
        episode_id = f"episode-{episode_index}"
        aggregate = _create(
            aggregate,
            step=step,
            entity_type="episode",
            entity_id=episode_id,
            attributes={
                "series_ref": series_ref,
                "arc_ref": arc_ref,
                "sequence": episode_index,
                "title": f"第{episode_index}集",
                "summary": "人物关系继续推进。",
                "creative_intent": "每集保留一个未回答的问题。",
                "reference_set_ref": refset_ref,
            },
        )
        step += 1
        episode_ref = _latest(aggregate, "episodes", episode_id).as_ref()
        for scene_index in range(1, 3):
            scene_id = f"{episode_id}-scene-{scene_index}"
            aggregate = _create(
                aggregate,
                step=step,
                entity_type="scene",
                entity_id=scene_id,
                attributes={
                    "episode_ref": episode_ref,
                    "sequence": scene_index,
                    "title": f"场景{scene_index}",
                    "summary": "行动与选择发生在这里。",
                    "creative_intent": "保持空间方向清楚。",
                    "reference_set_ref": refset_ref,
                },
            )
            step += 1
            scene_ref = _latest(aggregate, "scenes", scene_id).as_ref()
            for shot_index in range(1, 3):
                shot_id = f"{scene_id}-shot-{shot_index}"
                aggregate = _create(
                    aggregate,
                    step=step,
                    entity_type="shot",
                    entity_id=shot_id,
                    attributes={
                        "scene_ref": scene_ref,
                        "sequence": shot_index,
                        "title": f"镜头{shot_index}",
                        "summary": "角色完成一个可见动作。",
                        "creative_intent": "中景保持呼吸感。",
                        "duration_seconds": 4,
                        "reference_set_ref": refset_ref,
                    },
                )
                step += 1
    return aggregate


def test_empty_creator_aggregate_builds_two_episode_eight_shot_hierarchy_with_stable_refs() -> None:
    aggregate = _long_form_aggregate()

    assert len({_latest(aggregate, "episodes", item.entity_id).entity_id for item in aggregate.episodes}) == 2
    assert len({_latest(aggregate, "scenes", item.entity_id).entity_id for item in aggregate.scenes}) == 4
    assert len({_latest(aggregate, "shots", item.entity_id).entity_id for item in aggregate.shots}) == 8
    assert ProductionProjectAggregate.model_validate_json(aggregate.model_dump_json()) == aggregate
    assert all(shot.reference_set_ref == aggregate.reference_sets[0].as_ref() for shot in aggregate.shots)


def test_low_confidence_reference_requires_explicit_human_confirmation() -> None:
    aggregate = _empty_aggregate()
    with pytest.raises(ValueError, match="explicit human confirmation"):
        _create(
            aggregate,
            step=1,
            entity_type="reference_asset",
            entity_id="asset-low-confidence",
            attributes={
                "project_ref": aggregate.projects[0].as_ref(),
                "asset_kind": "animal",
                "label": "黑猫",
                "identity": "右耳缺口。",
                "confidence": 0.3,
                "approval_state": "approved",
                "human_confirmed": False,
            },
        )


def test_material_reference_edits_reset_human_approval_and_scope_is_enforced() -> None:
    aggregate = _long_form_aggregate()
    asset = _latest(aggregate, "reference_assets", "asset-hero")
    aggregate = revise_authoring_entity(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        target_ref=asset.as_ref(),
        new_version_id="asset-hero-v2",
        created_at=_time(40),
        changes={"identity": "短发，右眉浅疤。", "approval_state": "approved", "human_confirmed": True},
    )
    asset_v2 = _latest(aggregate, "reference_assets", "asset-hero")
    assert asset_v2.approval_state == "pending_human"
    assert asset_v2.human_confirmed is False

    episode_one = _latest(aggregate, "episodes", "episode-1")
    scoped = _create(
        aggregate,
        step=41,
        entity_type="reference_set",
        entity_id="refset-episode-one",
        attributes={
            "project_ref": aggregate.projects[0].as_ref(),
            "title": "仅第一集",
            "scope_kind": "episode",
            "scope_refs": (episode_one.as_ref(),),
            "asset_refs": (),
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    episode_two_shot = _latest(scoped, "shots", "episode-2-scene-1-shot-1")
    with pytest.raises(AuthoringStateError, match="scope"):
        preview_shot_revision(
            scoped,
            scope=SCOPE,
            expected_aggregate_version=scoped.aggregate_version,
            shot_ref=episode_two_shot.as_ref(),
            proposed_changes={
                "reference_set_ref": _latest(
                    scoped, "reference_sets", "refset-episode-one"
                ).as_ref()
            },
        )


def test_reference_approval_reset_uses_canonical_value_change_matrix() -> None:
    aggregate = _long_form_aggregate()
    asset = _latest(aggregate, "reference_assets", "asset-hero")
    unchanged_asset = revise_authoring_entity(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        target_ref=asset.as_ref(),
        new_version_id="asset-hero-v2",
        created_at=_time(40),
        changes={
            "label": asset.label,
            "identity": asset.identity,
            "confidence": asset.confidence,
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    asset_v2 = _latest(unchanged_asset, "reference_assets", "asset-hero")
    assert asset_v2.approval_state == "approved"
    assert asset_v2.human_confirmed is True

    changed_asset = revise_authoring_entity(
        unchanged_asset,
        scope=SCOPE,
        expected_aggregate_version=unchanged_asset.aggregate_version,
        target_ref=asset_v2.as_ref(),
        new_version_id="asset-hero-v3",
        created_at=_time(41),
        changes={
            "identity": "短发，右眉浅疤。",
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    asset_v3 = _latest(changed_asset, "reference_assets", "asset-hero")
    assert asset_v3.approval_state == "pending_human"
    assert asset_v3.human_confirmed is False

    reference_set = _latest(aggregate, "reference_sets", "refset-main")
    unchanged_set = revise_authoring_entity(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        target_ref=reference_set.as_ref(),
        new_version_id="refset-main-v2",
        created_at=_time(40),
        changes={
            "title": reference_set.title,
            "summary": reference_set.summary,
            "scope_kind": reference_set.scope_kind,
            "scope_refs": reference_set.scope_refs,
            "asset_refs": reference_set.asset_refs,
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    refset_v2 = _latest(unchanged_set, "reference_sets", "refset-main")
    assert refset_v2.approval_state == "approved"
    assert refset_v2.human_confirmed is True

    changed_set = revise_authoring_entity(
        unchanged_set,
        scope=SCOPE,
        expected_aggregate_version=unchanged_set.aggregate_version,
        target_ref=refset_v2.as_ref(),
        new_version_id="refset-main-v3",
        created_at=_time(41),
        changes={
            "scope_kind": "series",
            "scope_refs": (_latest(unchanged_set, "series", "series-main").as_ref(),),
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    refset_v3 = _latest(changed_set, "reference_sets", "refset-main")
    assert refset_v3.approval_state == "pending_human"
    assert refset_v3.human_confirmed is False


def test_reference_set_binding_requires_approved_exact_version() -> None:
    aggregate = _long_form_aggregate()
    project_ref = aggregate.projects[0].as_ref()
    aggregate = _create(
        aggregate,
        step=40,
        entity_type="reference_set",
        entity_id="refset-pending",
        attributes={
            "project_ref": project_ref,
            "title": "待确认基准",
            "summary": "需要确认后才能绑定。",
            "scope_kind": "project",
            "scope_refs": (project_ref,),
            "asset_refs": (_latest(aggregate, "reference_assets", "asset-hero").as_ref(),),
            "approval_state": "pending_human",
            "human_confirmed": False,
        },
    )
    episode = _latest(aggregate, "episodes", "episode-1")
    pending_set = _latest(aggregate, "reference_sets", "refset-pending")
    with pytest.raises(AuthoringStateError, match="human-approved exact version"):
        revise_authoring_entity(
            aggregate,
            scope=SCOPE,
            expected_aggregate_version=aggregate.aggregate_version,
            target_ref=episode.as_ref(),
            new_version_id="episode-1-v2",
            created_at=_time(41),
            changes={"reference_set_ref": pending_set.as_ref()},
        )

    approved = revise_authoring_entity(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        target_ref=pending_set.as_ref(),
        new_version_id="refset-pending-v2",
        created_at=_time(41),
        changes={
            "asset_refs": pending_set.asset_refs,
            "scope_refs": pending_set.scope_refs,
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    approved_set = _latest(approved, "reference_sets", "refset-pending")
    rebound = revise_authoring_entity(
        approved,
        scope=SCOPE,
        expected_aggregate_version=approved.aggregate_version,
        target_ref=episode.as_ref(),
        new_version_id="episode-1-v2",
        created_at=_time(42),
        changes={"reference_set_ref": approved_set.as_ref()},
    )
    assert _latest(rebound, "episodes", "episode-1").reference_set_ref == approved_set.as_ref()


def test_shot_revision_requires_exact_preview_and_preserves_every_protected_digest() -> None:
    aggregate = _long_form_aggregate()
    shot = _latest(aggregate, "shots", "episode-1-scene-1-shot-1")
    protected_before = {
        ref: next(
            item.content_digest
            for collection in (
                aggregate.projects,
                aggregate.series,
                aggregate.story_bibles,
                aggregate.arcs,
                aggregate.episodes,
                aggregate.scenes,
                aggregate.shots,
                aggregate.reference_assets,
                aggregate.reference_sets,
            )
            for item in collection
            if item.as_ref() == ref
        )
        for ref in preview_shot_revision(
            aggregate,
            scope=SCOPE,
            expected_aggregate_version=aggregate.aggregate_version,
            shot_ref=shot.as_ref(),
            proposed_changes={"creative_intent": "雨声中只保留一次迟疑。", "duration_seconds": 6},
        ).protected_refs
    }
    preview = preview_shot_revision(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        shot_ref=shot.as_ref(),
        proposed_changes={"creative_intent": "雨声中只保留一次迟疑。", "duration_seconds": 6},
    )
    revised = revise_shot_intent(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        shot_ref=shot.as_ref(),
        new_version_id="episode-1-scene-1-shot-1-v2",
        created_at=_time(30),
        proposed_changes=preview.proposed_changes,
        preview_digest=preview.preview_digest,
        confirmed_direct_refs=preview.direct_affected_refs,
        confirmed_transitive_refs=preview.transitive_affected_refs,
        confirmed_protected_refs=preview.protected_refs,
    )
    successor = _latest(revised, "shots", shot.entity_id)

    assert successor.revision == 2
    assert successor.parent_version_id == shot.version_id
    assert successor.lifecycle_state == "candidate"
    assert successor.review_state == "needs_review"
    assert diff_shot_versions(
        revised,
        scope=SCOPE,
        left_ref=shot.as_ref(),
        right_ref=successor.as_ref(),
    ) == {
        "creative_intent": {"before": "中景保持呼吸感。", "after": "雨声中只保留一次迟疑。"},
        "duration_seconds": {"before": 4.0, "after": 6.0},
    }
    for ref, digest in protected_before.items():
        assert any(
            item.as_ref() == ref and item.content_digest == digest
            for collection in (
                revised.projects,
                revised.series,
                revised.story_bibles,
                revised.arcs,
                revised.episodes,
                revised.scenes,
                revised.shots,
                revised.reference_assets,
                revised.reference_sets,
            )
            for item in collection
        )

    with pytest.raises(AuthoringVersionConflictError, match="preview digest"):
        revise_shot_intent(
            aggregate,
            scope=SCOPE,
            expected_aggregate_version=aggregate.aggregate_version,
            shot_ref=shot.as_ref(),
            new_version_id="bad-v2",
            created_at=_time(30),
            proposed_changes=preview.proposed_changes,
            preview_digest="0" * 64,
            confirmed_direct_refs=preview.direct_affected_refs,
            confirmed_transitive_refs=preview.transitive_affected_refs,
            confirmed_protected_refs=preview.protected_refs,
        )


def test_restore_appends_v3_and_reorder_requires_complete_current_sibling_set() -> None:
    aggregate = _long_form_aggregate()
    shot_v1 = _latest(aggregate, "shots", "episode-1-scene-1-shot-1")
    preview_v2 = preview_shot_revision(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        shot_ref=shot_v1.as_ref(),
        proposed_changes={"title": "雨中的回望"},
    )
    revised = revise_shot_intent(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        shot_ref=shot_v1.as_ref(),
        new_version_id=f"{shot_v1.entity_id}-v2",
        created_at=_time(30),
        proposed_changes=preview_v2.proposed_changes,
        preview_digest=preview_v2.preview_digest,
        confirmed_direct_refs=preview_v2.direct_affected_refs,
        confirmed_transitive_refs=preview_v2.transitive_affected_refs,
        confirmed_protected_refs=preview_v2.protected_refs,
    )
    shot_v2 = _latest(revised, "shots", shot_v1.entity_id)
    diff_v1_v2 = diff_shot_versions(
        revised,
        scope=SCOPE,
        left_ref=shot_v1.as_ref(),
        right_ref=shot_v2.as_ref(),
    )
    assert diff_v1_v2 == {"title": {"before": "镜头1", "after": "雨中的回望"}}
    restore_changes = {
        "title": shot_v1.title,
        "summary": shot_v1.summary,
        "creative_intent": shot_v1.creative_intent,
        "duration_seconds": shot_v1.duration_seconds,
        "reference_set_ref": shot_v1.reference_set_ref,
    }
    restore_preview = preview_shot_revision(
        revised,
        scope=SCOPE,
        expected_aggregate_version=revised.aggregate_version,
        shot_ref=shot_v2.as_ref(),
        proposed_changes=restore_changes,
    )
    restored = restore_shot_as_new(
        revised,
        scope=SCOPE,
        expected_aggregate_version=revised.aggregate_version,
        historical_ref=shot_v1.as_ref(),
        current_ref=shot_v2.as_ref(),
        new_version_id=f"{shot_v1.entity_id}-v3",
        created_at=_time(31),
        preview_digest=restore_preview.preview_digest,
        confirmed_direct_refs=restore_preview.direct_affected_refs,
        confirmed_transitive_refs=restore_preview.transitive_affected_refs,
        confirmed_protected_refs=restore_preview.protected_refs,
    )
    shot_v3 = _latest(restored, "shots", shot_v1.entity_id)

    assert shot_v3.revision == 3
    assert shot_v3.parent_version_id == shot_v2.version_id
    assert shot_v3.title == shot_v1.title
    assert len([item for item in restored.shots if item.entity_id == shot_v1.entity_id]) == 3

    siblings = tuple(
        sorted(
            (
                _latest(restored, "shots", entity_id)
                for entity_id in ("episode-1-scene-1-shot-1", "episode-1-scene-1-shot-2")
            ),
            key=lambda item: item.sequence,
        )
    )
    reordered = reorder_authoring_entities(
        restored,
        scope=SCOPE,
        expected_aggregate_version=restored.aggregate_version,
        ordered_refs=(siblings[1].as_ref(), siblings[0].as_ref()),
        new_version_ids=(f"{siblings[1].entity_id}-v2", f"{siblings[0].entity_id}-v4"),
        created_at=_time(32),
    )
    assert _latest(reordered, "shots", siblings[1].entity_id).sequence == 1
    assert _latest(reordered, "shots", siblings[0].entity_id).sequence == 2
    assert _latest(reordered, "shots", shot_v1.entity_id).version_id == f"{shot_v1.entity_id}-v4"
    assert diff_shot_versions(
        reordered,
        scope=SCOPE,
        left_ref=shot_v1.as_ref(),
        right_ref=shot_v2.as_ref(),
    ) == diff_v1_v2

    with pytest.raises(AuthoringStateError, match="complete current sibling"):
        reorder_authoring_entities(
            restored,
            scope=SCOPE,
            expected_aggregate_version=restored.aggregate_version,
            ordered_refs=(siblings[0].as_ref(),),
            new_version_ids=("incomplete-v5",),
            created_at=_time(32),
        )


def test_stale_exact_ref_fails_closed() -> None:
    aggregate = _long_form_aggregate()
    episode = aggregate.episodes[0]
    aggregate = revise_authoring_entity(
        aggregate,
        scope=SCOPE,
        expected_aggregate_version=aggregate.aggregate_version,
        target_ref=episode.as_ref(),
        new_version_id=f"{episode.entity_id}-v2",
        created_at=_time(30),
        changes={"title": "第一集·归来"},
    )
    with pytest.raises(AuthoringReferenceError, match="stale"):
        create_authoring_entity(
            aggregate,
            scope=SCOPE,
            expected_aggregate_version=aggregate.aggregate_version,
            entity_type="scene",
            entity_id="bad-scene",
            version_id="bad-scene-v1",
            created_at=_time(31),
            attributes={
                "episode_ref": episode.as_ref(),
                "sequence": 3,
                "title": "无效场景",
            },
        )
