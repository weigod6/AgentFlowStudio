from __future__ import annotations

import re
from typing import Any

from agentflow.algorithms.asset_facts import animal_assets_only, has_animal_asset, has_human_asset
from agentflow.algorithms.prompt_integrity import validate_prompt_integrity
from agentflow.algorithms.provider_gate_manifest.asset_graph_context import (
    asset_graph_from_context_bundle,
    asset_graph_feedback_overlay_from_context_bundle,
    asset_graph_feedback_overlay_from_context_subgraph,
    format_asset_graph_prompt_lines,
    summarize_asset_graph_for_plan,
    asset_graph_from_context_subgraph,
)
from agentflow.knowledge.director_scenarios import (
    director_scenario_from_text,
)
from agentflow.knowledge.expert_knowledge import expert_knowledge_from_text
from agentflow.knowledge.professional_reference import professional_reference_from_text


IMAGE_EDIT_REPLACEMENTS = {
    "\u672c\u6b21\u53ea\u505a\u8fd9\u4e00\u9879\u56fe\u751f\u56fe\u7f16\u8f91": "\u672c\u6b21\u751f\u6210\u8fde\u7eed\u89c6\u9891\u6bb5\u843d",
    "\u5355\u5e27\u56fe\u50cf\u7f16\u8f91\uff0c\u4e0d\u5236\u9020\u591a\u9636\u6bb5\u52a8\u4f5c\u6216\u5267\u60c5": "\u8fde\u7eed\u89c6\u9891\u8fd0\u52a8\uff0c\u52a8\u4f5c\u81ea\u7136\u63a8\u8fdb",
    "\u5355\u5e27\u5173\u952e\u753b\u9762\uff0c\u4e0d\u5236\u9020\u591a\u9636\u6bb5\u52a8\u4f5c": "\u8fde\u7eed\u89c6\u9891\u8fd0\u52a8\uff0c\u52a8\u4f5c\u81ea\u7136\u63a8\u8fdb",
    "\u4eba\u7269\u4fdd\u6301\u53c2\u8003\u56fe\u539f\u6709\u9759\u6001\u59ff\u6001\u548c\u8eab\u4f53\u671d\u5411": "\u4eba\u7269\u4ece\u9996\u5e27\u59ff\u6001\u81ea\u7136\u5f00\u59cb\u8fd0\u52a8\uff0c\u4fdd\u6301\u8eab\u4f53\u6bd4\u4f8b\u548c\u8eab\u4efd\u4e00\u81f4",
    "\u53ea\u5448\u73b0": "\u4ee5\u8fde\u7eed\u8fd0\u52a8\u5448\u73b0",
}


def strip_image_edit_language(value: str) -> str:
    text = str(value or "")
    for before, after in IMAGE_EDIT_REPLACEMENTS.items():
        text = text.replace(before, after)
    return re.sub(r"\s+", " ", text).strip()


def strip_keyframe_video_safety_language(value: str) -> str:
    text = strip_image_edit_language(value)
    for before, after in (
        ("保留对峙关系", "保持首帧空间关系"),
        ("对峙张力", "温和连续性"),
        ("对峙", "同框互动"),
        ("冲突张力增强", "情绪自然推进"),
        ("冲突张力", "情绪节奏"),
        ("冲突", "互动"),
        ("蓄势", "姿态微调"),
    ):
        text = text.replace(before, after)
    if _legacy_keyframe_video_prompt_text(text) and _care_sensitive_video_text(text):
        for before, after in (
            ("刚叼回一只", "正在照看一只"),
            ("叼回", "带回"),
            ("叼着", "靠近"),
            ("蹬踹", "轻微小幅动作"),
            ("爪子悬在半空", "爪子保持自然小幅动作"),
            ("挣扎", "轻微动作"),
            ("湿漉漉", "毛发湿润"),
            ("滴着水", "带有水珠"),
            ("炸毛", "毛发状态"),
            ("死命一塞", "轻轻靠近"),
            ("塞进", "靠近"),
            ("缺耳", "耳部特征"),
            ("缺了一小块", "耳部特征"),
            ("伤疤", "可见细节"),
            ("旧伤疤", "可见细节"),
            ("伤口", "可见细节"),
            ("流血", "可见细节"),
            ("锁链", "可见细节"),
            ("金属撞击", "可见细节"),
            ("攻击", "高强度动作"),
            ("威胁", "高强度动作"),
            ("追逐", "高强度动作"),
            ("打斗", "高强度动作"),
        ):
            text = text.replace(before, after)
    return re.sub(r"\s+", " ", text).strip()


def video_provider_prompt(
    *,
    prompt_text: str,
    optimized_prompt: str | None,
    duration_sec: int | float | str,
    motion: str | None,
    last_frame_image_asset_id: str | None,
    context_bundle: dict[str, Any] | None,
    context_subgraph: Any | None = None,
    limit: int = 4000,
) -> str:
    base = strip_keyframe_video_safety_language(optimized_prompt or prompt_text)
    plan = video_generation_plan(
        prompt_text=prompt_text,
        optimized_prompt=optimized_prompt,
        duration_sec=duration_sec,
        motion=motion,
        last_frame_image_asset_id=last_frame_image_asset_id,
        context_bundle=context_bundle,
        context_subgraph=context_subgraph,
    )
    asset_graph_context = plan.get("asset_graph_context")
    parts = [
        base,
        f"Video task: generate a continuous {duration_sec}s image-to-video clip from the first frame.",
        _first_frame_anchor_instruction(asset_graph_context),
        format_asset_graph_prompt_lines(asset_graph_context),
    ]
    if motion:
        parts.append(f"Motion: {strip_keyframe_video_safety_language(motion)}")
    if last_frame_image_asset_id:
        parts.append("Use the last frame as the ending visual anchor; interpolate motion smoothly between first and last frame.")
    text_channel = context_bundle.get("text_channel") if isinstance(context_bundle, dict) else None
    if isinstance(text_channel, dict):
        for label, key in (
            ("Asset identity", "asset_identity_segment"),
            ("Asset signatures", "asset_signature_segment"),
            ("Director setup", "scene_director_segment"),
            ("Style", "preference_segment"),
        ):
            value = strip_keyframe_video_safety_language(str(text_channel.get(key) or "").strip())
            if value:
                parts.append(f"{label}: {value}")
    parts.extend(
        [
            _format_professional_reference_for_video(plan.get("professional_reference", {})),
            _format_director_scenario_for_video(plan.get("director_scenario", {})),
            _format_expert_knowledge_for_video(plan.get("expert_knowledge", {}), asset_graph_context),
            _format_motion_plan_for_prompt(plan["motion_plan"]),
            _format_temporal_director_plan_for_prompt(plan.get("temporal_director_plan", {})),
        ]
    )
    parts.append(_avoid_prompt_line(asset_graph_context))
    text = "\n".join(part for part in parts if part.strip())
    validate_prompt_integrity(text, field_name="video_provider_prompt")
    return validate_prompt_integrity(text[:limit], field_name="video_provider_prompt")


def video_generation_plan(
    *,
    prompt_text: str,
    optimized_prompt: str | None,
    duration_sec: int | float | str,
    motion: str | None,
    last_frame_image_asset_id: str | None,
    context_bundle: dict[str, Any] | None,
    context_subgraph: Any | None = None,
) -> dict[str, Any]:
    source = _combined_source(prompt_text, optimized_prompt, motion, context_bundle)
    asset_graph = asset_graph_from_context_subgraph(context_subgraph) or asset_graph_from_context_bundle(context_bundle)
    feedback_overlay = asset_graph_feedback_overlay_from_context_subgraph(context_subgraph) or asset_graph_feedback_overlay_from_context_bundle(context_bundle)
    asset_graph_context = summarize_asset_graph_for_plan(asset_graph, feedback_overlay=feedback_overlay)
    expert_knowledge = expert_knowledge_from_text(source, node_type="video", generation_target="video")
    motion_plan = video_motion_plan(
        duration_sec=duration_sec,
        motion=motion,
        last_frame_image_asset_id=last_frame_image_asset_id,
        source_text=source,
    )
    return {
        "artifact_type": "agentflow_video_generation_plan",
        "schema_version": "0.1.0",
        "motion_plan": motion_plan,
        "editing_plan": video_editing_plan(
            source_text=source,
            last_frame_image_asset_id=last_frame_image_asset_id,
            asset_graph_context=asset_graph_context,
            expert_knowledge=expert_knowledge,
        ),
        "temporal_director_plan": video_temporal_director_plan(
            duration_sec=duration_sec,
            motion=motion,
            last_frame_image_asset_id=last_frame_image_asset_id,
            source_text=source,
            asset_graph_context=asset_graph_context,
            expert_knowledge=expert_knowledge,
        ),
        "asset_graph_context": asset_graph_context,
        "expert_knowledge": expert_knowledge,
        "professional_reference": professional_reference_from_text(source, node_type="video", generation_target="video"),
        "director_scenario": director_scenario_from_text(source, node_type="video", generation_target="video"),
        "prompt_contract": {
            "first_frame_is_strict_anchor": True,
            "time_beats_are_required": True,
            "second_level_director_timeline_required": True,
            "candidate_assets_are_editable": True,
            "asset_graph_context_used": bool(asset_graph_context.get("locked_assets")),
            "asset_graph_feedback_used": bool(asset_graph_context.get("feedback_decisions")),
            "expert_knowledge_used": True,
            "director_scenario_selected": True,
            "provider_prompt_uses_image_edit_language": False,
        },
    }


def video_motion_plan(
    *,
    duration_sec: int | float | str,
    motion: str | None,
    last_frame_image_asset_id: str | None,
    source_text: str,
) -> dict[str, Any]:
    duration = _duration_float(duration_sec)
    t1 = max(0.8, round(duration * 0.2, 1))
    t2 = max(t1 + 0.8, round(duration * 0.7, 1))
    final = round(duration, 1)
    action = strip_keyframe_video_safety_language(motion or "continue the current pose with subtle cinematic motion")
    beats = [
        {"time": f"0.0s-{t1:.1f}s", "intent": "hold first-frame identity, layout, lighting, and pose as the visual anchor"},
        {"time": f"{t1:.1f}s-{t2:.1f}s", "intent": action},
        {"time": f"{t2:.1f}s-{final:.1f}s", "intent": "settle into a readable end state without new scene or identity changes"},
    ]
    if last_frame_image_asset_id:
        beats[-1]["intent"] = "arrive at the last-frame visual anchor with smooth interpolation"
    if _has_stars(source_text):
        beats[1]["intent"] = f"{beats[1]['intent']}; add tiny star shimmer and restrained breathing motion"
    return {
        "duration_sec": final,
        "motion_style": "image_to_video_continuity",
        "time_beats": beats,
        "camera_policy": _camera_policy(source_text),
        "subject_motion_policy": "one readable action only; no rewritten plot or new subject identity",
    }


def video_temporal_director_plan(
    *,
    duration_sec: int | float | str,
    motion: str | None,
    last_frame_image_asset_id: str | None,
    source_text: str,
    asset_graph_context: dict[str, Any] | None = None,
    expert_knowledge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    duration = _duration_float(duration_sec)
    beat_count = max(1, min(int(round(duration)), 12))
    domains = _expert_domains(expert_knowledge)
    action = strip_keyframe_video_safety_language(motion or "continue the current first-frame action with restrained motion")
    continuity = _asset_continuity_phrase(asset_graph_context)
    forbidden = _forbidden_video_changes(source_text, asset_graph_context)
    beats = []
    for index in range(beat_count):
        start = round(index * duration / beat_count, 1)
        end = round((index + 1) * duration / beat_count, 1)
        phase = _timeline_phase(index, beat_count)
        beats.append(
            {
                "time": f"{start:.1f}s-{end:.1f}s",
                "phase": phase,
                "character_state": _timeline_character_state(phase, source_text, asset_graph_context),
                "action": _timeline_action(phase, action, source_text, last_frame_image_asset_id),
                "camera_state": _domain_decision(domains, "camera"),
                "lighting_state": _domain_decision(domains, "lighting"),
                "depth_of_field": _domain_decision(domains, "depth_of_field"),
                "composition_guard": _composition_guard(source_text, asset_graph_context),
                "asset_continuity": continuity,
                "forbidden_changes": forbidden[:8],
                "edit_intent": _timeline_edit_intent(phase),
            }
        )
    return {
        "artifact_type": "agentflow_temporal_director_plan",
        "schema_version": "0.1.0",
        "duration_sec": round(duration, 1),
        "granularity": "second_level",
        "beat_count": len(beats),
        "beats": beats,
        "knowledge_domains": sorted(domains),
        "asset_graph_context_used": bool(asset_graph_context and asset_graph_context.get("locked_assets")),
        "feedback_overlay_used": bool(asset_graph_context and asset_graph_context.get("feedback_decisions")),
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def video_editing_plan(
    *,
    source_text: str,
    last_frame_image_asset_id: str | None,
    asset_graph_context: dict[str, Any] | None = None,
    expert_knowledge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    domains = _expert_domains(expert_knowledge)
    return {
        "clip_role": "single_continuity_clip",
        "transition_in": "start_from_first_frame",
        "transition_out": "match_last_frame_anchor" if last_frame_image_asset_id else "hold_readable_end_state",
        "pacing": _domain_decision(domains, "editing_pacing") or ("slow_observational" if _has_stars(source_text) else "medium_continuous"),
        "continuity_locks": _continuity_locks(source_text, asset_graph_context),
        "forbidden_changes": _forbidden_video_changes(source_text, asset_graph_context),
    }


def _format_professional_reference_for_video(context: dict[str, Any]) -> str:
    if not isinstance(context, dict):
        return ""
    chunks: list[str] = []
    for key in ("scene_continuity", "camera", "lighting", "depth_of_field", "pacing"):
        ref = context.get(key)
        if not isinstance(ref, dict):
            continue
        decision = str(ref.get("decision") or "").strip()
        if decision:
            chunks.append(f"{key}={decision[:130]}")
    return "Professional video reference: " + "; ".join(chunks) if chunks else ""


def _format_director_scenario_for_video(context: dict[str, Any]) -> str:
    if not isinstance(context, dict):
        return ""
    packs = context.get("selected_packs") if isinstance(context.get("selected_packs"), list) else []
    primary = packs[0] if packs and isinstance(packs[0], dict) else {}
    label = str(primary.get("label") or context.get("primary_scenario") or "General Short Video")
    goal = str(context.get("scenario_goal") or primary.get("scenario_goal") or "").strip()
    timeline = "; ".join(str(item) for item in (context.get("timeline_template") or primary.get("timeline_template") or [])[:2])
    continuity = "; ".join(str(item) for item in (context.get("continuity_rules") or primary.get("continuity_rules") or [])[:2])
    parts = [f"[{label}]"]
    if goal:
        parts.append(f"goal={goal[:140]}")
    if timeline:
        parts.append(f"timeline={timeline[:180]}")
    if continuity:
        parts.append(f"continuity={continuity[:160]}")
    return "Director scenario video guidance: " + "; ".join(parts)


def _format_expert_knowledge_for_video(context: dict[str, Any], asset_graph_context: dict[str, Any] | None = None) -> str:
    domains = context.get("domains") if isinstance(context, dict) else {}
    if not isinstance(domains, dict):
        return ""
    lines = ["Expert knowledge reference:"]
    animal_only = animal_assets_only(_graph_locked_assets(asset_graph_context))
    for domain in ("camera", "lighting", "depth_of_field", "editing_pacing", "motion_design", "continuity"):
        section = domains.get(domain)
        if not isinstance(section, dict):
            continue
        decision = str(section.get("decision") or "").strip()
        if animal_only and domain == "continuity":
            decision = (
                "continuity tracks animal identity/species, fur or skin markings, body proportions, "
                "scene layout, light direction, and camera composition"
            )
        if decision:
            lines.append(f"- {domain}: {decision[:120]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_motion_plan_for_prompt(motion_plan: dict[str, Any]) -> str:
    beats = motion_plan.get("time_beats") if isinstance(motion_plan, dict) else []
    if not isinstance(beats, list):
        beats = []
    lines = ["Temporal plan:"]
    for beat in beats:
        if isinstance(beat, dict):
            lines.append(f"- {beat.get('time')}: {beat.get('intent')}")
    return "\n".join(lines)


def _format_temporal_director_plan_for_prompt(plan: dict[str, Any]) -> str:
    beats = plan.get("beats") if isinstance(plan, dict) else []
    if not isinstance(beats, list) or not beats:
        return ""
    lines = ["Second-level director timeline:"]
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        action = str(beat.get("action") or "")[:120]
        guard = str(beat.get("composition_guard") or "")[:100]
        lines.append(
            "- "
            f"{beat.get('time')}: "
            f"phase={beat.get('phase')}; "
            f"action={action}; "
            f"guard={guard}"
        )
    return "\n".join(lines)


def _combined_source(
    prompt_text: str,
    optimized_prompt: str | None,
    motion: str | None,
    context_bundle: dict[str, Any] | None,
) -> str:
    parts = [prompt_text, optimized_prompt or "", motion or ""]
    text_channel = context_bundle.get("text_channel") if isinstance(context_bundle, dict) else None
    if isinstance(text_channel, dict):
        parts.extend(str(text_channel.get(key) or "") for key in sorted(text_channel))
    return "\n".join(part for part in parts if str(part or "").strip())


def _expert_domains(expert_knowledge: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(expert_knowledge, dict):
        return {}
    domains = expert_knowledge.get("domains")
    return {str(key): value for key, value in domains.items() if isinstance(value, dict)} if isinstance(domains, dict) else {}


def _domain_decision(domains: dict[str, dict[str, Any]], domain: str) -> str:
    return str((domains.get(domain) or {}).get("decision") or "").strip()


def _asset_continuity_phrase(asset_graph_context: dict[str, Any] | None) -> str:
    assets = _graph_locked_assets(asset_graph_context)
    if not assets:
        return "preserve first-frame identity, scene layout, lighting direction, and composition"
    parts = []
    for asset in assets[:4]:
        label = str(asset.get("label") or asset.get("graph_asset_id") or "asset")
        locks = "; ".join(_strings(asset.get("continuity_locks"), limit=3))
        parts.append(f"{label}: {locks or 'approved identity and layout'}")
    blocked = _strings((asset_graph_context or {}).get("blocked_graph_asset_ids") if isinstance(asset_graph_context, dict) else [], limit=4)
    suffix = f"; rejected graph ids stay excluded: {', '.join(blocked)}" if blocked else ""
    return " | ".join(parts) + suffix


def _timeline_phase(index: int, beat_count: int) -> str:
    if index == 0:
        return "anchor"
    if index == beat_count - 1:
        return "settle"
    if index >= max(1, beat_count - 2):
        return "pre_settle"
    return "develop"


def _first_frame_anchor_instruction(asset_graph_context: dict[str, Any] | None) -> str:
    assets = _graph_locked_assets(asset_graph_context)
    if animal_assets_only(assets):
        return (
            "Use the first frame as a strict visual anchor for animal identity, species, fur/skin markings, "
            "ears/tail silhouette, body proportions, scene layout, lighting, color palette, and composition."
        )
    if has_animal_asset(assets) and not has_human_asset(assets):
        return (
            "Use the first frame as a strict visual anchor for visible subject identity, species/material, "
            "surface markings, body proportions, scene layout, lighting, color palette, and composition."
        )
    if has_human_asset(assets):
        return (
            "Use the first frame as a strict visual anchor for identity, clothing, hairstyle silhouette, "
            "body proportions, scene layout, lighting, color palette, and composition."
        )
    return (
        "Use the first frame as a strict visual anchor for visible identity, material/texture, body or structure "
        "proportions, scene layout, lighting, color palette, and composition."
    )


def _avoid_prompt_line(asset_graph_context: dict[str, Any] | None) -> str:
    assets = _graph_locked_assets(asset_graph_context)
    shared = "Avoid static single-frame language, image-edit wording, identity drift, sudden scene changes, text, watermark, UI, borders, or abrupt transitions."
    if animal_assets_only(assets):
        return (
            "Avoid static single-frame language, image-edit wording, identity/species drift, fur or marking changes, "
            "unrequested human adornments, sudden scene changes, text, watermark, UI, borders, distorted anatomy, or abrupt transitions."
        )
    if has_human_asset(assets):
        return (
            "Avoid static single-frame language, image-edit wording, identity drift, face changes, wardrobe changes, "
            "sudden scene changes, text, watermark, UI, borders, distorted limbs, or abrupt transitions."
        )
    return shared


def _timeline_character_state(phase: str, source_text: str, asset_graph_context: dict[str, Any] | None = None) -> str:
    assets = _graph_locked_assets(asset_graph_context)
    animal_only = animal_assets_only(assets)
    if phase == "anchor":
        if animal_only:
            return "exact first-frame animal identity, species, fur/markings, ears/tail silhouette, pose, and scene relationship"
        return "exact first-frame identity, silhouette, pose, material, and scene relationship"
    if phase == "settle":
        if animal_only:
            return "same animal identity, species, fur/markings, and readable final posture with no new character facts"
        return "same identity and materials, readable final pose with no new character facts"
    if _has_robot(source_text):
        return "robot keeps mechanical proportions and approved shell while making only joint-consistent micro movement"
    if animal_only:
        return "animal subjects keep species, fur/markings, ears/tail/body proportions, and current emotional direction"
    return "subject keeps identity, wardrobe/material, proportions, and emotional direction"


def _timeline_action(phase: str, action: str, source_text: str, last_frame_image_asset_id: str | None) -> str:
    if phase == "anchor":
        return "hold the first-frame pose long enough for visual identity to register"
    if phase == "settle":
        return "arrive at the last-frame anchor with smooth interpolation" if last_frame_image_asset_id else "settle into a stable end pose without adding plot"
    if phase == "pre_settle":
        return "reduce motion amplitude and prepare the final readable hold"
    if _has_stars(source_text):
        return f"{action}; restrained gaze shift, tiny star shimmer, and subtle breathing camera only"
    return action


def _timeline_edit_intent(phase: str) -> str:
    return {
        "anchor": "establish continuity from the first frame",
        "develop": "advance one controlled motion idea",
        "pre_settle": "ease motion and avoid a late new event",
        "settle": "hold a clear end state for downstream continuity",
    }.get(phase, "maintain continuity")


def _composition_guard(source_text: str, asset_graph_context: dict[str, Any] | None = None) -> str:
    if animal_assets_only(_graph_locked_assets(asset_graph_context)):
        return "keep animal subject scale, screen direction, and scene anchors stable"
    if _has_rooftop(source_text) and _has_stars(source_text):
        return "keep subject, rooftop boundary, and star field in the same readable spatial relationship"
    if _has_robot(source_text):
        return "keep robot scale, body proportions, and main scene anchors stable"
    return "keep subject scale, screen direction, and scene anchors stable"


def _duration_float(value: int | float | str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 5.0
    return max(1.0, parsed)


def _camera_policy(source_text: str) -> str:
    source = source_text.lower()
    if "push" in source or "\u63a8\u8fdb" in source_text:
        return "slow push-in, preserve subject scale and composition"
    if "follow" in source or "\u8ddf" in source_text:
        return "light follow motion, no abrupt reframing"
    return "locked-off or subtle breathing camera"


def _continuity_locks(source_text: str, asset_graph_context: dict[str, Any] | None = None) -> list[str]:
    assets = _graph_locked_assets(asset_graph_context)
    if animal_assets_only(assets):
        locks = ["identity/species", "fur/skin markings", "ears/tail/body proportions", "scene layout", "lighting direction", "camera composition"]
    elif has_human_asset(assets):
        locks = ["identity", "wardrobe/material", "scene layout", "lighting direction", "camera composition"]
    else:
        locks = ["identity", "visible materials/textures", "scene layout", "lighting direction", "camera composition"]
    if _has_robot(source_text):
        locks.append("robot shell and mechanical proportions")
    if _has_rooftop(source_text):
        locks.append("rooftop platform and sky relationship")
    for asset in assets:
        locks.extend(_strings(asset.get("continuity_locks"), limit=8))
    return _dedupe(locks)


def _forbidden_video_changes(source_text: str, asset_graph_context: dict[str, Any] | None = None) -> list[str]:
    changes = ["new characters", "new props", "text", "watermark", "UI", "borders", "identity drift", "abrupt scene transition"]
    assets = _graph_locked_assets(asset_graph_context)
    if animal_assets_only(assets):
        changes.extend(["species drift", "fur/marking changes", "unrequested human adornments"])
    elif has_human_asset(assets):
        changes.extend(["unrequested wardrobe changes", "face changes"])
    if _has_rooftop(source_text):
        changes.extend(["unrequested eaves", "unrequested chair", "unrequested stool"])
    for asset in assets:
        changes.extend(_strings(asset.get("negative_locks"), limit=8))
    changes.extend(_feedback_forbidden_changes(asset_graph_context))
    return _dedupe(changes)


def _graph_locked_assets(asset_graph_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(asset_graph_context, dict):
        return []
    assets = asset_graph_context.get("locked_assets")
    return [asset for asset in assets if isinstance(asset, dict)] if isinstance(assets, list) else []


def _feedback_forbidden_changes(asset_graph_context: dict[str, Any] | None) -> list[str]:
    if not isinstance(asset_graph_context, dict):
        return []
    decisions = asset_graph_context.get("feedback_decisions")
    if not isinstance(decisions, list):
        return []
    result: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("decision") != "reject":
            continue
        label = str(decision.get("label") or decision.get("graph_asset_id") or "asset").strip()
        result.append(f"do not use rejected asset {label}")
    return result


def _strings(value: Any, *, limit: int) -> list[str]:
    result: list[str] = []
    if not isinstance(value, list):
        return result
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _has_robot(source_text: str) -> bool:
    return "robot" in source_text.lower() or "\u673a\u5668\u4eba" in source_text


def _has_rooftop(source_text: str) -> bool:
    return "rooftop" in source_text.lower() or "\u5c4b\u9876" in source_text or "\u5929\u53f0" in source_text


def _has_stars(source_text: str) -> bool:
    return "star" in source_text.lower() or "\u661f" in source_text


def _care_sensitive_video_text(text: str) -> bool:
    return bool(
        re.search(
            r"猫|狗|犬|幼犬|奶狗|小狗|小猫|橘猫|儿童|孩子|小孩|学生|高中生|puppy|kitten|cat|dog|child|kid",
            str(text or ""),
            flags=re.I,
        )
    )


def _legacy_keyframe_video_prompt_text(text: str) -> bool:
    source = str(text or "")
    return "图生视频时间轴" in source or "上游关键帧摘要" in source or "资产连续性锁定" in source


__all__ = (
    "IMAGE_EDIT_REPLACEMENTS",
    "strip_image_edit_language",
    "strip_keyframe_video_safety_language",
    "video_editing_plan",
    "video_generation_plan",
    "video_motion_plan",
    "video_temporal_director_plan",
    "video_provider_prompt",
)
