from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from apps.api.runtime_service import create_runtime_app


PROJECT_ID = "creator-long-form"


def _ref(entity_type: str, entity_id: str, version_id: str) -> dict[str, str]:
    return {"entity_type": entity_type, "entity_id": entity_id, "version_id": version_id}


def _post(
    client: TestClient,
    *,
    version: int,
    key: str,
    body: dict[str, Any],
):
    response = client.post(
        f"/projects/{PROJECT_ID}/episode-production-aggregate/commands",
        headers={"Idempotency-Key": key},
        json={"expected_aggregate_version": version, **body},
    )
    assert response.status_code == 200, response.text
    return response


def _create(
    client: TestClient,
    *,
    version: int,
    step: int,
    entity_type: str,
    entity_id: str,
    entity: dict[str, Any],
):
    return _post(
        client,
        version=version,
        key=f"create-{entity_id}",
        body={
            "action": "authoring.create",
            "entity_id": entity_id,
            "version_id": f"{entity_id}-v1",
            "created_at": f"2026-07-16T00:00:{step:02d}+00:00",
            "entity": {"entity_type": entity_type, **entity},
        },
    )


def test_creator_routes_build_long_form_from_empty_and_reload_after_process_restart(
    tmp_path: Path,
) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    created = client.post(
        "/projects",
        json={
            "project_id": PROJECT_ID,
            "goal": "雨城纪事",
            "project_type": "studio_creator_authoring",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["episode_bootstrap"]["aggregate_version"] == 1
    empty = client.get(f"/projects/{PROJECT_ID}/creator-workspace")
    assert empty.status_code == 200, empty.text
    assert empty.json()["counts"] == {
        "episodes": 0,
        "scenes": 0,
        "shots": 0,
        "reference_assets": 0,
        "reference_sets": 0,
    }

    version = 1
    project_ref = empty.json()["project"]["ref"]
    profile = _post(
        client,
        version=version,
        key="revise-project-profile",
        body={
            "action": "authoring.revise",
            "target_ref": project_ref,
            "new_version_id": "creator-long-form-v2",
            "created_at": "2026-07-16T00:00:01+00:00",
            "changes": {
                "entity_type": "project",
                "summary": "一座雨城里，两代人重新理解离开与归来。",
                "creative_intent": "以人物选择推动悬念。",
                "ip_profile": "16 集都市奇幻长篇，面向青年观众。",
            },
        },
    )
    version = profile.json()["aggregate_version"]
    project_ref = profile.json()["aggregate"]["projects"][-1]
    project_ref = _ref("project", project_ref["entity_id"], project_ref["version_id"])

    bible = _create(
        client,
        version=version,
        step=2,
        entity_type="story_bible",
        entity_id="bible-main",
        entity={
            "project_ref": project_ref,
            "title": "雨城世界设定",
            "summary": "雨水会唤起未完成的记忆。",
            "world_rules": ["记忆不能复制", "每次唤起都会留下代价"],
        },
    )
    version = bible.json()["aggregate_version"]
    series = _create(
        client,
        version=version,
        step=3,
        entity_type="series",
        entity_id="series-main",
        entity={
            "project_ref": project_ref,
            "title": "雨城纪事",
            "summary": "主人公回到故乡寻找失踪的姐姐。",
            "creative_intent": "悬念服务于人物关系。",
        },
    )
    version = series.json()["aggregate_version"]
    series_ref = _ref("series", "series-main", "series-main-v1")
    bible_ref = _ref("story_bible", "bible-main", "bible-main-v1")
    arc = _create(
        client,
        version=version,
        step=4,
        entity_type="arc",
        entity_id="arc-main",
        entity={
            "series_ref": series_ref,
            "story_bible_ref": bible_ref,
            "sequence": 1,
            "title": "归城篇",
            "summary": "从拒绝面对到主动追索。",
            "creative_intent": "每集都让关系发生一次不可逆变化。",
        },
    )
    version = arc.json()["aggregate_version"]
    asset = _create(
        client,
        version=version,
        step=5,
        entity_type="reference_asset",
        entity_id="asset-hero",
        entity={
            "project_ref": project_ref,
            "asset_kind": "human",
            "label": "林澈",
            "identity": "短发，左眉浅疤，克制而警觉。",
            "confidence": 0.42,
            "approval_state": "pending_human",
            "human_confirmed": False,
        },
    )
    version = asset.json()["aggregate_version"]
    approved_asset = _post(
        client,
        version=version,
        key="approve-asset-hero",
        body={
            "action": "authoring.revise",
            "target_ref": _ref("reference_asset", "asset-hero", "asset-hero-v1"),
            "new_version_id": "asset-hero-v2",
            "created_at": "2026-07-16T00:00:06+00:00",
            "changes": {
                "entity_type": "reference_asset",
                "approval_state": "approved",
                "human_confirmed": True,
            },
        },
    )
    version = approved_asset.json()["aggregate_version"]
    reference_set = _create(
        client,
        version=version,
        step=7,
        entity_type="reference_set",
        entity_id="refset-main",
        entity={
            "project_ref": project_ref,
            "title": "主角基准",
            "summary": "前两集共同使用。",
            "scope_kind": "project",
            "scope_refs": [project_ref],
            "asset_refs": [_ref("reference_asset", "asset-hero", "asset-hero-v2")],
            "approval_state": "approved",
            "human_confirmed": True,
        },
    )
    version = reference_set.json()["aggregate_version"]
    reference_set_ref = _ref("reference_set", "refset-main", "refset-main-v1")
    arc_ref = _ref("arc", "arc-main", "arc-main-v1")
    step = 8
    for episode_index in range(1, 3):
        episode_id = f"episode-{episode_index}"
        response = _create(
            client,
            version=version,
            step=step,
            entity_type="episode",
            entity_id=episode_id,
            entity={
                "series_ref": series_ref,
                "arc_ref": arc_ref,
                "sequence": episode_index,
                "title": f"第{episode_index}集",
                "summary": "人物作出新的选择。",
                "creative_intent": "保留一个开放问题。",
                "reference_set_ref": reference_set_ref,
            },
        )
        version = response.json()["aggregate_version"]
        step += 1
        episode_ref = _ref("episode", episode_id, f"{episode_id}-v1")
        for scene_index in range(1, 3):
            scene_id = f"{episode_id}-scene-{scene_index}"
            response = _create(
                client,
                version=version,
                step=step,
                entity_type="scene",
                entity_id=scene_id,
                entity={
                    "episode_ref": episode_ref,
                    "sequence": scene_index,
                    "title": f"场景{scene_index}",
                    "summary": "动作发生在明确空间中。",
                    "creative_intent": "保持空间方向清楚。",
                    "reference_set_ref": reference_set_ref,
                },
            )
            version = response.json()["aggregate_version"]
            step += 1
            scene_ref = _ref("scene", scene_id, f"{scene_id}-v1")
            for shot_index in range(1, 3):
                shot_id = f"{scene_id}-shot-{shot_index}"
                response = _create(
                    client,
                    version=version,
                    step=step,
                    entity_type="shot",
                    entity_id=shot_id,
                    entity={
                        "scene_ref": scene_ref,
                        "sequence": shot_index,
                        "title": f"镜头{shot_index}",
                        "summary": "角色完成一个可见动作。",
                        "creative_intent": "中景保留呼吸感。",
                        "duration_seconds": 4,
                        "reference_set_ref": reference_set_ref,
                    },
                )
                version = response.json()["aggregate_version"]
                step += 1

    workspace = client.get(f"/projects/{PROJECT_ID}/creator-workspace")
    assert workspace.status_code == 200, workspace.text
    model = workspace.json()
    assert model["counts"] == {
        "episodes": 2,
        "scenes": 4,
        "shots": 8,
        "reference_assets": 1,
        "reference_sets": 1,
    }
    assert model["provider_dispatch_count"] == 0
    stable_refs = {item["ref"]["entity_id"] for item in model["shots"]}

    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    recovered = restarted.get(f"/projects/{PROJECT_ID}/creator-workspace")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["aggregate_version"] == version
    assert {item["ref"]["entity_id"] for item in recovered.json()["shots"]} == stable_refs


def test_command_receipt_is_replayed_before_cas_and_concurrent_double_write_has_one_winner(
    tmp_path: Path,
) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    created = client.post(
        "/projects",
        json={
            "project_id": PROJECT_ID,
            "goal": "并发测试",
            "project_type": "studio_creator_authoring",
        },
    )
    assert created.status_code == 200, created.text
    project_ref = client.get(f"/projects/{PROJECT_ID}/creator-workspace").json()["project"]["ref"]
    body = {
        "expected_aggregate_version": 1,
        "action": "authoring.create",
        "entity_id": "series-main",
        "version_id": "series-main-v1",
        "created_at": "2026-07-16T00:00:01+00:00",
        "entity": {
            "entity_type": "series",
            "project_ref": project_ref,
            "title": "并发长篇",
        },
    }
    route = f"/projects/{PROJECT_ID}/episode-production-aggregate/commands"
    first = client.post(route, headers={"Idempotency-Key": "series-create"}, json=body)
    replay = client.post(route, headers={"Idempotency-Key": "series-create"}, json=body)
    conflict = client.post(
        route,
        headers={"Idempotency-Key": "series-create"},
        json={**body, "entity_id": "series-other"},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["aggregate_version"] == first.json()["aggregate_version"] == 2
    assert conflict.status_code == 409

    candidates = [
        {
            "expected_aggregate_version": 2,
            "action": "authoring.create",
            "entity_id": f"bible-{index}",
            "version_id": f"bible-{index}-v1",
            "created_at": "2026-07-16T00:00:02+00:00",
            "entity": {
                "entity_type": "story_bible",
                "project_ref": project_ref,
                "title": f"设定 {index}",
            },
        }
        for index in (1, 2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda item: client.post(
                    route,
                    headers={"Idempotency-Key": f"concurrent-{item['entity_id']}"},
                    json=item,
                ),
                candidates,
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert client.get(f"/projects/{PROJECT_ID}/creator-workspace").json()["aggregate_version"] == 3


def test_http_shot_preview_revision_diff_and_restore_are_exact_and_immutable(tmp_path: Path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    client.post(
        "/projects",
        json={"project_id": PROJECT_ID, "goal": "版本测试", "project_type": "studio_creator_authoring"},
    )
    project_ref = client.get(f"/projects/{PROJECT_ID}/creator-workspace").json()["project"]["ref"]
    version = 1
    series = _create(
        client,
        version=version,
        step=1,
        entity_type="series",
        entity_id="series-versioned",
        entity={"project_ref": project_ref, "title": "版本长篇"},
    )
    version = series.json()["aggregate_version"]
    episode = _create(
        client,
        version=version,
        step=2,
        entity_type="episode",
        entity_id="episode-versioned",
        entity={
            "series_ref": _ref("series", "series-versioned", "series-versioned-v1"),
            "sequence": 1,
            "title": "第一集",
        },
    )
    version = episode.json()["aggregate_version"]
    scene = _create(
        client,
        version=version,
        step=3,
        entity_type="scene",
        entity_id="scene-versioned",
        entity={
            "episode_ref": _ref("episode", "episode-versioned", "episode-versioned-v1"),
            "sequence": 1,
            "title": "雨夜街口",
        },
    )
    version = scene.json()["aggregate_version"]
    shot = _create(
        client,
        version=version,
        step=4,
        entity_type="shot",
        entity_id="shot-versioned",
        entity={
            "scene_ref": _ref("scene", "scene-versioned", "scene-versioned-v1"),
            "sequence": 1,
            "title": "回望",
            "creative_intent": "克制",
            "duration_seconds": 4,
        },
    )
    version = shot.json()["aggregate_version"]
    v1 = _ref("shot", "shot-versioned", "shot-versioned-v1")

    preview = client.post(
        f"/projects/{PROJECT_ID}/episode-production-aggregate/shot-impact-preview",
        json={
            "expected_aggregate_version": version,
            "shot_ref": v1,
            "changes": {"creative_intent": "让回望成为是否离开的决定", "duration_seconds": 6},
        },
    )
    assert preview.status_code == 200, preview.text
    impact = preview.json()
    assert impact["direct_affected_refs"] == [v1]
    revise = _post(
        client,
        version=version,
        key="revise-shot-versioned",
        body={
            "action": "shot.revise_intent",
            "shot_ref": v1,
            "new_version_id": "shot-versioned-v2",
            "created_at": "2026-07-16T00:00:05+00:00",
            "changes": impact["proposed_changes"],
            "preview_digest": impact["preview_digest"],
            "confirmed_direct_refs": impact["direct_affected_refs"],
            "confirmed_transitive_refs": impact["transitive_affected_refs"],
            "confirmed_protected_refs": impact["protected_refs"],
        },
    )
    version = revise.json()["aggregate_version"]
    v2 = _ref("shot", "shot-versioned", "shot-versioned-v2")
    diff = client.post(
        f"/projects/{PROJECT_ID}/episode-production-aggregate/shot-version-diff",
        json={"left_ref": v1, "right_ref": v2},
    )
    assert diff.status_code == 200, diff.text
    assert set(diff.json()["changes"]) >= {"creative_intent", "duration_seconds"}

    restore_preview = client.post(
        f"/projects/{PROJECT_ID}/episode-production-aggregate/shot-restore-preview",
        json={"expected_aggregate_version": version, "historical_ref": v1, "current_ref": v2},
    )
    assert restore_preview.status_code == 200, restore_preview.text
    restore_impact = restore_preview.json()
    restored = _post(
        client,
        version=version,
        key="restore-shot-versioned",
        body={
            "action": "shot.restore",
            "historical_ref": v1,
            "current_ref": v2,
            "new_version_id": "shot-versioned-v3",
            "created_at": "2026-07-16T00:00:06+00:00",
            "preview_digest": restore_impact["preview_digest"],
            "confirmed_direct_refs": restore_impact["direct_affected_refs"],
            "confirmed_transitive_refs": restore_impact["transitive_affected_refs"],
            "confirmed_protected_refs": restore_impact["protected_refs"],
        },
    )
    assert restored.status_code == 200, restored.text
    versions = client.get(f"/projects/{PROJECT_ID}/creator-workspace").json()["shots"][0]["versions"]
    assert [item["revision"] for item in versions] == [1, 2, 3]
    assert versions[-1]["parent_version_id"] == "shot-versioned-v2"
