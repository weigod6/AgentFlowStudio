from __future__ import annotations

import hashlib
import re
from typing import Any

from agentflow.algorithms.asset_facts import build_asset_fact_profile
from agentflow.algorithms.asset_card_drafting._helpers import (
    animal_label_from_prompt,
    camera_motion,
    clean_text,
    is_animal_subject_text,
    missing_fields,
    prop_category,
    sentence_or_default,
)
from agentflow.algorithms.provider_gate_manifest import succeeded_manifest


ALGORITHM_ID = "afs.asset_card_drafting.v0.1"
INPUT_CONTRACT = "asset type, safe media artifact refs, prompt text, provider service id"
OUTPUT_CONTRACT = "editable asset card draft with confidence, missing fields, candidate locks, and safe evidence"
FAILURE_MODES = ("vision_gate_closed", "unsupported_asset_type", "missing_media_ref", "unsafe_draft_rejected")
EVIDENCE_BOUNDARY = "draft safe summary only; no fixed asset writes before human confirmation"

ASSET_TYPES = {"character", "scene", "prop", "video"}


def draft_asset_card(
    *,
    asset_type: str,
    project_id: str,
    draft_id: str,
    source_image_asset_refs: list[str],
    sampled_image_asset_refs: list[str],
    source_video_artifact_id: str | None,
    prompt_text: str,
    provider_service_id: str,
) -> dict[str, Any]:
    if asset_type not in ASSET_TYPES:
        raise ValueError("asset_type must be character, scene, prop, or video")
    text = clean_text(prompt_text)
    if asset_type == "character":
        draft = _character_draft(draft_id, project_id, source_image_asset_refs, text)
    elif asset_type == "scene":
        draft = _scene_draft(draft_id, project_id, source_image_asset_refs, text)
    elif asset_type == "prop":
        draft = _prop_draft(draft_id, project_id, source_image_asset_refs, text)
    else:
        draft = _video_draft(draft_id, project_id, source_video_artifact_id, sampled_image_asset_refs, text)
    draft["safe_manifest"] = succeeded_manifest(
        action="asset_card_draft",
        capability="vision",
        provider_service_id=provider_service_id,
        evidence=draft["safe_evidence"],
    )
    return draft


def draft_id_from_refs(project_id: str, generated_at: str, refs: list[str]) -> str:
    digest = hashlib.sha256(f"{project_id}:{generated_at}:{'|'.join(refs)}".encode("utf-8")).hexdigest()[:12]
    return f"draft_{digest}"


def _character_draft(draft_id: str, project_id: str, refs: list[str], prompt_text: str) -> dict[str, Any]:
    if is_animal_subject_text(prompt_text):
        return _animal_subject_draft(draft_id, project_id, refs, prompt_text)
    label = _label_from_prompt(prompt_text, fallback="角色资产草稿")
    card = {
        "identity": sentence_or_default(prompt_text, "参考图中的主要角色，身份待人工确认"),
        "hair": "发型、发色待人工确认",
        "face": "面部辨识点待人工确认",
        "build": "体态比例待人工确认",
        "wardrobe": "标志性服装待人工确认",
        "palette": "主色调待人工确认",
        "demeanor": "神态气质待人工确认",
        "reference_views": "正面全身、侧面全身、背面全身、头部/关键细节视图待人工确认",
    }
    return _base_draft(
        draft_id=draft_id,
        project_id=project_id,
        asset_type="character",
        label=label,
        signature=f"{label}: reference role subject, pending human confirmation",
        feature_card=card,
        candidate_locks=["keep role identity", "keep face recognizability", "keep signature wardrobe", "keep reference-sheet views consistent"],
        confidence=0.62,
        missing_fields=missing_fields(card),
        source_image_asset_refs=refs,
        sampled_image_asset_refs=[],
        source_video_artifact_id=None,
        prompt_text=prompt_text,
    )


def _animal_subject_draft(draft_id: str, project_id: str, refs: list[str], prompt_text: str) -> dict[str, Any]:
    label = animal_label_from_prompt(prompt_text)
    fact_profile = build_asset_fact_profile(
        asset_type="character",
        label=label,
        evidence_text=prompt_text,
    )
    facts = fact_profile.get("facts") if isinstance(fact_profile.get("facts"), dict) else {}
    marks = "、".join(str(item) for item in facts.get("distinctive_marks", [])[:4]) if isinstance(facts.get("distinctive_marks"), list) else ""
    actions = "、".join(str(item) for item in facts.get("current_action", [])[:4]) if isinstance(facts.get("current_action"), list) else ""
    card = {
        "identity": sentence_or_default(prompt_text, f"参考图中的同一只{label}，物种和身份来自当前证据"),
        "hair": f"毛色/毛发纹理：{facts.get('color_pattern')}" if facts.get("color_pattern") else "毛色、毛发纹理和斑纹待人工确认",
        "face": f"头部/脸部辨识点：{marks}" if marks else "脸部斑纹、眼睛、耳朵和胡须辨识点待人工确认",
        "build": f"体型/年龄感：{facts.get('size_or_age')}" if facts.get("size_or_age") else "体型比例、四肢和尾巴形态待人工确认",
        "wardrobe": "默认保持自然动物外观；服装、饰品或拟人化只在用户明确要求时添加",
        "palette": "主体毛色主色调待人工确认",
        "demeanor": f"动物神态和姿态：{actions}" if actions else "动物神态和姿态待人工确认",
        "reference_views": "正面全身、侧面全身、背面全身、头部/脸部细节视图待人工确认",
    }
    return _base_draft(
        draft_id=draft_id,
        project_id=project_id,
        asset_type="character",
        label=label,
        signature=f"{label}: reference animal subject, pending confirmation",
        feature_card=card,
        candidate_locks=[
            "keep animal identity", "keep fur color and markings", "keep eyes ears tail and body ratio",
            "keep reference-sheet views consistent", "only add human hair clothing or anthropomorphic traits when explicitly requested",
            *[str(item) for item in fact_profile.get("continuity_locks", [])[:4]],
        ],
        confidence=0.62,
        missing_fields=missing_fields(card),
        source_image_asset_refs=refs,
        sampled_image_asset_refs=[],
        source_video_artifact_id=None,
        prompt_text=prompt_text,
    )


def _scene_draft(draft_id: str, project_id: str, refs: list[str], prompt_text: str) -> dict[str, Any]:
    label = _label_from_prompt(prompt_text, fallback="场景资产草稿")
    card = {
        "location": sentence_or_default(prompt_text, "场景地点待人工确认"),
        "layout": "空间结构待人工确认",
        "props": "关键道具待人工确认",
        "lighting_mood": "光线和氛围待人工确认",
        "palette": "场景配色待人工确认",
        "time_weather": "时间与天气待人工确认",
        "view_set": "俯瞰全景、正向广角、入口/边缘视角、光影或材质细节视角待人工确认",
    }
    return _base_draft(
        draft_id=draft_id,
        project_id=project_id,
        asset_type="scene",
        label=label,
        signature=f"{label}: reference scene, pending human confirmation",
        feature_card=card,
        candidate_locks=["keep spatial layout", "keep multi-angle scene views consistent", "keep key props", "keep lighting mood"],
        confidence=0.6,
        missing_fields=missing_fields(card),
        source_image_asset_refs=refs,
        sampled_image_asset_refs=[],
        source_video_artifact_id=None,
        prompt_text=prompt_text,
    )


def _prop_draft(draft_id: str, project_id: str, refs: list[str], prompt_text: str) -> dict[str, Any]:
    label = _label_from_prompt(prompt_text, fallback="道具资产草稿")
    card = {
        "category": prop_category(prompt_text),
        "appearance": sentence_or_default(prompt_text, "参考图中的道具外观待人工确认"),
        "material": "材质工艺待人工确认",
        "scale": "与角色或场景的比例关系待人工确认",
        "usage": "使用方式待人工确认",
        "continuity": "后续镜头连续性待人工确认",
        "reference_views": "正面、侧面、俯视、局部结构/材质特写待人工确认",
    }
    return _base_draft(
        draft_id=draft_id,
        project_id=project_id,
        asset_type="prop",
        label=label,
        signature=f"{label}: reusable prop, pending human confirmation",
        feature_card=card,
        candidate_locks=["keep prop appearance", "keep prop multi-view sheet consistent", "keep material and scale", "keep usage continuity"],
        confidence=0.58,
        missing_fields=missing_fields(card),
        source_image_asset_refs=refs,
        sampled_image_asset_refs=[],
        source_video_artifact_id=None,
        prompt_text=prompt_text,
    )


def _video_draft(
    draft_id: str,
    project_id: str,
    source_video_artifact_id: str | None,
    sampled_refs: list[str],
    prompt_text: str,
) -> dict[str, Any]:
    label = _label_from_prompt(prompt_text, fallback="视频资产草稿")
    segment = {
        "segment_id": "seg_001",
        "start_time_sec": 0.0,
        "end_time_sec": 5.0,
        "visible_subjects": ["primary subject pending confirmation"],
        "actions": [sentence_or_default(prompt_text, "main action pending confirmation")],
        "scene_state": "scene continuity pending confirmation",
        "camera_motion": camera_motion(prompt_text),
        "props": [],
        "continuity_anchors": ["first usable frame", "last usable frame"],
        "drift_risks": ["identity drift", "motion drift"],
        "usable_reference_frames": [*sampled_refs[:4]],
    }
    summary = sentence_or_default(prompt_text, "视频内容摘要待人工确认")
    card = {
        "summary": summary,
        "temporal_scope": "single generated shot",
        "camera_motion": segment["camera_motion"],
        "continuity_anchors": segment["continuity_anchors"],
        "drift_risks": segment["drift_risks"],
    }
    draft = _base_draft(
        draft_id=draft_id,
        project_id=project_id,
        asset_type="video",
        label=label,
        signature=f"{label}: video motion reference, pending human confirmation",
        feature_card=card,
        candidate_locks=["preserve temporal action", "preserve camera motion", "preserve continuity anchors"],
        confidence=0.58,
        missing_fields=missing_fields(card),
        source_image_asset_refs=[],
        sampled_image_asset_refs=sampled_refs,
        source_video_artifact_id=source_video_artifact_id,
        prompt_text=prompt_text,
    )
    draft["summary"] = summary
    draft["segments"] = [segment]
    return draft


def _base_draft(
    *,
    draft_id: str,
    project_id: str,
    asset_type: str,
    label: str,
    signature: str,
    feature_card: dict[str, Any],
    candidate_locks: list[str],
    confidence: float,
    missing_fields: list[str],
    source_image_asset_refs: list[str],
    sampled_image_asset_refs: list[str],
    source_video_artifact_id: str | None,
    prompt_text: str,
) -> dict[str, Any]:
    fact_profile = build_asset_fact_profile(
        asset_type=asset_type if asset_type in {"character", "scene", "prop"} else "prop",
        label=label,
        evidence_text=prompt_text,
    )
    return {
        "artifact_type": "agentflow_asset_card_draft",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "draft_id": draft_id,
        "asset_type": asset_type,
        "character_subtype": str(fact_profile.get("character_subtype") or ""),
        "status": "draft",
        "label_suggestion": label,
        "signature": signature,
        "feature_card": feature_card,
        "facts": fact_profile.get("facts") if isinstance(fact_profile.get("facts"), dict) else {},
        "fact_evidence": fact_profile.get("fact_evidence") if isinstance(fact_profile.get("fact_evidence"), list) else [],
        "continuity_locks": fact_profile.get("continuity_locks") if isinstance(fact_profile.get("continuity_locks"), list) else [],
        "negative_locks": fact_profile.get("negative_locks") if isinstance(fact_profile.get("negative_locks"), list) else [],
        "missing_fact_fields": fact_profile.get("missing_fact_fields") if isinstance(fact_profile.get("missing_fact_fields"), list) else [],
        "candidate_locks": candidate_locks,
        "confidence": confidence,
        "missing_fields": missing_fields,
        "source_image_asset_refs": list(source_image_asset_refs),
        "sampled_image_asset_refs": list(sampled_image_asset_refs),
        "source_video_artifact_id": source_video_artifact_id,
        "safe_evidence": {
            "source_image_asset_count": len(source_image_asset_refs),
            "sampled_image_asset_count": len(sampled_image_asset_refs),
            "has_video_artifact_ref": bool(source_video_artifact_id),
            "prompt_char_count": len(prompt_text),
            "claim_boundary": "vision_draft_needs_human_confirmation",
        },
        "asset_memory_policy": {
            "writes_fixed_asset": False,
            "included_in_context_before_confirmation": False,
            "requires_human_confirmation": True,
        },
    }


def _label_from_prompt(prompt_text: str, *, fallback: str) -> str:
    text = clean_text(prompt_text)
    if not text:
        return fallback
    words = re.split(r"\s+", text)
    return " ".join(words[:6]).strip(" ,.;:，。；：")[:80] or fallback


__all__ = (
    "ALGORITHM_ID",
    "ASSET_TYPES",
    "EVIDENCE_BOUNDARY",
    "FAILURE_MODES",
    "INPUT_CONTRACT",
    "OUTPUT_CONTRACT",
    "draft_asset_card",
    "draft_id_from_refs",
)
