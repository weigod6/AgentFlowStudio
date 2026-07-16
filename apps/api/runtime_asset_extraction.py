from __future__ import annotations

import re
from typing import Any


ASSET_TYPES = {"character", "scene", "prop"}
CHARACTER_SUBTYPES = {"human", "animal", "robot", "subject"}
GENERIC_CHARACTER_LABELS = {"人", "人物", "主角", "角色", "主体"}
GENERIC_SCENE_LABELS = {"场景", "主要场景"}
PRONOUN_LABELS = {"他", "她", "它", "他们", "她们", "ta", "they", "he", "she"}

AUDIO_ONLY_TERMS = (
    "城市噪音",
    "城市环境底噪",
    "环境底噪",
    "底噪",
    "噪音",
    "环境音",
    "ambience",
    "ambient",
    "city noise",
    "distant city noise",
    "audio",
    "sound",
    "black screen",
)
CITY_TERMS = ("城市", "city", "街道", "street", "road")
KNOWN_CHARACTER_NAMES = ("唐僧", "白骨精", "孙悟空", "猪八戒", "沙僧", "金刚狼", "林晚")
VISUAL_CITY_TERMS = (
    "rain-night city street",
    "city street",
    "skyline",
    "building",
    "buildings",
    "neon",
    "wet road",
    "visible lights",
    "rooftop",
    "雨夜",
    "街道",
    "屋顶",
    "天际线",
    "建筑",
    "高楼",
    "霓虹",
    "湿路",
    "路面",
    "灯光",
)
VISUAL_CHARACTER_TERMS = (
    "walks",
    "runs",
    "face",
    "coat",
    "hand",
    "under the visible lights",
    "站",
    "走",
    "奔跑",
    "穿",
    "低头",
    "手部",
    "展开",
    "外套",
    "侧脸",
    "女孩",
    "林晚",
    "机器人",
    "唐僧",
    "白骨精",
    "孙悟空",
    "猪八戒",
)


def normalize_asset_refs_with_diagnostics(
    asset_refs: list[Any],
    *,
    context: str = "",
    include_inferred: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = [item for item in asset_refs if isinstance(item, dict)]
    if include_inferred:
        specific_types = _specific_asset_types(candidates)
        candidates = [
            *candidates,
            *[item for item in _inferred_asset_refs(context) if item.get("asset_type") not in specific_types],
        ]

    accepted: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, candidate in enumerate(candidates):
        normalized, diagnostic = normalize_asset_ref_for_contract(candidate, index, context=context)
        if normalized:
            key = (normalized["asset_type"], normalized["display_name"])
            if key in seen:
                continue
            seen.add(key)
            accepted.append(normalized)
        elif diagnostic:
            diagnostic_key = (diagnostic["asset_type"], diagnostic["display_name"], diagnostic["reason"])
            if diagnostic_key not in {(item["asset_type"], item["display_name"], item["reason"]) for item in diagnostics}:
                diagnostics.append(diagnostic)
    return accepted, diagnostics


def principal_asset_refs_with_diagnostics(
    asset_refs: list[dict[str, Any]],
    dropped_refs: list[dict[str, Any]] | None = None,
    *,
    max_auto_characters: int = 2,
    max_auto_scenes: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = list(dropped_refs or [])
    auto_character_count = 0
    auto_scene_count = 0
    for ref in asset_refs:
        asset_type = str(ref.get("asset_type") or "")
        if _is_manual_or_fixed_asset_ref(ref):
            accepted.append(ref)
            continue
        explicit_named = _is_explicit_named_asset_ref(ref)
        if asset_type == "prop":
            diagnostics.append(_principal_diagnostic(ref, "prop_requires_manual_asset_entry"))
            continue
        if asset_type == "character":
            if explicit_named or auto_character_count < max_auto_characters:
                accepted.append(ref)
                if not explicit_named:
                    auto_character_count += 1
            else:
                diagnostics.append(_principal_diagnostic(ref, "secondary_character_requires_manual_asset_entry"))
            continue
        if asset_type == "scene":
            if explicit_named or auto_scene_count < max_auto_scenes:
                accepted.append(ref)
                if not explicit_named:
                    auto_scene_count += 1
            else:
                diagnostics.append(_principal_diagnostic(ref, "secondary_scene_requires_manual_asset_entry"))
            continue
        diagnostics.append(_principal_diagnostic(ref, "unsupported_asset_type_requires_manual_entry"))
    return accepted, diagnostics


def normalize_asset_ref_for_contract(
    asset: dict[str, Any],
    index: int,
    *,
    context: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    asset_type = _asset_type(asset.get("asset_type"))
    raw_label = _clean_label(asset.get("display_name") or asset.get("label") or asset.get("name") or "")
    if not raw_label:
        return None, None

    evidence = _clean_text(asset.get("evidence_text") or asset.get("visual_evidence_span") or context)
    context_text = _clean_text(context or evidence)
    display_name = raw_label
    provisional_name = bool(asset.get("provisional_name"))
    name_source = str(asset.get("name_source") or asset.get("source") or "candidate")

    if asset_type == "scene" and _is_audio_only_city_reference(raw_label, evidence, context_text):
        return None, _diagnostic(raw_label, asset_type, "audio_only_non_visual_city_reference", evidence or context_text)

    if asset_type == "character" and raw_label in PRONOUN_LABELS:
        return None, _diagnostic(raw_label, asset_type, "ambiguous_alias_not_auto_merged", evidence or context_text)

    if asset_type == "character" and raw_label in GENERIC_CHARACTER_LABELS:
        provisional = _provisional_character_name(context_text)
        if not provisional:
            return None, _diagnostic(raw_label, asset_type, "unresolved_generic_character", evidence or context_text)
        display_name = provisional
        provisional_name = True
        name_source = "visual_context_provisional"

    if asset_type == "scene" and raw_label in GENERIC_SCENE_LABELS:
        scene_name = _visual_scene_name(context_text)
        if not scene_name:
            return None, _diagnostic(raw_label, asset_type, "unresolved_generic_scene", evidence or context_text)
        display_name = scene_name
        provisional_name = True
        name_source = "visual_context_provisional"

    visual_span = _visual_evidence_span(context_text, evidence, display_name, asset_type)
    if asset_type == "scene" and _has_audio_only_terms(evidence or context_text) and not visual_span:
        return None, _diagnostic(raw_label, asset_type, "audio_only_non_visual_reference", evidence or context_text)
    if not visual_span and asset_type == "scene":
        visual_span = (evidence or context_text)[:240]
    evidence_modality = "visual"

    normalized = {
        "label": display_name,
        "display_name": display_name,
        "asset_id": str(asset.get("asset_id") or f"candidate:{asset_type}:{_slug(display_name)}"),
        "graph_asset_id": str(asset.get("graph_asset_id") or asset.get("graphAssetId") or ""),
        "asset_type": asset_type,
        "status": str(asset.get("status") or "candidate"),
        "source": str(asset.get("source") or "candidate"),
        "scope": str(asset.get("scope") or "shot_tree"),
        "confidence": _confidence(asset.get("confidence"), provisional_name=provisional_name),
        "evidence_text": (visual_span or evidence or context_text)[:240],
        "descriptive_signature": _descriptive_signature(asset, visual_span or evidence or context_text),
        "evidence_modality": evidence_modality,
        "visual_evidence_span": visual_span,
        "modality_gate_status": "accepted",
        "name_source": name_source,
        "provisional_name": provisional_name,
    }
    character_subtype = _character_subtype(asset.get("character_subtype"))
    if asset_type == "character" and character_subtype:
        normalized["character_subtype"] = character_subtype
    return normalized, None


def _inferred_asset_refs(context: str) -> list[dict[str, Any]]:
    text = _clean_text(context)
    refs: list[dict[str, Any]] = []
    for name in _named_characters(text):
        refs.append({"label": name, "asset_type": "character", "source": "candidate", "evidence_text": text})
    scene_name = _visual_scene_name(text)
    if scene_name:
        refs.append({"label": scene_name, "asset_type": "scene", "source": "candidate", "evidence_text": text})
    return refs


def _specific_asset_types(candidates: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for item in candidates:
        asset_type = _asset_type(item.get("asset_type"))
        label = _clean_label(item.get("display_name") or item.get("label") or item.get("name") or "")
        if label and label not in GENERIC_CHARACTER_LABELS and label not in GENERIC_SCENE_LABELS and label not in PRONOUN_LABELS:
            result.add(asset_type)
    return result


def _named_characters(text: str) -> list[str]:
    names: list[str] = []
    for left, right in re.findall(
        r"([\u4e00-\u9fffA-Za-z0-9·]{2,12})(?:大战|对决|迎娶|娶了|娶|嫁给|爱上|遇见|面对|追击|追杀|营救|守护)([\u4e00-\u9fffA-Za-z0-9·]{2,12})",
        text,
    ):
        names.extend([_trim_character_name(left), _trim_character_name(right)])
    names.extend(_known_characters_in_source_order(text))
    if re.search(r"\bLin\s+Wan\b", text, flags=re.I):
        names.append("Lin Wan")
    if "女孩" in text:
        names.append("女孩")
    if "机器人" in text:
        names.append("机器人")
    if re.search(r"\bfuture robot\b|\brobot\b", text, flags=re.I):
        names.append("Future Robot")
    return _dedupe([name for name in names if name])


def _known_characters_in_source_order(text: str) -> list[str]:
    return [
        name
        for name, _index in sorted(
            ((name, text.find(name)) for name in KNOWN_CHARACTER_NAMES if text.find(name) >= 0),
            key=lambda item: item[1],
        )
    ]


def _trim_character_name(value: str) -> str:
    clean = re.sub(r"^(以|把|将|当|用|和|与|及|、)+", "", str(value or "")).strip()
    clean = re.sub(r"^.*(?:是|讲述|关于|围绕)", "", clean).strip()
    clean = re.sub(r"(为核心|为主题|为主|展开|对决|战斗|格斗|碰撞).*$", "", clean).strip()
    clean = re.sub(r"(但是|但|却|旁观|观战|从旁).*$", "", clean).strip()
    return clean[:24]


def _provisional_character_name(text: str) -> str:
    for name in _named_characters(text):
        if name not in {"他", "她", "它"}:
            return name
    lowered = text.lower()
    if any(term in text for term in ("红色外套", "侧脸", "霓虹")):
        return "红色外套人物"
    if "女孩" in text:
        return "女孩"
    if "robot" in lowered or "机器人" in text:
        return "Future Robot" if "robot" in lowered else "机器人"
    if _has_visual_character_context(text):
        return "可见人物"
    return ""


def _visual_scene_name(text: str) -> str:
    if _has_negated_visual_context(text):
        return ""
    lowered = text.lower()
    if "rain-night city street" in lowered:
        return "rain-night city street"
    if "city street" in lowered or ("street" in lowered and "city" in lowered):
        return "city street"
    if "rooftop" in lowered and "city" in lowered:
        return "city rooftop"
    if "雨夜" in text and ("城市" in text or "街道" in text):
        return "雨夜城市街道"
    if "城市" in text and "屋顶" in text:
        return "城市屋顶"
    if "城市" in text and any(term in text for term in ("街道", "天际线", "建筑", "高楼", "霓虹", "湿路", "路面", "灯光")):
        return "城市街道"
    return ""


def _visual_evidence_span(context: str, evidence: str, display_name: str, asset_type: str) -> str:
    source = _clean_text(context or evidence)
    if not source:
        return ""
    candidates = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", source) if part.strip()]
    candidates = candidates or [source]
    for sentence in candidates:
        if display_name and display_name in sentence:
            return sentence[:240]
    if asset_type == "scene":
        for sentence in candidates:
            if _has_visual_city_context(sentence):
                return sentence[:240]
    if asset_type == "character":
        for sentence in candidates:
            if _has_visual_character_context(sentence):
                return sentence[:240]
    if asset_type == "prop":
        return candidates[0][:240]
    return ""


def _is_audio_only_city_reference(label: str, evidence: str, context: str) -> bool:
    text = f"{label} {evidence} {context}"
    return _has_city_terms(text) and _has_audio_only_terms(text) and not _has_visual_city_context(text)


def _has_city_terms(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered if term.isascii() else term in text for term in CITY_TERMS)


def _has_audio_only_terms(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered if term.isascii() else term in text for term in AUDIO_ONLY_TERMS)


def _has_visual_city_context(text: str) -> bool:
    if _has_negated_visual_context(text):
        return False
    lowered = text.lower()
    return any(term in lowered if term.isascii() else term in text for term in VISUAL_CITY_TERMS)


def _has_visual_character_context(text: str) -> bool:
    if _has_negated_visual_context(text):
        return False
    lowered = text.lower()
    return any(term in lowered if term.isascii() else term in text for term in VISUAL_CHARACTER_TERMS)


def _has_negated_visual_context(text: str) -> bool:
    lowered = text.lower()
    return any(
        term in lowered if term.isascii() else term in text
        for term in (
            "没有可见",
            "不可见",
            "无可见",
            "没有画面",
            "no visible",
            "not visible",
            "black screen",
        )
    )


def _diagnostic(label: str, asset_type: str, reason: str, evidence: str) -> dict[str, Any]:
    return {
        "label": label,
        "display_name": label,
        "asset_type": asset_type,
        "reason": reason,
        "evidence_text": _clean_text(evidence)[:240],
        "evidence_modality": "audio" if _has_audio_only_terms(evidence) else "textual",
        "modality_gate_status": "held",
    }


def _principal_diagnostic(ref: dict[str, Any], reason: str) -> dict[str, Any]:
    label = str(ref.get("display_name") or ref.get("label") or "").strip()
    asset_type = str(ref.get("asset_type") or "prop").strip() or "prop"
    evidence = str(ref.get("evidence_text") or ref.get("visual_evidence_span") or "")
    return _diagnostic(label, asset_type, reason, evidence)


def _is_manual_or_fixed_asset_ref(ref: dict[str, Any]) -> bool:
    return (
        str(ref.get("source") or "").lower() == "manual"
        or str(ref.get("status") or "").lower() == "fixed"
        or bool(ref.get("graph_asset_id") or ref.get("graphAssetId"))
    )


def _is_explicit_named_asset_ref(ref: dict[str, Any]) -> bool:
    asset_type = str(ref.get("asset_type") or "")
    if asset_type == "prop":
        return False
    source = str(ref.get("source") or "").lower()
    status = str(ref.get("status") or "").lower()
    if "explicit" not in source and status != "mentioned":
        return False
    label = str(ref.get("display_name") or ref.get("label") or "").strip()
    return label not in GENERIC_CHARACTER_LABELS and label not in GENERIC_SCENE_LABELS


def _descriptive_signature(asset: dict[str, Any], fallback: str) -> str:
    return _clean_text(
        asset.get("descriptive_signature")
        or asset.get("signature")
        or asset.get("visual_description_seed")
        or fallback
    )[:240]


def _asset_type(value: Any) -> str:
    asset_type = str(value or "").strip()
    return asset_type if asset_type in ASSET_TYPES else "character"


def _character_subtype(value: Any) -> str:
    subtype = str(value or "").strip()
    return subtype if subtype in CHARACTER_SUBTYPES else ""


def _confidence(value: Any, *, provisional_name: bool = False) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    return 0.72 if provisional_name else 0.82


def _clean_label(value: Any) -> str:
    return re.sub(r"^[\s@]+|[\s，。；:：.!?！？]+$", "", str(value or "")).strip()[:40]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()[:48] or "asset"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


__all__ = (
    "GENERIC_CHARACTER_LABELS",
    "GENERIC_SCENE_LABELS",
    "normalize_asset_ref_for_contract",
    "normalize_asset_refs_with_diagnostics",
    "principal_asset_refs_with_diagnostics",
)
