from __future__ import annotations

import re
from typing import Any

from apps.api.runtime_models import ShotAssetPlanRequest
from apps.api.runtime_storyboard_local import local_storyboard_shots, normalize_asset_ref, structured_shot


GENERIC_CHARACTER_LABELS = {"主角", "角色", "人物"}
GENERIC_SCENE_LABELS = {"主要场景", "场景"}
PRESERVED_REF_FIELDS = {
    "display_name",
    "graph_asset_id",
    "descriptive_signature",
    "evidence_text",
    "evidence_modality",
    "visual_evidence_span",
    "modality_gate_status",
    "name_source",
    "provisional_name",
    "character_subtype",
    "facts",
    "fact_evidence",
    "continuity_locks",
    "identity_locks",
    "negative_locks",
    "role_in_shot",
    "provider_asset_contract",
}


def source_text(request: ShotAssetPlanRequest) -> str:
    shot = request.shot if isinstance(request.shot, dict) else {}
    parts = [
        str(shot.get("description") or ""),
        str(shot.get("source_text") or ""),
        str(request.script_text or ""),
    ]
    return "\n".join(part for part in parts if part.strip()).strip()


def structured_from_request(shot: dict[str, Any], text: str) -> dict[str, Any]:
    if isinstance(shot.get("asset_refs"), list) and shot.get("description"):
        return shot
    index = _safe_int(shot.get("index")) or _safe_int(_field(text, "镜号")) or 1
    description = str(shot.get("description") or _field(text, "画面描述") or text).strip()
    return structured_shot(description, index)


def local_asset_refs(
    request: ShotAssetPlanRequest,
    shot: dict[str, Any],
    inferred_shot: dict[str, Any],
    text: str,
) -> list[dict[str, Any]]:
    refs = _normalized_refs(shot.get("asset_refs"), text)
    refs.extend(_normalized_refs(inferred_shot.get("asset_refs"), text))
    refs.extend(_normalized_refs(request.existing_assets, text))
    refs = _apply_global_context(refs, request.script_text or text, text)
    return finalize_asset_refs(refs, text)


def finalize_asset_refs(refs: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    refs = _remove_generic_when_specific(refs)
    refs = _dedupe_refs(refs)
    return [_with_evidence(ref, text) for ref in refs]


def graph_shot(
    shot: dict[str, Any],
    inferred_shot: dict[str, Any],
    refs: list[dict[str, Any]],
    text: str,
    dropped_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        **inferred_shot,
        **{
            key: value
            for key, value in shot.items()
            if key in {"shot_id", "index", "description", "source_text", "source_span", "unsupported_additions"}
        },
        "asset_refs": refs,
        "dropped_asset_ref_diagnostics": list(dropped_refs or []),
        "source_text": str(shot.get("source_text") or inferred_shot.get("source_text") or text),
    }


def _apply_global_context(refs: list[dict[str, Any]], script_text: str, shot_text: str) -> list[dict[str, Any]]:
    combined = "\n".join(part for part in [script_text, shot_text] if part)
    if not combined:
        return refs
    global_refs: list[dict[str, Any]] = []
    for shot in local_storyboard_shots(combined):
        global_refs.extend(_normalized_refs(shot.get("asset_refs"), combined))
    if not any(ref.get("asset_type") == "character" for ref in refs):
        refs.extend(ref for ref in global_refs if ref.get("asset_type") == "character")
    if not any(ref.get("asset_type") == "scene" for ref in refs):
        refs.extend(ref for ref in global_refs if ref.get("asset_type") == "scene")
    if not any(ref.get("asset_type") == "prop" for ref in refs):
        refs.extend(ref for ref in global_refs if ref.get("asset_type") == "prop")
    return refs


def _normalized_refs(items: Any, context: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for index, item in enumerate(items if isinstance(items, list) else []):
        ref = normalize_asset_ref(item, index, context)
        if ref:
            refs.append(ref)
    return refs


def _remove_generic_when_specific(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    has_named_character = any(
        ref.get("asset_type") == "character" and ref.get("label") not in GENERIC_CHARACTER_LABELS
        for ref in refs
    )
    has_named_scene = any(
        ref.get("asset_type") == "scene" and ref.get("label") not in GENERIC_SCENE_LABELS
        for ref in refs
    )
    cleaned: list[dict[str, Any]] = []
    for ref in refs:
        if has_named_character and ref.get("asset_type") == "character" and ref.get("label") in GENERIC_CHARACTER_LABELS:
            continue
        if has_named_scene and ref.get("asset_type") == "scene" and ref.get("label") in GENERIC_SCENE_LABELS:
            continue
        cleaned.append(ref)
    return cleaned


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        asset_type = str(ref.get("asset_type") or "")
        label = str(ref.get("label") or "").strip()
        if asset_type not in {"character", "scene", "prop"} or not label:
            continue
        key = (asset_type, label)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                **{key: value for key, value in ref.items() if key in PRESERVED_REF_FIELDS},
                "label": label,
                "asset_id": str(ref.get("asset_id") or f"candidate:{asset_type}:{_slug(label)}"),
                "asset_type": asset_type,
                "status": str(ref.get("status") or "candidate"),
                "source": str(ref.get("source") or "local_asset_plan"),
                "scope": str(ref.get("scope") or "shot_tree"),
                "confidence": ref.get("confidence") if isinstance(ref.get("confidence"), (int, float)) else 0.72,
            }
        )
    return result[:12]


def _with_evidence(ref: dict[str, Any], text: str) -> dict[str, Any]:
    if str(ref.get("evidence_text") or "").strip():
        return ref
    evidence = _evidence_for_label(text, str(ref.get("label") or ""))
    return {**ref, "evidence_text": evidence or str(text or "").strip()[:240]}


def _evidence_for_label(text: str, label: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", clean) if part.strip()]
    for sentence in sentences:
        if label and label in sentence:
            return sentence[:240]
    for sentence in sentences:
        if re.search(r"山巅|山脊|石台|战场|云海|城市|街道|雨夜|屋顶|天台|金箍棒|钢爪", sentence):
            return sentence[:240]
    return sentences[0][:240] if sentences else clean[:240]


def _field(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*[:：]\s*(.+)", str(text or ""))
    return match.group(1).strip() if match else ""


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).lower()[:32] or "asset"


__all__ = (
    "finalize_asset_refs",
    "graph_shot",
    "local_asset_refs",
    "source_text",
    "structured_from_request",
)
