from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from apps.api.runtime_service import create_runtime_app


PROJECT_ID = "commercial-slice"
STAMP = "2026-07-15T09:00:00+00:00"


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_AUTH_ALLOW_OPEN_SIGNUP", "true")
    return TestClient(create_runtime_app(runtime_root=tmp_path))


def _register(client: TestClient, email: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "strong-password-123",
            "display_name": email.split("@", 1)[0],
            "invite_code": "",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _headers(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['session_token']}"}


def _create_project(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post(
        "/projects",
        headers=headers,
        json={
            "project_id": PROJECT_ID,
            "project_type": "studio_episode_production",
            "goal": "Commercial vertical slice",
        },
    )
    assert response.status_code == 200, response.text


def _post(client: TestClient, route: str, headers: dict[str, str], key: str, body: dict[str, Any]):
    return client.post(route, headers={**headers, "Idempotency-Key": key}, json=body)


def test_commercial_production_slice_is_persistent_scoped_and_local_rewrite_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _register(client, "owner@example.com")
    other = _register(client, "other@example.com")
    headers = _headers(owner)
    _create_project(client, headers)

    empty = client.get(f"/projects/{PROJECT_ID}/commercial-production", headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["production"]["status"] == "empty"

    sample_body = {"expected_version": 0, "title": "雾港异闻录", "created_at": STAMP}
    created = _post(
        client,
        f"/projects/{PROJECT_ID}/commercial-production/sample",
        headers,
        "sample-1",
        sample_body,
    )
    assert created.status_code == 200, created.text
    production = created.json()["production"]
    assert production["version"] == 1
    assert production["storyboard"] == {
        "default_mode": True,
        "scene_count": 4,
        "shot_count": 16,
        "locked": False,
    }
    assert len(production["episodes"]) == 3
    assert {asset["type"] for asset in production["assets"]} >= {
        "human",
        "animal",
        "scene_location",
        "prop",
    }
    assert all(asset["shot_local_state_policy"] == "shot-local state cannot mutate base identity" for asset in production["assets"])
    assert production["production_recipe"]["raw_prompt_exposed"] is False
    assert production["production_control"]["provider_dispatch_count"] == 0

    replay = _post(
        client,
        f"/projects/{PROJECT_ID}/commercial-production/sample",
        headers,
        "sample-1",
        sample_body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    conflict = _post(
        client,
        f"/projects/{PROJECT_ID}/commercial-production/sample",
        headers,
        "sample-1",
        {**sample_body, "title": "Different"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "commercial_production_idempotency_conflict"

    lock = _post(
        client,
        f"/projects/{PROJECT_ID}/commercial-production/stage-gate/lock",
        headers,
        "lock-1",
        {"expected_version": 1, "created_at": STAMP},
    )
    assert lock.status_code == 200, lock.text
    locked = lock.json()["production"]
    assert locked["version"] == 2
    assert locked["stage_gates"]["storyboard_scope_lock"]["status"] == "locked"
    assert locked["stage_gates"]["storyboard_scope_lock"]["recoverable"] is True

    before = {shot["shot_id"]: shot["content_digest"] for shot in locked["shots"]}
    rewrite = _post(
        client,
        f"/projects/{PROJECT_ID}/commercial-production/revision-requests/local-rewrite",
        headers,
        "rewrite-1",
        {
            "expected_version": 2,
            "target_shot_id": "shot-006",
            "replacement_beat": "近景：只改写第六镜的悬念节奏。",
            "reason": "局部返工",
            "created_at": STAMP,
        },
    )
    assert rewrite.status_code == 200, rewrite.text
    request = rewrite.json()["revision_request"]
    assert request["protected_digest_equal"] is True
    assert request["protected_ref_counts"] == {
        "episodes": 2,
        "scenes": 4,
        "shots": 15,
        "assets": 6,
    }
    after = rewrite.json()["production"]
    shot_006 = next(shot for shot in after["shots"] if shot["shot_id"] == "shot-006")
    assert shot_006["version_id"] == "shot-006-v2"
    assert shot_006["review_state"] == "needs_review"
    assert shot_006["content_digest"] != before["shot-006"]
    assert {shot["shot_id"]: shot["content_digest"] for shot in after["shots"] if shot["shot_id"] != "shot-006"} == {
        key: value for key, value in before.items() if key != "shot-006"
    }
    assert all(asset["base_identity"]["version_id"].endswith("-base-v1") for asset in after["assets"])

    stale = _post(
        client,
        f"/projects/{PROJECT_ID}/commercial-production/revision-requests/local-rewrite",
        headers,
        "rewrite-stale",
        {
            "expected_version": 2,
            "target_shot_id": "shot-007",
            "replacement_beat": "stale",
            "created_at": STAMP,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "commercial_production_version_conflict"

    late_sample_replay = _post(
        client,
        f"/projects/{PROJECT_ID}/commercial-production/sample",
        headers,
        "sample-1",
        sample_body,
    )
    assert late_sample_replay.status_code == 200, late_sample_replay.text
    assert late_sample_replay.json()["replayed"] is True
    assert late_sample_replay.json()["production"]["version"] == 1
    assert late_sample_replay.json()["production"]["storyboard"]["locked"] is False

    late_lock_replay = _post(
        client,
        f"/projects/{PROJECT_ID}/commercial-production/stage-gate/lock",
        headers,
        "lock-1",
        {"expected_version": 1, "created_at": STAMP},
    )
    assert late_lock_replay.status_code == 200, late_lock_replay.text
    assert late_lock_replay.json()["replayed"] is True
    assert late_lock_replay.json()["production"]["version"] == 2
    assert late_lock_replay.json()["production"]["stage_gates"]["storyboard_scope_lock"]["status"] == "locked"
    assert late_lock_replay.json()["production"]["revision_requests"] == []

    recovered = _client(tmp_path, monkeypatch).get(
        f"/projects/{PROJECT_ID}/commercial-production",
        headers=headers,
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["production"]["version"] == 3
    assert recovered.json()["production"]["revision_requests"][0]["protected_digest_equal"] is True

    foreign = client.get(f"/projects/{PROJECT_ID}/commercial-production", headers=_headers(other))
    assert foreign.status_code == 403


def test_commercial_production_openapi_paths_are_public(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("AFS_AUTH_ALLOW_OPEN_SIGNUP", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    schema = client.get("/openapi.json").json()
    assert "/projects/{project_id}/commercial-production" in schema["paths"]
    assert "/projects/{project_id}/commercial-production/sample" in schema["paths"]
    assert "/projects/{project_id}/commercial-production/stage-gate/lock" in schema["paths"]
    assert "/projects/{project_id}/commercial-production/revision-requests/local-rewrite" in schema["paths"]
