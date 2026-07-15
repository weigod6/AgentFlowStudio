from __future__ import annotations

import re
from typing import Any

from agentflow.algorithms.asset_facts import build_asset_fact_profile


PROFILE_STAGE = "candidate_profile_seed"


def build_asset_profile_plan(refs: list[dict[str, Any]], source_text: str) -> list[dict[str, Any]]:
    return [_asset_profile(ref, source_text) for ref in refs]


def attach_asset_profiles(refs: list[dict[str, Any]], source_text: str) -> list[dict[str, Any]]:
    return [{**ref, "profile_plan": _asset_profile(ref, source_text)} for ref in refs]


def _asset_profile(ref: dict[str, Any], source_text: str) -> dict[str, Any]:
    asset_type = str(ref.get("asset_type") or "character")
    label = str(ref.get("label") or "asset")
    evidence = str(ref.get("evidence_text") or source_text or "")[:240]
    fact_profile = build_asset_fact_profile(
        asset_type=asset_type,
        label=label,
        evidence_text=evidence,
        source_text=source_text,
    )
    character_subtype = str(fact_profile.get("character_subtype") or "")
    base_identity = [] if character_subtype == "animal" else _identity_locks(asset_type, label, evidence)
    base_negative = [] if character_subtype == "animal" else _negative_locks(asset_type, label, evidence)
    return {
        "asset_id": str(ref.get("asset_id") or ""),
        "asset_type": asset_type,
        "character_subtype": character_subtype,
        "label": label,
        "profile_stage": PROFILE_STAGE,
        "facts": fact_profile.get("facts") if isinstance(fact_profile.get("facts"), dict) else {},
        "fact_evidence": fact_profile.get("fact_evidence") if isinstance(fact_profile.get("fact_evidence"), list) else [],
        "missing_fact_fields": fact_profile.get("missing_fact_fields") if isinstance(fact_profile.get("missing_fact_fields"), list) else [],
        "identity_locks": _dedupe([*base_identity, *[str(item) for item in fact_profile.get("continuity_locks", [])]]),
        "editable_fields": _editable_fields(asset_type),
        "negative_locks": _dedupe([*base_negative, *[str(item) for item in fact_profile.get("negative_locks", [])]]),
        "evidence_text": evidence,
        "recommended_reference_output": _recommended_reference_output(asset_type),
        "writes_long_term_memory": False,
    }


def _identity_locks(asset_type: str, label: str, evidence: str) -> list[str]:
    text = f"{label} {evidence}"
    if asset_type == "character":
        locks = ["identity", "body proportions", "material/wardrobe", "silhouette"]
        if _has_robot(text):
            locks.extend(["robot head shell", "mechanical joint layout", "surface material finish"])
        return locks
    if asset_type == "scene":
        locks = ["location type", "layout geometry", "spatial scale", "lighting direction"]
        if _has_rooftop(text):
            locks.extend(["rooftop platform boundary", "skyline/background relationship"])
        return locks
    return ["prop geometry", "scale", "material", "attachment relationship"]


def _editable_fields(asset_type: str) -> list[str]:
    if asset_type == "character":
        return ["name", "surface detail", "wardrobe/material notes", "pose range", "negative identity constraints"]
    if asset_type == "scene":
        return ["location subtype", "layout notes", "time of day", "set dressing whitelist", "forbidden geometry"]
    return ["shape", "material", "scale", "usage relationship", "forbidden variants"]


def _negative_locks(asset_type: str, label: str, evidence: str) -> list[str]:
    text = f"{label} {evidence}"
    locks = ["do not add text/watermark/UI/borders"]
    if asset_type == "character":
        locks.extend(["do not change identity", "do not add unrequested characters"])
    if asset_type == "scene":
        locks.extend(["do not move to a different location", "do not add unrequested set pieces"])
        if _has_rooftop(text):
            locks.extend(["do not add eaves unless approved", "do not add chairs or stools unless approved"])
    if asset_type == "prop":
        locks.extend(["do not change prop function", "do not duplicate the prop unless scripted"])
    return locks


def _recommended_reference_output(asset_type: str) -> str:
    if asset_type == "character":
        return "character_reference_sheet"
    if asset_type == "scene":
        return "scene_reference_sheet"
    return "prop_reference_sheet"


def _has_robot(text: str) -> bool:
    return "robot" in text.lower() or "\u673a\u5668\u4eba" in text


def _has_rooftop(text: str) -> bool:
    return "rooftop" in text.lower() or "\u5c4b\u9876" in text or "\u5929\u53f0" in text


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


__all__ = ("PROFILE_STAGE", "attach_asset_profiles", "build_asset_profile_plan")
