from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.runtime_episode_domain_contract import ProductionProjectAggregate
from apps.api.runtime_episode_domain_routes import (
    LOCAL_ACTOR_ID,
    LOCAL_ORG_ID,
    _reject_unsafe_projection,
)
from apps.api.runtime_episode_domain_store import EpisodeDomainAggregateStore
from apps.api.runtime_service import create_runtime_app


PROJECT_ID = "episode-001"
ROUTE = f"/projects/{PROJECT_ID}/episode-production-aggregate"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _auth_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, dict[str, Any], dict[str, Any]]:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_INVITE_CODES", "owner-invite,other-invite")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    owner = _register(client, "owner-invite", "owner@example.com")
    other = _register(client, "other-invite", "other@example.com")
    created = client.post(
        "/projects",
        headers=_headers(owner),
        json={"project_id": PROJECT_ID, "goal": "Produce a recoverable episode"},
    )
    assert created.status_code == 200, created.text
    return client, owner, other


def _register(client: TestClient, invite_code: str, email: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "strong-password-123",
            "display_name": email.split("@", 1)[0],
            "invite_code": invite_code,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _headers(user: dict[str, Any], idempotency_key: str = "") -> dict[str, str]:
    headers = {"Authorization": f"Bearer {user['session_token']}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _aggregate(
    project_id: str,
    org_id: str,
    actor_id: str,
    *,
    aggregate_version: int = 1,
    title: str = "Rainlight Episode",
    retired: bool = False,
) -> dict[str, Any]:
    scope = {"org_id": org_id, "project_id": project_id, "actor_id": actor_id}
    projects = [
        {
            "entity_type": "project",
            "entity_id": project_id,
            "version_id": f"{project_id}-v1",
            "revision": 1,
            "parent_version_id": None,
            "lifecycle_state": "draft",
            "review_state": "not_requested",
            "content_digest": _digest(f"{project_id}:{title}:v1"),
            "scope": scope,
            "created_at": "2026-07-15T08:00:00+00:00",
            "source_refs": [],
            "title": title,
            "data_policy": {
                "visibility": "private",
                "training_use": "denied_by_default",
                "product_improvement_use": "denied_by_default",
                "export_enabled": True,
                "deletion_enabled": True,
            },
        }
    ]
    if retired:
        projects.append(
            {
                **projects[0],
                "version_id": f"{project_id}-v2",
                "revision": 2,
                "parent_version_id": f"{project_id}-v1",
                "lifecycle_state": "retired",
                "content_digest": _digest(f"{project_id}:{title}:retired"),
                "created_at": "2026-07-15T09:00:00+00:00",
            }
        )
    return {
        "schema_version": "afs_episode_production_aggregate.v0.1",
        "aggregate_version": aggregate_version,
        "evaluated_at": "2026-07-15T10:00:00+00:00",
        "scope": scope,
        "projects": projects,
        "series": [],
        "episodes": [],
        "scenes": [],
        "shots": [],
        "continuity_states": [],
        "asset_candidates": [],
        "selections": [],
        "review_decisions": [],
        "agent_proposals": [],
        "deliveries": [],
        "consent_records": [],
        "provider_contracts": [],
    }


def _replace_body(aggregate: dict[str, Any], expected: int) -> dict[str, Any]:
    return {"expected_aggregate_version": expected, "aggregate": aggregate}


def _aggregate_with_safe_artifact() -> dict[str, Any]:
    aggregate = _aggregate(PROJECT_ID, LOCAL_ORG_ID, LOCAL_ACTOR_ID)
    scope = aggregate["scope"]

    def fact(entity_type: str, entity_id: str, version_id: str, created_at: str) -> dict[str, Any]:
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "version_id": version_id,
            "revision": 1,
            "parent_version_id": None,
            "lifecycle_state": "draft",
            "review_state": "not_requested",
            "content_digest": _digest(f"{entity_type}:{entity_id}:{version_id}"),
            "scope": scope,
            "created_at": created_at,
            "source_refs": [],
        }

    project_ref = {
        "entity_type": "project",
        "entity_id": PROJECT_ID,
        "version_id": f"{PROJECT_ID}-v1",
    }
    series = {
        **fact("series", "series-001", "series-001-v1", "2026-07-15T08:01:00+00:00"),
        "project_ref": project_ref,
        "title": "Rainlight",
    }
    series_ref = {
        "entity_type": "series",
        "entity_id": "series-001",
        "version_id": "series-001-v1",
    }
    episode = {
        **fact("episode", "episode-001", "episode-001-v1", "2026-07-15T08:02:00+00:00"),
        "series_ref": series_ref,
        "title": "Episode 1",
    }
    episode_ref = {
        "entity_type": "episode",
        "entity_id": "episode-001",
        "version_id": "episode-001-v1",
    }
    scene = {
        **fact("scene", "scene-001", "scene-001-v1", "2026-07-15T08:03:00+00:00"),
        "episode_ref": episode_ref,
        "sequence": 1,
        "title": "Warehouse",
    }
    scene_ref = {
        "entity_type": "scene",
        "entity_id": "scene-001",
        "version_id": "scene-001-v1",
    }
    shot = {
        **fact("shot", "shot-001", "shot-001-v1", "2026-07-15T08:04:00+00:00"),
        "scene_ref": scene_ref,
        "sequence": 1,
        "duration_seconds": 6.0,
        "continuity_refs": [],
    }
    shot_ref = {
        "entity_type": "shot",
        "entity_id": "shot-001",
        "version_id": "shot-001-v1",
    }
    candidate = {
        **fact(
            "asset_candidate",
            "candidate-001",
            "candidate-001-v1",
            "2026-07-15T08:05:00+00:00",
        ),
        "target_ref": shot_ref,
        "artifact_ref": {
            "artifact_id": "artifact-001",
            "artifact_type": "image_asset",
            "content_digest": _digest("artifact-001"),
        },
        "job_id": "job-001",
        "job_state": "succeeded",
    }
    aggregate.update(
        series=[series],
        episodes=[episode],
        scenes=[scene],
        shots=[shot],
        asset_candidates=[candidate],
    )
    return aggregate


def _error(response) -> str:
    return str(response.json().get("detail", {}).get("error") or "")


def test_owner_create_get_replay_and_restart_recovery(tmp_path: Path, monkeypatch) -> None:
    client, owner, _ = _auth_client(tmp_path, monkeypatch)
    user_id = owner["user"]["user_id"]
    aggregate = _aggregate(PROJECT_ID, user_id, user_id)
    body = _replace_body(aggregate, expected=0)

    created = client.put(ROUTE, headers=_headers(owner, "create-aggregate-v1"), json=body)
    replay = client.put(ROUTE, headers=_headers(owner, "create-aggregate-v1"), json=body)

    assert created.status_code == 200, created.text
    assert created.json()["replayed"] is False
    assert created.json()["aggregate_version"] == 1
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["aggregate"] == created.json()["aggregate"]

    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    loaded = restarted.get(ROUTE, headers=_headers(owner))
    assert loaded.status_code == 200, loaded.text
    assert loaded.json() == {
        "aggregate": created.json()["aggregate"],
        "aggregate_version": 1,
    }


def test_server_computed_digest_rejects_changed_payload_for_same_key(tmp_path: Path, monkeypatch) -> None:
    client, owner, _ = _auth_client(tmp_path, monkeypatch)
    user_id = owner["user"]["user_id"]
    original = _replace_body(_aggregate(PROJECT_ID, user_id, user_id), expected=0)
    changed = _replace_body(
        _aggregate(PROJECT_ID, user_id, user_id, title="Changed payload"),
        expected=0,
    )

    assert client.put(ROUTE, headers=_headers(owner, "stable-key"), json=original).status_code == 200
    conflict = client.put(ROUTE, headers=_headers(owner, "stable-key"), json=changed)

    assert conflict.status_code == 409
    assert _error(conflict) == "episode_aggregate_idempotency_conflict"


def test_whole_aggregate_put_rejects_non_bootstrap_payload(tmp_path: Path, monkeypatch) -> None:
    client, owner, _ = _auth_client(tmp_path, monkeypatch)
    user_id = owner["user"]["user_id"]
    assert client.put(
        ROUTE,
        headers=_headers(owner, "create-v1"),
        json=_replace_body(_aggregate(PROJECT_ID, user_id, user_id), expected=0),
    ).status_code == 200

    stale = client.put(
        ROUTE,
        headers=_headers(owner, "stale-v2"),
        json=_replace_body(
            _aggregate(PROJECT_ID, user_id, user_id, aggregate_version=2),
            expected=0,
        ),
    )

    assert stale.status_code == 409
    assert _error(stale) == "episode_aggregate_bootstrap_only"


def test_cross_tenant_access_is_denied(tmp_path: Path, monkeypatch) -> None:
    client, owner, other = _auth_client(tmp_path, monkeypatch)
    user_id = owner["user"]["user_id"]
    aggregate = _aggregate(PROJECT_ID, user_id, user_id)
    assert client.put(
        ROUTE,
        headers=_headers(owner, "owner-create"),
        json=_replace_body(aggregate, expected=0),
    ).status_code == 200

    assert client.get(ROUTE, headers=_headers(other)).status_code == 403
    denied_write = client.put(
        ROUTE,
        headers=_headers(other, "foreign-write"),
        json=_replace_body(aggregate, expected=1),
    )
    assert denied_write.status_code == 403
    assert _error(denied_write) == "project_access_denied"


def test_path_project_and_authenticated_actor_org_must_match(tmp_path: Path, monkeypatch) -> None:
    client, owner, other = _auth_client(tmp_path, monkeypatch)
    owner_id = owner["user"]["user_id"]
    other_id = other["user"]["user_id"]

    project_mismatch = client.put(
        ROUTE,
        headers=_headers(owner, "project-mismatch"),
        json=_replace_body(_aggregate("episode-002", owner_id, owner_id), expected=0),
    )
    actor_mismatch = client.put(
        ROUTE,
        headers=_headers(owner, "actor-mismatch"),
        json=_replace_body(_aggregate(PROJECT_ID, owner_id, other_id), expected=0),
    )
    org_mismatch = client.put(
        ROUTE,
        headers=_headers(owner, "org-mismatch"),
        json=_replace_body(_aggregate(PROJECT_ID, other_id, owner_id), expected=0),
    )

    assert project_mismatch.status_code == 409
    assert _error(project_mismatch) == "episode_aggregate_project_mismatch"
    assert actor_mismatch.status_code == 403
    assert _error(actor_mismatch) == "episode_aggregate_scope_mismatch"
    assert org_mismatch.status_code == 403
    assert _error(org_mismatch) == "episode_aggregate_scope_mismatch"


def test_missing_aggregate_is_explicit_for_existing_project(tmp_path: Path, monkeypatch) -> None:
    client, owner, _ = _auth_client(tmp_path, monkeypatch)

    response = client.get(ROUTE, headers=_headers(owner))

    assert response.status_code == 404
    assert _error(response) == "episode_aggregate_not_found"


def test_bootstrap_only_put_prevents_service_gate_bypass(tmp_path: Path, monkeypatch) -> None:
    client, owner, _ = _auth_client(tmp_path, monkeypatch)
    user_id = owner["user"]["user_id"]
    assert client.put(
        ROUTE,
        headers=_headers(owner, "create-v1"),
        json=_replace_body(_aggregate(PROJECT_ID, user_id, user_id), expected=0),
    ).status_code == 200
    retirement = _aggregate(PROJECT_ID, user_id, user_id, aggregate_version=2, retired=True)
    bypass = client.put(
        ROUTE,
        headers=_headers(owner, "retire-v2"),
        json=_replace_body(retirement, expected=1),
    )

    assert bypass.status_code == 409
    assert _error(bypass) == "episode_aggregate_bootstrap_only"
    loaded = client.get(ROUTE, headers=_headers(owner)).json()
    assert loaded["aggregate_version"] == 1
    assert loaded["aggregate"]["projects"][-1]["lifecycle_state"] == "draft"


def test_integrity_failure_is_fail_closed_and_sanitized(tmp_path: Path, monkeypatch) -> None:
    client, owner, _ = _auth_client(tmp_path, monkeypatch)
    user_id = owner["user"]["user_id"]
    assert client.put(
        ROUTE,
        headers=_headers(owner, "create-v1"),
        json=_replace_body(_aggregate(PROJECT_ID, user_id, user_id), expected=0),
    ).status_code == 200
    path = EpisodeDomainAggregateStore(tmp_path).snapshot_path(org_id=user_id, project_id=PROJECT_ID)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["aggregate"]["projects"][0]["title"] = r"D:\private\snapshot.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    failed = client.get(ROUTE, headers=_headers(owner))

    assert failed.status_code == 500
    assert _error(failed) == "episode_aggregate_integrity_failed"
    lowered = failed.text.lower()
    assert "episode_aggregates" not in lowered
    assert "snapshot.json" not in lowered
    assert "d:\\private" not in lowered
    assert "traceback" not in lowered


@pytest.mark.parametrize(
    ("case", "unsafe_value"),
    (
        ("windows-drive", r"E:\private\episode.json"),
        ("windows-drive-lower", r"e:/private/episode.json"),
        ("windows-drive-encoded", "%45%3A%5Cprivate%5Cepisode.json"),
        ("windows-drive-double-encoded", "%2545%253A%255Cprivate%255Cepisode.json"),
        ("unc", r"\\server\share\episode.json"),
        ("posix", "/home/afs/private/episode.json"),
        ("file-uri", "file:///home/afs/private/episode.json"),
        ("file-uri-mixed-case", "FiLe://server/share/episode.json"),
        ("signed-url", "https://cdn.example/episode.png?X-Amz-Signature=secret"),
        ("signed-url-lower", "https://cdn.example/episode.png?x-amz-signature=secret"),
        ("signed-url-mixed", "https://cdn.example/episode.png?X-aMz-SiGnAtUrE=secret"),
        ("signed-url-key-encoded", "https://cdn.example/episode.png?X-Amz-%53ignature=secret"),
        (
            "signed-url-encoded",
            "https%3A%2F%2Fcdn.example%2Fepisode.png%3FX-Amz-Signature%3Dsecret",
        ),
        ("azure-signed-url", "https://cdn.example/episode.png?sv=1&sig=secret"),
        ("token-url", "https://cdn.example/episode.png?access_token=secret"),
        (
            "embedded-windows-drive",
            "Creator reopened C:/private/customer/secret.mov for review.",
        ),
        (
            "embedded-windows-encoded",
            "Creator reopened C%253A%255Cprivate%255Csecret.mov for review.",
        ),
        ("embedded-unc", r"Creator reopened \\server\share\secret.mov for review."),
        ("embedded-posix", "Creator reopened /home/afs/private/secret.mov for review."),
        ("embedded-file-uri", "Creator reopened file:///home/afs/secret.mov for review."),
        (
            "embedded-token-url",
            "Creator reopened https://cdn.example/episode.png?access_token=secret for review.",
        ),
        (
            "embedded-signed-url-encoded",
            "Creator reopened https%253A%252F%252Fcdn.example%252Fepisode.png"
            "%253FX-Amz-Signature%253Dsecret for review.",
        ),
    ),
)
def test_unsafe_projection_matrix_is_rejected_and_never_returned(
    tmp_path: Path,
    monkeypatch,
    case: str,
    unsafe_value: str,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    assert client.post(
        "/projects",
        json={"project_id": PROJECT_ID, "goal": "Local safety test"},
    ).status_code == 200
    unsafe = _aggregate(PROJECT_ID, LOCAL_ORG_ID, LOCAL_ACTOR_ID, title=unsafe_value)
    aggregate_store = EpisodeDomainAggregateStore(tmp_path)

    rejected = client.put(
        ROUTE,
        headers={"Idempotency-Key": f"unsafe-{case}"},
        json=_replace_body(unsafe, expected=0),
    )

    assert rejected.status_code == 422
    assert _error(rejected) == "episode_aggregate_unsafe_payload"
    assert unsafe_value not in rejected.text
    assert not aggregate_store.snapshot_path(org_id=LOCAL_ORG_ID, project_id=PROJECT_ID).exists()

    aggregate_store.save(
        ProductionProjectAggregate.model_validate(unsafe),
        expected_aggregate_version=0,
        idempotency_key=f"direct-{case}",
        payload_digest=_digest(f"direct-{case}"),
    )
    failed_read = client.get(ROUTE)
    assert failed_read.status_code == 500
    assert _error(failed_read) == "episode_aggregate_integrity_failed"
    assert unsafe_value not in failed_read.text


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "Creator reopened C:/private/customer/secret.mov for review.",
        r"Creator reopened C:\private\customer\secret.mov for review.",
        r"Creator reopened \\server\share\secret.mov for review.",
        "Creator reopened //server/share/secret.mov for review.",
        "Creator reopened /opt/afs/private/secret.mov for review.",
        "Creator reopened /OPT/afs/private/secret.mov for review.",
        "Creator reopened file:///home/afs/private/secret.mov for review.",
        "Creator reopened https://cdn.example/item?X-Amz-Signature=secret for review.",
        "Creator reopened https%253A%252F%252Fcdn.example%252Fitem"
        "%253Faccess_token%253Dsecret for review.",
    ),
)
def test_projection_sanitizer_scans_embedded_private_locations(unsafe_value: str) -> None:
    with pytest.raises(ValueError, match="unsafe projection string"):
        _reject_unsafe_projection({"note": unsafe_value})


@pytest.mark.parametrize(
    "safe_value",
    (
        "Creator reopened scene 7 for review.",
        "Use assets/episode-001/preview.mp4?variant=small for review.",
        "Use ./assets/episode-001/preview.mp4?variant=small for review.",
        "Use https://cdn.example/episode.png?variant=small for review.",
        "Use https://cdn.example/home/preview.mp4?variant=small for review.",
        "Use https://cdn.example/opt/preview.mp4?variant=small for review.",
        "Compare choices A/B before review.",
        "Drive C: contains no shared path here.",
    ),
)
def test_projection_sanitizer_preserves_safe_creator_text(safe_value: str) -> None:
    _reject_unsafe_projection({"note": safe_value})


def test_projection_safety_preserves_ordinary_text_and_relative_artifact_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    assert client.post(
        "/projects",
        json={"project_id": PROJECT_ID, "goal": "Local positive safety test"},
    ).status_code == 200
    aggregate = _aggregate(
        PROJECT_ID,
        LOCAL_ORG_ID,
        LOCAL_ACTOR_ID,
        title="File: 雨灯失窃案中的剧情道具清单",
    )
    relative_url = "assets/episode-001/preview.mp4?variant=small"
    aggregate["provider_contracts"] = [
        {
            "provider_id": "local-preview",
            "surface": relative_url,
            "training_use": "prohibited",
            "no_training_supported": True,
            "retention_days": 0,
            "deletion_api_supported": True,
            "withdrawal_supported": True,
            "region": "local",
            "subprocessors_documented": True,
        }
    ]

    created = client.put(
        ROUTE,
        headers={"Idempotency-Key": "safe-relative-url"},
        json=_replace_body(aggregate, expected=0),
    )
    loaded = client.get(ROUTE)

    assert created.status_code == 200, created.text
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["aggregate"]["projects"][0]["title"] == aggregate["projects"][0]["title"]
    assert loaded.json()["aggregate"]["provider_contracts"][0]["surface"] == relative_url


def test_episode_safe_artifact_projection_roundtrips_through_frozen_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    assert client.post(
        "/projects",
        json={"project_id": PROJECT_ID, "goal": "Safe artifact projection"},
    ).status_code == 200
    aggregate = _aggregate_with_safe_artifact()

    created = client.put(
        ROUTE,
        headers={"Idempotency-Key": "safe-artifact-projection"},
        json=_replace_body(aggregate, expected=0),
    )
    loaded = client.get(ROUTE)
    frozen = EpisodeDomainAggregateStore(tmp_path).load(
        org_id=LOCAL_ORG_ID,
        project_id=PROJECT_ID,
    )

    assert created.status_code == 200, created.text
    assert loaded.status_code == 200, loaded.text
    expected_ref = aggregate["asset_candidates"][0]["artifact_ref"]
    assert created.json()["aggregate"]["asset_candidates"][0]["artifact_ref"] == expected_ref
    assert loaded.json()["aggregate"]["asset_candidates"][0]["artifact_ref"] == expected_ref
    assert frozen.asset_candidates[0].artifact_ref is not None
    assert frozen.asset_candidates[0].artifact_ref.model_dump(mode="json") == expected_ref


def test_v01_wire_payload_gets_v011_operation_membership_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    assert client.post(
        "/projects",
        json={"project_id": PROJECT_ID, "goal": "Wire compatibility"},
    ).status_code == 200
    aggregate = _aggregate_with_safe_artifact()
    shot = aggregate["shots"][0]
    shot_ref = {
        "entity_type": "shot",
        "entity_id": shot["entity_id"],
        "version_id": shot["version_id"],
    }
    proposal = {
        "entity_type": "agent_proposal",
        "entity_id": "proposal-001",
        "version_id": "proposal-001-v1",
        "revision": 1,
        "parent_version_id": None,
        "lifecycle_state": "draft",
        "review_state": "not_requested",
        "content_digest": _digest("proposal-001-v1"),
        "scope": aggregate["scope"],
        "created_at": "2026-07-15T08:06:00+00:00",
        "source_refs": [],
        "target_ref": shot_ref,
        "impact_refs": [shot_ref],
        "action": "inspect_continuity",
        "decision_state": "accepted",
    }
    aggregate["agent_proposals"] = [proposal]
    assert "source_proposal_ref" not in shot
    assert "applied_refs" not in proposal

    created = client.put(
        ROUTE,
        headers={"Idempotency-Key": "v011-defaults"},
        json=_replace_body(aggregate, expected=0),
    )
    loaded = client.get(ROUTE)

    assert created.status_code == 200, created.text
    assert loaded.status_code == 200, loaded.text
    assert created.json()["aggregate"]["shots"][0]["source_proposal_ref"] is None
    assert created.json()["aggregate"]["agent_proposals"][0]["applied_refs"] == []
    assert loaded.json()["aggregate"]["shots"][0]["source_proposal_ref"] is None
    assert loaded.json()["aggregate"]["agent_proposals"][0]["applied_refs"] == []


def test_auth_off_uses_explicit_local_scope_and_keeps_project_binding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    assert client.post(
        "/projects",
        json={"project_id": PROJECT_ID, "goal": "Local episode"},
    ).status_code == 200
    local = _aggregate(PROJECT_ID, LOCAL_ORG_ID, LOCAL_ACTOR_ID)

    created = client.put(
        ROUTE,
        headers={"Idempotency-Key": "local-create"},
        json=_replace_body(local, expected=0),
    )
    wrong_project = client.put(
        ROUTE,
        headers={"Idempotency-Key": "local-wrong-project"},
        json=_replace_body(_aggregate("episode-002", LOCAL_ORG_ID, LOCAL_ACTOR_ID), expected=1),
    )
    wrong_scope = client.put(
        ROUTE,
        headers={"Idempotency-Key": "local-wrong-scope"},
        json=_replace_body(_aggregate(PROJECT_ID, "another-runtime", LOCAL_ACTOR_ID), expected=1),
    )
    missing_project_id = "missing-project"
    missing_project = client.put(
        f"/projects/{missing_project_id}/episode-production-aggregate",
        headers={"Idempotency-Key": "local-missing-project"},
        json=_replace_body(
            _aggregate(missing_project_id, LOCAL_ORG_ID, LOCAL_ACTOR_ID),
            expected=0,
        ),
    )

    assert created.status_code == 200, created.text
    assert created.json()["aggregate"]["scope"] == {
        "org_id": LOCAL_ORG_ID,
        "project_id": PROJECT_ID,
        "actor_id": LOCAL_ACTOR_ID,
    }
    assert wrong_project.status_code == 409
    assert wrong_scope.status_code == 403
    assert _error(wrong_scope) == "episode_aggregate_scope_mismatch"
    assert missing_project.status_code == 404
    assert _error(missing_project) == "project_not_found"
    assert not EpisodeDomainAggregateStore(tmp_path).snapshot_path(
        org_id=LOCAL_ORG_ID,
        project_id=missing_project_id,
    ).exists()


def test_idempotency_header_is_required_and_openapi_exposes_no_store_internals(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, owner, _ = _auth_client(tmp_path, monkeypatch)
    user_id = owner["user"]["user_id"]
    missing_header = client.put(
        ROUTE,
        headers=_headers(owner),
        json=_replace_body(_aggregate(PROJECT_ID, user_id, user_id), expected=0),
    )

    assert missing_header.status_code == 422
    schema = client.app.openapi()
    operation = schema["paths"]["/projects/{project_id}/episode-production-aggregate"]["put"]
    assert operation["summary"] == "Bootstrap Episode Production Aggregate"
    assert "typed /commands endpoint" in operation["description"]
    idempotency = next(item for item in operation["parameters"] if item["name"] == "Idempotency-Key")
    assert idempotency["required"] is True
    serialized = json.dumps(schema, ensure_ascii=False).lower()
    assert "expected_aggregate_version" in serialized
    assert "envelope_sha256" not in serialized
    assert "idempotency_records" not in serialized
    assert "snapshot_path" not in serialized
    assert "episode_aggregates" not in serialized
    assert "data/processed/runs" not in serialized
    assert "d:\\" not in serialized


def test_openapi_preserves_existing_safe_artifact_component_and_ref(tmp_path: Path) -> None:
    schema = create_runtime_app(runtime_root=tmp_path).openapi()
    schemas = schema["components"]["schemas"]

    assert "SafeArtifactRef" in schemas
    assert schemas["ProductionCandidate"]["properties"]["safe_artifact_refs"]["items"] == {
        "$ref": "#/components/schemas/SafeArtifactRef"
    }


def test_openapi_uses_unique_episode_projection_components(tmp_path: Path) -> None:
    schema = create_runtime_app(runtime_root=tmp_path).openapi()
    schemas = schema["components"]["schemas"]

    assert "EpisodeSafeArtifactRef" in schemas
    assert not any(
        name.endswith("runtime_production_models__SafeArtifactRef")
        or name.endswith("runtime_episode_domain_contract__SafeArtifactRef")
        for name in schemas
    )
    operation = schema["paths"]["/projects/{project_id}/episode-production-aggregate"]
    assert operation["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EpisodeAggregateReadResponse"
    }
    assert operation["put"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EpisodeAggregateWriteResponse"
    }
    assert "source_proposal_ref" in schemas["ShotVersion"]["properties"]
    assert "source_proposal_ref" not in schemas["ShotVersion"].get("required", [])
    assert "applied_refs" in schemas["AgentProposal"]["properties"]
    assert "applied_refs" not in schemas["AgentProposal"].get("required", [])
