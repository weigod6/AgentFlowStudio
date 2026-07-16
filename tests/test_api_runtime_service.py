from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.runtime_episode_domain_store import EpisodeDomainAggregateStore
from apps.api.runtime_errors import response_contains_unsafe_marker
from apps.api.runtime_info import runtime_root_is_persisted
from apps.api.runtime_service import create_runtime_app
from apps.api.runtime_store import RuntimeStore


def test_runtime_service_reports_health_and_capabilities_without_secrets(tmp_path, monkeypatch) -> None:
    for name in (
        "AFS_ALLOW_REMOTE_LLM",
        "AFS_ALLOW_REMOTE_IMAGE",
        "AFS_ALLOW_REMOTE_VIDEO",
        "AFS_ALLOW_REMOTE_VISION",
        "AFS_ALLOW_REMOTE_ASR",
        "AFS_ALLOW_EXTERNAL_DOWNLOAD",
        "AFS_PROVIDER_CONFIG",
        "AFS_AUTH_ENABLED",
        "AFS_RUNTIME_SERVICE_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    health = client.get("/health").json()
    capabilities = client.get("/capabilities").json()
    serialized = json.dumps({"health": health, "capabilities": capabilities}, ensure_ascii=False).lower()

    assert health["service"] == "agentflow_runtime_service"
    assert health["status"] == "ready"
    assert health["service_health"] == {
        "status": "ready",
        "scope": "process_health_only",
        "claims_acceptance_ready": False,
    }
    assert health["runtime_root_persisted"] is runtime_root_is_persisted(tmp_path)
    assert health["studio_static"] == {
        "mounted": True,
        "root_exists": True,
        "index_exists": True,
        "entry_js_exists": True,
        "status": "ready",
    }
    assert health["provider_gates"] == {
        "llm": False,
        "image": False,
        "video": False,
        "vision": False,
        "asr": False,
        "external_download": False,
    }
    assert health["exposure"] == {
        "bind_host": "127.0.0.1",
        "local_only": True,
        "public_bind": False,
        "auth_required": False,
        "public_edge_verified": False,
        "claim_status": "local_bind_only",
    }
    assert health["readiness"]["service_ready"] is True
    assert health["readiness"]["auth_ready_for_public_edge"] is False
    assert health["readiness"]["runtime_three_end_alignment_evidence"] is False
    assert health["readiness"]["runtime_loaded_code_freshness_claim"] == "not_claimed"
    assert health["readiness"]["public_edge_verified"] is False
    assert health["readiness"]["acceptance_ready"] is False
    assert health["readiness"]["product_readiness"] is False
    assert "runtime_auth_disabled" in health["readiness"]["blocked_or_unverified"]
    assert "not_human_creative_acceptance" in health["readiness"]["non_claims"]
    assert health["boundaries"]["local_only"] is True
    assert health["boundaries"]["public_edge_verified"] is False
    assert health["boundaries"]["runtime_loaded_code_freshness_claim"] == "not_claimed"
    assert health["boundaries"]["acceptance_ready"] is False
    assert capabilities["actions"] == [
        "create_project",
        "list_projects",
        "read_project_manifest",
        "read_artifact",
        "read_job",
        "record_feedback",
        "company_os_gfr_projection",
        "prompt_optimization",
        "script_draft_plan",
        "storyboard_breakdown",
        "image_asset_upload",
        "asset_card_draft",
        "visual_asset_register",
        "video_asset_register",
        "keyframe_generation",
        "video_generation",
        "generation_comparison",
        "studio_state",
        "export_openapi_schema",
    ]
    assert capabilities["studio_flow"]["target_status"] == "ready_for_next_round"
    assert capabilities["studio_flow"]["actions"] == [
        "add_reference",
        "draft_canvas",
        "start_first_generation_check",
        "record_review_note",
        "start_next_round",
        "request_gated_generation",
    ]
    assert "asset_test_run" not in capabilities["actions"]
    assert "two_round_validate" not in capabilities["actions"]
    assert "provider_validation_plan" not in capabilities["actions"]
    assert "api_key" not in serialized
    assert "token" not in serialized
    assert "d:\\" not in serialized
    assert "providers.local" not in serialized
    assert "afs_provider_config" not in serialized
    assert str(tmp_path).lower() not in serialized


def test_runtime_health_public_bind_auth_disabled_is_not_local_or_acceptance_ready(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AFS_AUTH_ENABLED", raising=False)

    client = TestClient(create_runtime_app(runtime_root=tmp_path, runtime_bind_host="0.0.0.0"))
    health = client.get("/health").json()

    assert health["status"] == "ready"
    assert health["auth_required"] is False
    assert health["exposure"]["public_bind"] is True
    assert health["exposure"]["local_only"] is False
    assert health["exposure"]["claim_status"] == "public_bind_without_runtime_auth"
    assert health["boundaries"]["local_only"] is False
    assert health["readiness"]["service_ready"] is True
    assert health["readiness"]["auth_ready_for_public_edge"] is False
    assert health["readiness"]["acceptance_ready"] is False
    assert health["readiness"]["product_readiness"] is False
    assert health["readiness"]["runtime_three_end_alignment_evidence"] is False
    assert health["readiness"]["runtime_loaded_code_freshness_claim"] == "not_claimed"
    assert "public_bind_runtime_auth_disabled" in health["readiness"]["blocked_or_unverified"]


def test_runtime_health_keeps_repo_relative_runtime_root_non_persisted(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=Path("data/processed/runs/runtime_service")))

    health = client.get("/health").json()
    serialized = json.dumps(health, ensure_ascii=False).lower()

    assert health["runtime_root_persisted"] is False
    assert "data/processed/runs" not in serialized


def test_runtime_health_reports_missing_studio_static_without_private_paths(tmp_path) -> None:
    missing_studio_root = tmp_path / "missing-studio"
    client = TestClient(create_runtime_app(runtime_root=tmp_path / "runtime", studio_root=missing_studio_root))

    health = client.get("/health").json()
    serialized = json.dumps(health, ensure_ascii=False).lower()

    assert health["studio_static"] == {
        "mounted": False,
        "root_exists": False,
        "index_exists": False,
        "entry_js_exists": False,
        "status": "missing",
    }
    assert str(tmp_path).lower() not in serialized
    assert "d:\\" not in serialized
    assert "c:\\" not in serialized


def test_runtime_health_provider_gate_projection_is_isolated_and_secret_free(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AFS_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setenv("AFS_ALLOW_REMOTE_IMAGE", "true")
    monkeypatch.setenv("AFS_ALLOW_REMOTE_VIDEO", "true")
    monkeypatch.setenv("AFS_ALLOW_REMOTE_VISION", "true")
    monkeypatch.setenv("AFS_PROVIDER_CONFIG", r"D:\private\providers.local.json")
    monkeypatch.delenv("AFS_ALLOW_REMOTE_ASR", raising=False)

    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    health = client.get("/health").json()
    serialized = json.dumps(health, ensure_ascii=False).lower()

    assert health["provider_gates"] == {
        "llm": True,
        "image": True,
        "video": True,
        "vision": True,
        "asr": False,
        "external_download": False,
    }
    assert "providers.local" not in serialized
    assert "afs_provider_config" not in serialized
    assert "d:\\" not in serialized


def test_runtime_service_allows_local_studio_cors(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    localhost_response = client.options(
        "/health",
        headers={
            "Origin": "http://127.0.0.1:8789",
            "Access-Control-Request-Method": "GET",
        },
    )
    file_response = client.options(
        "/health",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert localhost_response.status_code == 200
    assert localhost_response.headers["access-control-allow-origin"] == "http://127.0.0.1:8789"
    assert file_response.status_code == 200
    assert file_response.headers["access-control-allow-origin"] == "null"


def test_runtime_service_serves_studio_static_entry_without_private_paths(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    redirect = client.get("/studio", follow_redirects=False)
    index = client.get("/studio/")
    favicon_redirect = client.get("/favicon.ico", follow_redirects=False)
    favicon = client.get("/studio/favicon.svg")
    app_js = client.get("/studio/src/main.js")
    serialized = (index.text + app_js.text).lower()

    assert redirect.status_code in {307, 308}
    assert redirect.headers["location"] == "/studio/"
    assert favicon_redirect.status_code in {307, 308}
    assert favicon_redirect.headers["location"] == "/studio/favicon.svg"
    assert favicon.status_code == 200
    assert favicon.headers["cache-control"] == "no-store"
    assert "<svg" in favicon.text
    assert index.status_code == 200
    assert '<div id="app">' in index.text
    assert '<link rel="icon" href="./favicon.svg" type="image/svg+xml" />' in index.text
    assert '<script type="module" src="./src/main.js"></script>' in index.text
    assert app_js.status_code == 200
    assert index.headers["cache-control"] == "no-store"
    assert app_js.headers["cache-control"] == "no-store"
    assert "createStore" in app_js.text
    assert "d:\\" not in serialized
    assert "c:\\" not in serialized
    assert "api_key" not in serialized
    assert "signed_url" not in serialized


def test_runtime_service_serves_site_homepage_as_root_entry(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    home = client.get("/")
    base_css = client.get("/site/styles/site.css")
    preview_css = client.get("/site/styles/site-preview.css")
    responsive_css = client.get("/site/styles/site-responsive.css")
    combined = "\n".join([home.text, base_css.text, preview_css.text, responsive_css.text]).lower()

    assert home.status_code == 200
    assert home.headers["cache-control"] == "no-store"
    assert "<title>AFS Studio" in home.text
    assert '<html lang="zh-CN">' in home.text
    assert 'href="/studio/"' in home.text
    assert "数字内容制作工作空间" in home.text
    assert "进入制作工作空间" in home.text
    assert "让智能制片中枢推进一集内容" in home.text
    assert "studio-wall" in home.text
    assert 'href="/site/social-square.html"' not in home.text
    assert "社交广场" not in home.text
    assert client.get("/site/social-square.html").status_code == 200
    assert base_css.status_code == 200
    assert preview_css.status_code == 200
    assert responsive_css.status_code == 200
    assert base_css.headers["cache-control"] == "no-store"
    assert preview_css.headers["cache-control"] == "no-store"
    assert responsive_css.headers["cache-control"] == "no-store"
    assert response_contains_unsafe_marker(combined) is False
    assert "provider raw" not in combined
    assert "signed_url" not in combined
    assert "d:\\" not in combined
    assert "c:\\" not in combined


def test_runtime_service_does_not_serve_retired_workbench_static_entry(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    assert client.get("/workbench", follow_redirects=False).status_code == 404
    assert client.get("/workbench/").status_code == 404
    assert client.get("/workbench/src/app.js").status_code == 404


def test_runtime_service_creates_project_manifest_and_reads_safe_artifact(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    created = client.post(
        "/projects",
        json={
            "project_id": "proj_runtime_demo",
            "project_type": "short_video_campaign",
            "goal": "Validate local AFS runtime service contract.",
            "status": "in_progress",
        },
    ).json()

    assert created["project_id"] == "proj_runtime_demo"
    assert created["manifest"]["artifact_type"] == "agentflow_project_manifest"
    assert created["artifact"]["artifact_id"]
    assert "path" not in created["artifact"]

    fetched = client.get("/projects/proj_runtime_demo/manifest").json()
    artifact = client.get(f"/artifacts/{created['artifact']['artifact_id']}").json()

    assert fetched["manifest"]["project_id"] == "proj_runtime_demo"
    assert artifact["artifact_type"] == "agentflow_project_manifest"
    assert artifact["payload"]["does_not_store_secrets"] is True
    assert "path" not in json.dumps(artifact, ensure_ascii=False).lower()


def test_studio_project_create_bootstraps_episode_facts_without_raw_aggregate_put(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_AUTH_ALLOW_OPEN_SIGNUP", "true")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    owner = client.post(
        "/auth/register",
        json={
            "email": "owner@example.com",
            "password": "strong-password-123",
            "display_name": "Owner",
            "invite_code": "",
        },
    )
    other = client.post(
        "/auth/register",
        json={
            "email": "other@example.com",
            "password": "strong-password-123",
            "display_name": "Other",
            "invite_code": "",
        },
    )
    assert owner.status_code == 200, owner.text
    assert other.status_code == 200, other.text
    owner_headers = {"Authorization": f"Bearer {owner.json()['session_token']}"}
    other_headers = {"Authorization": f"Bearer {other.json()['session_token']}"}

    created = client.post(
        "/projects",
        headers=owner_headers,
        json={
            "project_id": "creator-ui-project",
            "project_type": "studio_episode_production",
            "goal": "创作者主导的一集制作",
        },
    )
    replay = client.post(
        "/projects",
        headers=owner_headers,
        json={
            "project_id": "creator-ui-project",
            "project_type": "studio_episode_production",
            "goal": "创作者主导的一集制作",
        },
    )

    assert created.status_code == 200, created.text
    assert replay.status_code == 200, replay.text
    assert created.json()["episode_bootstrap"]["created"] is True
    assert replay.json()["episode_bootstrap"]["replayed"] is True
    assert created.json()["episode_bootstrap"]["workspace_entry"] == {
        "episode_id": "episode-001",
        "episode_version_id": "episode-001-v1",
        "href": "/studio/episode-workspace/?project=creator-ui-project&episode=episode-001&version=episode-001-v1",
    }

    aggregate = client.get(
        "/projects/creator-ui-project/episode-production-aggregate",
        headers=owner_headers,
    )
    assert aggregate.status_code == 200, aggregate.text
    payload = aggregate.json()["aggregate"]
    assert payload["scope"]["org_id"] == owner.json()["user"]["user_id"]
    assert payload["scope"]["actor_id"] == owner.json()["user"]["user_id"]
    assert payload["projects"][0]["data_policy"]["visibility"] == "private"
    assert payload["projects"][0]["data_policy"]["training_use"] == "denied_by_default"
    assert [item["entity_id"] for item in payload["shots"]] == [
        "shot-001",
        "shot-002",
        "shot-003",
    ]

    workspace = client.get(
        "/projects/creator-ui-project/episodes/episode-001/versions/episode-001-v1/workspace",
        headers=owner_headers,
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["workspace"]["truth"] == {
        "scene_count": 1,
        "shot_count": 3,
        "duration_seconds": 9.0,
        "missing_asset_count": 0,
        "generation_dispatch_count": 0,
        "playable_preview_available": False,
    }

    assert client.get(
        "/projects/creator-ui-project/episode-production-aggregate",
        headers=other_headers,
    ).status_code == 403

    restarted = TestClient(create_runtime_app(runtime_root=tmp_path))
    recovered = restarted.get(
        "/projects/creator-ui-project/episodes/episode-001/versions/episode-001-v1/workspace",
        headers=owner_headers,
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["workspace"]["shots"][0]["ref"] == {
        "entity_type": "shot",
        "entity_id": "shot-001",
        "version_id": "shot-001-v1",
    }


def test_studio_project_create_replay_repairs_manifest_success_aggregate_failure(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_AUTH_ALLOW_OPEN_SIGNUP", "true")
    failures = {"remaining": 1}

    import apps.api.runtime_service as runtime_service

    original = runtime_service.ensure_minimal_episode_bootstrap

    def fail_once(*args, **kwargs):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("injected aggregate bootstrap failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_service, "ensure_minimal_episode_bootstrap", fail_once)
    client = TestClient(create_runtime_app(runtime_root=tmp_path), raise_server_exceptions=False)
    owner = client.post(
        "/auth/register",
        json={
            "email": "owner@example.com",
            "password": "strong-password-123",
            "display_name": "Owner",
            "invite_code": "",
        },
    )
    assert owner.status_code == 200, owner.text
    headers = {"Authorization": f"Bearer {owner.json()['session_token']}"}
    body = {
        "project_id": "bootstrap-replay",
        "project_type": "studio_episode_production",
        "goal": "可恢复创建",
    }

    failed = client.post("/projects", headers=headers, json=body)
    assert failed.status_code == 500
    assert (tmp_path / "projects" / "bootstrap-replay" / "project_manifest.json").is_file()
    snapshot = EpisodeDomainAggregateStore(tmp_path).snapshot_path(
        org_id=owner.json()["user"]["user_id"],
        project_id="bootstrap-replay",
    )
    assert not snapshot.exists()

    replay = client.post("/projects", headers=headers, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["episode_bootstrap"]["created"] is True
    assert snapshot.is_file()
    workspace = client.get(
        "/projects/bootstrap-replay/episodes/episode-001/versions/episode-001-v1/workspace",
        headers=headers,
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["workspace"]["truth"]["shot_count"] == 3


def test_studio_project_create_rejects_divergent_replay_without_manifest_or_aggregate_drift(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AFS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AFS_AUTH_ALLOW_OPEN_SIGNUP", "true")
    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    owner = client.post(
        "/auth/register",
        json={
            "email": "owner@example.com",
            "password": "strong-password-123",
            "display_name": "Owner",
            "invite_code": "",
        },
    )
    assert owner.status_code == 200, owner.text
    headers = {"Authorization": f"Bearer {owner.json()['session_token']}"}
    first_body = {
        "project_id": "create-replay-guard",
        "project_type": "studio_episode_production",
        "goal": "第一版制作目标",
        "status": "in_progress",
    }
    divergent_body = {
        **first_body,
        "goal": "第二版不应覆盖",
        "status": "blocked",
    }

    created = client.post("/projects", headers=headers, json=first_body)
    assert created.status_code == 200, created.text
    rejected = client.post("/projects", headers=headers, json=divergent_body)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error"] == "project_create_replay_conflict"

    manifest = client.get("/projects/create-replay-guard/manifest", headers=headers)
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["manifest"]["goal"] == "第一版制作目标"
    assert manifest.json()["manifest"]["status"] == "in_progress"

    aggregate = client.get(
        "/projects/create-replay-guard/episode-production-aggregate",
        headers=headers,
    )
    assert aggregate.status_code == 200, aggregate.text
    assert aggregate.json()["aggregate"]["projects"][0]["title"] == "第一版制作目标"


def test_runtime_service_removed_production_memory_http_routes_return_404(tmp_path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    for path in ("/runs/asset-test", "/runs/two-round-validate", "/provider/validation-plan"):
        response = client.post(path, json={"project_id": "proj_runtime_demo"})
        assert response.status_code == 404, path


def test_runtime_service_current_error_projection_does_not_leak_unsafe_exception_text(tmp_path, monkeypatch) -> None:
    def raise_unsafe_error(self: RuntimeStore, project_id: str) -> dict:
        raise ValueError(r"D:\private\providers.local.json api_key token signed_url provider raw response")

    monkeypatch.setattr(RuntimeStore, "ensure_project_manifest", raise_unsafe_error)
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    response = client.get("/projects/proj_runtime_demo/manifest")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_project_manifest"
    assert detail["detail_code"] == "invalid_request"
    assert detail["status"] == "failed"
    assert detail["retryable"] is False
    assert detail["project_id"] == "proj_runtime_demo"
    assert detail["request_id"].startswith("req_")
    assert detail["message"]
    assert "details" not in detail
    assert response_contains_unsafe_marker(response.json()) is False


def test_frontend_runtime_service_request_examples_match_current_api_contract(tmp_path, monkeypatch) -> None:
    for name in (
        "AFS_ALLOW_REMOTE_LLM",
        "AFS_ALLOW_REMOTE_IMAGE",
        "AFS_ALLOW_REMOTE_VIDEO",
        "AFS_ALLOW_REMOTE_VISION",
        "AFS_ALLOW_REMOTE_ASR",
        "AFS_PROVIDER_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)

    client = TestClient(create_runtime_app(runtime_root=tmp_path))
    fixture_dir = Path("examples/frontend_runtime_service")

    project_request = _load_fixture(fixture_dir / "create_project.request.example.json")
    project = client.post("/projects", json=project_request).json()
    assert project["manifest"]["project_id"] == project_request["project_id"]

    feedback_request = _load_fixture(fixture_dir / "feedback_record.request.example.json")
    feedback = client.post("/feedback", json=feedback_request).json()
    assert feedback["feedback_event"]["feedback_is_memory"] is False

    prompt_fixture = _load_fixture(fixture_dir / "prompt_optimizer_nodes" / "text_node.zh.json")
    prompt = client.post(
        f"/projects/{project_request['project_id']}/prompt-optimizations",
        json=prompt_fixture["request"],
    ).json()
    assert prompt["job"]["action"] == "prompt_optimization"
    assert prompt["provider_calls_started"] is False

    keyframe = client.post(
        f"/projects/{project_request['project_id']}/keyframe-generations",
        json={
            "node_id": "image-node-fixture-001",
            "prompt_text": "A quiet cinematic street scene.",
            "target_platform": "short_video",
            "style": "cinematic",
            "candidate_count": 1,
            "generated_at": "2026-06-13T12:00:00+08:00",
        },
    ).json()
    assert keyframe["job"]["action"] == "keyframe_generation"
    assert keyframe["job"]["status"] == "blocked"
    assert keyframe["provider_calls_started"] is False

    script = client.post(
        "/provider/script-draft-plan",
        json={
            "project_id": project_request["project_id"],
            "goal": "Draft a short scene script from the current canvas.",
            "target_platform": "short_video",
            "style": "clear_demo",
            "generated_at": "2026-06-13T12:10:00+08:00",
        },
    ).json()
    assert script["job"]["action"] == "llm_script_draft_plan"
    assert script["provider_calls_started"] is False


def _load_fixture(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "api_key" not in serialized
    assert "token" not in serialized
    assert "d:\\" not in serialized
    assert "c:\\" not in serialized
    return payload
