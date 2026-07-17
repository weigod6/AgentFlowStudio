from __future__ import annotations

import hashlib
from typing import Any

ASSET_TYPES = {"character", "scene", "prop"}
FACT_FIELDS_BY_TYPE = {
    "character": {
        "identity", "species", "appearance", "color", "marking", "body", "clothing", "state", "relationship",
    },
    "scene": {"location", "layout", "element", "lighting", "palette", "color", "time_weather", "state"},
    "prop": {
        "category", "appearance", "color", "marking", "material", "scale", "usage", "interaction", "state", "relationship",
    },
}


class StoryboardContractError(ValueError):
    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def validated_shot_fields(
    raw: Any,
    script_text: str,
    fallback_index: int,
    *,
    require_grounded_mention_label: bool,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StoryboardContractError("shot must be an object")
    index = _positive_int(raw.get("index"), fallback_index)
    shot_id = str(raw.get("shot_id") or f"shot_{index:02d}").strip()[:80]
    required_fields = ("duration", "description", "shot_size", "light_atmosphere", "camera_motion")
    fields = {name: str(raw.get(name) or "").strip() for name in required_fields}
    missing_fields = [name for name, value in fields.items() if not value]
    if missing_fields:
        raise StoryboardContractError(
            "shot is missing a required display field",
            details={
                "shot_id": shot_id,
                "missing_fields": missing_fields,
                "fields": [{"field": name} for name in missing_fields],
            },
        )
    fields["dialogue"] = str(raw.get("dialogue") or "").strip() or "无明确对白"
    fields["sound"] = str(raw.get("sound") or "").strip() or "无明确音效"
    evidence_items = raw.get("source_evidence")
    if not isinstance(evidence_items, list) or not evidence_items:
        raise StoryboardContractError("shot is missing source evidence", details={"shot_id": shot_id})
    source_evidence = [validated_evidence(item, script_text) for item in evidence_items]
    mentions_raw = raw.get("asset_mentions", [])
    if not isinstance(mentions_raw, list):
        raise StoryboardContractError("shot asset mentions must be a list", details={"shot_id": shot_id})
    mentions = [
        _validated_mention(
            item,
            script_text,
            shot_id,
            require_grounded_label=require_grounded_mention_label,
        )
        for item in mentions_raw
    ]
    if len({item["mention_id"] for item in mentions}) != len(mentions):
        raise StoryboardContractError("shot contains duplicate asset mentions", details={"shot_id": shot_id})
    return {
        "shot_id": shot_id,
        "index": index,
        **fields,
        "source_evidence": source_evidence,
        "asset_mentions": mentions,
        "unsupported_additions": string_list(raw.get("unsupported_additions"), limit=24),
    }


def validated_grounded_facts(raw: Any, script_text: str, asset_type: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise StoryboardContractError("grounded facts must be a list")
    facts: list[dict[str, Any]] = []
    for item in raw[:16]:
        if not isinstance(item, dict):
            raise StoryboardContractError("grounded fact must be an object")
        fact = str(item.get("fact") or "").strip()[:160]
        field = str(item.get("field") or "").strip().lower()
        if not fact or field not in FACT_FIELDS_BY_TYPE[asset_type]:
            raise StoryboardContractError("grounded fact text or field is invalid")
        facts.append(
            {"field": field, "fact": fact, "evidence": validated_evidence(item.get("evidence"), script_text)}
        )
    return facts


def validate_storyboard_set(shots: list[dict[str, Any]]) -> None:
    if len({shot["shot_id"] for shot in shots}) != len(shots):
        raise StoryboardContractError("storyboard contains duplicate shot ids")
    if [shot["index"] for shot in shots] != list(range(1, len(shots) + 1)):
        raise StoryboardContractError("storyboard shot indexes must be contiguous")
    evidence_keys: set[tuple[tuple[int, int], ...]] = set()
    descriptions: set[str] = set()
    previous_start = -1
    for shot in shots:
        key = tuple((item["start"], item["end"]) for item in shot["source_evidence"])
        if key in evidence_keys:
            raise StoryboardContractError("storyboard repeats the same source evidence")
        evidence_keys.add(key)
        description_key = "".join(shot["description"].split())
        if description_key in descriptions:
            raise StoryboardContractError("storyboard repeats the same shot description")
        descriptions.add(description_key)
        first_start = min(item["start"] for item in shot["source_evidence"])
        if first_start < previous_start:
            raise StoryboardContractError("storyboard source evidence is out of narrative order")
        previous_start = first_start


def string_list(value: Any, *, limit: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StoryboardContractError("expected a list")
    return [str(item).strip()[:160] for item in value[:limit] if str(item).strip()]


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def dedupe(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def validated_evidence(raw: Any, script_text: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StoryboardContractError("evidence must be an object")
    quote = str(raw.get("quote") or raw.get("text") or "")
    if not quote or quote not in script_text:
        raise StoryboardContractError("evidence quote is not present in the script")
    start = raw.get("start")
    end = raw.get("end")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(script_text):
        if script_text[start:end] == quote:
            return {"quote": quote, "start": start, "end": end}
    offsets = _all_offsets(script_text, quote)
    if len(offsets) != 1:
        raise StoryboardContractError("evidence offsets are invalid or ambiguous")
    return {"quote": quote, "start": offsets[0], "end": offsets[0] + len(quote)}


def _validated_mention(
    raw: Any,
    script_text: str,
    shot_id: str,
    *,
    require_grounded_label: bool,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StoryboardContractError("asset mention must be an object", details={"shot_id": shot_id})
    asset_type = str(raw.get("asset_type") or "").strip().lower()
    label = str(raw.get("label") or "").strip()[:80]
    if asset_type not in ASSET_TYPES or not label:
        raise StoryboardContractError("asset mention has invalid type or label", details={"shot_id": shot_id})
    evidence = validated_evidence(raw.get("evidence"), script_text)
    if require_grounded_label and label not in evidence["quote"]:
        raise StoryboardContractError("asset mention label is not present in its evidence", details={"shot_id": shot_id})
    mention_id = stable_id("mention", shot_id, asset_type, str(evidence["start"]), str(evidence["end"]), label)
    return {
        "mention_id": mention_id,
        "asset_type": asset_type,
        "label": label,
        "evidence": evidence,
        "relevance": str(raw.get("relevance") or "reusable_visual_asset").strip()[:120],
    }


def _all_offsets(source: str, value: str) -> list[int]:
    result: list[int] = []
    cursor = 0
    while True:
        index = source.find(value, cursor)
        if index < 0:
            return result
        result.append(index)
        cursor = index + 1


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    if parsed < 1:
        raise StoryboardContractError("shot index must be positive")
    return parsed


__all__ = (
    "ASSET_TYPES",
    "StoryboardContractError",
    "dedupe",
    "stable_id",
    "string_list",
    "validate_storyboard_set",
    "validated_grounded_facts",
    "validated_shot_fields",
)
