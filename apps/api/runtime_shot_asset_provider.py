from __future__ import annotations

import json
import re
from typing import Any

from apps.api.runtime_shot_asset_provider_prompt import ASSET_RECOGNITION_CONTRACT


ALLOWED_ASSET_TYPES = {"character", "scene", "prop"}
ALLOWED_CHARACTER_SUBTYPES = {"human", "animal", "robot", "subject", ""}
GENERIC_LABELS = {"主角", "角色", "人物", "主体", "主要场景", "场景", "道具"}


def asset_refs_from_provider_text(text: str, *, source_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = _json_from_text(text)
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("provider asset response missing assets")

    refs: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for index, item in enumerate(raw_assets):
        ref, diagnostic = _normalize_provider_asset(item, index, source_text=source_text)
        if ref:
            refs.append(ref)
        elif diagnostic:
            dropped.append(diagnostic)

    for item in payload.get("dropped_candidates") or []:
        diagnostic = _provider_diagnostic(item, source_text=source_text)
        if diagnostic:
            dropped.append(diagnostic)

    refs = _dedupe_refs(refs)
    if not refs:
        raise ValueError("provider asset response has no grounded usable assets")
    return refs[:12], _dedupe_diagnostics(dropped)


def _normalize_provider_asset(item: Any, index: int, *, source_text: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(item, dict):
        return None, None
    label = _clean_label(item.get("label") or item.get("display_name") or item.get("name"))
    asset_type = _asset_type(item.get("asset_type"))
    if not label or asset_type not in ALLOWED_ASSET_TYPES:
        return None, None
    evidence = _clean_text(item.get("evidence_text") or item.get("visual_evidence_span") or item.get("source_text"))
    if not evidence:
        return None, _diagnostic(label, asset_type, "missing_evidence_text", "")
    if not _evidence_is_grounded(evidence, source_text):
        return None, _diagnostic(label, asset_type, "evidence_text_not_in_source", evidence)
    if not _label_is_grounded(label, evidence, source_text):
        return None, _diagnostic(label, asset_type, "label_not_in_source", evidence)
    if label in GENERIC_LABELS and not _generic_label_is_source_name(label, source_text):
        return None, _diagnostic(label, asset_type, "generic_label_not_accepted", evidence)

    subtype = _character_subtype(item.get("character_subtype") or item.get("subtype"))
    if asset_type != "character":
        subtype = ""
    elif subtype not in ALLOWED_CHARACTER_SUBTYPES:
        subtype = "subject"

    facts = _clean_dict(item.get("facts"))
    locks = _clean_list(item.get("continuity_locks") or item.get("identity_locks"))
    negative_locks = _clean_list(item.get("negative_locks"))
    confidence = _confidence(item.get("confidence"))
    return (
        {
            "label": label,
            "display_name": label,
            "asset_id": str(item.get("asset_id") or f"candidate:{asset_type}:{_slug(label)}"),
            "asset_type": asset_type,
            "status": str(item.get("status") or "mentioned"),
            "source": "llm_asset_recognition",
            "scope": "shot_tree",
            "confidence": confidence if confidence is not None else 0.88,
            "evidence_text": evidence[:240],
            "descriptive_signature": _clean_text(item.get("descriptive_signature") or item.get("signature") or evidence)[:240],
            "evidence_modality": "visual",
            "visual_evidence_span": evidence[:240],
            "modality_gate_status": "accepted",
            "name_source": "provider_grounded_label",
            "provisional_name": False,
            "character_subtype": subtype,
            "facts": facts,
            "fact_evidence": [evidence[:240]],
            "continuity_locks": locks,
            "negative_locks": negative_locks,
            "role_in_shot": _clean_text(item.get("role_in_shot")),
            "provider_asset_contract": ASSET_RECOGNITION_CONTRACT,
            "provider_asset_index": index + 1,
        },
        None,
    )


def _provider_diagnostic(item: Any, *, source_text: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    label = _clean_label(item.get("label") or item.get("display_name") or item.get("name"))
    asset_type = _asset_type(item.get("asset_type"))
    evidence = _clean_text(item.get("evidence_text") or item.get("source_text"))
    if evidence and not _evidence_is_grounded(evidence, source_text):
        evidence = ""
    reason = _clean_text(item.get("reason") or "provider_dropped_candidate")
    return _diagnostic(label or "候选资产", asset_type, reason, evidence)


def _json_from_text(text: str) -> dict[str, Any]:
    source = _strip_json_fences(text)
    try:
        payload = json.loads(source)
    except json.JSONDecodeError:
        payload = _first_json_object_with_assets(source)
    if not isinstance(payload, dict):
        raise ValueError("provider asset response root is not object")
    return payload


def _strip_json_fences(text: str) -> str:
    source = str(text or "").strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        source = "\n".join(lines).strip()
    return source


def _first_json_object_with_assets(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("assets"), list):
            return payload
    raise ValueError("provider asset response is not json") from None


def _asset_type(value: Any) -> str:
    text = _clean_text(value).lower()
    aliases = {
        "character": "character",
        "角色": "character",
        "人物": "character",
        "animal_character": "character",
        "scene": "scene",
        "场景": "scene",
        "location": "scene",
        "prop": "prop",
        "道具": "prop",
        "object": "prop",
    }
    return aliases.get(text, text)


def _character_subtype(value: Any) -> str:
    text = _clean_text(value).lower()
    aliases = {
        "human": "human",
        "人物": "human",
        "人类": "human",
        "animal": "animal",
        "动物": "animal",
        "robot": "robot",
        "机器人": "robot",
        "subject": "subject",
        "主体": "subject",
    }
    return aliases.get(text, text)


def _evidence_is_grounded(evidence: str, source_text: str) -> bool:
    return _compact(evidence) in _compact(source_text)


def _label_is_grounded(label: str, evidence: str, source_text: str) -> bool:
    clean_label = label.lstrip("@")
    return clean_label in evidence or clean_label in source_text


def _generic_label_is_source_name(label: str, source_text: str) -> bool:
    return f"@{label}" in source_text


def _clean_label(value: Any) -> str:
    return re.sub(r"^[\s@]+|[\s，。；:：!?！？]+$", "", str(value or "")).strip()[:80]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _clean_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = _clean_text(key)
        if not clean_key:
            continue
        if isinstance(item, list):
            clean_items = _clean_list(item)
            if clean_items:
                result[clean_key] = clean_items
        elif isinstance(item, dict):
            nested = _clean_dict(item)
            if nested:
                result[clean_key] = nested
        else:
            clean_item = _clean_text(item)
            if clean_item:
                result[clean_key] = clean_item[:160]
    return result


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        text = _clean_text(value)
        return [text[:160]] if text else []
    result: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text and text not in result:
            result.append(text[:160])
    return result[:12]


def _confidence(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0.0, min(float(value), 1.0))


def _diagnostic(label: str, asset_type: str, reason: str, evidence: str) -> dict[str, Any]:
    return {
        "label": label,
        "display_name": label,
        "asset_type": asset_type if asset_type in ALLOWED_ASSET_TYPES else "character",
        "reason": _clean_text(reason)[:80] or "provider_asset_not_accepted",
        "evidence_text": _clean_text(evidence)[:240],
        "evidence_modality": "textual",
        "modality_gate_status": "held",
    }


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (str(ref.get("asset_type") or ""), str(ref.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _dedupe_diagnostics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (
            str(item.get("asset_type") or ""),
            str(item.get("label") or ""),
            str(item.get("reason") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()[:48] or "asset"


__all__ = (
    "asset_refs_from_provider_text",
)
