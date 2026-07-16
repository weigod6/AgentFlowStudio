import json
import subprocess

from studio_static_helpers import STUDIO_ROOT


def test_studio_asset_extraction_contract_module_is_wired() -> None:
    contract = (STUDIO_ROOT / "src" / "asset-extraction-contract.js").read_text(encoding="utf-8")
    structured = (STUDIO_ROOT / "src" / "structured-shot.js").read_text(encoding="utf-8")
    script_breakdown = (STUDIO_ROOT / "src" / "script-breakdown.js").read_text(encoding="utf-8")

    assert "normalizeAssetExtractionRefs" in contract
    assert "audio_only_non_visual_city_reference" in contract
    assert "visual_evidence_span" in contract
    assert "display_name" in contract
    assert "asset-extraction-contract.js" in structured
    assert "normalizeShotAssetRefs" in script_breakdown
    assert "display_name" in script_breakdown
    assert "descriptive_signature" in script_breakdown
    assert "evidence_modality" in script_breakdown


def test_studio_structured_shot_applies_modality_and_generic_name_gate() -> None:
    script = r'''
import { structuredShotFromSegment, normalizeShotAssetRefs } from "./apps/studio/src/structured-shot.js";

const audio = structuredShotFromSegment("城市环境底噪和 distant city noise 持续，只有城市噪音。", 1);
const visual = structuredShotFromSegment("Rain-night city street with skyline, buildings, neon signs, and wet road.", 2);
const generic = structuredShotFromSegment("@人物 在雨夜城市街道奔跑，穿红色外套，霓虹照亮侧脸。", 3);
const unresolved = structuredShotFromSegment("@人物", 4);
const providerRefs = normalizeShotAssetRefs(
  [{ label: "城市噪音", asset_type: "scene", source: "provider", evidence_text: "distant city noise" }],
  "distant city noise"
);

process.stdout.write(JSON.stringify({ audio, visual, generic, unresolved, providerRefs }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert not any(ref["asset_type"] == "scene" for ref in payload["audio"]["asset_refs"])
    assert any(item["reason"] == "audio_only_non_visual_city_reference" for item in payload["audio"]["dropped_asset_ref_diagnostics"])

    scene = next(ref for ref in payload["visual"]["asset_refs"] if ref["asset_type"] == "scene")
    assert scene["evidence_modality"] == "visual"
    assert scene["visual_evidence_span"]

    character = next(ref for ref in payload["generic"]["asset_refs"] if ref["asset_type"] == "character")
    assert character["display_name"] not in {"人", "人物", "主角"}
    assert character["provisional_name"] is True

    assert payload["unresolved"]["asset_refs"] == []
    assert any(item["reason"] == "unresolved_generic_character" for item in payload["unresolved"]["dropped_asset_ref_diagnostics"])
    assert payload["providerRefs"] == []


def test_studio_structured_shot_auto_assets_keep_characters_scenes_and_key_props() -> None:
    script = r'''
import { structuredShotFromSegment } from "./apps/studio/src/structured-shot.js";

const auto = structuredShotFromSegment("孙悟空握着金箍棒，猪八戒在云栈洞口后撤，铁链拖地。", 1);
const explicit = structuredShotFromSegment("@金箍棒 横在画面前景，@孙悟空 站在@云栈洞口。", 2);

process.stdout.write(JSON.stringify({ auto, explicit }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    auto_refs = {(ref["label"], ref["asset_type"]) for ref in payload["auto"]["asset_refs"]}
    explicit_refs = {(ref["label"], ref["asset_type"]) for ref in payload["explicit"]["asset_refs"]}

    assert ("孙悟空", "character") in auto_refs
    assert ("猪八戒", "character") in auto_refs
    assert ("云栈洞口", "scene") in auto_refs
    assert ("金箍棒", "prop") in auto_refs
    assert ("孙悟空", "character") in explicit_refs
    assert ("云栈洞口", "scene") in explicit_refs
    assert ("金箍棒", "prop") in explicit_refs
    assert not any(item["reason"] == "prop_requires_manual_asset_entry" and item["label"] == "金箍棒" for item in payload["explicit"]["dropped_asset_ref_diagnostics"])


def test_studio_fallback_asset_inference_rejects_action_fragments_and_future_leaks() -> None:
    script_breakdown = (STUDIO_ROOT / "src" / "script-breakdown.js").read_text(encoding="utf-8")
    assert "allowLocalAssetInference ? source : \"\"" not in script_breakdown

    script = r'''
import { refineStructuredShotAssets, structuredShotFromSegment, normalizeShotAssetRefs } from "./apps/studio/src/structured-shot.js";

const shot1 = refineStructuredShotAssets(
  structuredShotFromSegment("片名：《捡到一只狗》 小明蹲在老城区巷口的青石台阶上，指尖沾着猫毛，正给怀里的橘猫顺毛。", 1),
  "",
  { inferMissingAssets: true },
);
const shot3 = refineStructuredShotAssets(
  structuredShotFromSegment("它挣脱怀抱，四爪无声落地，转身轻巧跃下三级台阶，叼回一只浑身湿漉漉、耳朵耷拉、项圈锈迹斑斑的土狗幼崽。小狗四肢僵直，爪子还死死勾着半截断绳。", 3),
  "",
  { inferMissingAssets: true },
);
const shot4 = refineStructuredShotAssets(
  structuredShotFromSegment("小明愣住，右手本能前伸。他掏出手机，屏幕亮起却迟迟没有按下拍摄键。", 4),
  "",
  { inferMissingAssets: true },
);
const normalizedProviderRef = normalizeShotAssetRefs(
  [{ label: "他掏出手机", asset_type: "character", source: "provider", evidence_text: "他掏出手机，屏幕亮起却迟迟没有按下拍摄键。" }],
  "他掏出手机，屏幕亮起却迟迟没有按下拍摄键。",
);

process.stdout.write(JSON.stringify({ shot1, shot3, shot4, normalizedProviderRef }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    labels_by_shot = [
        {(ref["label"], ref["asset_type"]) for ref in payload[key]["asset_refs"]}
        for key in ("shot1", "shot3", "shot4")
    ]
    all_labels = {label for labels in labels_by_shot for label, _asset_type in labels}

    assert {"它挣脱怀", "转身轻巧", "右眼", "他掏出手机"}.isdisjoint(all_labels)
    assert ("小明", "character") in labels_by_shot[0]
    assert ("橘猫", "character") in labels_by_shot[0]
    assert ("老城区巷口", "scene") in labels_by_shot[0]
    assert ("小狗", "character") in labels_by_shot[1]
    assert ("项圈", "prop") in labels_by_shot[1]
    assert ("断绳", "prop") in labels_by_shot[1]
    assert all(("手机", "prop") not in labels for labels in labels_by_shot[:2])
    assert ("手机", "prop") in labels_by_shot[2]
    assert {(ref["label"], ref["asset_type"]) for ref in payload["normalizedProviderRef"]} == {("手机", "prop")}
