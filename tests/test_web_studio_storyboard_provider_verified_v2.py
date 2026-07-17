from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_verified_empty_assets_stay_empty_and_render_without_placeholders() -> None:
    script = r'''
import { refineStructuredShotAssets, structuredShotText } from "./apps/studio/src/structured-shot.js";

const shot = {
  shot_id: "shot_01",
  index: 1,
  duration: "3s",
  description: "黑屏持续三秒。",
  shot_size: "特写",
  light_atmosphere: "无可见光源",
  camera_motion: "固定机位",
  dialogue: "无明确对白",
  sound: "钟鸣声",
  source_text: "黑屏持续三秒，只听见一声钟鸣。",
  asset_refs: [],
  asset_refs_authoritative: true,
  asset_ref_authority: "runtime_provider_verified_v2",
};
const refined = refineStructuredShotAssets(shot, "小华和一只猫站在城市屋顶");
process.stdout.write(JSON.stringify({ refs: refined.asset_refs, text: structuredShotText(refined) }));
'''

    payload = _run_node(script)

    assert payload["refs"] == []
    assert "资产：无明确可固定资产" in payload["text"]
    assert "@主角" not in payload["text"]
    assert "@主要场景" not in payload["text"]
    assert "小华" not in payload["text"]


def test_verified_animal_asset_card_uses_grounded_facts() -> None:
    script = r'''
import { assetCardDraftFromRef, assetCardText } from "./apps/studio/src/asset-card-drafts.js";

const asset = {
  entity_id: "entity:test-dog",
  label: "the hound",
  asset_type: "character",
  character_subtype: "animal",
  verification_status: "verified",
  descriptive_signature: "the hound：黑色短毛；右耳有缺口",
  evidence_text: "the hound has short black fur and a notch in its right ear",
  grounded_facts: [
    { field: "species", fact: "猎犬" },
    { field: "color", fact: "黑色短毛" },
    { field: "marking", fact: "右耳有缺口" },
  ],
  negative_locks: ["不得改变黑色短毛", "不得改变右耳缺口"],
};
const shot = { shot_id: "shot_02", description: "the hound enters", source_text: "the hound enters" };
const draft = assetCardDraftFromRef(asset, shot, { sourceScriptNodeId: "script_02" });
process.stdout.write(JSON.stringify({ draft, text: assetCardText(draft) }));
'''

    payload = _run_node(script)

    assert payload["draft"]["character_subtype"] == "animal"
    assert payload["draft"]["entity_id"] == "entity:test-dog"
    assert "资产类型：动物角色资产" in payload["text"]
    assert "黑色短毛" in payload["text"]
    assert "右耳有缺口" in payload["text"]
    assert "身份与外观待确认" not in payload["text"]


def test_same_label_distinct_entities_receive_distinct_asset_card_ids() -> None:
    script = r'''
import { assetCardDraftFromRef } from "./apps/studio/src/asset-card-drafts.js";

const base = {
  label: "小狗",
  asset_type: "character",
  character_subtype: "animal",
  verification_status: "verified",
  grounded_facts: [{ field: "species", fact: "狗" }],
};
const shot = { shot_id: "shot_01", description: "两只小狗进入画面" };
const first = assetCardDraftFromRef({ ...base, entity_id: "entity:first" }, shot);
const second = assetCardDraftFromRef({ ...base, entity_id: "entity:second" }, shot);
process.stdout.write(JSON.stringify({ first: first.card_id, second: second.card_id }));
'''

    payload = _run_node(script)

    assert payload["first"] != payload["second"]
    assert "entity3afirst" in payload["first"]
    assert "entity3asecond" in payload["second"]


def test_studio_requests_v2_and_only_legacy_mode_keeps_local_fallback() -> None:
    source = (ROOT / "apps" / "studio" / "src" / "script-breakdown.js").read_text(encoding="utf-8")

    assert 'const VERIFIED_STORYBOARD_PIPELINE = "provider_verified_v2"' in source
    assert "storyboard_pipeline: requestedPipeline" in source
    assert 'requestedPipeline !== "legacy_storyboard_v1"' in source
    assert 'return { shots: [], mode: "provider_verified_v2_failed"' in source
    assert "Runtime 不可用，严格分镜流程未执行" in source


def _run_node(script: str) -> dict:
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)
