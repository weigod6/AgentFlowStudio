from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from apps.api.runtime_episode_domain_contract import (
    AgentProposal,
    ContinuityStateVersion,
    ProductionProjectAggregate,
)
from apps.api.runtime_service import create_runtime_app
from tests.test_api_runtime_episode_creator_workflow_service import (
    build_episode as workflow_episode,
    common,
)


PROJECT_ID = "project-1"
WORKSPACE_ROUTE = (
    f"/projects/{PROJECT_ID}/episodes/episode-1/versions/episode-1.v1/workspace"
)
COMMAND_ROUTE = f"/projects/{PROJECT_ID}/episode-production-aggregate/commands"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _ref(item: Any) -> dict[str, str]:
    return item.as_ref().model_dump(mode="json")


def _representative_episode() -> ProductionProjectAggregate:
    base = workflow_episode(shot_count=15)
    continuity = ContinuityStateVersion(
        **common(base.scope, "character-rainlight"),
        subject_type="character",
        subject_id="rainlight-lead",
        identity_baseline=("creator-approved-character-baseline",),
        prohibited_changes=("identity-direction",),
    )
    shots = []
    for shot in base.shots:
        update: dict[str, object] = {
            "lifecycle_state": "approved",
            "review_state": "approved",
        }
        if shot.sequence in {6, 7}:
            update.update(lifecycle_state="candidate", review_state="needs_review")
        if 8 <= shot.sequence <= 15:
            update["continuity_refs"] = (continuity.as_ref(),)
        shots.append(shot.model_copy(update=update))
    shot_by_sequence = {shot.sequence: shot for shot in shots}
    candidate = base.asset_candidates[0].model_copy(
        update={
            "target_ref": shot_by_sequence[11].as_ref(),
            "lifecycle_state": "approved",
            "review_state": "approved",
        }
    )
    impact_refs = tuple(shot_by_sequence[number].as_ref() for number in range(8, 16))
    proposal = AgentProposal(
        **common(base.scope, "proposal-shot-11-continuity"),
        target_ref=continuity.as_ref(),
        action="inspect-continuity-version-impact",
        decision_state="pending",
        impact_refs=impact_refs,
    )
    return ProductionProjectAggregate.model_validate(
        {
            **base.model_dump(mode="python"),
            "shots": tuple(shots),
            "continuity_states": (continuity,),
            "asset_candidates": (candidate,),
            "agent_proposals": (proposal,),
        }
    )


def _owner_scoped(aggregate: ProductionProjectAggregate, owner_id: str) -> dict[str, Any]:
    payload = aggregate.model_dump(mode="json")
    scope = {"org_id": owner_id, "project_id": PROJECT_ID, "actor_id": owner_id}
    payload["scope"] = scope
    payload["aggregate_version"] = 1
    for collection in (
        "projects",
        "series",
        "episodes",
        "scenes",
        "shots",
        "continuity_states",
        "asset_candidates",
        "selections",
        "review_decisions",
        "agent_proposals",
        "deliveries",
    ):
        for record in payload[collection]:
            record["scope"] = scope
    return payload


def _register_owner(client: TestClient) -> tuple[dict[str, Any], dict[str, str]]:
    response = client.post(
        "/auth/register",
        json={
            "email": "episode-owner@example.com",
            "password": "strong-password-123",
            "display_name": "Episode owner",
            "invite_code": "episode-owner-invite",
        },
    )
    assert response.status_code == 200, response.text
    owner = response.json()
    return owner, {"Authorization": f"Bearer {owner['session_token']}"}


def _bootstrap_owner_episode(
    client: TestClient,
) -> tuple[dict[str, Any], dict[str, str], ProductionProjectAggregate, dict[str, Any]]:
    owner, headers = _register_owner(client)
    owner_id = owner["user"]["user_id"]
    created = client.post(
        "/projects",
        headers=headers,
        json={"project_id": PROJECT_ID, "goal": "Authenticated episode workspace"},
    )
    assert created.status_code == 200, created.text
    aggregate = _representative_episode()
    seeded = client.put(
        f"/projects/{PROJECT_ID}/episode-production-aggregate",
        headers={**headers, "Idempotency-Key": "workspace-bootstrap"},
        json={
            "expected_aggregate_version": 0,
            "aggregate": _owner_scoped(aggregate, owner_id),
        },
    )
    assert seeded.status_code == 200, seeded.text
    return owner, headers, aggregate, seeded.json()["aggregate"]


def _shot_review_payload(shot: dict[str, Any], *, expected_version: int) -> dict[str, Any]:
    return {
        "action": "shot.review",
        "expected_aggregate_version": expected_version,
        "shot_ref": {
            key: shot[key] for key in ("entity_type", "entity_id", "version_id")
        },
        "decision": "approve",
        "shot_version_id": f"{shot['entity_id']}.v2",
        "decision_entity_id": f"recovery-review-{shot['entity_id']}",
        "decision_version_id": f"recovery-review-{shot['entity_id']}.v1",
        "created_at": "2026-07-15T09:00:00+00:00",
    }


def _post_command(
    client: TestClient,
    headers: dict[str, str],
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        COMMAND_ROUTE,
        headers={**headers, "Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _latest(records: list[dict[str, Any]], entity_id: str) -> dict[str, Any]:
    return max(
        (item for item in records if item["entity_id"] == entity_id),
        key=lambda item: item["revision"],
    )


def test_authenticated_vertical_slice_and_three_eight_reload_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_INVITE_CODES", "episode-owner-invite")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    owner, headers = _register_owner(client)
    owner_id = owner["user"]["user_id"]
    assert client.post(
        "/projects",
        headers=headers,
        json={"project_id": PROJECT_ID, "goal": "Authenticated episode workspace"},
    ).status_code == 200
    aggregate = _representative_episode()
    seeded = client.put(
        f"/projects/{PROJECT_ID}/episode-production-aggregate",
        headers={**headers, "Idempotency-Key": "workspace-bootstrap"},
        json={
            "expected_aggregate_version": 0,
            "aggregate": _owner_scoped(aggregate, owner_id),
        },
    )
    assert seeded.status_code == 200, seeded.text

    initial = client.get(WORKSPACE_ROUTE, headers=headers)
    assert initial.status_code == 200, initial.text
    initial_workspace = initial.json()["workspace"]
    assert initial_workspace["next_action"]["action"] == "review_shot"
    assert initial_workspace["next_action"]["subject_ref"]["entity_id"] == "shot-6"
    shots = seeded.json()["aggregate"]["shots"]
    shot6 = _latest(shots, "shot-6")
    shot7 = _latest(shots, "shot-7")
    shot8_before = [item for item in shots if item["entity_id"] == "shot-8"]

    after_shot6 = _post_command(
        client,
        headers,
        "workspace-review-shot-6",
        {
            "action": "shot.review",
            "expected_aggregate_version": 1,
            "shot_ref": {key: shot6[key] for key in ("entity_type", "entity_id", "version_id")},
            "decision": "approve",
            "shot_version_id": "shot-6.v2",
            "decision_entity_id": "workspace-review-shot-6",
            "decision_version_id": "workspace-review-shot-6.v1",
            "created_at": "2026-07-15T08:01:00+00:00",
        },
    )
    target_scene = next(
        item for item in after_shot6["aggregate"]["scenes"] if item["entity_id"] == "scene-b"
    )
    after_reassign = _post_command(
        client,
        headers,
        "workspace-reassign-shot-7",
        {
            "action": "shot.reassign_scene",
            "expected_aggregate_version": 2,
            "shot_ref": {key: shot7[key] for key in ("entity_type", "entity_id", "version_id")},
            "scene_ref": {key: target_scene[key] for key in ("entity_type", "entity_id", "version_id")},
            "new_version_id": "shot-7.v2",
            "created_at": "2026-07-15T08:02:00+00:00",
        },
    )
    assert [
        item for item in after_reassign["aggregate"]["shots"] if item["entity_id"] == "shot-8"
    ] == shot8_before
    shot7_v2 = _latest(after_reassign["aggregate"]["shots"], "shot-7")
    after_shot7 = _post_command(
        client,
        headers,
        "workspace-review-shot-7",
        {
            "action": "shot.review",
            "expected_aggregate_version": 3,
            "shot_ref": {key: shot7_v2[key] for key in ("entity_type", "entity_id", "version_id")},
            "decision": "approve",
            "shot_version_id": "shot-7.v3",
            "decision_entity_id": "workspace-review-shot-7",
            "decision_version_id": "workspace-review-shot-7.v1",
            "created_at": "2026-07-15T08:03:00+00:00",
        },
    )

    legacy_state = {
        "meta": {"projectName": "Legacy Studio state", "canvasName": "画布 1", "seq": 2},
        "episode_workspace": {
            "episode_ref": _ref(aggregate.episodes[0]),
            "active_shot_ref": {
                "entity_type": "shot",
                "entity_id": "shot-7",
                "version_id": "shot-7.v3",
            },
            "mode": "storyboard",
            "focused_control": "shot-shot-7",
            "inspector_section": "overview",
            "scroll_top": 360,
            "pending_idempotency_key": "",
        },
    }
    checkpoint_three = client.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={"state": legacy_state},
    )
    assert checkpoint_three.status_code == 200, checkpoint_three.text
    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    restored_three = restarted.get(f"/projects/{PROJECT_ID}/studio-state", headers=headers)
    assert restored_three.status_code == 200
    restored_state = restored_three.json()["state"]
    assert restored_state["meta"]["projectName"] == "Legacy Studio state"
    assert restored_state["episode_workspace"]["active_shot_ref"]["version_id"] == "shot-7.v3"
    assert restored_state["episode_workspace"]["scroll_top"] == 360

    projected = restarted.get(WORKSPACE_ROUTE, headers=headers)
    assert projected.status_code == 200, projected.text
    workspace = projected.json()["workspace"]
    assert workspace["next_action"]["action"] == "adopt_candidate"
    assert workspace["next_action"]["subject_ref"]["entity_id"] == "shot-11"
    shot11 = next(item for item in workspace["shots"] if item["sequence"] == 11)
    assert shot11["agent_proposal"]["declared_impact_count"] == 8
    assert len(shot11["agent_proposal"]["declared_impact_refs"]) == 8
    candidate = shot11["candidates"][0]

    selected = _post_command(
        restarted,
        headers,
        "workspace-select-shot-11",
        {
            "action": "candidate.select",
            "expected_aggregate_version": 4,
            "target_shot_ref": shot11["ref"],
            "candidate_ref": candidate["ref"],
            "purpose": "storyboard",
            "selection_entity_id": "workspace-selection-shot-11",
            "selection_version_id": "workspace-selection-shot-11.v1",
            "created_at": "2026-07-15T08:04:00+00:00",
        },
    )
    selection = _latest(selected["aggregate"]["selections"], "workspace-selection-shot-11")
    after_selection_projection = restarted.get(WORKSPACE_ROUTE, headers=headers).json()["workspace"]
    assert after_selection_projection["next_action"]["action"] == "review_selection"
    assert after_selection_projection["next_action"]["subject_ref"]["version_id"] == selection["version_id"]
    approved = _post_command(
        restarted,
        headers,
        "workspace-approve-selection",
        {
            "action": "selection.review",
            "expected_aggregate_version": 5,
            "selection_ref": {key: selection[key] for key in ("entity_type", "entity_id", "version_id")},
            "decision": "approve",
            "selection_version_id": "workspace-selection-shot-11.v2",
            "decision_entity_id": "workspace-approve-selection",
            "decision_version_id": "workspace-approve-selection.v1",
            "created_at": "2026-07-15T08:05:00+00:00",
        },
    )
    approved_selection = _latest(approved["aggregate"]["selections"], "workspace-selection-shot-11")
    after_review_projection = restarted.get(WORKSPACE_ROUTE, headers=headers).json()["workspace"]
    assert after_review_projection["next_action"]["action"] == "lock_selection"
    assert after_review_projection["next_action"]["subject_ref"]["version_id"] == approved_selection["version_id"]
    locked = _post_command(
        restarted,
        headers,
        "workspace-lock-selection",
        {
            "action": "selection.lock",
            "expected_aggregate_version": 6,
            "selection_ref": {key: approved_selection[key] for key in ("entity_type", "entity_id", "version_id")},
            "selection_version_id": "workspace-selection-shot-11.v3",
            "decision_entity_id": "workspace-lock-selection",
            "decision_version_id": "workspace-lock-selection.v1",
            "created_at": "2026-07-15T08:06:00+00:00",
        },
    )
    locked_selection = _latest(locked["aggregate"]["selections"], "workspace-selection-shot-11")
    assert locked_selection["lifecycle_state"] == "locked"

    delivery_blocked = restarted.post(
        COMMAND_ROUTE,
        headers={**headers, "Idempotency-Key": "workspace-freeze-blocked"},
        json={
            "action": "delivery.freeze",
            "expected_aggregate_version": 7,
            "episode_ref": _ref(aggregate.episodes[0]),
            "selection_refs": [
                {key: locked_selection[key] for key in ("entity_type", "entity_id", "version_id")}
            ],
            "missing_inventory_count": 0,
            "preview_artifact_ref": None,
            "export_artifact_refs": [],
            "artifact_proofs": [],
            "delivery_entity_id": "workspace-delivery",
            "delivery_version_id": "workspace-delivery.v1",
            "created_at": "2026-07-15T08:07:00+00:00",
        },
    )
    assert delivery_blocked.status_code == 409
    assert delivery_blocked.json()["detail"]["error"] == "episode_delivery_not_ready"

    latest_state = restored_three.json()["state"]
    latest_state["episode_workspace"] = {
        **latest_state["episode_workspace"],
        "active_shot_ref": shot11["ref"],
        "mode": "delivery",
        "focused_control": "mode-delivery",
        "scroll_top": 880,
        "pending_idempotency_key": "",
    }
    checkpoint_eight = restarted.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={
            "state": latest_state,
            "expected_version": restored_three.json()["state_version"],
        },
    )
    assert checkpoint_eight.status_code == 200, checkpoint_eight.text
    final_restart = TestClient(create_runtime_app(runtime_root=tmp_path))
    restored_eight = final_restart.get(f"/projects/{PROJECT_ID}/studio-state", headers=headers)
    assert restored_eight.status_code == 200
    final_ui = restored_eight.json()["state"]["episode_workspace"]
    assert final_ui["active_shot_ref"] == shot11["ref"]
    assert final_ui["mode"] == "delivery"
    assert final_ui["focused_control"] == "mode-delivery"
    assert final_ui["scroll_top"] == 880
    final_workspace = final_restart.get(WORKSPACE_ROUTE, headers=headers).json()["workspace"]
    assert final_workspace["delivery"]["status"] == "blocked"
    assert final_workspace["delivery"]["playable_preview_available"] is False
    assert final_workspace["truth"]["playable_preview_available"] is False


def test_pending_command_replays_exact_identity_and_payload_after_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_INVITE_CODES", "episode-owner-invite")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    _, headers, aggregate, seeded = _bootstrap_owner_episode(client)
    shot6 = _latest(seeded["shots"], "shot-6")
    command = _shot_review_payload(shot6, expected_version=1)
    key = "restart-exact-review-shot-6"
    pending = {
        "schema_version": "afs_episode_workspace_ui.v0.1",
        "episode_ref": _ref(aggregate.episodes[0]),
        "active_shot_ref": command["shot_ref"],
        "mode": "storyboard",
        "focused_control": "command:shot.review.approve",
        "inspector_section": "overview",
        "scroll_top": 240,
        "pending_idempotency_key": key,
        "pending_command": {"idempotency_key": key, "payload": command},
    }
    saved = client.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={
            "state": {
                "meta": {"projectName": "Concurrent-safe legacy state", "seq": 4},
                "episode_workspace": pending,
            }
        },
    )
    assert saved.status_code == 200, saved.text

    first = client.post(
        COMMAND_ROUTE,
        headers={**headers, "Idempotency-Key": key},
        json=command,
    )
    assert first.status_code == 200, first.text
    assert first.json()["replayed"] is False
    assert first.json()["aggregate"]["aggregate_version"] == 2

    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    restored = restarted.get(
        f"/projects/{PROJECT_ID}/studio-state", headers=headers
    )
    assert restored.status_code == 200, restored.text
    envelope = restored.json()["state"]["episode_workspace"]["pending_command"]
    assert envelope["idempotency_key"] == key
    assert envelope["payload"] == {**command, "note": ""}
    replay = restarted.post(
        COMMAND_ROUTE,
        headers={**headers, "Idempotency-Key": envelope["idempotency_key"]},
        json=envelope["payload"],
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["aggregate"]["aggregate_version"] == 2

    state = restored.json()["state"]
    state["episode_workspace"] = {
        **state["episode_workspace"],
        "pending_idempotency_key": "",
        "pending_command": None,
    }
    cleared = restarted.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={
            "state": state,
            "expected_version": restored.json()["state_version"],
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["state"]["meta"]["projectName"] == "Concurrent-safe legacy state"
    assert cleared.json()["state"]["episode_workspace"]["pending_command"] is None


def test_pending_command_cas_merge_preserves_concurrent_legacy_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_INVITE_CODES", "episode-owner-invite")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    _, headers, aggregate, seeded = _bootstrap_owner_episode(client)
    shot6 = _latest(seeded["shots"], "shot-6")
    command = _shot_review_payload(shot6, expected_version=1)
    key = "cas-merge-review-shot-6"
    initial = client.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={"state": {"meta": {"projectName": "Initial", "seq": 1}}},
    )
    assert initial.status_code == 200, initial.text
    stale_version = initial.json()["state_version"]

    concurrent_state = initial.json()["state"]
    concurrent_state["meta"] = {
        **concurrent_state["meta"],
        "projectName": "Concurrent legacy edit",
        "seq": 9,
    }
    concurrent = client.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={"state": concurrent_state, "expected_version": stale_version},
    )
    assert concurrent.status_code == 200, concurrent.text

    pending_workspace = {
        "schema_version": "afs_episode_workspace_ui.v0.1",
        "episode_ref": _ref(aggregate.episodes[0]),
        "active_shot_ref": command["shot_ref"],
        "mode": "storyboard",
        "focused_control": "command:shot.review.approve",
        "inspector_section": "overview",
        "scroll_top": 120,
        "pending_idempotency_key": key,
        "pending_command": {"idempotency_key": key, "payload": command},
    }
    stale = client.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={
            "state": {
                **initial.json()["state"],
                "episode_workspace": pending_workspace,
            },
            "expected_version": stale_version,
        },
    )
    assert stale.status_code == 409

    refreshed = client.get(f"/projects/{PROJECT_ID}/studio-state", headers=headers)
    merged = {
        **refreshed.json()["state"],
        "episode_workspace": pending_workspace,
    }
    retried = client.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={
            "state": merged,
            "expected_version": refreshed.json()["state_version"],
        },
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["state"]["meta"]["projectName"] == "Concurrent legacy edit"
    assert retried.json()["state"]["meta"]["seq"] == 9
    stored_envelope = retried.json()["state"]["episode_workspace"]["pending_command"]
    assert stored_envelope["idempotency_key"] == key
    assert stored_envelope["payload"] == {**command, "note": ""}


def test_pending_command_rejects_malformed_or_sensitive_envelopes_without_echo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_INVITE_CODES", "episode-owner-invite")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    _, headers, aggregate, seeded = _bootstrap_owner_episode(client)
    shot6 = _latest(seeded["shots"], "shot-6")
    command = _shot_review_payload(shot6, expected_version=1)
    route = f"/projects/{PROJECT_ID}/studio-state"
    base = {
        "episode_ref": _ref(aggregate.episodes[0]),
        "mode": "storyboard",
    }
    bad_states = [
        {**base, "pending_idempotency_key": "key-only"},
        {
            **base,
            "pending_idempotency_key": "bad-envelope",
            "pending_command": {
                "idempotency_key": "bad-envelope",
                "payload": command,
                "unexpected": True,
            },
        },
        {
            **base,
            "pending_idempotency_key": "sensitive-command",
            "pending_command": {
                "idempotency_key": "sensitive-command",
                "payload": {
                    **command,
                    "decision_entity_id": "token-super-private-marker",
                },
            },
        },
    ]
    for episode_workspace in bad_states:
        response = client.put(
            route,
            headers=headers,
            json={"state": {"episode_workspace": episode_workspace}},
        )
        assert response.status_code == 400, response.text
        assert "super-private-marker" not in response.text


def test_stale_pending_command_can_be_cleared_after_authoritative_refresh(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_INVITE_CODES", "episode-owner-invite")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    _, headers, aggregate, seeded = _bootstrap_owner_episode(client)
    shot6 = _latest(seeded["shots"], "shot-6")
    stale_command = _shot_review_payload(shot6, expected_version=1)
    stale_key = "stale-pending-review-shot-6"
    saved = client.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={
            "state": {
                "meta": {"projectName": "Preserve after stale", "seq": 6},
                "episode_workspace": {
                    "episode_ref": _ref(aggregate.episodes[0]),
                    "active_shot_ref": stale_command["shot_ref"],
                    "mode": "storyboard",
                    "pending_idempotency_key": stale_key,
                    "pending_command": {
                        "idempotency_key": stale_key,
                        "payload": stale_command,
                    },
                },
            }
        },
    )
    assert saved.status_code == 200, saved.text
    _post_command(client, headers, "advance-before-recovery", stale_command)

    rejected = client.post(
        COMMAND_ROUTE,
        headers={**headers, "Idempotency-Key": stale_key},
        json=stale_command,
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error"] == "episode_aggregate_version_conflict"
    authority = client.get(WORKSPACE_ROUTE, headers=headers)
    assert authority.status_code == 200, authority.text
    assert authority.json()["aggregate"]["aggregate_version"] == 2

    restored = client.get(f"/projects/{PROJECT_ID}/studio-state", headers=headers)
    state = restored.json()["state"]
    state["episode_workspace"] = {
        **state["episode_workspace"],
        "pending_idempotency_key": "",
        "pending_command": None,
    }
    cleared = client.put(
        f"/projects/{PROJECT_ID}/studio-state",
        headers=headers,
        json={
            "state": state,
            "expected_version": restored.json()["state_version"],
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["state"]["meta"]["projectName"] == "Preserve after stale"
    assert cleared.json()["state"]["episode_workspace"]["pending_command"] is None
