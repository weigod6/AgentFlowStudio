from __future__ import annotations

import re
from typing import Any

from apps.api.runtime_storyboard_asset_coverage import reconcile_storyboard_asset_coverage
from apps.api.runtime_storyboard_provider_text import clean_text as _clean


def reconcile_cross_shot_asset_refs(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return reconcile_storyboard_asset_coverage(shots)


def _shot_context_text(shot: dict[str, Any]) -> str:
    span = shot.get("source_span") if isinstance(shot.get("source_span"), dict) else {}
    return "\n".join(
        part
        for part in (
            str(shot.get("description") or ""),
            str(shot.get("source_text") or ""),
            str(span.get("text") or ""),
        )
        if part
    )


def _mentions_dog_coreference(text: str) -> bool:
    return bool(re.search(r"狗|犬|拉布拉多|金毛|边牧|柯基|哈士奇|柴犬", str(text or "")))


def _mentions_cat_coreference(text: str) -> bool:
    return bool(re.search(r"猫|橘猫|狸花猫|黑猫|白猫|猫咪", str(text or "")))


def _has_animal_ref(refs: list[dict[str, Any]], species: str) -> bool:
    return any(_animal_ref_species(ref) == species for ref in refs)


def _drop_generic_animal_refs(refs: list[dict[str, Any]], species: str) -> list[dict[str, Any]]:
    return [
        ref
        for ref in refs
        if not (_animal_ref_species(ref) == species and str(ref.get("label") or ref.get("display_name") or "").strip() in {"狗", "犬", "猫"})
    ]


def _animal_ref_species(ref: dict[str, Any]) -> str:
    if str(ref.get("asset_type") or "") != "character":
        return ""
    label_text = " ".join(str(ref.get(key) or "") for key in ("label", "display_name"))
    subtype = str(ref.get("character_subtype") or "")
    text = label_text
    if subtype == "animal":
        text = " ".join(
            str(ref.get(key) or "")
            for key in ("label", "display_name", "evidence_text", "descriptive_signature", "character_subtype")
        )
    if subtype == "animal" or _mentions_dog_coreference(label_text) or _mentions_cat_coreference(label_text):
        if _mentions_dog_coreference(text):
            return "dog"
        if _mentions_cat_coreference(text):
            return "cat"
    return ""


def _coreference_ref(ref: dict[str, Any], evidence: str) -> dict[str, Any]:
    label = str(ref.get("label") or ref.get("display_name") or "").strip()
    asset_type = str(ref.get("asset_type") or "character")
    return {
        **ref,
        "label": label,
        "display_name": label,
        "asset_id": str(ref.get("asset_id") or f"candidate:{asset_type}:{_slug(label)}"),
        "asset_type": asset_type,
        "status": "mentioned",
        "source": "cross_shot_coreference",
        "scope": "shot_tree",
        "confidence": max(float(ref.get("confidence") or 0.0), 0.88),
        "evidence_text": _clean(evidence)[:240],
        "visual_evidence_span": _clean(evidence)[:240],
        "character_subtype": "animal",
    }


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).lower()[:48] or "asset"


__all__ = ("reconcile_cross_shot_asset_refs",)
