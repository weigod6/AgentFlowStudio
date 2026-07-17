from __future__ import annotations

import re
from typing import Any


ASSET_GRAPH_STAGE = "candidate_asset_graph"
ASSET_TYPES = {"character", "scene", "prop"}


def build_asset_graph(
    shots: list[dict[str, Any]],
    *,
    source_text: str = "",
    graph_source: str = "storyboard",
) -> dict[str, Any]:
    builders: dict[str, dict[str, Any]] = {}
    relationships: list[dict[str, Any]] = []
    unsupported: list[dict[str, str]] = []
    held_asset_refs: list[dict[str, Any]] = []
    for index, shot in enumerate(shots if isinstance(shots, list) else [], start=1):
        shot_id = str(shot.get("shot_id") or f"shot_{index:02d}")
        source_span = _source_span(shot, source_text, index)
        for diagnostic in _list(shot.get("dropped_asset_ref_diagnostics")):
            if isinstance(diagnostic, dict):
                held_asset_refs.append(
                    {
                        **diagnostic,
                        "shot_id": shot_id,
                        "source_span_id": source_span.get("span_id", ""),
                    }
                )
        for addition in _list(shot.get("unsupported_additions")):
            unsupported.append(
                {
                    "shot_id": shot_id,
                    "addition": str(addition)[:80],
                    "source_span_id": source_span.get("span_id", ""),
                    "evidence_text": source_span.get("text", ""),
                }
            )
        for ref in _list(shot.get("asset_refs")):
            if not isinstance(ref, dict):
                continue
            normalized = _normalize_ref(ref)
            if not normalized:
                continue
            key = _asset_identity(normalized)
            builder = builders.setdefault(key, _new_asset_builder(normalized))
            _merge_ref(builder, normalized, shot_id, source_span)
            relationships.append(
                {
                    "relationship_type": "shot_contains_asset",
                    "shot_id": shot_id,
                    "graph_asset_id": builder["graph_asset_id"],
                    "role": _role(normalized["asset_type"]),
                    "source": normalized.get("source", "candidate"),
                }
            )
    assets = [_final_asset(builder) for builder in builders.values()]
    _mark_same_label_entity_collisions(assets)
    graph = {
        "artifact_type": "agentflow_asset_graph",
        "schema_version": "0.1.0",
        "graph_stage": ASSET_GRAPH_STAGE,
        "source": graph_source,
        "asset_count": len(assets),
        "relationship_count": len(relationships),
        "assets": assets,
        "relationships": relationships,
        "unsupported_additions": unsupported,
        "held_asset_refs": held_asset_refs,
        "held_asset_ref_count": len(held_asset_refs),
        "merge_candidates": _merge_candidates(assets),
        "review_state": "needs_review_unsupported_addition" if unsupported else "candidate_review_required",
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }
    return graph


def attach_graph_asset_ids_to_shots(shots: list[dict[str, Any]], asset_graph: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**shot, "asset_refs": attach_graph_asset_ids_to_refs(_list(shot.get("asset_refs")), asset_graph)}
        if isinstance(shot, dict)
        else shot
        for shot in shots
    ]


def attach_graph_asset_ids_to_refs(refs: list[dict[str, Any]], asset_graph: dict[str, Any]) -> list[dict[str, Any]]:
    label_index = {
        (str(asset.get("asset_type") or ""), str(asset.get("label") or "")): str(asset.get("graph_asset_id") or "")
        for asset in _list(asset_graph.get("assets"))
        if isinstance(asset, dict)
    }
    entity_index = {
        str(asset.get("entity_id") or ""): str(asset.get("graph_asset_id") or "")
        for asset in _list(asset_graph.get("assets"))
        if isinstance(asset, dict) and asset.get("entity_id")
    }
    result: list[dict[str, Any]] = []
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        key = (str(ref.get("asset_type") or ""), str(ref.get("label") or ""))
        entity_id = str(ref.get("entity_id") or "")
        graph_asset_id = entity_index.get(entity_id) or label_index.get(key)
        result.append({**ref, **({"graph_asset_id": graph_asset_id} if graph_asset_id else {})})
    return result


def _new_asset_builder(ref: dict[str, Any]) -> dict[str, Any]:
    asset_type = ref["asset_type"]
    label = ref["label"]
    return {
        "graph_asset_id": f"graph:{ref['entity_id']}" if ref.get("entity_id") else f"graph:{asset_type}:{_slug(label)}",
        "entity_id": str(ref.get("entity_id") or ""),
        "asset_id": str(ref.get("asset_id") or ""),
        "asset_type": asset_type,
        "label": label,
        "display_name": str(ref.get("display_name") or label),
        "descriptive_signatures": [],
        "evidence_modalities": [],
        "visual_evidence_spans": [],
        "name_sources": [],
        "provisional_name": False,
        "aliases": {label},
        "character_subtypes": [],
        "continuity_locks": [],
        "negative_locks": [],
        "statuses": [str(ref.get("status") or "candidate")],
        "sources": [str(ref.get("source") or "candidate")],
        "confidences": [_confidence(ref.get("confidence"))],
        "shot_refs": [],
        "evidence_spans": [],
    }


def _merge_ref(builder: dict[str, Any], ref: dict[str, Any], shot_id: str, source_span: dict[str, str]) -> None:
    if ref.get("asset_id") and not builder.get("asset_id"):
        builder["asset_id"] = str(ref.get("asset_id"))
    builder["aliases"].add(str(ref["label"]))
    builder["aliases"].update(str(item) for item in _list(ref.get("aliases")) if str(item).strip())
    builder["statuses"].append(str(ref.get("status") or "candidate"))
    builder["sources"].append(str(ref.get("source") or "candidate"))
    builder["confidences"].append(_confidence(ref.get("confidence")))
    signature = str(ref.get("descriptive_signature") or "").strip()
    if signature and signature not in builder["descriptive_signatures"]:
        builder["descriptive_signatures"].append(signature[:240])
    modality = str(ref.get("evidence_modality") or "").strip()
    if modality and modality not in builder["evidence_modalities"]:
        builder["evidence_modalities"].append(modality)
    visual_span = str(ref.get("visual_evidence_span") or "").strip()
    if visual_span and visual_span not in builder["visual_evidence_spans"]:
        builder["visual_evidence_spans"].append(visual_span[:240])
    name_source = str(ref.get("name_source") or "").strip()
    if name_source and name_source not in builder["name_sources"]:
        builder["name_sources"].append(name_source)
    builder["provisional_name"] = bool(builder["provisional_name"] or ref.get("provisional_name"))
    subtype = str(ref.get("character_subtype") or "").strip()
    if subtype and subtype not in builder["character_subtypes"]:
        builder["character_subtypes"].append(subtype)
    for lock in _list(ref.get("continuity_locks")):
        text = str(lock or "").strip()
        if text and text not in builder["continuity_locks"]:
            builder["continuity_locks"].append(text[:200])
    for lock in _list(ref.get("negative_locks")):
        text = str(lock or "").strip()
        if text and text not in builder["negative_locks"]:
            builder["negative_locks"].append(text[:200])
    if shot_id not in builder["shot_refs"]:
        builder["shot_refs"].append(shot_id)
    evidence = str(ref.get("evidence_text") or source_span.get("text") or "").strip()
    if evidence:
        span = {
            "shot_id": shot_id,
            "source_span_id": str(source_span.get("span_id") or ""),
            "text": evidence[:240],
            "source": str(ref.get("source") or "candidate"),
        }
        if span not in builder["evidence_spans"]:
            builder["evidence_spans"].append(span)


def _final_asset(builder: dict[str, Any]) -> dict[str, Any]:
    asset_type = str(builder["asset_type"])
    evidence_text = " ".join(item["text"] for item in builder["evidence_spans"][:3])
    return {
        "graph_asset_id": builder["graph_asset_id"],
        "entity_id": builder.get("entity_id") or "",
        "asset_id": builder.get("asset_id") or builder["graph_asset_id"],
        "asset_type": asset_type,
        "label": builder["label"],
        "display_name": builder.get("display_name") or builder["label"],
        "role": _role(asset_type),
        "status": _merged_status(builder["statuses"]),
        "review_state": "candidate_review_required",
        "aliases": sorted(builder["aliases"]),
        "merge_key": builder.get("entity_id") or f"{asset_type}:{_slug(builder['label'])}",
        "confidence": round(max(builder["confidences"] or [0.6]), 3),
        "shot_refs": builder["shot_refs"][:24],
        "evidence_spans": builder["evidence_spans"][:12],
        "descriptive_signature": (builder["descriptive_signatures"] or [evidence_text])[0][:240],
        "evidence_modality": (builder["evidence_modalities"] or ["visual"])[0],
        "visual_evidence_span": (builder["visual_evidence_spans"] or [""])[0],
        "modality_gate_status": "accepted",
        "name_source": (builder["name_sources"] or ["candidate"])[0],
        "provisional_name": bool(builder.get("provisional_name")),
        "character_subtype": (builder.get("character_subtypes") or [""])[0],
        "continuity_locks": builder.get("continuity_locks") or _continuity_locks(asset_type, builder["label"], evidence_text),
        "negative_locks": builder.get("negative_locks") or _negative_locks(asset_type, builder["label"], evidence_text),
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def _normalize_ref(ref: dict[str, Any]) -> dict[str, Any]:
    asset_type = str(ref.get("asset_type") or "").strip()
    label = str(ref.get("display_name") or ref.get("label") or "").strip()
    if asset_type not in ASSET_TYPES or not label:
        return {}
    if str(ref.get("modality_gate_status") or "accepted") != "accepted":
        return {}
    return {
        **ref,
        "asset_type": asset_type,
        "label": label[:40],
        "display_name": str(ref.get("display_name") or label)[:80],
        "confidence": _confidence(ref.get("confidence")),
    }


def _asset_identity(ref: dict[str, Any]) -> str:
    entity_id = str(ref.get("entity_id") or "").strip()
    if entity_id:
        return entity_id
    return f"{ref['asset_type']}\x1f{ref['label']}"


def _mark_same_label_entity_collisions(assets: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for asset in assets:
        if not str(asset.get("entity_id") or ""):
            continue
        key = (str(asset.get("asset_type") or ""), str(asset.get("label") or "").casefold())
        groups.setdefault(key, []).append(asset)
    for group in groups.values():
        if len(group) < 2:
            continue
        for asset in group:
            asset["identity_collision"] = True
            asset["auto_binding_block_reasons"] = ["same_label_distinct_entities"]


def _source_span(shot: dict[str, Any], source_text: str, index: int) -> dict[str, str]:
    span = shot.get("source_span") if isinstance(shot.get("source_span"), dict) else {}
    text = str(span.get("text") or shot.get("source_text") or shot.get("description") or source_text or "").strip()
    return {
        "span_id": str(span.get("span_id") or f"script_span_{index:02d}"),
        "text": text[:500],
    }


def _merged_status(values: list[str]) -> str:
    lowered = {str(value or "").lower() for value in values}
    if "fixed" in lowered:
        return "fixed"
    if "mentioned" in lowered or "explicit" in lowered:
        return "mentioned"
    return "candidate"


def _role(asset_type: str) -> str:
    if asset_type == "character":
        return "story_character"
    if asset_type == "scene":
        return "scene_anchor"
    if asset_type == "prop":
        return "story_prop"
    return "asset"


def _continuity_locks(asset_type: str, label: str, evidence: str) -> list[str]:
    text = f"{label} {evidence}"
    if asset_type == "character":
        locks = ["identity", "silhouette", "body proportions", "surface material/wardrobe"]
        if _has_robot(text):
            locks.extend(["robot head shell", "mechanical joint layout"])
        return locks
    if asset_type == "scene":
        locks = ["location type", "layout geometry", "spatial relationship", "lighting direction"]
        if _has_rooftop(text):
            locks.extend(["rooftop boundary", "sky/background relationship"])
        return locks
    return ["prop geometry", "scale", "material", "use relationship"]


def _negative_locks(asset_type: str, label: str, evidence: str) -> list[str]:
    text = f"{label} {evidence}"
    locks = ["no text/watermark/UI/borders"]
    if asset_type == "character":
        locks.extend(["do not change identity", "do not add unrequested characters"])
    elif asset_type == "scene":
        locks.extend(["do not move to a different location", "do not add unrequested set pieces"])
        if _has_rooftop(text):
            locks.extend(["do not add unapproved chairs/stools", "do not add unapproved eaves"])
    else:
        locks.extend(["do not change prop function", "do not duplicate unless scripted"])
    return locks


def _merge_candidates(assets: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    candidates: list[dict[str, str]] = []
    for asset in assets:
        key = str(asset.get("merge_key") or "")
        current = str(asset.get("graph_asset_id") or "")
        if key in seen and seen[key] != current:
            candidates.append({"merge_key": key, "left": seen[key], "right": current})
        else:
            seen[key] = current
    return candidates


def _confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    return 0.6


def _has_robot(text: str) -> bool:
    return "robot" in text.lower() or "机器人" in text


def _has_rooftop(text: str) -> bool:
    return "rooftop" in text.lower() or "屋顶" in text or "天台" in text


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()[:48] or "asset"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


__all__ = (
    "ASSET_GRAPH_STAGE",
    "attach_graph_asset_ids_to_refs",
    "attach_graph_asset_ids_to_shots",
    "build_asset_graph",
)
