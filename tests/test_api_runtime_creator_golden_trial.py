from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from apps.api import runtime_creator_golden_trial, runtime_creator_golden_trial_service
from apps.api.runtime_episode_domain_contract import TenantScope
from apps.api.runtime_service import create_runtime_app
from apps.api.runtime_store import RuntimeStore


STAMP = "2026-07-15T09:00:00+00:00"


def test_creator_golden_trial_dispatch_writes_episode_candidate_and_replays_without_duplicate_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _register(client, "owner@example.com")
    headers = _headers(owner)
    _create_project(client, headers, "golden-trial")
    calls: list[str] = []

    def fake_dispatch(*_args, shot_id: str, provider_attempt_id: str, **_kwargs):
        calls.append(shot_id)
        digest = hashlib.sha256(f"{shot_id}:{provider_attempt_id}".encode("utf-8")).hexdigest()
        return {
            "status": "succeeded",
            "job_id": f"fake-job-{shot_id}",
            "provider_gate": {"status": "ready", "required_gate": "AFS_ALLOW_REMOTE_IMAGE"},
            "provider_calls_started": True,
            "safe_manifest": {"status": "succeeded", "provider_raw_response_stored": False},
            "candidate_previews": [
                {
                    "candidate_id": "candidate_001",
                    "preview_url": f"/projects/golden-trial/keyframe-generations/fake-job-{shot_id}/candidates/candidate_001/preview",
                    "byte_count": 68,
                    "sha256": digest,
                    "width": 1,
                    "height": 1,
                    "aspect_ratio": "1:1",
                }
            ],
            "selected_artifact_ref": {
                "artifact_id": f"fake-artifact-{shot_id}",
                "artifact_type": "image_keyframe",
                "content_digest": digest,
            },
        }

    monkeypatch.setattr(runtime_creator_golden_trial, "_dispatch_image_keyframe", fake_dispatch)

    mission = _post(
        client,
        "/projects/golden-trial/creator-golden-trial/mission",
        headers,
        "mission-1",
        _mission(project_ceiling_amount=1.0, estimated_unit_cost_amount=0.1),
    )
    assert mission.status_code == 200, mission.text
    assert mission.json()["trial"]["status"] == "planned"

    approve = _post(
        client,
        "/projects/golden-trial/creator-golden-trial/approve",
        headers,
        "approve-1",
        {"expected_event_count": mission.json()["trial"]["event_count"], "created_at": STAMP},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["trial"]["status"] == "approved"

    body = {
        "expected_event_count": approve.json()["trial"]["event_count"],
        "provider_service_id": "image_relay",
        "estimated_cost_amount": 0.1,
        "generated_at": STAMP,
    }
    dispatch = _post(
        client,
        "/projects/golden-trial/creator-golden-trial/dispatch-next",
        headers,
        "dispatch-1",
        body,
    )
    assert dispatch.status_code == 200, dispatch.text
    payload = dispatch.json()
    assert payload["provider_calls_started"] is True
    assert payload["receipt"]["episode_writeback"]["status"] == "written"
    assert payload["receipt"]["cost_receipt"]["receipt_kind"] == "synthetic_admission"
    assert payload["receipt"]["cost_receipt"]["actual_cost"]["status"] == "unknown_unverified"
    boundary = payload["receipt"]["production_control_boundary"]
    assert boundary["trial_ledger_role"] == "discardable_experiment_adapter_cache"
    assert boundary["creates_production_control_objects"] is False
    assert boundary["created_production_control_run_id"] is None
    assert boundary["created_production_control_attempt_id"] is None
    assert boundary["created_production_control_cost_receipt_id"] is None
    assert payload["receipt"]["cost_receipt"]["production_control_boundary"] == boundary
    assert payload["trial"]["adapter_authority"]["trial_ledger_role"] == "discardable_experiment_adapter_cache"
    assert payload["trial"]["adapter_authority"]["creates_production_control_objects"] is False
    assert "cost" + "_receipts" not in payload["trial"]
    assert payload["trial"]["admission_receipts"]
    assert payload["trial"]["dispatches"]["shot-001"]["episode_writeback"]["human_review_state"] == "needs_review"
    assert payload["trial"]["dispatches"]["shot-001"]["episode_writeback"]["control_provenance_status"] == "not_written_by_adapter_cache"
    assert "production_run_id" not in payload["trial"]["dispatches"]["shot-001"]
    assert calls == ["shot-001"]

    replay = _post(
        client,
        "/projects/golden-trial/creator-golden-trial/dispatch-next",
        headers,
        "dispatch-1",
        {**body, "generated_at": "2026-07-15T09:00:01+00:00"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == payload
    assert calls == ["shot-001"]

    changed = _post(
        client,
        "/projects/golden-trial/creator-golden-trial/dispatch-next",
        headers,
        "dispatch-1",
        {**body, "provider_service_id": "different-image"},
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["error"] == "creator_golden_trial_idempotency_conflict"

    aggregate = client.get("/projects/golden-trial/episode-production-aggregate", headers=headers)
    assert aggregate.status_code == 200, aggregate.text
    candidates = aggregate.json()["aggregate"]["asset_candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["target_ref"]["entity_id"] == "shot-001"
    assert candidate["artifact_ref"]["artifact_type"] == "image_keyframe"
    assert candidate["job_state"] == "succeeded"
    assert candidate["review_state"] == "needs_review"
    assert candidate["control_provenance"] is None


def test_creator_golden_trial_uses_stored_unit_estimate_and_blocks_lowball_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _register(client, "lowball-owner@example.com")
    headers = _headers(owner)
    _create_project(client, headers, "golden-lowball")
    calls: list[str] = []

    def fake_dispatch(*_args, shot_id: str, provider_attempt_id: str, **_kwargs):
        calls.append(shot_id)
        digest = hashlib.sha256(f"{shot_id}:{provider_attempt_id}".encode("utf-8")).hexdigest()
        return {
            "status": "succeeded",
            "job_id": f"fake-job-{shot_id}",
            "provider_gate": {"status": "ready", "required_gate": "AFS_ALLOW_REMOTE_IMAGE"},
            "provider_calls_started": True,
            "safe_manifest": {"status": "succeeded", "provider_raw_response_stored": False},
            "candidate_previews": [],
            "selected_artifact_ref": {
                "artifact_id": f"fake-artifact-{shot_id}",
                "artifact_type": "image_keyframe",
                "content_digest": digest,
            },
        }

    monkeypatch.setattr(runtime_creator_golden_trial, "_dispatch_image_keyframe", fake_dispatch)
    mission = _post(
        client,
        "/projects/golden-lowball/creator-golden-trial/mission",
        headers,
        "mission-1",
        _mission(project_ceiling_amount=0.15, estimated_unit_cost_amount=0.1),
    )
    approve = _post(
        client,
        "/projects/golden-lowball/creator-golden-trial/approve",
        headers,
        "approve-1",
        {"expected_event_count": mission.json()["trial"]["event_count"], "created_at": STAMP},
    )
    first = _post(
        client,
        "/projects/golden-lowball/creator-golden-trial/dispatch-next",
        headers,
        "dispatch-1",
        {
            "expected_event_count": approve.json()["trial"]["event_count"],
            "estimated_cost_amount": 0.01,
            "generated_at": STAMP,
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["provider_calls_started"] is True
    second = _post(
        client,
        "/projects/golden-lowball/creator-golden-trial/dispatch-next",
        headers,
        "dispatch-2",
        {
            "expected_event_count": first.json()["trial"]["event_count"],
            "estimated_cost_amount": 0.01,
            "generated_at": "2026-07-15T09:00:01+00:00",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["provider_calls_started"] is False
    assert second.json()["receipt"]["reason"] == "budget_ceiling"
    assert second.json()["receipt"]["production_control_boundary"]["creates_production_control_objects"] is False
    assert second.json()["receipt"]["production_control_boundary"]["created_production_control_run_id"] is None
    assert calls == ["shot-001"]


def test_creator_golden_trial_blocks_second_key_while_attempt_is_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _register(client, "running-owner@example.com")
    headers = _headers(owner)
    _create_project(client, headers, "golden-running")
    calls: list[str] = []
    nested: dict[str, Any] = {}

    def fake_dispatch(*_args, shot_id: str, provider_attempt_id: str, **_kwargs):
        calls.append(shot_id)
        if len(calls) == 1:
            current = client.get("/projects/golden-running/creator-golden-trial", headers=headers)
            assert current.status_code == 200, current.text
            nested["response"] = _post(
                client,
                "/projects/golden-running/creator-golden-trial/dispatch-next",
                headers,
                "dispatch-2",
                {
                    "expected_event_count": current.json()["trial"]["event_count"],
                    "estimated_cost_amount": 0.1,
                    "generated_at": "2026-07-15T09:00:01+00:00",
                },
            )
        digest = hashlib.sha256(f"{shot_id}:{provider_attempt_id}".encode("utf-8")).hexdigest()
        return {
            "status": "succeeded",
            "job_id": f"fake-job-{shot_id}",
            "provider_gate": {"status": "ready", "required_gate": "AFS_ALLOW_REMOTE_IMAGE"},
            "provider_calls_started": True,
            "safe_manifest": {"status": "succeeded", "provider_raw_response_stored": False},
            "candidate_previews": [],
            "selected_artifact_ref": {
                "artifact_id": f"fake-artifact-{shot_id}",
                "artifact_type": "image_keyframe",
                "content_digest": digest,
            },
        }

    monkeypatch.setattr(runtime_creator_golden_trial, "_dispatch_image_keyframe", fake_dispatch)
    mission = _post(
        client,
        "/projects/golden-running/creator-golden-trial/mission",
        headers,
        "mission-1",
        _mission(project_ceiling_amount=1.0, estimated_unit_cost_amount=0.1),
    )
    approve = _post(
        client,
        "/projects/golden-running/creator-golden-trial/approve",
        headers,
        "approve-1",
        {"expected_event_count": mission.json()["trial"]["event_count"], "created_at": STAMP},
    )
    dispatch = _post(
        client,
        "/projects/golden-running/creator-golden-trial/dispatch-next",
        headers,
        "dispatch-1",
        {
            "expected_event_count": approve.json()["trial"]["event_count"],
            "estimated_cost_amount": 0.1,
            "generated_at": STAMP,
        },
    )
    assert dispatch.status_code == 200, dispatch.text
    assert calls == ["shot-001"]
    assert nested["response"].status_code == 200
    assert nested["response"].json()["provider_calls_started"] is False


def test_creator_golden_trial_keyframe_dispatch_disables_provider_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_build_keyframe_generation(store, project_id, request, output_dir, **_kwargs):
        captured["node_parameters"] = request.node_parameters
        return {
            "status": "succeeded",
            "provider_gate": {"status": "ready", "required_gate": "AFS_ALLOW_REMOTE_IMAGE"},
            "provider_calls_started": True,
            "provider_outputs": [],
            "safe_manifest": {"status": "succeeded", "provider_raw_response_stored": False},
        }

    monkeypatch.setattr(runtime_creator_golden_trial_service, "build_keyframe_generation", fake_build_keyframe_generation)
    monkeypatch.setattr(runtime_creator_golden_trial_service, "keyframe_generation_artifacts", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runtime_creator_golden_trial_service, "_candidate_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runtime_creator_golden_trial_service, "_candidate_previews", lambda *_args, **_kwargs: [])

    store = RuntimeStore(tmp_path)
    store.create_project_manifest(
        project_id="retry-disabled",
        project_type="studio_episode_production",
        goal="Retry disabled",
        status="in_progress",
    )
    result = runtime_creator_golden_trial_service.dispatch_image_keyframe(
        store,
        "retry-disabled",
        TenantScope(org_id="org", project_id="retry-disabled", actor_id="actor"),
        shot_id="shot-001",
        body=runtime_creator_golden_trial.DispatchNextRequest(
            expected_event_count=0,
            estimated_cost_amount=0.1,
            generated_at=STAMP,
        ),
        provider_attempt_id="attempt-1",
    )

    assert result["provider_calls_started"] is True
    assert captured["node_parameters"]["disable_provider_retry"] is True


def test_creator_golden_trial_budget_ceiling_blocks_without_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _register(client, "budget-owner@example.com")
    headers = _headers(owner)
    _create_project(client, headers, "golden-budget")

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError("budget ceiling must block before provider dispatch")

    monkeypatch.setattr(runtime_creator_golden_trial, "_dispatch_image_keyframe", fail_dispatch)
    mission = _post(
        client,
        "/projects/golden-budget/creator-golden-trial/mission",
        headers,
        "mission-1",
        _mission(project_ceiling_amount=0.05, estimated_unit_cost_amount=0.1),
    )
    approve = _post(
        client,
        "/projects/golden-budget/creator-golden-trial/approve",
        headers,
        "approve-1",
        {"expected_event_count": mission.json()["trial"]["event_count"], "created_at": STAMP},
    )
    dispatch = _post(
        client,
        "/projects/golden-budget/creator-golden-trial/dispatch-next",
        headers,
        "dispatch-1",
        {
            "expected_event_count": approve.json()["trial"]["event_count"],
            "estimated_cost_amount": 0.1,
            "generated_at": STAMP,
        },
    )
    assert dispatch.status_code == 200, dispatch.text
    payload = dispatch.json()
    assert payload["provider_calls_started"] is False
    assert payload["receipt"]["status"] == "blocked"
    assert payload["trial"]["status"] == "blocked"
    assert payload["trial"]["dispatches"]["shot-001"]["reason"] == "budget_ceiling"

    aggregate = client.get("/projects/golden-budget/episode-production-aggregate", headers=headers)
    assert aggregate.status_code == 200, aggregate.text
    assert aggregate.json()["aggregate"]["asset_candidates"] == []


def test_creator_golden_trial_requires_approval_and_project_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _register(client, "scope-owner@example.com")
    other = _register(client, "scope-other@example.com")
    owner_headers = _headers(owner)
    other_headers = _headers(other)
    _create_project(client, owner_headers, "golden-scope")

    mission = _post(
        client,
        "/projects/golden-scope/creator-golden-trial/mission",
        owner_headers,
        "mission-1",
        _mission(project_ceiling_amount=1.0, estimated_unit_cost_amount=0.1),
    )
    assert mission.status_code == 200, mission.text

    early_dispatch = _post(
        client,
        "/projects/golden-scope/creator-golden-trial/dispatch-next",
        owner_headers,
        "dispatch-before-approval",
        {
            "expected_event_count": mission.json()["trial"]["event_count"],
            "estimated_cost_amount": 0.1,
            "generated_at": STAMP,
        },
    )
    assert early_dispatch.status_code == 409
    assert early_dispatch.json()["detail"]["error"] == "creator_golden_trial_not_approved"

    forbidden = client.get("/projects/golden-scope/creator-golden-trial", headers=other_headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["error"] == "project_access_denied"


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


def _create_project(client: TestClient, headers: dict[str, str], project_id: str) -> None:
    response = client.post(
        "/projects",
        headers=headers,
        json={
            "project_id": project_id,
            "project_type": "studio_episode_production",
            "goal": "Creator Golden Trial",
        },
    )
    assert response.status_code == 200, response.text


def _post(
    client: TestClient,
    route: str,
    headers: dict[str, str],
    key: str,
    body: dict[str, Any],
):
    return client.post(route, headers={**headers, "Idempotency-Key": key}, json=body)


def _mission(*, project_ceiling_amount: float, estimated_unit_cost_amount: float) -> dict[str, Any]:
    return {
        "objective": "制作一个三镜头的创作者主导 AI 原生制片系统样片。",
        "constraints": ["保持三镜头连续性。", "生成后等待人类审核。"],
        "project_ceiling_amount": project_ceiling_amount,
        "estimated_unit_cost_amount": estimated_unit_cost_amount,
        "currency": "USD",
        "created_at": STAMP,
    }
