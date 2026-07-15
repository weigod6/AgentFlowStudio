from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api import runtime_generation_comparisons, runtime_keyframe_routes, runtime_video_routes
from apps.api.runtime_submit_idempotency import begin_submit_idempotency, stable_submit_request_id
from apps.api.runtime_service import create_runtime_app
from apps.api.runtime_store import RuntimeStore


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_keyframe_submit_replays_same_request_and_conflicts_changed_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_IMAGE", raising=False)
    runtime_root = tmp_path / "runtime"
    client = TestClient(create_runtime_app(runtime_root=runtime_root))
    project_id = "idem-keyframe"
    request = _keyframe_request(generated_at="2026-07-03T10:00:00+00:00")
    headers = {"X-Client-Request-ID": "idem-keyframe-submit-001"}

    first = client.post(f"/projects/{project_id}/keyframe-generations", json=request, headers=headers)
    assert first.status_code == 200
    first_payload = first.json()
    first_job_id = first_payload["job"]["job_id"]

    def fail_build(*_args, **_kwargs):
        raise AssertionError("idempotent replay/conflict must not rebuild keyframe generation")

    monkeypatch.setattr(runtime_keyframe_routes, "build_keyframe_generation", fail_build)
    replay = client.post(
        f"/projects/{project_id}/keyframe-generations",
        json={**request, "generated_at": "2026-07-03T10:00:01+00:00"},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json() == first_payload
    assert replay.json()["job"]["job_id"] == first_job_id

    changed = client.post(
        f"/projects/{project_id}/keyframe-generations",
        json={**request, "prompt_text": "A changed keyframe prompt."},
        headers=headers,
    )
    _assert_idempotency_conflict(changed, existing_job_id=first_job_id)
    _assert_completed_ledger(runtime_root, project_id, "keyframe_generation", first_job_id)


def test_video_submit_replays_same_request_and_conflicts_changed_payload(tmp_path, monkeypatch) -> None:
    config = _fake_video_provider_config(tmp_path)
    monkeypatch.setenv("AFS_PROVIDER_CONFIG", str(config))
    monkeypatch.delenv("AFS_ALLOW_REMOTE_VIDEO", raising=False)
    runtime_root = tmp_path / "runtime"
    client = TestClient(create_runtime_app(runtime_root=runtime_root))
    project_id = "idem-video"
    client.post("/projects", json={"project_id": project_id, "goal": "Video idempotency"}).raise_for_status()
    frame_id = _upload_image(client, project_id)
    request = _video_request(frame_id, generated_at="2026-07-03T10:10:00+00:00")
    headers = {"X-Client-Request-ID": "idem-video-submit-001"}

    first = client.post(f"/projects/{project_id}/video-generations", json=request, headers=headers)
    assert first.status_code == 200
    first_payload = first.json()
    first_job_id = first_payload["job"]["job_id"]

    def fail_submit(*_args, **_kwargs):
        raise AssertionError("idempotent replay/conflict must not resubmit video generation")

    monkeypatch.setattr(runtime_video_routes, "submit_video_generation", fail_submit)
    replay = client.post(
        f"/projects/{project_id}/video-generations",
        json={**request, "generated_at": "2026-07-03T10:10:01+00:00"},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json() == first_payload
    assert replay.json()["job"]["job_id"] == first_job_id

    changed = client.post(
        f"/projects/{project_id}/video-generations",
        json={**request, "motion": "A changed camera move."},
        headers=headers,
    )
    _assert_idempotency_conflict(changed, existing_job_id=first_job_id)
    _assert_completed_ledger(runtime_root, project_id, "video_generation", first_job_id)


def test_generation_comparison_submit_replays_same_request_and_conflicts_changed_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_IMAGE", raising=False)
    runtime_root = tmp_path / "runtime"
    client = TestClient(create_runtime_app(runtime_root=runtime_root))
    project_id = "idem-comparison"
    request = _comparison_request(generated_at="2026-07-03T10:20:00+00:00")
    headers = {"X-Client-Request-ID": "idem-comparison-submit-001"}

    first = client.post(f"/projects/{project_id}/generation-comparisons", json=request, headers=headers)
    assert first.status_code == 200
    first_payload = first.json()
    first_job_id = first_payload["job"]["job_id"]

    def fail_report(*_args, **_kwargs):
        raise AssertionError("idempotent replay/conflict must not rebuild comparison report")

    monkeypatch.setattr(runtime_generation_comparisons, "build_generation_comparison_report", fail_report)
    replay = client.post(
        f"/projects/{project_id}/generation-comparisons",
        json={**request, "generated_at": "2026-07-03T10:20:01+00:00"},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json() == first_payload
    assert replay.json()["job"]["job_id"] == first_job_id

    changed = client.post(
        f"/projects/{project_id}/generation-comparisons",
        json={**request, "optimized_prompt": "A changed comparison prompt."},
        headers=headers,
    )
    _assert_idempotency_conflict(changed, existing_job_id=first_job_id)
    _assert_completed_ledger(runtime_root, project_id, "generation_comparison", first_job_id)


def test_submit_idempotency_uses_short_path_tokens_for_long_runtime_ids(tmp_path) -> None:
    store = RuntimeStore(tmp_path / "runtime")
    project_id = "video-generation-asset-graph-feedback-with-extra-long-project-name"
    action = "video_generation_with_extra_long_action_name"
    client_request_id = "client-request-id-" + ("x" * 120)

    reservation = begin_submit_idempotency(
        store,
        project_id=project_id,
        action=action,
        request={"prompt_text": "stable", "generated_at": "2026-07-15T10:00:00+08:00"},
        client_request_id=client_request_id,
        request_id="req_long_path",
    )

    assert reservation.state == "reserved"
    assert reservation.ledger["project_id"] == project_id
    assert reservation.ledger["action"] == action
    assert reservation.ledger["stable_request_id"] == stable_submit_request_id(client_request_id, reservation.fingerprint)
    assert all(len(part) <= 28 for part in reservation.ledger_dir.parts[-3:-1])
    assert len(reservation.ledger_dir.parts[-1]) <= 24
    assert (reservation.ledger_dir / "ledger.json").is_file()


def test_runtime_store_uses_short_storage_paths_for_long_job_ids(tmp_path) -> None:
    store = RuntimeStore(tmp_path / "runtime")
    project_id = "video-generation-asset-graph-feedback"
    job_id = f"{project_id}-video_generation-" + ("x" * 80)

    run_dir = store.run_dir(project_id, job_id)
    job_path = store.job_path(job_id)

    assert run_dir.parts[-2] == project_id
    assert run_dir.parts[-1] != job_id
    assert len(run_dir.parts[-1]) <= 24
    assert job_path.name != f"{job_id}.json"
    assert len(job_path.stem) <= 80


def _assert_idempotency_conflict(response, *, existing_job_id: str) -> None:
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "idempotency_conflict"
    assert detail["stage"] == "idempotency"
    assert detail["status"] == "failed"
    assert detail["retryable"] is False
    assert detail["provider_calls_started"] is False
    assert detail["details"]["provider_calls_started"] is False
    assert detail["details"]["existing_job_id"] == existing_job_id


def _assert_completed_ledger(runtime_root: Path, project_id: str, action: str, job_id: str) -> None:
    ledgers = list((runtime_root / "submit_idempotency" / project_id / action).glob("*/ledger.json"))
    assert len(ledgers) == 1
    ledger = json.loads(ledgers[0].read_text(encoding="utf-8"))
    assert ledger["status"] == "completed"
    assert ledger["job_id"] == job_id
    assert ledger["provider_calls_started"] is False
    assert len(ledger["fingerprint"]) == 64
    assert len(ledger["response_sha256"]) == 64


def _keyframe_request(*, generated_at: str) -> dict[str, object]:
    return {
        "node_id": "idem_keyframe_node",
        "prompt_text": "A controlled keyframe of a local studio scene.",
        "optimized_prompt": "Controlled local studio scene, stable visual plan.",
        "provider_service_id": "image_relay",
        "candidate_count": 1,
        "generated_at": generated_at,
    }


def _comparison_request(*, generated_at: str) -> dict[str, object]:
    return {
        "node_id": "idem_comparison_node",
        "prompt_text": "A controlled keyframe of a local studio scene.",
        "optimized_prompt": "Controlled local studio scene, stable visual plan.",
        "provider_service_id": "image_relay",
        "candidate_count": 1,
        "generated_at": generated_at,
    }


def _video_request(first_frame_image_asset_id: str, *, generated_at: str) -> dict[str, object]:
    return {
        "node_id": "idem_video_node",
        "prompt_text": "A slow camera push in from the first frame.",
        "provider_service_id": "fake_video",
        "first_frame_image_asset_id": first_frame_image_asset_id,
        "duration_sec": 5,
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "candidate_count": 1,
        "generated_at": generated_at,
    }


def _upload_image(client: TestClient, project_id: str) -> str:
    upload = client.post(
        f"/projects/{project_id}/image-assets",
        json={
            "node_id": "first_frame_upload",
            "filename": "first-frame.png",
            "mime_type": "image/png",
            "data_base64": base64.b64encode(PNG_BYTES).decode("ascii"),
            "role": "first_frame",
            "generated_at": "2026-07-03T10:00:00+00:00",
        },
    )
    upload.raise_for_status()
    return upload.json()["asset"]["asset_id"]


def _fake_video_provider_config(tmp_path: Path) -> Path:
    payload = {
        "schema_version": "company_provider_secrets.local.v2",
        "accounts": {
            "fake_video_account": {
                "auth_type": "none",
                "base_url": "https://video.example.test",
                "default_models": {"video": "fake-video"},
            }
        },
        "account_pools": {
            "video_pool": {
                "accounts": [
                    {
                        "account_id": "fake_video_account",
                        "service_id": "fake_video",
                        "enabled_capabilities": ["video"],
                        "enabled": True,
                        "priority": 10,
                        "weight": 1,
                        "concurrency_limit": 1,
                        "health_state": "healthy",
                    }
                ]
            }
        },
        "services": {
            "fake_video": {
                "provider": "fake",
                "account_ref": "fake_video_account",
                "capability": "video",
                "required_gate": "AFS_ALLOW_REMOTE_VIDEO",
                "descriptor": {
                    "schema_version": "provider_descriptor.v0.2",
                    "modality": "video",
                    "execution_mode": "async",
                    "capabilities": ["video"],
                    "account_pool_id": "video_pool",
                    "reference_image_slots": 1,
                    "supported_aspect_ratios": ["9:16"],
                    "prompt_char_limit": 2000,
                    "seed_supported": False,
                    "cost_hint": "fake-only",
                    "rate_limit_hint": "fake-only",
                    "required_gate": "AFS_ALLOW_REMOTE_VIDEO",
                    "frame_slots": {"first_frame": "required"},
                    "frame_modes": ["first_frame"],
                    "supported_durations_sec": [5],
                    "supported_resolutions": ["720p"],
                    "async_poll_interval_sec": 0.1,
                    "async_timeout_sec": 10,
                    "async_max_polls": 1,
                    "prompt_profile": "video_i2v_v1",
                    "cost_estimate": {"unit": "task"},
                },
            }
        },
    }
    path = tmp_path / "providers.local.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
