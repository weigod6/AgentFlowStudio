from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from apps.api.runtime_service import create_runtime_app


def _ref(entity_type: str, entity_id: str, version_id: str) -> dict[str, str]:
    return {"entity_type": entity_type, "entity_id": entity_id, "version_id": version_id}


def _project(client: TestClient, project_id: str) -> dict[str, object]:
    created = client.post(
        "/projects",
        json={"project_id": project_id, "goal": "恢复测试", "project_type": "studio_creator_authoring"},
    )
    assert created.status_code == 200, created.text
    return client.get(f"/projects/{project_id}/creator-workspace").json()["project"]["ref"]


def test_creator_pending_command_round_trips_through_server_state(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "creator-state-roundtrip"
    project_ref = _project(client, project_id)
    pending = {
        "schema_version": "afs_creator_pending_command.v0.1",
        "idempotency_key": "creator-project-edit-1",
        "status": "pending",
        "command": {
            "action": "authoring.revise",
            "expected_aggregate_version": 1,
            "target_ref": project_ref,
            "new_version_id": "creator-state-roundtrip-v2",
            "created_at": "2026-07-16T00:00:01+00:00",
            "changes": {"entity_type": "project", "title": "恢复后的项目"},
        },
    }
    response = client.put(
        f"/projects/{project_id}/studio-state",
        json={
            "state": {
                "creator_authoring": {
                    "mode": "canvas",
                    "selected_episode": "",
                    "selected_shot": "",
                    "selected_section": f"project:{project_id}",
                    "mobile_inspector_open": False,
                    "technical_open": False,
                    "pending_command": pending,
                    "pending_failure": "",
                }
            }
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()["state"]["creator_authoring"]
    assert saved["mode"] == "canvas"
    assert saved["pending_command"] == pending

    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    recovered = restarted.get(f"/projects/{project_id}/studio-state")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["state"]["creator_authoring"]["pending_command"] == pending


def test_pending_command_accepts_canonical_episode_ref_union_and_rejects_aliases(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "creator-state-canonical-union"
    _project(client, project_id)
    route = f"/projects/{project_id}/studio-state"
    canonical_types = (
        "asset_candidate",
        "selected_version",
        "review_decision",
        "agent_proposal",
        "delivery_version",
    )

    for index, entity_type in enumerate(canonical_types, start=1):
        pending = {
            "schema_version": "afs_creator_pending_command.v0.1",
            "idempotency_key": f"creator-canonical-{index}",
            "status": "pending",
            "command": {
                "action": "shot.revise_intent",
                "expected_aggregate_version": 2,
                "shot_ref": _ref("shot", "shot-001", "shot-001-v1"),
                "new_version_id": f"shot-001-canonical-{index}",
                "created_at": "2026-07-16T00:00:01+00:00",
                "changes": {"title": "恢复命令"},
                "preview_digest": "a" * 64,
                "confirmed_direct_refs": [_ref("shot", "shot-001", "shot-001-v1")],
                "confirmed_transitive_refs": [
                    _ref(entity_type, f"{entity_type}-001", f"{entity_type}-001-v1")
                ],
                "confirmed_protected_refs": [],
            },
        }
        response = _save_pending(client, project_id, pending)
        assert response.json()["state"]["creator_authoring"]["pending_command"] == pending
        restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
        recovered = restarted.get(route)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["state"]["creator_authoring"]["pending_command"] == pending

    for alias in ("candidate", "selection", "review", "proposal", "delivery"):
        rejected = client.put(
            route,
            json={
                "expected_version": _studio_state_version(client, project_id),
                "state": {
                    "creator_authoring": {
                        "pending_command": {
                            "schema_version": "afs_creator_pending_command.v0.1",
                            "idempotency_key": f"creator-alias-{alias}",
                            "status": "pending",
                            "command": {
                                "action": "shot.restore",
                                "expected_aggregate_version": 2,
                                "historical_ref": _ref("shot", "shot-001", "shot-001-v1"),
                                "current_ref": _ref("shot", "shot-001", "shot-001-v2"),
                                "new_version_id": f"shot-001-alias-{alias}",
                                "created_at": "2026-07-16T00:00:02+00:00",
                                "preview_digest": "b" * 64,
                                "confirmed_direct_refs": [_ref("shot", "shot-001", "shot-001-v2")],
                                "confirmed_transitive_refs": [
                                    _ref(alias, f"{alias}-001", f"{alias}-001-v1")
                                ],
                                "confirmed_protected_refs": [],
                            },
                        }
                    }
                }
            },
        )
        assert rejected.status_code == 400


def test_creator_state_rejects_truncated_envelope_and_domain_facts(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "creator-state-reject"
    _project(client, project_id)
    route = f"/projects/{project_id}/studio-state"

    truncated = client.put(
        route,
        json={"state": {"creator_authoring": {"pending_command": {"status": "pending"}}}},
    )
    domain_fact = client.put(
        route,
        json={"state": {"creator_authoring": {"episodes": [{"title": "不应写入"}]}}},
    )
    assert truncated.status_code == 400
    assert domain_fact.status_code == 400


def test_studio_state_cas_is_atomic_and_corrupt_state_cannot_be_overwritten(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "creator-state-cas"
    _project(client, project_id)
    route = f"/projects/{project_id}/studio-state"
    creator_ui = {
        "creator_authoring": {
            "mode": "storyboard",
            "selected_episode": "",
            "selected_shot": "",
            "selected_section": f"project:{project_id}",
            "mobile_inspector_open": False,
            "technical_open": False,
            "pending_command": None,
            "pending_failure": "",
        }
    }
    first = client.put(route, json={"expected_version": "", "state": creator_ui})
    assert first.status_code == 200, first.text
    version = first.json()["state_version"]

    stale = client.put(route, json={"expected_version": "", "state": creator_ui})
    current = client.put(route, json={"expected_version": version, "state": creator_ui})
    assert stale.status_code == 409
    assert current.status_code == 200

    state_path = tmp_path / "projects" / project_id / "studio_state.json"
    state_path.write_text('{"state_version":', encoding="utf-8")
    corrupt = client.put(
        route,
        json={"expected_version": current.json()["state_version"], "state": creator_ui},
    )
    assert corrupt.status_code == 409
    assert state_path.read_text(encoding="utf-8") == '{"state_version":'


def test_studio_state_same_version_concurrent_writers_have_one_winner(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "creator-state-double-write"
    _project(client, project_id)
    route = f"/projects/{project_id}/studio-state"
    first = client.put(route, json={"state": {"creator_authoring": {"selected_section": f"project:{project_id}"}}})
    assert first.status_code == 200, first.text
    version = first.json()["state_version"]
    payloads = [
        {"expected_version": version, "state": {"creator_authoring": {"selected_section": f"project:{project_id}", "mode": mode}}}
        for mode in ("storyboard", "canvas")
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda payload: client.put(route, json=payload), payloads))
    assert sorted(response.status_code for response in responses) == [200, 409]


def test_semantically_truncated_pending_envelope_cannot_be_silently_overwritten(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "creator-state-semantic-corrupt"
    _project(client, project_id)
    state_path = tmp_path / "projects" / project_id / "studio_state.json"
    state_path.write_text(
        json.dumps(
            {
                "state_version": "studio_state:corrupt-v1",
                "saved_at": "2026-07-16T00:00:01+00:00",
                "state": {
                    "creator_authoring": {
                        "schema_version": "afs_creator_authoring_ui.v0.1",
                        "selected_section": f"project:{project_id}",
                        "pending_command": {
                            "schema_version": "afs_creator_pending_command.v0.1",
                            "idempotency_key": "truncated-1",
                            "status": "pending",
                        },
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    response = client.put(
        f"/projects/{project_id}/studio-state",
        json={
            "expected_version": "studio_state:corrupt-v1",
            "state": {"creator_authoring": {"selected_section": f"project:{project_id}"}},
        },
    )
    assert response.status_code == 409
    assert "truncated-1" in state_path.read_text(encoding="utf-8")


def test_pending_revise_and_restore_save_before_command_and_replay_after_restart(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "creator-state-route-recovery"
    project_ref = _project(client, project_id)
    version = 1
    series = _command(
        client,
        project_id,
        version=version,
        key="create-series",
        body={
            "action": "authoring.create",
            "entity_id": "series-main",
            "version_id": "series-main-v1",
            "created_at": "2026-07-16T00:00:01+00:00",
            "entity": {"entity_type": "series", "project_ref": project_ref, "title": "长篇"},
        },
    )
    version = series.json()["aggregate_version"]
    episode = _command(
        client,
        project_id,
        version=version,
        key="create-episode",
        body={
            "action": "authoring.create",
            "entity_id": "episode-main",
            "version_id": "episode-main-v1",
            "created_at": "2026-07-16T00:00:02+00:00",
            "entity": {
                "entity_type": "episode",
                "series_ref": _ref("series", "series-main", "series-main-v1"),
                "sequence": 1,
                "title": "第一集",
            },
        },
    )
    version = episode.json()["aggregate_version"]
    scene = _command(
        client,
        project_id,
        version=version,
        key="create-scene",
        body={
            "action": "authoring.create",
            "entity_id": "scene-main",
            "version_id": "scene-main-v1",
            "created_at": "2026-07-16T00:00:03+00:00",
            "entity": {
                "entity_type": "scene",
                "episode_ref": _ref("episode", "episode-main", "episode-main-v1"),
                "sequence": 1,
                "title": "雨夜",
            },
        },
    )
    version = scene.json()["aggregate_version"]
    shot = _command(
        client,
        project_id,
        version=version,
        key="create-shot",
        body={
            "action": "authoring.create",
            "entity_id": "shot-main",
            "version_id": "shot-main-v1",
            "created_at": "2026-07-16T00:00:04+00:00",
            "entity": {
                "entity_type": "shot",
                "scene_ref": _ref("scene", "scene-main", "scene-main-v1"),
                "sequence": 1,
                "title": "回望",
                "duration_seconds": 4,
            },
        },
    )
    version = shot.json()["aggregate_version"]
    v1 = _ref("shot", "shot-main", "shot-main-v1")
    preview = client.post(
        f"/projects/{project_id}/episode-production-aggregate/shot-impact-preview",
        json={
            "expected_aggregate_version": version,
            "shot_ref": v1,
            "changes": {"title": "雨中回望"},
        },
    )
    assert preview.status_code == 200, preview.text
    revise_pending = {
        "schema_version": "afs_creator_pending_command.v0.1",
        "idempotency_key": "pending-revise-shot",
        "status": "pending",
        "command": {
            "action": "shot.revise_intent",
            "expected_aggregate_version": version,
            "shot_ref": v1,
            "new_version_id": "shot-main-v2",
            "created_at": "2026-07-16T00:00:05+00:00",
            "changes": preview.json()["proposed_changes"],
            "preview_digest": preview.json()["preview_digest"],
            "confirmed_direct_refs": preview.json()["direct_affected_refs"],
            "confirmed_transitive_refs": preview.json()["transitive_affected_refs"],
            "confirmed_protected_refs": preview.json()["protected_refs"],
        },
    }
    _save_pending(client, project_id, revise_pending)
    revised = _command(
        client,
        project_id,
        version=version,
        key=revise_pending["idempotency_key"],
        body=revise_pending["command"],
    )
    version = revised.json()["aggregate_version"]
    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    recovered_pending = restarted.get(
        f"/projects/{project_id}/studio-state"
    ).json()["state"]["creator_authoring"]["pending_command"]
    replay = restarted.post(
        f"/projects/{project_id}/episode-production-aggregate/commands",
        headers={"Idempotency-Key": recovered_pending["idempotency_key"]},
        json=recovered_pending["command"],
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["aggregate_version"] == version

    v2 = _ref("shot", "shot-main", "shot-main-v2")
    restore_preview = restarted.post(
        f"/projects/{project_id}/episode-production-aggregate/shot-restore-preview",
        json={"expected_aggregate_version": version, "historical_ref": v1, "current_ref": v2},
    )
    assert restore_preview.status_code == 200, restore_preview.text
    restore_pending = {
        "schema_version": "afs_creator_pending_command.v0.1",
        "idempotency_key": "pending-restore-shot",
        "status": "pending",
        "command": {
            "action": "shot.restore",
            "expected_aggregate_version": version,
            "historical_ref": v1,
            "current_ref": v2,
            "new_version_id": "shot-main-v3",
            "created_at": "2026-07-16T00:00:06+00:00",
            "preview_digest": restore_preview.json()["preview_digest"],
            "confirmed_direct_refs": restore_preview.json()["direct_affected_refs"],
            "confirmed_transitive_refs": restore_preview.json()["transitive_affected_refs"],
            "confirmed_protected_refs": restore_preview.json()["protected_refs"],
        },
    }
    _save_pending(restarted, project_id, restore_pending)
    restored = _command(
        restarted,
        project_id,
        version=version,
        key=restore_pending["idempotency_key"],
        body=restore_pending["command"],
    )
    restarted_again = TestClient(create_runtime_app(runtime_root=tmp_path))
    recovered_restore = restarted_again.get(
        f"/projects/{project_id}/studio-state"
    ).json()["state"]["creator_authoring"]["pending_command"]
    restore_replay = restarted_again.post(
        f"/projects/{project_id}/episode-production-aggregate/commands",
        headers={"Idempotency-Key": recovered_restore["idempotency_key"]},
        json=recovered_restore["command"],
    )
    assert restore_replay.status_code == 200, restore_replay.text
    assert restore_replay.json()["replayed"] is True
    workspace = restarted_again.get(f"/projects/{project_id}/creator-workspace")
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["aggregate_version"] == restored.json()["aggregate_version"]
    assert [item["revision"] for item in workspace.json()["shots"][0]["versions"]] == [1, 2, 3]


def _command(
    client: TestClient,
    project_id: str,
    *,
    version: int,
    key: str,
    body: dict[str, object],
):
    response = client.post(
        f"/projects/{project_id}/episode-production-aggregate/commands",
        headers={"Idempotency-Key": key},
        json={"expected_aggregate_version": version, **body},
    )
    assert response.status_code == 200, response.text
    return response


def _studio_state_version(client: TestClient, project_id: str) -> str:
    response = client.get(f"/projects/{project_id}/studio-state")
    assert response.status_code == 200, response.text
    return response.json()["state_version"]


def _save_pending(client: TestClient, project_id: str, pending: dict[str, object]):
    response = client.put(
        f"/projects/{project_id}/studio-state",
        json={
            "expected_version": _studio_state_version(client, project_id),
            "state": {"creator_authoring": {"pending_command": pending}},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"]["creator_authoring"]["pending_command"] == pending
    return response
