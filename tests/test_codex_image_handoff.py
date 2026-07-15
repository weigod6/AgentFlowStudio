from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentflow_studio.model_gateway import codex_image_worker
from agentflow_studio.model_gateway.codex_image_worker import (
    CodexExecImageExecutor,
    FakeCodexImageExecutor,
    process_one,
    recover_stale_running_jobs,
)
from agentflow_studio.model_gateway.company_secrets import load_company_provider_secrets
from agentflow_studio.model_gateway.provider_adapter import ProviderDispatchRequest, ProviderRegistry
from apps.api.runtime_service import create_runtime_app
from apps.api.runtime_generated_image_assets import register_generated_image_asset
from apps.api.runtime_store import RuntimeStore


class FakeCodexProcess:
    def __init__(self, returncode: int | None = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self) -> tuple[str, str]:
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int | None:  # noqa: ARG002 - fake process protocol.
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_codex_image_handoff_provider_lifecycle_is_file_based_and_safe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    store = _store(tmp_path, _codex_provider_config())
    registry = ProviderRegistry.from_store(store)
    output_dir = tmp_path / "run"
    reference = tmp_path / "reference.png"
    reference.write_bytes(FakeCodexImageExecutor.PNG_BYTES)

    task = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(
            prompt="Generate a clean cinematic keyframe.",
            output_dir=output_dir,
            aspect_ratio="9:16",
            reference_image_paths=(reference,),
            subject_reference_image_path=reference,
            candidate_count=1,
            seed=120617,
        ),
    )

    assert task["task"]["status"] == "submitted"
    request_path = next((output_dir / "codex_image_job" / "pending").glob("*/request.json"))
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    serialized = json.dumps(request_payload, ensure_ascii=False).lower()
    assert request_payload["created_at"]
    assert request_payload["reference_images"][0]["path"] == "references/reference_001.png"
    assert "默认写实照片风格" in request_payload["prompt"]
    assert str(reference).lower() not in serialized
    assert "c:\\" not in serialized
    assert "d:\\" not in serialized
    assert "api_key" not in serialized
    assert "http://" not in serialized
    assert "https://" not in serialized

    pending = registry.poll("image", "codex_image", task)
    assert pending["status"] == "pending"
    assert pending["progress"]["mode"] == "queued"
    assert pending["progress"]["created_at"]
    assert pending["progress"]["elapsed_sec"] >= 0
    assert pending["provider_calls_started"] is True

    processed = process_one(output_dir, executor=FakeCodexImageExecutor())
    assert processed is not None
    assert processed.status == "succeeded"
    assert {item.name for item in processed.job_dir.iterdir()} == {"result.json"}

    result = registry.poll("image", "codex_image", task)
    assert result["status"] == "succeeded"
    assert result["outputs"][0]["status"] == "succeeded"
    assert result["progress"]["mode"] == "complete"
    assert result["progress"]["elapsed_sec"] >= 0
    assert result["progress"]["queued_sec"] >= 0
    assert result["progress"]["running_sec"] >= 0
    assert result["provider_calls_started"] is True
    assert result["provider_raw_response_stored"] is False
    assert result["outputs"][0]["candidate_id"] == "candidate_001"
    assert result["outputs"][0]["image_path"] == "image_candidates/candidate_001.png"
    assert (output_dir / "image_candidates" / "candidate_001.png").read_bytes() == FakeCodexImageExecutor.PNG_BYTES
    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert str(output_dir).lower() not in serialized
    assert "c:\\" not in serialized
    assert "d:\\" not in serialized


def test_codex_image_worker_prompt_has_non_visual_job_nonce() -> None:
    prompt = codex_image_worker._worker_prompt(  # noqa: SLF001 - guard regression for the worker prompt contract.
        {
            "job_id": "codex_img_parallel_001",
            "prompt": "帮我生成一只黑色的狸花猫",
            "aspect_ratio": "9:16",
            "reference_images": [],
        }
    )

    assert "Create a fresh image for this specific job" in prompt
    assert "Do not reuse a previous job output" in prompt
    assert "default to a realistic photographic image" in prompt
    assert "Non-visual job nonce, do not draw or write this text: codex_img_parallel_001" in prompt
    assert "帮我生成一只黑色的狸花猫" in prompt


def test_codex_image_handoff_runtime_poll_route_completes_after_worker(tmp_path, monkeypatch) -> None:
    provider_path = tmp_path / "providers.local.json"
    provider_path.write_text(json.dumps(_codex_provider_config()), encoding="utf-8")
    monkeypatch.setenv("AFS_PROVIDER_CONFIG", str(provider_path))
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    request = {
        "node_id": "image-node-1",
        "prompt_text": "Generate a controlled character keyframe.",
        "optimized_prompt": "A controlled character keyframe, cinematic lighting.",
        "target_platform": "short_video",
        "style": "cinematic",
        "aspect_ratio": "9:16",
        "candidate_count": 1,
        "provider_service_id": "codex_image",
        "seed": 120617,
        "generated_at": "2026-06-17T10:26:00+08:00",
    }
    submit = _submit_keyframe_with_preflight(client, "proj_codex_image", request)
    assert submit.status_code == 200
    submitted_payload = submit.json()
    job_id = submitted_payload["job"]["job_id"]
    assert submitted_payload["job"]["status"] == "submitted"
    assert submitted_payload["candidate_previews"] == []

    pending = client.post(f"/projects/proj_codex_image/keyframe-generations/{job_id}/poll")
    assert pending.status_code == 200
    assert pending.json()["job"]["status"] == "pending"
    assert pending.json()["job"]["progress"]["mode"] == "queued"
    assert pending.json()["job"]["progress"]["elapsed_sec"] >= 0

    processed = process_one(tmp_path, executor=FakeCodexImageExecutor())
    assert processed is not None
    assert processed.status == "succeeded"
    assert {item.name for item in processed.job_dir.iterdir()} == {"result.json"}

    completed = client.post(f"/projects/proj_codex_image/keyframe-generations/{job_id}/poll")
    assert completed.status_code == 200
    payload = completed.json()
    assert payload["job"]["status"] == "succeeded"
    assert payload["job"]["progress"]["mode"] == "complete"
    assert payload["job"]["progress"]["elapsed_sec"] >= 0
    assert payload["job"]["progress"]["running_sec"] >= 0
    assert payload["provider_calls_started"] is True
    assert payload["candidate_previews"][0]["preview_url"].endswith("/candidate_001/preview")
    assert payload["reusable_image_assets"][0]["source_candidate_id"] == "candidate_001"
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "codex_image_job" not in serialized
    assert "request.json" not in serialized
    assert "c:\\" not in serialized
    assert "d:\\" not in serialized

    preview = client.get(payload["candidate_previews"][0]["preview_url"])
    assert preview.status_code == 200
    assert preview.content == FakeCodexImageExecutor.PNG_BYTES

    repeated = client.post(f"/projects/proj_codex_image/keyframe-generations/{job_id}/poll")
    assert repeated.status_code == 200
    assert repeated.json()["reusable_image_assets"][0]["asset_id"] == payload["reusable_image_assets"][0]["asset_id"]
    assets = client.get("/projects/proj_codex_image/image-assets").json()["assets"]
    generated = [
        item
        for item in assets
        if item.get("source_job_id") == job_id and item.get("source_candidate_id") == "candidate_001"
    ]
    assert len(generated) == 1


def test_codex_image_handoff_runtime_poll_recovers_terminal_failed_state_after_worker_success(
    tmp_path,
    monkeypatch,
) -> None:
    provider_path = tmp_path / "providers.local.json"
    provider_path.write_text(json.dumps(_codex_provider_config()), encoding="utf-8")
    monkeypatch.setenv("AFS_PROVIDER_CONFIG", str(provider_path))
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    project_id = "proj_codex_terminal_recovery"

    request = {
        "node_id": "image-node-1",
        "prompt_text": "Generate a controlled tabby cat keyframe.",
        "optimized_prompt": "A controlled tabby cat keyframe, cinematic lighting.",
        "provider_service_id": "codex_image",
        "generated_at": "2026-06-17T10:26:00+08:00",
    }
    submit = _submit_keyframe_with_preflight(client, project_id, request)
    assert submit.status_code == 200
    job_id = submit.json()["job"]["job_id"]
    output_dir = RuntimeStore(tmp_path).run_dir(project_id, job_id)
    pending = client.post(f"/projects/{project_id}/keyframe-generations/{job_id}/poll")
    assert pending.status_code == 200
    assert pending.json()["job"]["status"] == "pending"
    assert pending.json()["job"]["progress"]["mode"] == "queued"

    processed = process_one(output_dir, executor=FakeCodexImageExecutor())
    assert processed is not None
    assert processed.status == "succeeded"
    state_path = output_dir / "keyframe_task_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "failed"
    state["last_provider_poll"] = {"status": "running", "provider_raw_persisted": False}
    state_path.write_text(json.dumps(state), encoding="utf-8")

    recovered = client.post(f"/projects/{project_id}/keyframe-generations/{job_id}/poll")

    assert recovered.status_code == 200
    payload = recovered.json()
    assert payload["job"]["status"] == "succeeded"
    assert payload["candidate_previews"][0]["preview_url"].endswith("/candidate_001/preview")
    assert payload["safe_manifest"]["status"] == "succeeded"
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "succeeded"


def test_codex_image_handoff_poll_fails_safely_when_provider_config_disappears(tmp_path, monkeypatch) -> None:
    provider_path = tmp_path / "providers.local.json"
    provider_path.write_text(json.dumps(_codex_provider_config()), encoding="utf-8")
    monkeypatch.setenv("AFS_PROVIDER_CONFIG", str(provider_path))
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    request = {
        "node_id": "image-node-1",
        "prompt_text": "Generate a controlled keyframe.",
        "optimized_prompt": "A controlled keyframe.",
        "provider_service_id": "codex_image",
        "generated_at": "2026-06-17T10:26:00+08:00",
    }
    submit = _submit_keyframe_with_preflight(client, "proj_codex_missing_config", request)
    assert submit.status_code == 200
    job_id = submit.json()["job"]["job_id"]
    monkeypatch.delenv("AFS_PROVIDER_CONFIG", raising=False)

    poll = client.post(f"/projects/proj_codex_missing_config/keyframe-generations/{job_id}/poll")

    assert poll.status_code == 200
    payload = poll.json()
    assert payload["job"]["status"] == "failed"
    assert payload["safe_manifest"]["blocks"][0]["block_id"] == "remote_image_provider_not_ready"
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert str(provider_path).lower() not in serialized
    assert "c:\\" not in serialized
    assert "d:\\" not in serialized


def test_codex_image_handoff_worker_trims_failed_job_payload_after_result(tmp_path, monkeypatch) -> None:
    class FailingExecutor:
        def execute(self, request, work_dir):  # noqa: ANN001 - test protocol double.
            raise RuntimeError("planned failure")

    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    store = _store(tmp_path, _codex_provider_config())
    registry = ProviderRegistry.from_store(store)
    output_dir = tmp_path / "run"

    task = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(
            prompt="Generate a clean cinematic keyframe.",
            output_dir=output_dir,
            aspect_ratio="9:16",
            candidate_count=1,
        ),
    )
    processed = process_one(output_dir, executor=FailingExecutor())

    assert processed is not None
    assert processed.status == "failed"
    assert {item.name for item in processed.job_dir.iterdir()} == {"result.json"}
    result = registry.poll("image", "codex_image", task)
    assert result["status"] == "failed"
    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "request.json" not in serialized
    assert "worker_prompt.md" not in serialized
    assert "references/" not in serialized


def test_codex_image_worker_resolves_user_local_codex_when_path_is_missing(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    codex = home / ".local" / "bin" / "codex"
    codex.parent.mkdir(parents=True)
    codex.write_text("#!/bin/sh\n", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    captured: dict[str, list[str]] = {}

    def fake_popen(command, *, cwd, **kwargs):  # noqa: ANN001, ANN202
        captured["command"] = list(command)
        (Path(cwd) / "candidate_001.png").write_bytes(FakeCodexImageExecutor.PNG_BYTES)
        return FakeCodexProcess(returncode=0)

    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.shutil.which", lambda _command: None)
    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.Path.home", lambda: home)
    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.subprocess.Popen", fake_popen)

    produced = CodexExecImageExecutor(timeout_sec=1).execute(_image_worker_request(), work_dir)

    assert produced == work_dir / "candidate_001.png"
    assert captured["command"][0] == str(codex)


def test_codex_image_worker_reports_missing_cli_as_safe_runtime_error(tmp_path, monkeypatch) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    def missing_codex(*args, **kwargs):  # noqa: ANN001, ANN202
        raise FileNotFoundError("codex")

    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.shutil.which", lambda _command: None)
    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.Path.home", lambda: tmp_path / "missing-home")
    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.subprocess.Popen", missing_codex)

    with pytest.raises(RuntimeError, match="Codex image worker command is not available"):
        CodexExecImageExecutor(timeout_sec=1).execute(_image_worker_request(), work_dir)


def test_codex_image_worker_recovers_stale_running_jobs_without_blocking_queue(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    store = _store(tmp_path, _codex_provider_config())
    registry = ProviderRegistry.from_store(store)
    output_dir = tmp_path / "run"

    first = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(prompt="First job.", output_dir=output_dir, candidate_count=1),
    )
    second = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(prompt="Second job.", output_dir=output_dir, candidate_count=1),
    )
    pending_dir = output_dir / "codex_image_job" / "pending" / first["task"]["job_id"]
    running_dir = output_dir / "codex_image_job" / "running" / first["task"]["job_id"]
    running_dir.parent.mkdir(parents=True, exist_ok=True)
    pending_dir.rename(running_dir)
    assert running_dir.is_dir()
    old_time = time.time() - 7200
    for path in [running_dir, *running_dir.iterdir()]:
        os.utime(path, (old_time, old_time))

    recovered = recover_stale_running_jobs(output_dir, stale_running_sec=3600)
    processed = process_one(output_dir, executor=FakeCodexImageExecutor(), stale_running_sec=3600)

    assert [item.status for item in recovered] == ["failed"]
    assert processed is not None
    assert processed.job_id == second["task"]["job_id"]
    assert processed.status == "succeeded"
    assert registry.poll("image", "codex_image", first)["status"] == "failed"
    assert registry.poll("image", "codex_image", second)["status"] == "succeeded"


def test_codex_image_worker_skips_job_claimed_by_another_worker(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    store = _store(tmp_path, _codex_provider_config())
    registry = ProviderRegistry.from_store(store)
    output_dir = tmp_path / "run"

    first = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(prompt="First queued job.", output_dir=output_dir, candidate_count=1),
    )
    second = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(prompt="Second queued job.", output_dir=output_dir, candidate_count=1),
    )
    first_pending = output_dir / "codex_image_job" / "pending" / first["task"]["job_id"]
    first_running = output_dir / "codex_image_job" / "running" / first["task"]["job_id"]
    original_rename = Path.rename
    raced = {"done": False}

    def race_once(self, target):  # noqa: ANN001, ANN202 - pathlib monkeypatch for worker claim race.
        if not raced["done"] and self == first_pending:
            raced["done"] = True
            first_running.parent.mkdir(parents=True, exist_ok=True)
            original_rename(self, first_running)
            raise FileNotFoundError("already claimed")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", race_once)

    processed = process_one(output_dir, executor=FakeCodexImageExecutor())

    assert processed is not None
    assert processed.job_id == second["task"]["job_id"]
    assert processed.status == "succeeded"
    assert first_running.is_dir()


def test_codex_image_handoff_reports_pending_queue_position(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    registry = ProviderRegistry.from_store(_store(tmp_path, _codex_provider_config()))
    output_dir = tmp_path / "run"
    first = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(prompt="First queued job.", output_dir=output_dir, candidate_count=1),
    )
    second = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(prompt="Second queued job.", output_dir=output_dir, candidate_count=1),
    )

    first_poll = registry.poll("image", "codex_image", first)
    second_poll = registry.poll("image", "codex_image", second)

    assert first_poll["status"] == "pending"
    assert first_poll["progress"]["mode"] == "queued"
    assert first_poll["progress"]["queue_position"] == 1
    assert first_poll["progress"]["pending_count"] == 2
    assert first_poll["progress"]["elapsed_sec"] >= 0
    assert second_poll["status"] == "pending"
    assert second_poll["progress"]["queue_position"] == 2


def test_codex_image_handoff_poll_keeps_active_running_candidate_with_worker(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    registry = ProviderRegistry.from_store(_store(tmp_path, _codex_provider_config()))
    output_dir = tmp_path / "run"
    task = registry.submit(
        "image",
        "codex_image",
        ProviderDispatchRequest(prompt="Recover stable candidate.", output_dir=output_dir, candidate_count=1),
    )
    job_id = task["task"]["job_id"]
    pending_dir = output_dir / "codex_image_job" / "pending" / job_id
    running_dir = output_dir / "codex_image_job" / "running" / job_id
    running_dir.parent.mkdir(parents=True, exist_ok=True)
    pending_dir.rename(running_dir)
    candidate = running_dir / "candidate_001.png"
    candidate.write_bytes(FakeCodexImageExecutor.PNG_BYTES)
    old_time = time.time() - 10
    os.utime(candidate, (old_time, old_time))

    result = registry.poll("image", "codex_image", task)

    assert result["status"] == "running"
    assert result["progress"]["mode"] == "indeterminate"
    assert result["progress"]["elapsed_sec"] >= 0
    assert result["outputs"] == []
    completed_dir = output_dir / "codex_image_job" / "completed" / job_id
    failed_dir = output_dir / "codex_image_job" / "failed" / job_id
    assert running_dir.is_dir()
    assert not completed_dir.exists()
    assert not failed_dir.exists()
    assert not (output_dir / "image_candidates" / "candidate_001.png").exists()


def test_codex_image_executor_returns_candidate_written_before_timeout(tmp_path, monkeypatch) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    process = FakeCodexProcess(returncode=None)

    def fake_popen(command, *, cwd, **kwargs):  # noqa: ANN001, ANN202
        (Path(cwd) / "candidate_001.png").write_bytes(FakeCodexImageExecutor.PNG_BYTES)
        return process

    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.shutil.which", lambda command: command)
    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.subprocess.Popen", fake_popen)

    produced = CodexExecImageExecutor(timeout_sec=1, poll_interval_sec=0.1, candidate_settled_sec=0).execute(
        _image_worker_request(),
        work_dir,
    )

    assert produced == work_dir / "candidate_001.png"
    assert process.terminated is True


def test_generated_image_asset_id_is_stable_for_same_job_candidate(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    project_id = "proj_stable_generated_asset"
    store.ensure_project_manifest(project_id)
    image_path = tmp_path / "runs" / project_id / "job_1" / "image_candidates" / "candidate_001.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(FakeCodexImageExecutor.PNG_BYTES)

    first = register_generated_image_asset(
        store,
        project_id,
        source_node_id="image_1",
        source_job_id="job_1",
        source_candidate_id="candidate_001",
        image_path=image_path,
        source_candidate_status="succeeded",
    )
    second = register_generated_image_asset(
        store,
        project_id,
        source_node_id="image_1",
        source_job_id="job_1",
        source_candidate_id="candidate_001",
        image_path=image_path,
        source_candidate_status="succeeded",
    )

    assert first["asset"]["asset_id"] == second["asset"]["asset_id"]
    assert first["asset"]["asset_id"].startswith("img_gen_")


def test_codex_image_executor_uses_job_scoped_codex_home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    codex = home / ".local" / "bin" / "codex"
    codex.parent.mkdir(parents=True)
    codex.write_text("#!/bin/sh\n", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    captured: dict[str, dict[str, str]] = {}

    def fake_popen(command, *, cwd, env, **kwargs):  # noqa: ANN001, ANN202
        captured["env"] = dict(env)
        (Path(cwd) / "candidate_001.png").write_bytes(FakeCodexImageExecutor.PNG_BYTES)
        return FakeCodexProcess(returncode=0)

    monkeypatch.setenv("AFS_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.shutil.which", lambda _command: None)
    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.Path.home", lambda: home)
    monkeypatch.setattr("agentflow_studio.model_gateway.codex_image_worker.subprocess.Popen", fake_popen)

    CodexExecImageExecutor(timeout_sec=1).execute(_image_worker_request(), work_dir)

    codex_home = Path(captured["env"]["CODEX_HOME"])
    assert codex_home.parent == work_dir
    assert codex_home.name == ".codex-home"


def _store(tmp_path: Path, payload: dict):
    path = tmp_path / "providers.local.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_company_provider_secrets(path)


def _submit_keyframe_with_preflight(client: TestClient, project_id: str, request: dict[str, object]):
    preflight = client.post(f"/projects/{project_id}/keyframe-generations/preflight", json=request)
    assert preflight.status_code == 200
    return client.post(
        f"/projects/{project_id}/keyframe-generations",
        json={**request, "preflight_token": preflight.json()["preflight_token"]},
    )


def _image_worker_request() -> dict:
    return {
        "job_id": "codex_img_test",
        "service_id": "codex_image",
        "prompt": "Generate a clean cinematic keyframe.",
        "aspect_ratio": "9:16",
        "reference_images": [],
    }


def _codex_provider_config() -> dict:
    return {
        "schema_version": "company_provider_secrets.local.v2",
        "accounts": {
            "local_codex": {
                "auth_type": "none",
                "execution_backend": "codex_exec",
                "cli_command": "codex",
                "default_models": {"image": "codex-image-worker"},
            }
        },
        "account_pools": {
            "codex_image_pool": {
                "accounts": [
                    {
                        "account_id": "local_codex",
                        "service_id": "codex_image",
                        "enabled_capabilities": ["image"],
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
            "codex_image": {
                "provider": "codex_handoff",
                "account_ref": "local_codex",
                "capability": "image",
                "required_gate": "AFS_ALLOW_REMOTE_IMAGE",
                "descriptor": {
                    "schema_version": "provider_descriptor.v0.1",
                    "modality": "image",
                    "execution_mode": "async",
                    "capabilities": ["image"],
                    "account_pool_id": "codex_image_pool",
                    "reference_image_slots": 4,
                    "supported_aspect_ratios": ["1:1", "4:3", "3:4", "16:9", "9:16"],
                    "prompt_char_limit": 4000,
                    "seed_supported": True,
                    "cost_hint": "Server-local Codex image worker; downstream model cost depends on server configuration.",
                    "rate_limit_hint": "Run one worker per runtime root for MVP validation.",
                    "required_gate": "AFS_ALLOW_REMOTE_IMAGE",
                    "async_poll_interval_sec": 2,
                    "async_timeout_sec": 900,
                    "async_max_polls": 450,
                },
            }
        },
    }
