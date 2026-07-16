from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.runtime_service import create_runtime_app


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "apps" / "studio" / "episode-workspace"


def _read(name: str) -> str:
    return (WORKSPACE / name).read_text(encoding="utf-8")


def _ref(entity_type: str, entity_id: str, version_id: str | None = None) -> dict[str, str]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "version_id": version_id or f"{entity_id}.v1",
    }


def _record(entity_type: str, entity_id: str, **extra: object) -> dict[str, object]:
    return {
        **_ref(entity_type, entity_id),
        "revision": 1,
        "lifecycle_state": "candidate",
        "review_state": "needs_review",
        **extra,
    }


def projection(*, next_action: bool = True) -> dict[str, object]:
    project = _record(
        "project",
        "project-test",
        title="测试项目",
        data_policy={
            "visibility": "private",
            "training_use": "denied_by_default",
            "product_improvement_use": "denied_by_default",
        },
    )
    episode = _record("episode", "episode-test", title="测试单集")
    scene = _record("scene", "scene-test", sequence=1, title="测试场景")
    shot = _record(
        "shot",
        "shot-test",
        scene_ref=_ref("scene", "scene-test"),
        sequence=1,
        duration_seconds=3,
    )
    shot_row = {
        "ref": _ref("shot", "shot-test"),
        "scene_ref": _ref("scene", "scene-test"),
        "sequence": 1,
        "duration_seconds": 3,
        "lifecycle_state": "candidate",
        "review_state": "needs_review",
        "production_state": None,
        "selection_state": None,
        "selection_lifecycle_state": None,
        "ai_check_state": None,
        "delivery_invalid": False,
        "blocking": True,
        "script": None,
        "thumbnail_url": None,
        "review_note": None,
        "facts": [],
        "continuity": [],
        "continuity_issue": None,
        "candidates": [],
        "selections": [],
        "agent_proposals": [],
        "agent_proposal": None,
        "prior_shot_blockers": [],
        "allowed_actions": [
            {"action": "review_shot", "enabled": True, "reason": "", "blocked_by": []},
            {"action": "adopt_candidate", "enabled": False, "reason": "没有候选", "blocked_by": []},
        ],
    }
    return {
        "schema_version": "afs_episode_workspace_projection.v0.1",
        "aggregate": {
            "schema_version": "afs_episode_production_aggregate.v0.1",
            "aggregate_version": 3,
            "evaluated_at": "2026-07-15T08:00:00+00:00",
            "scope": {"org_id": "owner-test", "project_id": "project-test", "actor_id": "owner-test"},
            "projects": [project],
            "series": [],
            "episodes": [episode],
            "scenes": [scene],
            "shots": [shot],
        },
        "workspace": {
            "episode_ref": _ref("episode", "episode-test"),
            "scenes": [{"ref": _ref("scene", "scene-test"), "sequence": 1, "title": "测试场景"}],
            "shots": [shot_row],
            "next_action": (
                {
                    "action": "review_shot",
                    "label": "审核镜头 1",
                    "subject_ref": _ref("shot", "shot-test"),
                }
                if next_action
                else None
            ),
            "recovery": None,
            "truth": {
                "scene_count": 1,
                "shot_count": 1,
                "duration_seconds": 3,
                "missing_asset_count": 0,
                "generation_dispatch_count": 0,
                "playable_preview_available": False,
            },
            "delivery": {
                "current_ref": None,
                "status": "blocked",
                "missing_asset_count": 0,
                "preview_artifact_present": False,
                "playable_preview_available": False,
                "blockers": ["delivery_not_frozen"],
            },
            "evidence_environment": None,
        },
    }


def _run(module: str, script: str, payload: dict[str, object]) -> dict[str, object]:
    module_uri = (WORKSPACE / module).resolve().as_uri()
    source = f"""
      import fs from "node:fs";
      import * as subject from {json.dumps(module_uri)};
      const payload = JSON.parse(fs.readFileSync(0, "utf8"));
      {script}
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_workspace_is_mounted_under_existing_production_studio_tree(tmp_path: Path) -> None:
    client = TestClient(create_runtime_app(runtime_root=tmp_path))

    response = client.get("/studio/episode-workspace/")

    assert response.status_code == 200
    assert "AFS · 单集制作工作区" in response.text
    assert client.get("/studio/").status_code == 200


def test_product_source_uses_real_authenticated_routes_without_local_business_state() -> None:
    app = _read("app.mjs")
    api = _read("api-client.mjs")
    commands = _read("commands.mjs")
    state = _read("state.mjs")
    runtime_client = (ROOT / "apps" / "studio" / "src" / "runtime-client.js").read_text(encoding="utf-8")
    product_source = "\n".join((app, api, commands, state))

    assert "loadEpisodeWorkspace" in api + runtime_client
    assert "/episodes/${episode}/versions/${version}/workspace" in runtime_client
    assert "/episode-production-aggregate/commands" in runtime_client
    assert '"Idempotency-Key": idempotencyKey' in runtime_client
    assert "episode-production-aggregate\", { method: \"PUT\"" not in product_source + runtime_client
    assert "loadStudioState" in api + runtime_client
    assert "saveStudioState" in api + runtime_client
    assert "localStorage" not in product_source
    assert "sessionStorage" not in product_source
    assert "episode_workspace" in state
    assert "pending_idempotency_key" in state


def test_product_javascript_has_no_representative_fixture_or_invented_media_claims() -> None:
    source = "\n".join(_read(name) for name in ("app.mjs", "state.mjs", "api-client.mjs", "commands.mjs"))
    for forbidden in (
        "shot-006",
        "shot-007",
        "shot-011",
        "林遥",
        "铜制提灯扣",
        "missing_asset_count: 25",
        "playable_preview_available: true",
        "thumbnail_url:",
        "visual_action:",
    ):
        assert forbidden not in source
    assert "服务未提供镜头脚本文本" in source
    assert "服务还没有确认预览可播放" in source
    assert "artifact availability proof" not in source


def test_ui_state_restores_exact_identity_mode_focus_scroll_and_pending_key() -> None:
    saved = {
        "episode_ref": _ref("episode", "episode-test"),
        "active_shot_ref": _ref("shot", "shot-test"),
        "mode": "review",
        "focused_control": "shot-shot-test",
        "inspector_section": "continuity",
        "scroll_top": 420,
        "pending_idempotency_key": "shot-review-pending-1",
        "pending_command": {
            "idempotency_key": "shot-review-pending-1",
            "payload": {
                "action": "shot.review",
                "expected_aggregate_version": 3,
                "shot_ref": _ref("shot", "shot-test"),
                "decision": "approve",
                "shot_version_id": "shot-test.v2",
                "decision_entity_id": "review-shot-test",
                "decision_version_id": "review-shot-test.v1",
                "created_at": "2026-07-15T08:01:00+00:00",
                "note": "Creator approved exact shot.",
            },
        },
    }
    result = _run(
        "state.mjs",
        f"""
        const model = subject.buildWorkspaceModel(payload);
        const ui = subject.createInitialUiState(model, {json.dumps(saved)});
        const merged = subject.mergeEpisodeWorkspaceState({{ legacy: {{ keep: true }} }}, model, ui);
        console.log(JSON.stringify({{
          active: subject.activeShot(model, ui).ref,
          next: subject.nextShot(model, ui).ref,
          mode: ui.mode,
          focus: ui.focusedControl,
          section: ui.inspectorSection,
          scroll: ui.scrollTop,
          pending: ui.pendingIdempotencyKey,
          pendingAction: ui.pendingCommand.payload.action,
          legacy: merged.legacy.keep,
          namespace: merged.episode_workspace,
        }}));
        """,
        projection(),
    )

    assert result["active"] == _ref("shot", "shot-test")
    assert result["next"] == _ref("shot", "shot-test")
    assert result["mode"] == "review"
    assert result["focus"] == "shot-shot-test"
    assert result["section"] == "continuity"
    assert result["scroll"] == 420
    assert result["pending"] == "shot-review-pending-1"
    assert result["pendingAction"] == "shot.review"
    assert result["legacy"] is True
    assert result["namespace"]["episode_ref"] == _ref("episode", "episode-test")


def test_no_next_action_does_not_invent_one_and_uses_first_real_shot_for_inspection() -> None:
    result = _run(
        "state.mjs",
        """
        const model = subject.buildWorkspaceModel(payload);
        const ui = subject.createInitialUiState(model);
        console.log(JSON.stringify({ active: subject.activeShot(model, ui).sequence, next: subject.nextShot(model, ui), action: model.nextAction }));
        """,
        projection(next_action=False),
    )

    assert result == {"active": 1, "next": None, "action": None}


def test_restore_focus_is_noop_for_empty_or_stale_selector_and_calls_real_focus() -> None:
    result = _run(
        "state.mjs",
        """
        const calls = [];
        const element = { focus(options) { calls.push(options); } };
        console.log(JSON.stringify({
          empty: subject.focusIfAvailable(""),
          missing: subject.focusIfAvailable(null),
          stale: subject.focusIfAvailable({}),
          real: subject.focusIfAvailable(element),
          calls,
        }));
        """,
        {},
    )

    assert result == {
        "empty": False,
        "missing": False,
        "stale": False,
        "real": True,
        "calls": [{"preventScroll": True}],
    }


def test_pending_command_retention_distinguishes_persistence_from_command_rejection() -> None:
    result = _run(
        "state.mjs",
        """
        console.log(JSON.stringify({
          persistenceConflict: subject.retainPendingCommandAfterFailure("stale", false),
          persistenceInvalid: subject.retainPendingCommandAfterFailure("invalid", false),
          persistenceServer: subject.retainPendingCommandAfterFailure("server", false),
          dispatchedServer: subject.retainPendingCommandAfterFailure("server", true),
          dispatchedAuth: subject.retainPendingCommandAfterFailure("auth", true),
          dispatchedStale: subject.retainPendingCommandAfterFailure("stale", true),
          dispatchedInvalid: subject.retainPendingCommandAfterFailure("invalid", true),
          dispatchedNotFound: subject.retainPendingCommandAfterFailure("not_found", true),
        }));
        """,
        {},
    )

    assert result == {
        "persistenceConflict": True,
        "persistenceInvalid": False,
        "persistenceServer": False,
        "dispatchedServer": True,
        "dispatchedAuth": True,
        "dispatchedStale": False,
        "dispatchedInvalid": False,
        "dispatchedNotFound": False,
    }


def test_pre_dispatch_invalid_envelope_clears_pending_and_unblocks_next_command() -> None:
    result = _run(
        "state.mjs",
        """
        const model = subject.buildWorkspaceModel(payload.projection);
        const pending = {
          episode_ref: model.episode.ref,
          pending_command: {
            idempotency_key: "invalid-before-dispatch",
            payload: { action: "shot.review", expected_aggregate_version: 1 },
          },
        };
        let ui = subject.createInitialUiState(model, pending);
        const retained = subject.retainPendingCommandAfterFailure("invalid", false);
        if (!retained) {
          ui = subject.updateUiRecovery(ui, {
            pendingIdempotencyKey: "",
            pendingCommand: null,
          });
        }
        console.log(JSON.stringify({
          retained,
          pendingKey: ui.pendingIdempotencyKey,
          pendingCommand: ui.pendingCommand,
          nextCommandUnblocked: !ui.pendingCommand,
        }));
        """,
        {"projection": projection()},
    )

    assert result == {
        "retained": False,
        "pendingKey": "",
        "pendingCommand": None,
        "nextCommandUnblocked": True,
    }


def test_command_builders_use_exact_refs_current_cas_and_unique_safe_identity() -> None:
    payload = {
        "model": {
            "aggregateVersion": 3,
            "evaluatedAt": "2026-07-15T08:00:00+00:00",
            "shots": [],
        },
        "shot": {"ref": _ref("shot", "shot-test")},
    }
    result = _run(
        "commands.mjs",
        """
        const first = subject.buildShotReviewCommand(payload.model, payload.shot, "approve");
        const second = subject.buildShotReviewCommand(payload.model, payload.shot, "approve");
        console.log(JSON.stringify({ first, unique: first.shot_version_id !== second.shot_version_id, key: subject.commandIdFor(first.action) }));
        """,
        payload,
    )

    assert result["first"]["action"] == "shot.review"
    assert result["first"]["expected_aggregate_version"] == 3
    assert result["first"]["shot_ref"] == _ref("shot", "shot-test")
    assert result["unique"] is True
    assert result["key"].startswith("shot-review-")


def test_mobile_layout_is_card_and_detail_based_without_wide_table() -> None:
    styles = _read("styles.css")
    app = _read("app.mjs")

    assert "@media (max-width: 760px)" in styles
    assert ".shot-card { grid-template-columns: 120px 1fr;" in styles
    assert ".workspace-layout { display: block;" in styles
    assert "overflow-x: auto" not in styles
    assert "<table" not in app.lower()
    assert 'data-action="back-to-shots"' in app
    assert 'data-action="open-mobile-nav"' in app


def test_tablet_width_keeps_storyboard_and_inspector_inside_hidden_overflow_shell() -> None:
    styles = _read("styles.css")

    assert "@media (min-width: 761px) and (max-width: 980px)" in styles
    assert ".workspace-layout { grid-template-columns: minmax(420px, 1fr) 300px; }" in styles
    assert ".left-rail { display: none; }" in styles


def test_workspace_sources_are_utf8_clean_and_do_not_expose_sensitive_errors() -> None:
    source = "\n".join(_read(name) for name in ("index.html", "app.mjs", "api-client.mjs", "state.mjs", "commands.mjs"))
    for corrupted in ("�", "娴嬭瘯", "鏈", "锛屾", "銆傛", "Ã", "â€"):
        assert corrupted not in source
    assert "error?.message" not in _read("app.mjs")
    assert "Authorization" not in _read("app.mjs")
