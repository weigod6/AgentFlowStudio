from __future__ import annotations

from typing import Any


def materialize_verified_shots(
    shots: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    verification_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entity_by_mention = {
        mention_id: entity
        for entity in entities
        for mention_id in entity.get("mention_ids", [])
    }
    verification_by_shot = {str(item["shot_id"]): item for item in verification_records}
    result: list[dict[str, Any]] = []
    for shot in shots:
        refs: list[dict[str, Any]] = []
        seen_entities: set[str] = set()
        for mention in shot.get("asset_mentions", []):
            entity = entity_by_mention.get(str(mention.get("mention_id") or ""))
            if not entity or entity["entity_id"] in seen_entities:
                continue
            seen_entities.add(entity["entity_id"])
            refs.append(_asset_ref(entity, shot))
        source_evidence = list(shot.get("source_evidence") or [])
        source_text = " ".join(str(item.get("quote") or "") for item in source_evidence).strip()
        source_start = min((int(item["start"]) for item in source_evidence), default=0)
        source_end = max((int(item["end"]) for item in source_evidence), default=0)
        verification = verification_by_shot.get(str(shot["shot_id"]), {})
        result.append(
            {
                "shot_id": shot["shot_id"],
                "index": shot["index"],
                "duration": shot["duration"],
                "description": shot["description"],
                "shot_size": shot["shot_size"],
                "light_atmosphere": shot["light_atmosphere"],
                "camera_motion": shot["camera_motion"],
                "dialogue": shot["dialogue"],
                "sound": shot["sound"],
                "asset_refs": refs,
                "asset_refs_authoritative": True,
                "asset_ref_authority": "runtime_provider_verified_v2",
                "dropped_asset_ref_diagnostics": [],
                "source_text": source_text,
                "source_span": {
                    "span_id": f"verified_span_{int(shot['index']):02d}",
                    "text": source_text,
                    "start": source_start,
                    "end": source_end,
                    "grounding_status": "source_grounded",
                },
                "source_evidence": source_evidence,
                "grounding_status": "source_grounded",
                "unsupported_additions": [],
                "verification": {
                    "status": str(verification.get("status") or "accepted"),
                    "reason_codes": list(verification.get("reason_codes") or []),
                    "verifier": "provider_independent_shot_review",
                },
                "planning_agent": {
                    "agent_id": "storyboard_provider_verified_v2",
                    "mode": "provider_generated_schema_verified",
                    "evidence_policy": "exact_script_quote_required",
                    "semantic_fallback_used": False,
                    "asset_refs_authoritative": True,
                },
            }
        )
    return result


def _asset_ref(entity: dict[str, Any], shot: dict[str, Any]) -> dict[str, Any]:
    mention_ids = {str(item) for item in entity.get("mention_ids", [])}
    local_mentions = [
        item for item in shot.get("asset_mentions", []) if str(item.get("mention_id") or "") in mention_ids
    ]
    local_quotes = _dedupe(
        str(item.get("evidence", {}).get("quote") or "").strip() for item in local_mentions
    )
    facts = [item for item in entity.get("grounded_facts", []) if isinstance(item, dict)]
    fact_texts = _dedupe(str(item.get("fact") or "").strip() for item in facts)
    label = str(entity["canonical_label"])
    signature = "；".join(fact_texts) or "；".join(local_quotes) or label
    return {
        "entity_id": entity["entity_id"],
        "label": label,
        "display_name": label,
        "aliases": list(entity.get("aliases") or [label]),
        "asset_id": f"candidate:{entity['entity_id']}",
        "asset_type": entity["asset_type"],
        "character_subtype": entity.get("character_subtype") or "",
        "status": "verified_candidate",
        "source": "provider_verified_v2",
        "scope": "storyboard",
        "confidence": 0.95,
        "evidence_text": " ".join(local_quotes)[:500],
        "visual_evidence_span": " ".join(local_quotes)[:500],
        "descriptive_signature": signature[:500],
        "evidence_modality": "visual",
        "modality_gate_status": "accepted",
        "name_source": "global_entity_resolution",
        "provisional_name": False,
        "mention_ids": [str(item.get("mention_id") or "") for item in local_mentions],
        "grounded_facts": facts,
        "continuity_locks": fact_texts,
        "negative_locks": [f"不得改变已验证事实：{fact}" for fact in fact_texts],
        "verification_status": "verified",
    }


def _dedupe(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


__all__ = ("materialize_verified_shots",)
