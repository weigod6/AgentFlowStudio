from __future__ import annotations

import json

from agentflow.algorithms.asset_card_candidates import build_asset_card_candidates
from apps.api.runtime_asset_graph import build_asset_graph
from apps.api.runtime_storyboard_local import local_storyboard_shots
from apps.api.runtime_storyboard_provider_parse import shots_from_provider_text


def _all_refs(shots: list[dict]) -> list[dict]:
    return [ref for shot in shots for ref in shot.get("asset_refs", [])]


def _all_diagnostics(shots: list[dict]) -> list[dict]:
    return [item for shot in shots for item in shot.get("dropped_asset_ref_diagnostics", [])]


def test_audio_only_city_noise_is_held_out_of_visual_graph_and_candidates() -> None:
    script = "城市环境底噪和 distant city noise 持续，只有城市噪音，没有可见人物、街道或建筑画面。"

    shots = local_storyboard_shots(script)
    refs = _all_refs(shots)
    diagnostics = _all_diagnostics(shots)
    graph = build_asset_graph(shots, source_text=script)
    candidate_set = build_asset_card_candidates(project_id="proj_audio_city", asset_graph=graph)

    assert not any(ref.get("asset_type") == "scene" and "city" in json.dumps(ref, ensure_ascii=False).lower() for ref in refs)
    assert not any(ref.get("asset_type") == "scene" and "城市" in json.dumps(ref, ensure_ascii=False) for ref in refs)
    assert diagnostics
    assert any(item["reason"] == "audio_only_non_visual_city_reference" for item in diagnostics)
    assert graph["asset_count"] == 0
    assert graph["held_asset_ref_count"] >= 1
    assert candidate_set["summary"]["candidate_count"] == 0


def test_visible_city_evidence_creates_visual_scene_with_span() -> None:
    script = "Rain-night city street with skyline, buildings, neon signs, and wet road. 林晚 walks under the visible lights."

    shots = local_storyboard_shots(script)
    refs = _all_refs(shots)
    graph = build_asset_graph(shots, source_text=script)
    scene_refs = [ref for ref in refs if ref.get("asset_type") == "scene"]

    assert scene_refs
    scene = scene_refs[0]
    assert scene["evidence_modality"] == "visual"
    assert scene["visual_evidence_span"]
    assert scene["modality_gate_status"] == "accepted"
    assert any(asset.get("asset_type") == "scene" and asset.get("evidence_modality") == "visual" for asset in graph["assets"])


def test_generic_character_is_provisional_only_with_visual_context() -> None:
    generic_only = local_storyboard_shots("@人物")
    generic_refs = _all_refs(generic_only)
    generic_diagnostics = _all_diagnostics(generic_only)

    assert not any(ref.get("display_name") in {"人", "人物", "主角"} or ref.get("label") in {"人", "人物", "主角"} for ref in generic_refs)
    assert any(item["reason"] == "unresolved_generic_character" for item in generic_diagnostics)

    visual_generic = local_storyboard_shots("@人物 在雨夜城市街道奔跑，穿红色外套，霓虹照亮侧脸。")
    character = next(ref for ref in _all_refs(visual_generic) if ref["asset_type"] == "character")

    assert character["display_name"] not in {"人", "人物", "主角"}
    assert character["provisional_name"] is True
    assert character["name_source"] == "visual_context_provisional"
    assert character["modality_gate_status"] == "accepted"


def test_exact_named_character_aggregates_without_canonical_identity_claim() -> None:
    script = "林晚站在雨夜城市街道。林晚回头看向霓虹。"
    shots = local_storyboard_shots(script, shot_count_hint=2)
    graph = build_asset_graph(shots, source_text=script)
    candidate_set = build_asset_card_candidates(project_id="proj_linwan", asset_graph=graph)

    linwan = next(asset for asset in graph["assets"] if asset.get("display_name") == "林晚")
    candidate = next(item for item in candidate_set["candidates"] if item["draft_fields"]["display_name"] == "林晚")
    serialized = json.dumps({"asset": linwan, "candidate": candidate}, ensure_ascii=False).lower()

    assert len(linwan["shot_refs"]) == 2
    assert candidate["reuse_policy"]["suggested_reuse_scope"] == "project_reuse_candidate"
    assert "canonical" not in serialized
    assert "fixed identity" not in serialized
    assert candidate["asset_memory_policy"]["writes_fixed_asset"] is False


def test_ambiguous_aliases_are_not_auto_merged_into_named_character() -> None:
    script = "林晚站在雨夜城市街道。女孩低头穿过人群。她听见远处城市噪音。"
    shots = local_storyboard_shots(script, shot_count_hint=3)
    graph = build_asset_graph(shots, source_text=script)
    character_names = {asset.get("display_name") for asset in graph["assets"] if asset.get("asset_type") == "character"}

    assert "林晚" in character_names
    assert "女孩" in character_names
    assert len([asset for asset in graph["assets"] if asset.get("display_name") == "林晚"]) == 1
    assert not any("她" in str(asset.get("aliases") or []) and asset.get("display_name") == "林晚" for asset in graph["assets"])


def test_principal_character_extraction_prioritizes_relationship_subjects_and_keeps_key_props() -> None:
    script = "唐僧娶了白骨精，孙悟空和猪八戒在远处旁观。@金箍棒 靠在殿门边。"
    shots = local_storyboard_shots(script, shot_count_hint=1)
    refs = _all_refs(shots)
    diagnostics = _all_diagnostics(shots)
    graph = build_asset_graph(shots, source_text=script)
    character_names = [ref["display_name"] for ref in refs if ref["asset_type"] == "character"]

    assert character_names == ["唐僧", "白骨精"]
    assert not any(ref.get("display_name") == "孙悟空" for ref in refs)
    assert not any(ref.get("display_name") == "猪八戒" for ref in refs)
    assert any(ref.get("display_name") == "金箍棒" and ref.get("asset_type") == "prop" for ref in refs)
    assert any(item["display_name"] == "孙悟空" and item["reason"] == "secondary_character_requires_manual_asset_entry" for item in diagnostics)
    assert not any(item["display_name"] == "金箍棒" and item["reason"] == "prop_requires_manual_asset_entry" for item in diagnostics)
    assert graph["held_asset_ref_count"] >= 1


def test_provider_asset_refs_pass_through_same_modality_gate() -> None:
    audio_payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "5s",
                "description": "Only distant city noise and ambience under a black screen.",
                "source_span": {"span_id": "script_span_01", "text": "distant city noise and ambience"},
                "asset_refs": [{"label": "城市噪音", "asset_type": "scene", "status": "mentioned", "source": "provider"}],
            }
        ]
    }
    visual_payload = {
        "shots": [
            {
                "shot_id": "shot_01",
                "index": 1,
                "duration": "5s",
                "description": "Rain-night city street, skyline, buildings, neon signs, wet road.",
                "source_span": {"span_id": "script_span_01", "text": "Rain-night city street, skyline, buildings, neon signs, wet road."},
                "asset_refs": [{"label": "rain-night city street", "asset_type": "scene", "status": "mentioned", "source": "provider"}],
            }
        ]
    }

    audio_shots = shots_from_provider_text(json.dumps(audio_payload), source_script_text="distant city noise and ambience")
    visual_shots = shots_from_provider_text(json.dumps(visual_payload), source_script_text=visual_payload["shots"][0]["source_span"]["text"])

    assert audio_shots[0]["asset_refs"] == []
    assert audio_shots[0]["dropped_asset_ref_diagnostics"][0]["reason"] == "audio_only_non_visual_city_reference"
    assert visual_shots[0]["asset_refs"][0]["evidence_modality"] == "visual"
    assert visual_shots[0]["asset_refs"][0]["visual_evidence_span"]
