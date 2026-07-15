from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.runtime_service import create_runtime_app
from apps.api.runtime_store import RuntimeStore


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
UNSAFE_FRAGMENTS = (
    "bearer ",
    "api_key",
    "secret-value",
    "provider_result_url",
    "data_base64",
    "d:\\",
    "c:\\",
)


def test_keyframe_generation_response_and_artifacts_pass_leak_assertions(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_ALLOW_REMOTE_IMAGE", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "manifest-keyframe-safety"

    response = client.post(
        f"/projects/{project_id}/keyframe-generations",
        json={
            "node_id": "image_1",
            "prompt_text": "A quiet studio portrait.",
            "optimized_prompt": "A quiet studio portrait.",
            "candidate_count": 1,
            "generated_at": "2026-06-14T10:00:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["safe_manifest"]["provider_calls_started"] is False
    assert payload["safe_manifest"]["raw_provider_response_stored"] is False
    assert payload["safe_manifest"]["generated_media_bytes_returned"] is False
    _assert_safe_payload(payload)
    for artifact in payload["artifacts"].values():
        artifact_payload = client.get(f"/artifacts/{artifact['artifact_id']}").json()["payload"]
        _assert_safe_payload(artifact_payload)


def test_video_generation_response_task_state_and_manifest_pass_leak_assertions(tmp_path, monkeypatch) -> None:
    config = _fake_video_provider_config(tmp_path)
    monkeypatch.setenv("AFS_PROVIDER_CONFIG", str(config))
    monkeypatch.delenv("AFS_ALLOW_REMOTE_VIDEO", raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path / "runtime"))
    project_id = "manifest-video-safety"
    frame_id = _upload_image(client, project_id)

    response = client.post(
        f"/projects/{project_id}/video-generations",
        json={
            "node_id": "video_1",
            "prompt_text": "A slow camera push in.",
            "provider_service_id": "fake_video",
            "first_frame_image_asset_id": frame_id,
            "duration_sec": 5,
            "resolution": "720p",
            "candidate_count": 1,
            "generated_at": "2026-06-14T10:00:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["safe_manifest"]["provider_calls_started"] is False
    assert payload["safe_manifest"]["provider_raw_response_stored"] is False
    assert payload["safe_manifest"]["provider_urls_persisted"] is False
    assert payload["safe_manifest"]["media_bytes_returned_by_api"] is False
    _assert_safe_payload(payload)

    run_dir = RuntimeStore(tmp_path / "runtime").run_dir(project_id, payload["job"]["job_id"])
    manifest = json.loads((run_dir / "video_generation_safe_manifest.json").read_text(encoding="utf-8"))
    _assert_safe_payload(manifest)
    assert not (run_dir / "video_task_state.json").exists()


def _upload_image(client: TestClient, project_id: str) -> str:
    upload = client.post(
        f"/projects/{project_id}/image-assets",
        json={
            "node_id": "image_1",
            "filename": "frame.png",
            "mime_type": "image/png",
            "data_base64": base64.b64encode(PNG_BYTES).decode("ascii"),
            "role": "first_frame",
            "generated_at": "2026-06-14T10:00:00+08:00",
        },
    )
    assert upload.status_code == 200
    return upload.json()["asset"]["asset_id"]


def _assert_safe_payload(payload: object) -> None:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for fragment in UNSAFE_FRAGMENTS:
        assert fragment not in serialized


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
                    "supported_aspect_ratios": ["16:9", "9:16"],
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
