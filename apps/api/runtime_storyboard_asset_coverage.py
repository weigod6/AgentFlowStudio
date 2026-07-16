from __future__ import annotations

import re
from typing import Any

from apps.api.runtime_asset_extraction import (
    GENERIC_CHARACTER_LABELS,
    GENERIC_SCENE_LABELS,
    normalize_asset_refs_with_diagnostics,
    principal_asset_refs_with_diagnostics,
)
from apps.api.runtime_storyboard_provider_text import clean_text as _clean


ANIMAL_SPECIES_TERMS: dict[str, tuple[str, ...]] = {
    "dog": ("拉布拉多", "金毛", "边牧", "柯基", "哈士奇", "贵宾犬", "萨摩耶", "柴犬", "奶狗", "幼犬", "小狗", "狗狗", "狗", "犬"),
    "cat": ("橘猫", "流浪猫", "狸花猫", "黑猫", "白猫", "小猫", "猫咪", "猫"),
    "bird": ("鸟", "乌鸦", "鸽子", "鹰"),
    "horse": ("马", "战马"),
    "dragon": ("龙",),
}
GENERIC_ANIMAL_LABELS: dict[str, set[str]] = {
    "dog": {"狗", "犬", "狗狗", "那狗", "这狗"},
    "cat": {"猫", "猫咪", "那猫", "这猫"},
    "bird": {"鸟"},
    "horse": {"马"},
    "dragon": {"龙"},
}
HUMAN_PRONOUN_RE = re.compile(r"(?<![\u4e00-\u9fff])(?:他|她)(?!们)")
ANIMAL_PRONOUN_RE = re.compile(r"它|那只(?:狗|猫|犬|鸟|马|龙)|这只(?:狗|猫|犬|鸟|马|龙)|那狗|那猫|这狗|这猫")
VISUAL_CONTINUITY_RE = re.compile(
    r"抬头|低头|回头|侧身|转身|站|蹲|跪|坐|走|跑|追|冲|跃|扑|伸手|抬手|握|攥|拿|捧|抱|咬牙|"
    r"喉结|瞳孔|肩|右臂|左臂|指节|手指|下颌|呼吸|开口|呛出|眼神|尾巴|爪|耳朵|鼻尖|嘴里|叼|吐"
)
SCENE_BREAK_RE = re.compile(r"另一边|与此同时|转场|切到|新场景|回到|来到|走进|进入")
PROP_COREFERENCE_TERMS = ("球", "戟", "剑", "刀", "棍", "棒", "伞", "绳", "试卷", "草稿纸", "竹简", "虎符", "军旗", "手机", "地图", "钥匙")


def reconcile_storyboard_asset_coverage(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply one grounded asset coverage contract to provider and fallback shots.

    The contract is intentionally evidence-driven:
    - infer missing assets only from the current shot text/description;
    - resolve pronouns or generic animal/object mentions only to a unique prior asset;
    - carry a previous scene only when the current shot has no new scene and visually
      continues the same action space.
    """

    recent_animals: dict[str, dict[str, Any]] = {}
    recent_humans: list[dict[str, Any]] = []
    recent_scene: dict[str, Any] | None = None
    recent_props: list[dict[str, Any]] = []

    for shot in shots:
        text = _shot_context_text(shot)
        refs = list(shot.get("asset_refs") or [])
        dropped = list(shot.get("dropped_asset_ref_diagnostics") or [])

        refs, inferred_dropped = normalize_asset_refs_with_diagnostics(refs, context=text, include_inferred=True)
        dropped = [*dropped, *inferred_dropped]
        refs = _inherit_recent_character_subtypes(refs, recent_animals, recent_humans)
        refs = _resolve_animal_coreferences(refs, text, recent_animals)
        refs = _resolve_human_coreferences(refs, text, recent_humans)
        refs = _resolve_prop_coreferences(refs, text, recent_props)
        refs = _resolve_scene_continuity(refs, text, recent_scene)
        refs = _drop_generic_animal_refs_when_alias_exists(refs)
        refs = _drop_cross_type_duplicate_labels(refs)
        refs, dropped = principal_asset_refs_with_diagnostics(refs, dropped, max_auto_characters=2, max_auto_scenes=1, max_auto_props=3)

        shot["asset_refs"] = refs
        shot["dropped_asset_ref_diagnostics"] = dropped

        for ref in refs:
            asset_type = str(ref.get("asset_type") or "")
            if asset_type == "character":
                species = _animal_ref_species(ref)
                if species:
                    recent_animals[species] = ref
                elif _is_named_human_ref(ref):
                    recent_humans = _append_recent_unique(recent_humans, ref, limit=3)
            elif asset_type == "scene" and _is_named_scene_ref(ref):
                recent_scene = ref
            elif asset_type == "prop" and _is_named_prop_ref(ref):
                recent_props = _append_recent_unique(recent_props, ref, limit=5)
    return shots


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


def _resolve_animal_coreferences(
    refs: list[dict[str, Any]],
    text: str,
    recent_animals: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    for species in ANIMAL_SPECIES_TERMS:
        if not _mentions_animal_species(text, species) and not (ANIMAL_PRONOUN_RE.search(text) and species in {"dog", "cat"}):
            continue
        prior = recent_animals.get(species)
        if not prior:
            continue
        refs = [ref for ref in refs if not _is_generic_animal_ref(ref, species)]
        if not any(_same_ref_identity(ref, prior) for ref in refs):
            refs.append(_cross_shot_ref(prior, text, "cross_shot_coreference"))
    return refs


def _resolve_human_coreferences(
    refs: list[dict[str, Any]],
    text: str,
    recent_humans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not HUMAN_PRONOUN_RE.search(text):
        return refs
    if _has_current_named_human(refs, text):
        return refs
    if len(recent_humans) != 1:
        return refs
    prior = recent_humans[0]
    if not any(_same_ref_identity(ref, prior) for ref in refs):
        refs.append(_cross_shot_ref(prior, text, "cross_shot_coreference"))
    return refs


def _resolve_prop_coreferences(
    refs: list[dict[str, Any]],
    text: str,
    recent_props: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for prop in reversed(recent_props):
        label = str(prop.get("label") or prop.get("display_name") or "")
        term = next((item for item in PROP_COREFERENCE_TERMS if item and item in label and item in text), "")
        if not term:
            continue
        if _has_current_named_prop(refs, label):
            continue
        refs = [ref for ref in refs if not _is_generic_prop_ref(ref, term)]
        refs.append(_cross_shot_ref(prop, text, "cross_shot_coreference"))
        break
    return refs


def _resolve_scene_continuity(
    refs: list[dict[str, Any]],
    text: str,
    recent_scene: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not recent_scene or any(str(ref.get("asset_type") or "") == "scene" for ref in refs):
        return refs
    if SCENE_BREAK_RE.search(text) or not VISUAL_CONTINUITY_RE.search(text):
        return refs
    refs.append(_cross_shot_ref(recent_scene, text, "cross_shot_scene_continuity"))
    return refs


def _inherit_recent_character_subtypes(
    refs: list[dict[str, Any]],
    recent_animals: dict[str, dict[str, Any]],
    recent_humans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    priors = [*recent_animals.values(), *recent_humans]
    result: list[dict[str, Any]] = []
    for ref in refs:
        if str(ref.get("asset_type") or "") != "character" or ref.get("character_subtype"):
            result.append(ref)
            continue
        prior = next((item for item in priors if _same_ref_identity(item, ref)), None)
        if prior and prior.get("character_subtype"):
            result.append({**ref, "character_subtype": prior["character_subtype"]})
        else:
            result.append(ref)
    return result


def _drop_generic_animal_refs_when_alias_exists(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alias_species = {
        species
        for ref in refs
        if str(ref.get("asset_type") or "") == "character"
        and str(ref.get("label") or ref.get("display_name") or "").strip() not in GENERIC_ANIMAL_LABELS.get((species := _animal_ref_species(ref)), set())
        and species
    }
    if not alias_species:
        return refs
    return [ref for ref in refs if not (_animal_ref_species(ref) in alias_species and _is_generic_animal_ref(ref, _animal_ref_species(ref)))]


def _drop_cross_type_duplicate_labels(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    character_labels = {str(ref.get("label") or ref.get("display_name") or "").strip() for ref in refs if str(ref.get("asset_type") or "") == "character"}
    return [
        ref
        for ref in refs
        if not (str(ref.get("asset_type") or "") != "character" and str(ref.get("label") or ref.get("display_name") or "").strip() in character_labels)
    ]


def _animal_ref_species(ref: dict[str, Any]) -> str:
    if str(ref.get("asset_type") or "") != "character":
        return ""
    label_text = " ".join(str(ref.get(key) or "") for key in ("label", "display_name"))
    subtype = str(ref.get("character_subtype") or "")
    label_species = _species_in_text(label_text)
    if label_species:
        return label_species
    if subtype != "animal":
        return ""
    text = " ".join(str(ref.get(key) or "") for key in ("label", "display_name", "evidence_text", "descriptive_signature"))
    return _species_in_text(text)


def _species_in_text(text: str) -> str:
    for species, terms in ANIMAL_SPECIES_TERMS.items():
        if any(term in text for term in terms):
            return species
    return ""


def _mentions_animal_species(text: str, species: str) -> bool:
    return any(term in str(text or "") for term in ANIMAL_SPECIES_TERMS.get(species, ()))


def _is_generic_animal_ref(ref: dict[str, Any], species: str) -> bool:
    label = str(ref.get("label") or ref.get("display_name") or "").strip()
    terms = GENERIC_ANIMAL_LABELS.get(species, set())
    return _animal_ref_species(ref) == species and label in terms


def _has_current_named_human(refs: list[dict[str, Any]], text: str) -> bool:
    for ref in refs:
        if not _is_named_human_ref(ref):
            continue
        label = str(ref.get("label") or ref.get("display_name") or "")
        if label and label in text:
            return True
    return False


def _has_current_named_prop(refs: list[dict[str, Any]], label: str) -> bool:
    return any(str(ref.get("asset_type") or "") == "prop" and str(ref.get("label") or ref.get("display_name") or "") == label for ref in refs)


def _is_generic_prop_ref(ref: dict[str, Any], term: str) -> bool:
    return str(ref.get("asset_type") or "") == "prop" and str(ref.get("label") or ref.get("display_name") or "").strip() == term


def _is_named_human_ref(ref: dict[str, Any]) -> bool:
    label = str(ref.get("label") or ref.get("display_name") or "").strip()
    subtype = str(ref.get("character_subtype") or "")
    return (
        str(ref.get("asset_type") or "") == "character"
        and label
        and label not in GENERIC_CHARACTER_LABELS
        and subtype != "animal"
        and not ref.get("provisional_name")
    )


def _is_named_scene_ref(ref: dict[str, Any]) -> bool:
    label = str(ref.get("label") or ref.get("display_name") or "").strip()
    return str(ref.get("asset_type") or "") == "scene" and label and label not in GENERIC_SCENE_LABELS


def _is_named_prop_ref(ref: dict[str, Any]) -> bool:
    label = str(ref.get("label") or ref.get("display_name") or "").strip()
    return str(ref.get("asset_type") or "") == "prop" and bool(label)


def _same_ref_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        str(left.get("asset_type") or "") == str(right.get("asset_type") or "")
        and str(left.get("label") or left.get("display_name") or "") == str(right.get("label") or right.get("display_name") or "")
    )


def _cross_shot_ref(ref: dict[str, Any], evidence: str, source: str) -> dict[str, Any]:
    label = str(ref.get("label") or ref.get("display_name") or "").strip()
    asset_type = str(ref.get("asset_type") or "character")
    merged = {
        **ref,
        "label": label,
        "display_name": label,
        "asset_id": str(ref.get("asset_id") or f"candidate:{asset_type}:{_slug(label)}"),
        "asset_type": asset_type,
        "status": "mentioned",
        "source": source,
        "scope": "shot_tree",
        "confidence": max(float(ref.get("confidence") or 0.0), 0.86),
        "evidence_text": _clean(evidence)[:240],
        "visual_evidence_span": _clean(evidence)[:240],
    }
    if asset_type == "character" and _animal_ref_species(ref):
        merged["character_subtype"] = "animal"
    elif asset_type == "character" and not merged.get("character_subtype"):
        merged["character_subtype"] = "human"
    return merged


def _append_recent_unique(refs: list[dict[str, Any]], ref: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    result = [item for item in refs if not _same_ref_identity(item, ref)]
    result.append(ref)
    return result[-limit:]


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()[:48] or "asset"


__all__ = ("reconcile_storyboard_asset_coverage",)
