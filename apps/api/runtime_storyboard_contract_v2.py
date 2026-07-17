from __future__ import annotations

from typing import Any

from apps.api.runtime_storyboard_contract_fields_v2 import (
    ASSET_TYPES,
    StoryboardContractError,
    dedupe,
    stable_id,
    string_list,
    validate_storyboard_set,
    validated_grounded_facts,
    validated_shot_fields,
)
from apps.api.runtime_storyboard_json_v2 import json_object_from_provider_text

CHARACTER_SUBTYPES = {"human", "animal", "robot", "other"}
VERIFICATION_STATUSES = {"accepted", "corrected", "rejected", "requires_review"}


def validated_generation(payload: dict[str, Any], script_text: str) -> list[dict[str, Any]]:
    raw_shots = payload.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raise StoryboardContractError("provider response must contain non-empty shots")
    if len(raw_shots) > 80:
        raise StoryboardContractError("provider response exceeds shot limit")
    shots = [
        validated_shot_fields(item, script_text, index + 1, require_grounded_mention_label=False)
        for index, item in enumerate(raw_shots)
    ]
    validate_storyboard_set(shots)
    return shots


def validated_generation_draft(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_shots = payload.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raise StoryboardContractError("provider response must contain non-empty shots")
    if len(raw_shots) > 80:
        raise StoryboardContractError("provider response exceeds shot limit")
    shots = [_validated_draft_shot(item, index + 1) for index, item in enumerate(raw_shots)]
    if len({shot["shot_id"] for shot in shots}) != len(shots):
        raise StoryboardContractError("storyboard contains duplicate shot ids")
    if [shot["index"] for shot in shots] != list(range(1, len(shots) + 1)):
        raise StoryboardContractError("storyboard shot indexes must be contiguous")
    return shots


def validated_verifier_result(
    payload: dict[str, Any],
    original_shot: dict[str, Any],
    script_text: str,
) -> tuple[str, dict[str, Any], list[str]]:
    status = str(payload.get("status") or "").strip().lower()
    if status not in VERIFICATION_STATUSES:
        raise StoryboardContractError("shot verifier returned an unsupported status")
    shot_id = str(payload.get("shot_id") or "").strip()
    if shot_id != str(original_shot.get("shot_id") or ""):
        raise StoryboardContractError("shot verifier returned a mismatched shot id")
    reasons = string_list(payload.get("reason_codes"), limit=12)
    if status == "corrected":
        corrected = validated_shot_fields(
            payload.get("corrected_shot"),
            script_text,
            int(original_shot["index"]),
            require_grounded_mention_label=True,
        )
        if corrected["shot_id"] != shot_id or corrected["index"] != original_shot["index"]:
            raise StoryboardContractError("shot verifier changed shot identity")
        return status, corrected, reasons
    if status == "accepted":
        accepted = validated_shot_fields(
            original_shot,
            script_text,
            int(original_shot["index"]),
            require_grounded_mention_label=True,
        )
        return status, accepted, reasons
    return status, original_shot, reasons


def validated_resolution(
    payload: dict[str, Any],
    shots: list[dict[str, Any]],
    script_text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mentions = {
        str(mention["mention_id"]): mention
        for shot in shots
        for mention in shot.get("asset_mentions", [])
    }
    raw_entities = payload.get("entities")
    unresolved = payload.get("unresolved_mentions", [])
    if not isinstance(raw_entities, list) or not isinstance(unresolved, list):
        raise StoryboardContractError("entity resolver response has invalid collections")
    assigned: set[str] = set()
    entities: list[dict[str, Any]] = []
    for raw in raw_entities:
        if not isinstance(raw, dict):
            raise StoryboardContractError("entity resolver returned a non-object entity")
        mention_ids = string_list(raw.get("mention_ids"), limit=80)
        if not mention_ids or any(item not in mentions for item in mention_ids):
            raise StoryboardContractError("entity resolver referenced an unknown mention")
        if assigned.intersection(mention_ids):
            raise StoryboardContractError("entity resolver assigned a mention more than once")
        cluster = [mentions[item] for item in mention_ids]
        asset_types = {str(item["asset_type"]) for item in cluster}
        asset_type = str(raw.get("asset_type") or "").strip()
        if len(asset_types) != 1 or asset_type not in asset_types:
            raise StoryboardContractError("entity resolver merged incompatible asset types")
        labels = dedupe(str(item["label"]) for item in cluster)
        canonical_label = str(raw.get("canonical_label") or "").strip()
        if canonical_label not in labels:
            raise StoryboardContractError("entity resolver invented a canonical label")
        subtype = str(raw.get("character_subtype") or "").strip().lower()
        if asset_type == "character" and subtype not in CHARACTER_SUBTYPES:
            raise StoryboardContractError("entity resolver omitted character subtype")
        if asset_type != "character":
            subtype = ""
        facts = validated_grounded_facts(raw.get("grounded_facts"), script_text, asset_type)
        entity_id = stable_id("entity", asset_type, *sorted(mention_ids))
        entities.append(
            {
                "entity_id": entity_id,
                "asset_type": asset_type,
                "character_subtype": subtype,
                "canonical_label": canonical_label,
                "aliases": labels,
                "mention_ids": mention_ids,
                "grounded_facts": facts,
            }
        )
        assigned.update(mention_ids)
    review_items: list[dict[str, Any]] = []
    for raw in unresolved:
        if not isinstance(raw, dict):
            raise StoryboardContractError("entity resolver returned invalid unresolved mention")
        mention_ids = string_list(raw.get("mention_ids"), limit=80)
        if not mention_ids or any(item not in mentions for item in mention_ids):
            raise StoryboardContractError("entity resolver marked an unknown mention unresolved")
        if assigned.intersection(mention_ids):
            raise StoryboardContractError("resolved and unresolved mention sets overlap")
        assigned.update(mention_ids)
        review_items.append(
            {
                "mention_ids": mention_ids,
                "reason_code": str(raw.get("reason_code") or "ambiguous_entity").strip()[:80],
            }
        )
    if assigned != set(mentions):
        raise StoryboardContractError("entity resolver did not account for every mention")
    return entities, review_items


def _validated_draft_shot(raw: Any, fallback_index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StoryboardContractError("shot must be an object")
    try:
        index = int(raw.get("index") or fallback_index)
    except (TypeError, ValueError) as exc:
        raise StoryboardContractError("shot index must be an integer") from exc
    if index < 1:
        raise StoryboardContractError("shot index must be positive")
    shot_id = str(raw.get("shot_id") or f"shot_{index:02d}").strip()[:80]
    source_evidence = raw.get("source_evidence", [])
    asset_mentions = raw.get("asset_mentions", [])
    if not isinstance(source_evidence, list):
        raise StoryboardContractError("shot source evidence must be a list", details={"shot_id": shot_id})
    if not isinstance(asset_mentions, list):
        raise StoryboardContractError("shot asset mentions must be a list", details={"shot_id": shot_id})
    if any(not isinstance(item, dict) for item in [*source_evidence, *asset_mentions]):
        raise StoryboardContractError("shot evidence and asset mentions must be objects", details={"shot_id": shot_id})
    description = str(raw.get("description") or "").strip()
    has_evidence_quote = any(str(item.get("quote") or item.get("text") or "").strip() for item in source_evidence)
    if not description and not has_evidence_quote:
        missing_fields = ["description", "source_evidence"]
        raise StoryboardContractError(
            "shot draft lacks a narrative anchor",
            details={
                "shot_id": shot_id,
                "missing_fields": missing_fields,
                "fields": [{"field": name} for name in missing_fields],
            },
        )
    return {
        "shot_id": shot_id,
        "index": index,
        "duration": str(raw.get("duration") or "").strip(),
        "description": description,
        "shot_size": str(raw.get("shot_size") or "").strip(),
        "light_atmosphere": str(raw.get("light_atmosphere") or "").strip(),
        "camera_motion": str(raw.get("camera_motion") or "").strip(),
        "dialogue": str(raw.get("dialogue") or "").strip() or "无明确对白",
        "sound": str(raw.get("sound") or "").strip() or "无明确音效",
        "source_evidence": source_evidence,
        "asset_mentions": asset_mentions,
        "unsupported_additions": raw.get("unsupported_additions", []),
    }


__all__ = (
    "ASSET_TYPES",
    "StoryboardContractError",
    "json_object_from_provider_text",
    "validate_storyboard_set",
    "validated_generation",
    "validated_generation_draft",
    "validated_resolution",
    "validated_verifier_result",
)
