from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from apps.api.runtime_episode_command_routes import ShotReviewCommand, _command_digest
from apps.api.runtime_episode_domain_contract import ProductionProjectAggregate, TenantScope
from apps.api.runtime_episode_domain_routes import LOCAL_ACTOR_ID, LOCAL_ORG_ID
from apps.api.runtime_service import create_runtime_app
from tests.test_api_runtime_episode_continuity_service import build_episode as continuity_episode
from tests.test_api_runtime_episode_creator_workflow_service import build_episode as workflow_episode
from tests.test_api_runtime_episode_review_delivery_service import (
    TIMES,
    _aggregate as review_episode,
    _artifact,
    _approved,
    _delivery_proofs,
    _locked,
    _selected_v2,
)


COMMAND_SUFFIX = "/commands"


def _localize(aggregate: ProductionProjectAggregate) -> ProductionProjectAggregate:
    payload = aggregate.model_dump(mode="json")
    scope = {
        "org_id": LOCAL_ORG_ID,
        "project_id": aggregate.scope.project_id,
        "actor_id": LOCAL_ACTOR_ID,
    }
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
    return ProductionProjectAggregate.model_validate(payload)


def _bootstrap(tmp_path: Path, aggregate: ProductionProjectAggregate) -> tuple[TestClient, str]:
    aggregate = _localize(aggregate)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = aggregate.scope.project_id
    created = client.post(
        "/projects",
        json={"project_id": project_id, "goal": "Episode command route test"},
    )
    assert created.status_code == 200, created.text
    route = f"/projects/{project_id}/episode-production-aggregate"
    seeded = client.put(
        route,
        headers={"Idempotency-Key": "bootstrap-v1"},
        json={
            "expected_aggregate_version": 0,
            "aggregate": aggregate.model_dump(mode="json"),
        },
    )
    assert seeded.status_code == 200, seeded.text
    return client, route + "/commands"


def _post(client: TestClient, route: str, key: str, body: dict[str, Any]):
    return client.post(route, headers={"Idempotency-Key": key}, json=body)


def _ref(item) -> dict[str, str]:
    return item.as_ref().model_dump(mode="json")


def _error(response) -> str:
    return str(response.json().get("detail", {}).get("error") or "")


def test_shot_command_is_durable_replay_safe_and_stale_new_key_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    aggregate = workflow_episode()
    client, route = _bootstrap(tmp_path, aggregate)
    shot6 = next(shot for shot in aggregate.shots if shot.entity_id == "shot-6")
    body = {
        "action": "shot.review",
        "expected_aggregate_version": 1,
        "shot_ref": _ref(shot6),
        "decision": "approve",
        "shot_version_id": "shot-6.v2",
        "decision_entity_id": "review-shot-6",
        "decision_version_id": "review-shot-6.v1",
        "created_at": "2026-07-15T08:02:00+00:00",
        "note": "Approved exact shot.",
    }

    first = _post(client, route, "review-shot-6-command", body)
    replay = _post(client, route, "review-shot-6-command", body)
    changed = _post(client, route, "review-shot-6-command", {**body, "note": "changed"})
    stale = _post(client, route, "new-stale-command", body)

    assert first.status_code == 200, first.text
    assert first.json()["aggregate_version"] == 2
    assert first.json()["command_id"] == "review-shot-6-command"
    assert first.json()["replayed"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["aggregate"] == first.json()["aggregate"]
    assert changed.status_code == 409
    assert _error(changed) == "episode_aggregate_idempotency_conflict"
    assert stale.status_code == 409
    assert _error(stale) == "episode_aggregate_version_conflict"

    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    persisted = restarted.get(route.removesuffix(COMMAND_SUFFIX))
    assert persisted.status_code == 200
    assert persisted.json()["aggregate_version"] == 2


def test_command_receipt_digest_binds_exact_org_project_actor_scope() -> None:
    command = ShotReviewCommand(
        action="shot.review",
        expected_aggregate_version=1,
        shot_ref={
            "entity_type": "shot",
            "entity_id": "shot-6",
            "version_id": "shot-6.v1",
        },
        decision="approve",
        shot_version_id="shot-6.v2",
        decision_entity_id="review-shot-6",
        decision_version_id="review-shot-6.v1",
        created_at="2026-07-15T08:02:00+00:00",
    )
    base = TenantScope(org_id="org-1", project_id="rainlight", actor_id="creator-1")
    assert _command_digest(base, command) != _command_digest(
        base.model_copy(update={"actor_id": "creator-2"}),
        command,
    )
    assert _command_digest(base, command) != _command_digest(
        base.model_copy(update={"org_id": "org-2"}),
        command,
    )
    assert _command_digest(base, command) != _command_digest(
        base.model_copy(update={"project_id": "other-project"}),
        command,
    )


def test_shot7_reassign_appends_exact_successor_and_preserves_shot8(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    aggregate = workflow_episode()
    client, route = _bootstrap(tmp_path, aggregate)
    shots = {shot.entity_id: shot for shot in aggregate.shots}
    target_scene = next(scene for scene in aggregate.scenes if scene.entity_id == "scene-b")
    shot8_before = _ref(shots["shot-8"])

    reassigned = _post(
        client,
        route,
        "reassign-shot-7",
        {
            "action": "shot.reassign_scene",
            "expected_aggregate_version": 1,
            "shot_ref": _ref(shots["shot-7"]),
            "scene_ref": _ref(target_scene),
            "new_version_id": "shot-7.v2",
            "created_at": "2026-07-15T08:01:00+00:00",
        },
    )

    assert reassigned.status_code == 200, reassigned.text
    payload = reassigned.json()["aggregate"]
    shot7_history = [shot for shot in payload["shots"] if shot["entity_id"] == "shot-7"]
    shot8_history = [shot for shot in payload["shots"] if shot["entity_id"] == "shot-8"]
    assert len(shot7_history) == 2
    assert shot7_history[-1]["parent_version_id"] == shots["shot-7"].version_id
    assert shot7_history[-1]["scene_ref"] == _ref(target_scene)
    assert shot7_history[-1]["review_state"] == "needs_review"
    assert len(shot8_history) == 1
    assert {
        key: shot8_history[0][key]
        for key in ("entity_type", "entity_id", "version_id")
        if key in shot8_history[0]
    } == {key: value for key, value in shot8_before.items() if key in shot8_history[0]}


def test_shot11_selection_is_blocked_until_shot6_and_shot7_are_approved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    aggregate = workflow_episode()
    client, route = _bootstrap(tmp_path, aggregate)
    shots = {shot.entity_id: shot for shot in aggregate.shots}
    candidate = aggregate.asset_candidates[0]
    select = {
        "action": "candidate.select",
        "expected_aggregate_version": 1,
        "target_shot_ref": _ref(shots["shot-11"]),
        "candidate_ref": _ref(candidate),
        "purpose": "storyboard",
        "selection_entity_id": "selection-shot-11",
        "selection_version_id": "selection-shot-11.v1",
        "created_at": "2026-07-15T08:04:00+00:00",
    }
    blocked = _post(client, route, "select-shot-11-blocked", select)
    assert blocked.status_code == 409
    assert _error(blocked) == "episode_command_state_conflict"

    version = 1
    latest_refs: dict[str, dict[str, str]] = {}
    for entity_id, time in (
        ("shot-6", "2026-07-15T08:02:00+00:00"),
        ("shot-7", "2026-07-15T08:03:00+00:00"),
    ):
        reviewed = _post(
            client,
            route,
            f"review-{entity_id}",
            {
                "action": "shot.review",
                "expected_aggregate_version": version,
                "shot_ref": _ref(shots[entity_id]),
                "decision": "approve",
                "shot_version_id": f"{entity_id}.v2",
                "decision_entity_id": f"review-{entity_id}",
                "decision_version_id": f"review-{entity_id}.v1",
                "created_at": time,
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        version += 1
        latest = [
            item
            for item in reviewed.json()["aggregate"]["shots"]
            if item["entity_id"] == entity_id
        ][-1]
        latest_refs[entity_id] = {
            "entity_type": "shot",
            "entity_id": entity_id,
            "version_id": latest["version_id"],
        }

    select["expected_aggregate_version"] = version
    selected = _post(client, route, "select-shot-11-ready", select)
    assert selected.status_code == 200, selected.text
    assert selected.json()["aggregate"]["selections"][-1]["target_ref"] == _ref(
        shots["shot-11"]
    )


def test_concurrent_new_commands_allow_only_one_exact_cas_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    aggregate = workflow_episode()
    client, route = _bootstrap(tmp_path, aggregate)
    shot6 = next(shot for shot in aggregate.shots if shot.entity_id == "shot-6")

    def submit(suffix: str):
        return _post(
            client,
            route,
            f"concurrent-{suffix}",
            {
                "action": "shot.review",
                "expected_aggregate_version": 1,
                "shot_ref": _ref(shot6),
                "decision": "approve",
                "shot_version_id": f"shot-6.v2-{suffix}",
                "decision_entity_id": f"review-shot-6-{suffix}",
                "decision_version_id": f"review-shot-6-{suffix}.v1",
                "created_at": "2026-07-15T08:02:00+00:00",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, ("a", "b")))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert _error(conflict) == "episode_aggregate_version_conflict"
    loaded = client.get(route.removesuffix(COMMAND_SUFFIX)).json()
    assert loaded["aggregate_version"] == 2
    assert len([shot for shot in loaded["aggregate"]["shots"] if shot["entity_id"] == "shot-6"]) == 2


def test_continuity_apply_and_undo_use_explicit_exact_membership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    aggregate = continuity_episode()
    client, route = _bootstrap(tmp_path, aggregate)
    old = aggregate.continuity_states[0]
    selected_shots = tuple(
        shot for shot in aggregate.shots if old.as_ref() in shot.continuity_refs
    )[:2]
    applied = _post(
        client,
        route,
        "continuity-apply",
        {
            "action": "continuity.apply",
            "expected_aggregate_version": 1,
            "old_continuity_ref": _ref(old),
            "new_version_id": "character-lin.v2",
            "proposal_entity_id": "proposal-lin-wardrobe",
            "planned_at": "2026-07-15T20:05:00+00:00",
            "applied_at": "2026-07-15T20:06:00+00:00",
            "identity_baseline": ["black-coat", "left-cheek-scar"],
            "selected_shot_refs": [_ref(shot) for shot in selected_shots],
        },
    )
    assert applied.status_code == 200, applied.text
    proposal = applied.json()["aggregate"]["agent_proposals"][-1]
    assert len(proposal["impact_refs"]) == 3
    assert len(proposal["applied_refs"]) == 2

    undone = _post(
        client,
        route,
        "continuity-undo",
        {
            "action": "continuity.undo",
            "expected_aggregate_version": 2,
            "proposal_ref": {
                "entity_type": "agent_proposal",
                "entity_id": proposal["entity_id"],
                "version_id": proposal["version_id"],
            },
            "created_at": "2026-07-15T20:09:00+00:00",
        },
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["aggregate"]["agent_proposals"][-1]["decision_state"] == "undone"


def test_review_lock_revision_restore_commands_preserve_append_only_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    aggregate = review_episode()
    client, route = _bootstrap(tmp_path, aggregate)
    candidate = aggregate.asset_candidates[1]
    shot = aggregate.shots[0]

    selected = _post(
        client,
        route,
        "select-v2",
        {
            "action": "candidate.select",
            "expected_aggregate_version": 1,
            "target_shot_ref": _ref(shot),
            "candidate_ref": _ref(candidate),
            "purpose": "storyboard",
            "selection_entity_id": "selection-shot-1",
            "selection_version_id": "selection-shot-1.v1",
            "created_at": TIMES[4],
        },
    )
    assert selected.status_code == 200, selected.text
    selection_ref = {
        "entity_type": "selected_version",
        "entity_id": "selection-shot-1",
        "version_id": "selection-shot-1.v1",
    }
    approved = _post(
        client,
        route,
        "approve-selection",
        {
            "action": "selection.review",
            "expected_aggregate_version": 2,
            "selection_ref": selection_ref,
            "decision": "approve",
            "selection_version_id": "selection-shot-1.v2",
            "decision_entity_id": "review-selection",
            "decision_version_id": "review-selection.v1",
            "created_at": TIMES[5],
        },
    )
    assert approved.status_code == 200, approved.text
    selection_ref["version_id"] = "selection-shot-1.v2"
    locked = _post(
        client,
        route,
        "lock-selection",
        {
            "action": "selection.lock",
            "expected_aggregate_version": 3,
            "selection_ref": selection_ref,
            "selection_version_id": "selection-shot-1.v3",
            "decision_entity_id": "lock-selection",
            "decision_version_id": "lock-selection.v1",
            "created_at": TIMES[6],
        },
    )
    assert locked.status_code == 200, locked.text
    selection_ref["version_id"] = "selection-shot-1.v3"
    revision = _post(
        client,
        route,
        "request-revision",
        {
            "action": "selection.request_revision",
            "expected_aggregate_version": 4,
            "selection_ref": selection_ref,
            "selection_version_id": "selection-shot-1.v4",
            "decision_entity_id": "revision-request",
            "decision_version_id": "revision-request.v1",
            "unlock_decision_entity_id": "unlock-for-revision",
            "unlock_decision_version_id": "unlock-for-revision.v1",
            "created_at": TIMES[7],
        },
    )
    assert revision.status_code == 200, revision.text
    selection_ref["version_id"] = "selection-shot-1.v4"
    restored = _post(
        client,
        route,
        "restore-v1",
        {
            "action": "selection.restore",
            "expected_aggregate_version": 5,
            "selection_ref": selection_ref,
            "historical_candidate_ref": _ref(aggregate.asset_candidates[0]),
            "selection_version_id": "selection-shot-1.v5",
            "created_at": TIMES[8],
        },
    )
    assert restored.status_code == 200, restored.text
    assert len(restored.json()["aggregate"]["selections"]) == 5
    assert restored.json()["aggregate"]["selections"][-1]["candidate_ref"] == _ref(
        aggregate.asset_candidates[0]
    )


def test_selection_unlock_is_typed_append_only_and_restart_recoverable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    aggregate = _locked(_approved(_selected_v2(review_episode())))
    client, route = _bootstrap(tmp_path, aggregate)
    locked_ref = _ref(aggregate.selections[-1])
    before = client.get(route.removesuffix(COMMAND_SUFFIX))
    assert before.status_code == 200
    private_marker = "Creator reopened /OPT/afs/private/secret.mov for review."

    unsafe = _post(
        client,
        route,
        "unlock-selection",
        {
            "action": "selection.unlock",
            "expected_aggregate_version": 1,
            "selection_ref": locked_ref,
            "selection_version_id": "selection-shot-1.v4",
            "decision_entity_id": "unlock-selection-v3",
            "decision_version_id": "unlock-selection-v3.v1",
            "created_at": TIMES[7],
            "note": private_marker,
        },
    )
    assert unsafe.status_code == 422
    assert _error(unsafe) == "episode_aggregate_unsafe_payload"
    assert private_marker not in unsafe.text

    unchanged = client.get(route.removesuffix(COMMAND_SUFFIX))
    assert unchanged.status_code == 200
    assert unchanged.json() == before.json()

    unlocked = _post(
        client,
        route,
        "unlock-selection",
        {
            "action": "selection.unlock",
            "expected_aggregate_version": 1,
            "selection_ref": locked_ref,
            "selection_version_id": "selection-shot-1.v4",
            "decision_entity_id": "unlock-selection-v3",
            "decision_version_id": "unlock-selection-v3.v1",
            "created_at": TIMES[7],
            "note": "Creator reopened this exact selection.",
        },
    )

    assert unlocked.status_code == 200, unlocked.text
    selections = unlocked.json()["aggregate"]["selections"]
    assert len(selections) == 4
    assert selections[-1]["parent_version_id"] == locked_ref["version_id"]
    assert selections[-1]["lifecycle_state"] == "approved"
    assert unlocked.json()["aggregate"]["review_decisions"][-1]["decision"] == "unlock"

    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    restored = restarted.get(route.removesuffix(COMMAND_SUFFIX))
    assert restored.status_code == 200
    assert restored.json()["aggregate_version"] == 2
    assert restored.json()["aggregate"]["selections"][-1]["version_id"] == "selection-shot-1.v4"


def test_delivery_missing_inventory_blocks_and_verified_ready_path_freezes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    aggregate = _locked(_approved(_selected_v2(review_episode())))
    client, route = _bootstrap(tmp_path, aggregate)
    preview = _artifact("preview-episode", "video")
    exports = (_artifact("export-episode", "video"),)
    proofs = _delivery_proofs(aggregate, preview, exports)
    body = {
        "action": "delivery.freeze",
        "expected_aggregate_version": 1,
        "episode_ref": _ref(aggregate.episodes[0]),
        "selection_refs": [_ref(aggregate.selections[-1])],
        "missing_inventory_count": 25,
        "preview_artifact_ref": preview.model_dump(mode="json"),
        "export_artifact_refs": [item.model_dump(mode="json") for item in exports],
        "artifact_proofs": [
            {
                "artifact_ref": proof.artifact_ref.model_dump(mode="json"),
                "verification_id": proof.verification_id,
                "available": proof.available,
                "playable": proof.playable,
            }
            for proof in proofs
        ],
        "delivery_entity_id": "delivery-episode-1",
        "delivery_version_id": "delivery-episode-1.v1",
        "created_at": TIMES[7],
    }
    blocked = _post(client, route, "freeze-blocked", body)
    assert blocked.status_code == 409
    assert _error(blocked) == "episode_delivery_not_ready"
    assert client.get(route.removesuffix(COMMAND_SUFFIX)).json()["aggregate"]["deliveries"] == []

    body["missing_inventory_count"] = 0
    frozen = _post(client, route, "freeze-ready", body)
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["aggregate"]["deliveries"][-1]["lifecycle_state"] == "locked"

    delivery_ref = {
        "entity_type": "delivery_version",
        "entity_id": "delivery-episode-1",
        "version_id": "delivery-episode-1.v1",
    }
    unlocked = _post(
        client,
        route,
        "unlock-delivery",
        {
            "action": "delivery.unlock",
            "expected_aggregate_version": 2,
            "delivery_ref": delivery_ref,
            "delivery_version_id": "delivery-episode-1.v2",
            "decision_entity_id": "unlock-delivery-v1",
            "decision_version_id": "unlock-delivery-v1.v1",
            "created_at": TIMES[8],
            "note": "Creator reopened delivery.",
        },
    )
    assert unlocked.status_code == 200, unlocked.text
    deliveries = unlocked.json()["aggregate"]["deliveries"]
    assert len(deliveries) == 2
    assert deliveries[-1]["parent_version_id"] == delivery_ref["version_id"]
    assert deliveries[-1]["lifecycle_state"] == "approved"

    private_marker = "C:/private/customer/secret.mov"
    stale = _post(
        client,
        route,
        "stale-delivery-unlock",
        {
            "action": "delivery.unlock",
            "expected_aggregate_version": 3,
            "delivery_ref": delivery_ref,
            "delivery_version_id": "delivery-episode-1.v3",
            "decision_entity_id": "unlock-delivery-stale",
            "decision_version_id": "unlock-delivery-stale.v1",
            "created_at": TIMES[9],
            "note": private_marker,
        },
    )
    assert stale.status_code == 409
    assert _error(stale) == "episode_command_version_conflict"
    assert private_marker not in stale.text


def test_command_auth_is_project_scoped_and_request_cannot_supply_scope_or_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_INVITE_CODES", "owner-invite,other-invite")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    def register(code: str, email: str) -> dict[str, Any]:
        response = client.post(
            "/auth/register",
            json={
                "email": email,
                "password": "strong-password-123",
                "display_name": email.split("@", 1)[0],
                "invite_code": code,
            },
        )
        assert response.status_code == 200
        return response.json()

    owner = register("owner-invite", "owner-command@example.com")
    other = register("other-invite", "other-command@example.com")
    owner_id = owner["user"]["user_id"]
    aggregate = review_episode().model_dump(mode="json")
    scope = {"org_id": owner_id, "project_id": "rainlight", "actor_id": owner_id}
    aggregate["scope"] = scope
    for collection in (
        "projects", "series", "episodes", "scenes", "shots", "continuity_states",
        "asset_candidates", "review_decisions",
    ):
        for record in aggregate[collection]:
            record["scope"] = scope
    assert client.post(
        "/projects",
        headers={"Authorization": f"Bearer {owner['session_token']}"},
        json={"project_id": "rainlight", "goal": "Authenticated episode"},
    ).status_code == 200
    base = "/projects/rainlight/episode-production-aggregate"
    assert client.put(
        base,
        headers={
            "Authorization": f"Bearer {owner['session_token']}",
            "Idempotency-Key": "auth-bootstrap",
        },
        json={"expected_aggregate_version": 0, "aggregate": aggregate},
    ).status_code == 200
    command = {
        "action": "candidate.select",
        "expected_aggregate_version": 1,
        "target_shot_ref": _ref(review_episode().shots[0]),
        "candidate_ref": _ref(review_episode().asset_candidates[1]),
        "purpose": "storyboard",
        "selection_entity_id": "selection-auth",
        "selection_version_id": "selection-auth.v1",
        "created_at": TIMES[4],
    }
    denied = client.post(
        base + "/commands",
        headers={
            "Authorization": f"Bearer {other['session_token']}",
            "Idempotency-Key": "foreign-command",
        },
        json=command,
    )
    assert denied.status_code == 403
    assert _error(denied) == "project_access_denied"

    injection = client.post(
        base + "/commands",
        headers={
            "Authorization": f"Bearer {owner['session_token']}",
            "Idempotency-Key": "scope-injection",
        },
        json={**command, "scope": scope, "payload_digest": hashlib.sha256(b"x").hexdigest()},
    )
    assert injection.status_code == 422


def test_openapi_exposes_only_typed_commands_without_scope_or_digest(tmp_path: Path) -> None:
    schema = create_runtime_app(runtime_root=tmp_path).openapi()
    path = "/projects/{project_id}/episode-production-aggregate/commands"
    operation = schema["paths"][path]["post"]
    serialized = str(operation)
    assert "Idempotency-Key" in serialized
    assert "EpisodeCommand" in serialized
    command_schema_names = {
        name for name in schema["components"]["schemas"] if name.endswith("Command")
    }
    assert command_schema_names
    command_schemas = str(
        {name: schema["components"]["schemas"][name] for name in command_schema_names}
    )
    assert "scope" not in command_schemas
    assert "payload_digest" not in command_schemas
    assert "command_id" not in command_schemas
    assert not any(
        "additionalProperties" in schema["components"]["schemas"][name]
        and schema["components"]["schemas"][name]["additionalProperties"] is True
        for name in command_schema_names
    )
