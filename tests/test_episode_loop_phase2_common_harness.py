from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "experiments" / "episode-loop-phase2" / "common"
PACKAGE = ROOT / "examples" / "representative_episode" / "episode_package.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(name: str) -> dict:
    return json.loads((COMMON / name).read_text(encoding="utf-8"))


def test_manifest_freezes_contract_research_protocol_and_fixtures() -> None:
    manifest = load_json("fixture-manifest.json")

    assert manifest["schema_version"] == "afs_episode_loop_phase2_fixture_manifest.v0.2"
    assert manifest["base_commit"] == "7f74f2280dca8d4a8d86b9aefda017e5b2e5f62a"
    assert manifest["base_commit_role"] == "phase1_research_evidence_commit"
    assert manifest["common_harness_commit"] == "9bda0a284bdee18866cf4b3c99764065af10fa61"
    assert manifest["domain_contract_commit"] == "bf44e54edaf53a32917522ae7cfbf43563277ded"
    assert manifest["domain_contract_revision"] == "v0.1.1"
    assert (
        manifest["domain_contract_wire_schema_literal"]
        == "afs_episode_production_aggregate.v0.1"
    )
    for entry in (
        manifest["domain_contract_artifact"],
        manifest["evidence_matrix"],
        manifest["evaluation_protocol"],
        manifest["evaluation_result"],
        *manifest["fixtures"],
    ):
        path = ROOT / entry["path"]
        assert path.is_file(), entry["path"]
        assert sha256(path) == entry["sha256"]

    assert manifest["provider_dispatch_allowed"] is False
    assert manifest["expected_missing_assets"] == 25
    assert manifest["expected_provider_dispatch_count"] == 0


def test_manifest_freezes_the_exact_repaired_comparison_heads() -> None:
    manifest = load_json("fixture-manifest.json")

    assert manifest["variant_commits"] == {
        "guided": "a704a1cc1ba69e745d185ea3098209eaa419bee5",
        "storyboard": "9edd03f68e9e5c77819985e2ffc4605fc75d4332",
        "hybrid": "3346f5d4813d2e87a0abf113e73f9757a9eb5531",
    }
    assert (
        manifest["frontend_direction"]
        == "hybrid_shell_storyboard_workspace_contextual_decision_inspector"
    )
    assert manifest["comparison_purpose"] == "evidence_hygiene_and_structure_risk_only"


def test_scenario_is_variant_neutral_and_preserves_truth_constraints() -> None:
    scenario = load_json("scenario.json")
    overlays = {item["target"]: item for item in scenario["overlays"]}

    assert scenario["truth_constraints"] == {
        "shot_count": 15,
        "duration_seconds": 135,
        "missing_asset_count": 25,
        "provider_dispatch_count": 0,
        "playable_preview_available": False,
    }
    assert overlays["shot-006"]["expected_scene"] == "scene-archive-tower"
    assert overlays["shot-007"]["kind"] == "continuity_conflict"
    assert overlays["shot-008"]["expected_action"] == "leave_unchanged"
    assert scenario["revision_task"]["target"] == "shot-011"
    assert scenario["revision_task"]["unchanged_shot_count"] == 14
    assert scenario["revision_task"]["reload_after_reconfirmed_count"] == 3
    assert scenario["revision_task"]["expected_final_reconfirmed_count"] == 8
    assert len(scenario["tasks"]) == 5
    assert not any(key in json.dumps(scenario).lower() for key in ("guided", "storyboard-first", "hybrid"))


def test_variant_roots_are_isolated_from_production_and_each_other() -> None:
    manifest = load_json("fixture-manifest.json")
    roots = list(manifest["variants"].values())

    assert len(roots) == len(set(roots)) == 3
    assert all(path.startswith("experiments/episode-loop-phase2/prototypes/") for path in roots)
    assert not any(path.startswith("apps/") for path in roots)
    runtime_service = (ROOT / "apps" / "api" / "runtime_service.py").read_text(encoding="utf-8")
    assert "experiments/episode-loop-phase2" not in runtime_service


def test_common_javascript_has_an_independent_syntax_gate() -> None:
    result = subprocess.run(
        ["node", "tools/check-phase2-prototype-js.mjs"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 files" in result.stdout


def test_protocol_consumes_research_and_has_human_evidence_boundary() -> None:
    protocol = (ROOT / "docs" / "AFS_EPISODE_LOOP_PHASE2_EVALUATION_PROTOCOL.md").read_text(
        encoding="utf-8"
    )
    compact_protocol = " ".join(protocol.split())

    assert "research/AFS_PHASE1_EVIDENCE_MATRIX.md" in protocol
    assert "same-task" in protocol.lower()
    assert "frontend_direction_decided" in protocol
    assert "must not select a winner" in compact_protocol
    assert "cannot claim real creator acceptance" in compact_protocol
    assert "`evidence_valid` or `evidence_invalid`" in protocol
    assert "reopen architecture selection" in protocol
    assert "select_for_production_validation" not in protocol
    assert "decision_needed" not in protocol


def test_protocol_locks_fixture_visible_facts_and_new_semantic_hard_gates() -> None:
    protocol = (ROOT / "docs" / "AFS_EPISODE_LOOP_PHASE2_EVALUATION_PROTOCOL.md").read_text(
        encoding="utf-8"
    )
    compact_protocol = " ".join(protocol.split())
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    lin_yao = next(
        character for character in package["characters"] if character["character_id"] == "char-lin-yao"
    )

    assert lin_yao["name"] == "林遥"
    assert "铜制灯扣" in lin_yao["appearance"]
    assert "灯扣位于右肩" in lin_yao["continuity_constraints"]
    assert "左眉疤不可镜像" in lin_yao["continuity_constraints"]
    for visible_fact in ("林遥", "铜制提灯扣位于右肩", "左眉疤不可镜像"):
        assert visible_fact in protocol

    assert "active Shot 6 matching the suggested next action" in compact_protocol
    assert '"currently viewing" from "suggested next"' in compact_protocol
    assert "selecting v2 and reconfirming are disabled" in compact_protocol
    assert "direct handler call fails closed without mutation" in compact_protocol
    assert "Request reset, cancel it" in compact_protocol
    assert "cancel leaves state unchanged" in compact_protocol


def test_protocol_names_exact_heads_without_reopening_the_structure_vote() -> None:
    protocol = (ROOT / "docs" / "AFS_EPISODE_LOOP_PHASE2_EVALUATION_PROTOCOL.md").read_text(
        encoding="utf-8"
    )

    for revision in (
        "bf44e54edaf53a32917522ae7cfbf43563277ded",
        "9bda0a284bdee18866cf4b3c99764065af10fa61",
        "a704a1cc1ba69e745d185ea3098209eaa419bee5",
        "9edd03f68e9e5c77819985e2ffc4605fc75d4332",
        "3346f5d4813d2e87a0abf113e73f9757a9eb5531",
    ):
        assert revision in protocol
    assert "Hybrid shell and Storyboard-centered workspace" in protocol
    assert "must not output a winner" in protocol


def test_evaluation_result_records_per_variant_validity_without_a_new_vote() -> None:
    result = (ROOT / "docs" / "AFS_EPISODE_LOOP_PHASE2_EVALUATION_RESULT.md").read_text(
        encoding="utf-8"
    )
    compact_result = " ".join(result.split())

    for revision in (
        "a704a1cc1ba69e745d185ea3098209eaa419bee5",
        "9edd03f68e9e5c77819985e2ffc4605fc75d4332",
        "3346f5d4813d2e87a0abf113e73f9757a9eb5531",
    ):
        assert revision in result
    assert "A | Guided repair" in result
    assert "B | Storyboard" in result
    assert "C | Hybrid" in result
    assert result.count("`evidence_valid`") == 1
    assert result.count("`evidence_invalid`") == 2
    assert "Browser is not available: iab" in result
    assert "Playwright fallback" in result
    assert "Initial active Shot 7 did not match next Shot 6" in compact_result
    assert "mutation succeeded before Shot 6/7 completion" in compact_result
    assert "did not explicitly state the authoritative right-shoulder lamp-buckle fact" in compact_result
    assert "does not select a winner" in compact_result
    assert "not a new architecture vote or human acceptance" in compact_result


def test_neutral_tokens_provide_accessibility_basics_without_layout_choice() -> None:
    css = (COMMON / "neutral-tokens.css").read_text(encoding="utf-8")

    assert "min-height: 44px" in css
    assert ":focus-visible" in css
    assert "display: grid" not in css
    assert "display: flex" not in css


def run_node_module(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_store_rejects_contradictory_or_drifted_recovery_envelopes() -> None:
    result = run_node_module(
        """
        import { readFileSync } from 'node:fs';
        import { createInitialState, FIXTURE_SHA256 } from './experiments/episode-loop-phase2/common/prototype-contract.js';
        import { createEnvelope, loadEnvelope, saveEnvelope } from './experiments/episode-loop-phase2/common/prototype-store.js';
        const scenario = JSON.parse(readFileSync('./experiments/episode-loop-phase2/common/scenario.json', 'utf8'));
        const values = new Map();
        const storage = {
          getItem: (key) => values.get(key) ?? null,
          setItem: (key, value) => values.set(key, value),
          removeItem: (key) => values.delete(key),
        };
        const state = createInitialState({ variant: 'guided', scenario });
        state.checkpoint.completed_reconfirmations = 3;
        const envelope = createEnvelope({
          variant: 'guided', fixtureSha256: FIXTURE_SHA256, sessionId: 'session-1', state, eventLog: [],
        });
        saveEnvelope(storage, envelope);
        const valid = loadEnvelope(storage, { variant: 'guided', fixtureSha256: FIXTURE_SHA256 });
        const key = 'afs:episode-loop:phase2:guided:v1';
        const wrongVariant = structuredClone(envelope);
        wrongVariant.state.variant = 'hybrid';
        storage.setItem(key, JSON.stringify(wrongVariant));
        const variantRejected = loadEnvelope(storage, { variant: 'guided', fixtureSha256: FIXTURE_SHA256 }) === null;
        const falseDelivery = structuredClone(envelope);
        falseDelivery.state.delivery.missing_asset_count = 0;
        storage.setItem(key, JSON.stringify(falseDelivery));
        const deliveryRejected = loadEnvelope(storage, { variant: 'guided', fixtureSha256: FIXTURE_SHA256 }) === null;
        const splitCheckpoint = structuredClone(envelope);
        splitCheckpoint.checkpoint = { completed_reconfirmations: 8 };
        storage.setItem(key, JSON.stringify(splitCheckpoint));
        const splitCheckpointRejected = loadEnvelope(
          storage, { variant: 'guided', fixtureSha256: FIXTURE_SHA256 },
        ) === null;
        const foreignSession = structuredClone(envelope);
        foreignSession.event_log = [{
          sequence: 1, variant: 'guided', session_id: 'foreign-session', elapsed_ms: 1,
        }];
        storage.setItem(key, JSON.stringify(foreignSession));
        const foreignSessionRejected = loadEnvelope(
          storage, { variant: 'guided', fixtureSha256: FIXTURE_SHA256 },
        ) === null;
        const skippedReload = structuredClone(envelope);
        skippedReload.state.checkpoint.completed_reconfirmations = 8;
        skippedReload.state.checkpoint.reload_observed = false;
        storage.setItem(key, JSON.stringify(skippedReload));
        const skippedReloadRejected = loadEnvelope(
          storage, { variant: 'guided', fixtureSha256: FIXTURE_SHA256 },
        ) === null;
        console.log(JSON.stringify({
          valid: valid !== null, variantRejected, deliveryRejected, splitCheckpointRejected,
          foreignSessionRejected, skippedReloadRejected,
        }));
        """
    )

    assert result == {
        "valid": True,
        "variantRejected": True,
        "deliveryRejected": True,
        "splitCheckpointRejected": True,
        "foreignSessionRejected": True,
        "skippedReloadRejected": True,
    }


def test_event_log_deduplicates_activation_and_resumes_sequence_and_time() -> None:
    result = run_node_module(
        """
        import { createEventLogger, summarizeEvents } from './experiments/episode-loop-phase2/common/event-log.js';
        let clockValue = 1000;
        const first = createEventLogger({ variant: 'guided', sessionId: 'session-1', clock: () => clockValue });
        clockValue = 1010;
        first.record({
          task: 'repair_scene_split', action: 'confirm', objectType: 'shot', objectKey: 'shot-006',
          fromView: 'breakdown', toView: 'scene_editor', inputMethod: 'keyboard', stateSummary: 'repaired',
          activationId: 'repair-shot-006',
        });
        clockValue = 1011;
        first.record({
          task: 'repair_scene_split', action: 'confirm', objectType: 'shot', objectKey: 'shot-006',
          fromView: 'breakdown', toView: 'scene_editor', inputMethod: 'mouse', stateSummary: 'synthetic-click',
          activationId: 'repair-shot-006',
        });
        const saved = first.snapshot();
        clockValue = 5;
        const resumed = createEventLogger({
          variant: 'guided', sessionId: 'session-1', clock: () => clockValue, existingEvents: saved,
        });
        clockValue = 15;
        resumed.record({
          task: 'resolve_continuity', action: 'open', objectType: 'shot', objectKey: 'shot-007',
          fromView: 'scene_editor', toView: 'continuity', inputMethod: 'keyboard', stateSummary: 'conflict-open',
          activationId: 'open-shot-007',
        });
        const events = resumed.snapshot();
        console.log(JSON.stringify({
          sequences: events.map((event) => event.sequence),
          elapsed: events.map((event) => event.elapsed_ms),
          meaningful: events.map((event) => event.meaningful_activation),
          transitions: events.map((event) => event.screen_transition),
          summary: summarizeEvents(events),
        }));
        """
    )

    assert result["sequences"] == [1, 2, 3]
    assert result["elapsed"] == [10, 11, 21]
    assert result["meaningful"] == [True, False, True]
    assert result["transitions"] == [True, False, True]
    assert result["summary"] == {
        "meaningful_activations": 2,
        "context_transitions": 2,
        "elapsed_ms": 21,
    }
