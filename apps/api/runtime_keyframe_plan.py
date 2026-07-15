from __future__ import annotations

import re
from typing import Any

from agentflow.algorithms.asset_facts import animal_assets_only, has_human_asset
from agentflow.knowledge.director_scenarios import director_scenario_from_text
from agentflow.knowledge.professional_reference import professional_reference_from_text
from agentflow.algorithms.provider_gate_manifest.asset_graph_context import (
    asset_graph_from_context_bundle,
    asset_graph_feedback_overlay_from_context_bundle,
    asset_graph_feedback_overlay_from_context_subgraph,
    asset_graph_from_context_subgraph,
    summarize_asset_graph_for_plan,
)
from apps.api.runtime_models import KeyframeGenerationRequest


def build_keyframe_plan(
    request: KeyframeGenerationRequest,
    *,
    provider_prompt: str,
    reference_images: list[dict[str, Any]],
    context_bundle: dict[str, Any] | None,
) -> dict[str, Any]:
    source = _plan_source(request, provider_prompt, context_bundle)
    refs = [item.get("public") or {} for item in reference_images]
    ref_ids = [str(item.get("asset_id")) for item in refs if item.get("asset_id")]
    asset_graph = asset_graph_from_context_subgraph(request.context_subgraph) or asset_graph_from_context_bundle(context_bundle)
    feedback_overlay = asset_graph_feedback_overlay_from_context_subgraph(request.context_subgraph) or asset_graph_feedback_overlay_from_context_bundle(context_bundle)
    asset_graph_context = summarize_asset_graph_for_plan(asset_graph, feedback_overlay=feedback_overlay)
    return {
        "artifact_type": "agentflow_keyframe_plan",
        "schema_version": "0.1.0",
        "node_id": request.node_id,
        "frame_role": "story_continuity_keyframe",
        "composition": _composition_plan(source, request.aspect_ratio),
        "subject_pose": _subject_pose_plan(source),
        "asset_locks": _asset_locks(source, refs, context_bundle, asset_graph_context),
        "scene_locks": _scene_locks(source, context_bundle, asset_graph_context),
        "lighting_plan": _lighting_plan(source),
        "camera_plan": _camera_plan(source),
        "professional_reference": professional_reference_from_text(source, node_type="image", generation_target="keyframe"),
        "director_scenario": director_scenario_from_text(
            source,
            node_type="image",
            generation_target="keyframe",
            target_platform=request.target_platform,
            style=request.style,
            node_parameters=request.node_parameters,
        ),
        "reference_asset_ids": ref_ids,
        "asset_graph_context": asset_graph_context,
        "candidate_assets_are_editable": True,
        "forbidden_changes": _forbidden_changes(source, asset_graph_context),
    }


def _plan_source(
    request: KeyframeGenerationRequest,
    provider_prompt: str,
    context_bundle: dict[str, Any] | None,
) -> str:
    parts = [request.prompt_text, request.optimized_prompt or "", provider_prompt]
    text_channel = context_bundle.get("text_channel") if isinstance(context_bundle, dict) else None
    if isinstance(text_channel, dict):
        parts.extend(str(text_channel.get(key) or "") for key in sorted(text_channel))
    return "\n".join(part for part in parts if str(part or "").strip())


def _composition_plan(source: str, aspect_ratio: str) -> dict[str, str]:
    shot_size = "wide_or_medium_wide" if _wide_scene(source) else "medium"
    if "close" in source.lower() or "\u7279\u5199" in source:
        shot_size = "close_or_extreme_close"
    return {
        "aspect_ratio": aspect_ratio,
        "shot_size": shot_size,
        "subject_placement": "clear primary subject with stable readable environment",
        "background_policy": "use only scripted or referenced scene geometry",
    }


def _subject_pose_plan(source: str) -> str:
    if "sitting" in source.lower() or "\u5750" in source:
        return "sitting only because source explicitly requests it"
    if "standing" in source.lower() or "\u7ad9" in source:
        return "standing or lightly leaning, matching source action"
    return "neutral readable pose; do not add chair, stool, or extra furniture to justify the pose"


def _asset_locks(
    source: str,
    refs: list[dict[str, Any]],
    context_bundle: dict[str, Any] | None,
    asset_graph_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    locks: list[dict[str, Any]] = []
    for ref in refs:
        role = str(ref.get("role") or ref.get("asset_type") or "reference")
        locks.append(
            {
                "asset_id": str(ref.get("asset_id") or ""),
                "role": role,
                "lock_fields": ["identity", "silhouette", "proportions", "materials"],
            }
        )
    for asset in _included_assets(context_bundle):
        asset_id = str(asset.get("asset_id") or "")
        if asset_id and not any(item.get("asset_id") == asset_id for item in locks):
            locks.append(
                {
                    "asset_id": asset_id,
                    "role": str(asset.get("asset_type") or "asset"),
                    "lock_fields": ["identity", "approved signature", "relationship to scene"],
                }
            )
    for asset in _graph_locked_assets(asset_graph_context):
        graph_asset_id = str(asset.get("graph_asset_id") or "")
        asset_id = str(asset.get("asset_id") or graph_asset_id)
        known_ids = {item.get("asset_id") for item in locks} | {item.get("graph_asset_id") for item in locks}
        if asset_id in known_ids or graph_asset_id in known_ids:
            continue
        lock_fields = _dedupe(asset.get("continuity_locks") or [])
        if not lock_fields:
            lock_fields = ["identity", "approved signature", "relationship to scene"]
        locks.append(
            {
                "asset_id": asset_id,
                "graph_asset_id": graph_asset_id,
                "label": str(asset.get("label") or ""),
                "asset_type": str(asset.get("asset_type") or "asset"),
                "role": str(asset.get("role") or asset.get("asset_type") or "asset"),
                "status": str(asset.get("status") or "candidate"),
                "lock_fields": lock_fields[:8],
            }
        )
    if _has_robot(source) and not locks:
        locks.append({"asset_id": "candidate:future_robot", "role": "character", "lock_fields": ["robot head shell", "body proportions", "material finish"]})
    return locks[:16]


def _scene_locks(
    source: str,
    context_bundle: dict[str, Any] | None,
    asset_graph_context: dict[str, Any] | None,
) -> list[str]:
    locks = ["scene layout", "main spatial relationship", "lighting direction"]
    if _has_rooftop(source):
        locks.extend(["rooftop platform geometry", "sky/background relationship"])
    if _included_assets(context_bundle):
        locks.append("approved context bundle scene assets")
    for asset in _graph_locked_assets(asset_graph_context):
        if str(asset.get("asset_type") or "") == "scene":
            locks.extend(_dedupe(asset.get("continuity_locks") or []))
    return _dedupe(locks)


def _lighting_plan(source: str) -> str:
    if "night" in source.lower() or "\u591c" in source or _has_stars(source):
        return "night ambient light with readable subject edges and stable color temperature"
    return "motivated natural light matching upstream scene"


def _camera_plan(source: str) -> str:
    if "close" in source.lower() or "\u7279\u5199" in source:
        return "close composition while preserving identity and environment anchors"
    if _wide_scene(source):
        return "medium-wide composition with clear subject and environment"
    return "medium composition, stable lens, no abrupt reframing"


def _forbidden_changes(source: str, asset_graph_context: dict[str, Any] | None) -> list[str]:
    changes = ["text", "watermark", "UI", "borders", "new characters", "identity drift", "unrequested props"]
    assets = _graph_locked_assets(asset_graph_context)
    if animal_assets_only(assets):
        changes.extend(["species drift", "fur/marking changes", "unrequested human adornments"])
    elif has_human_asset(assets):
        changes.extend(["unrequested wardrobe changes"])
    if _has_rooftop(source):
        changes.extend(["unrequested eaves", "unrequested chair", "unrequested stool"])
    if _has_robot(source):
        changes.extend(["humanizing the robot beyond approved design", "changing robot head shell"])
    for asset in assets:
        changes.extend(_dedupe(asset.get("negative_locks") or []))
    changes.extend(_feedback_forbidden_changes(asset_graph_context))
    return _dedupe(changes)


def _included_assets(context_bundle: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(context_bundle, dict):
        return []
    assets = context_bundle.get("included_assets")
    return assets if isinstance(assets, list) else []


def _graph_locked_assets(asset_graph_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(asset_graph_context, dict):
        return []
    assets = asset_graph_context.get("locked_assets")
    return [asset for asset in assets if isinstance(asset, dict)] if isinstance(assets, list) else []


def _feedback_forbidden_changes(asset_graph_context: dict[str, Any] | None) -> list[str]:
    if not isinstance(asset_graph_context, dict):
        return []
    result: list[str] = []
    decisions = asset_graph_context.get("feedback_decisions")
    if not isinstance(decisions, list):
        return result
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("decision") != "reject":
            continue
        label = str(decision.get("label") or decision.get("graph_asset_id") or "asset").strip()
        result.append(f"do not use rejected asset {label}")
    return result


def _dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _wide_scene(source: str) -> bool:
    return any(token in source.lower() for token in ("wide", "establishing", "landscape")) or any(
        token in source for token in ("\u5168\u666f", "\u8fdc\u666f", "\u73af\u5883")
    )


def _has_robot(source: str) -> bool:
    return "robot" in source.lower() or "\u673a\u5668\u4eba" in source


def _has_rooftop(source: str) -> bool:
    return "rooftop" in source.lower() or "\u5c4b\u9876" in source or "\u5929\u53f0" in source


def _has_stars(source: str) -> bool:
    return "star" in source.lower() or "\u661f" in source


__all__ = ("build_keyframe_plan",)
