from __future__ import annotations

import re
from typing import Any

from agentflow.algorithms.asset_facts import render_asset_prompt_line


ALGORITHM_ID = "afs.asset_card_candidates.v0.1"
INPUT_CONTRACT = "project id and candidate asset graph"
OUTPUT_CONTRACT = "safe asset card candidate set with confirmation state and evidence refs"
FAILURE_MODES = ("missing_asset_graph", "missing_candidate_assets", "unsupported_asset_type", "unsafe_candidate_rejected")
EVIDENCE_BOUNDARY = "candidate asset-card metadata only; no provider calls, media generation, or fixed asset writes"

CANDIDATE_STAGE = "storyboard_asset_card_candidates"
SUPPORTED_ASSET_TYPES = {"character", "scene", "prop"}
NON_CLAIMS = [
    "not fixed asset memory",
    "not vision asset-card draft",
    "not generated media",
    "not provider smoke",
    "not human acceptance",
    "not business validation",
    "not durable memory promotion",
]


def build_asset_card_candidates(*, project_id: str, asset_graph: dict[str, Any]) -> dict[str, Any]:
    assets = [
        asset
        for asset in _list(asset_graph.get("assets"))
        if isinstance(asset, dict) and str(asset.get("asset_type") or "") in SUPPORTED_ASSET_TYPES
        and str(asset.get("modality_gate_status") or "accepted") == "accepted"
        and str(asset.get("evidence_modality") or "visual") == "visual"
    ]
    candidates = [_candidate(project_id, asset) for asset in assets]
    relationships = [
        {
            "relationship_type": "graph_asset_has_asset_card_candidate",
            "from_node_id": candidate["source_graph_asset_id"],
            "to_candidate_id": candidate["candidate_id"],
        }
        for candidate in candidates
    ]
    return {
        "artifact_type": "agentflow_asset_card_candidate_set",
        "schema_version": "0.1.0",
        "algorithm_id": ALGORITHM_ID,
        "candidate_stage": CANDIDATE_STAGE,
        "summary": {
            "project_id": project_id,
            "candidate_count": len(candidates),
            "asset_types": sorted({candidate["asset_type"] for candidate in candidates}),
            "reuse_scope_counts": _reuse_scope_counts(candidates),
            "human_review_needed": True,
            "writes_fixed_asset": False,
            "provider_calls_started": False,
        },
        "candidates": candidates,
        "relationships": relationships,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": NON_CLAIMS,
    }


def _candidate(project_id: str, asset: dict[str, Any]) -> dict[str, Any]:
    graph_asset_id = str(asset.get("graph_asset_id") or asset.get("asset_id") or "asset")
    asset_type = str(asset.get("asset_type") or "")
    label = str(asset.get("display_name") or asset.get("label") or asset.get("asset_id") or "asset")[:80]
    evidence_spans = _evidence_spans(asset)
    return {
        "candidate_id": f"asset_card_candidate:{_safe_id(graph_asset_id)}",
        "project_id": project_id,
        "source_graph_asset_id": graph_asset_id,
        "source_asset_id": str(asset.get("asset_id") or graph_asset_id),
        "asset_type": asset_type,
        "character_subtype": str(asset.get("character_subtype") or ""),
        "status": "candidate",
        "confirmation_state": "needs_human_confirmation",
        "draft_fields": {
            "display_name": label,
            "descriptive_signature": str(asset.get("descriptive_signature") or "")[:240],
            "character_subtype": str(asset.get("character_subtype") or ""),
            "facts": dict(asset.get("facts") or {}) if isinstance(asset.get("facts"), dict) else {},
            "fact_evidence": _text_list(asset.get("fact_evidence"), limit=6, max_len=240),
            "missing_fact_fields": _text_list(asset.get("missing_fact_fields"), limit=8, max_len=80),
            "evidence_modality": str(asset.get("evidence_modality") or "visual"),
            "visual_evidence_span": str(asset.get("visual_evidence_span") or "")[:240],
            "name_source": str(asset.get("name_source") or "candidate"),
            "provisional_name": bool(asset.get("provisional_name")),
            "narrative_role": str(asset.get("role") or _role(asset_type)),
            "visual_description_seed": _visual_description_seed(asset_type, label, evidence_spans, asset),
            "constraints": [str(item)[:120] for item in _list(asset.get("continuity_locks"))[:8]],
            "negative_constraints": [str(item)[:120] for item in _list(asset.get("negative_locks"))[:8]],
            "reference_policy": "requires safe media refs before provider-backed asset-card drafting",
        },
        "safe_evidence": {
            "shot_refs": [str(item) for item in _list(asset.get("shot_refs"))[:24]],
            "evidence_span_count": len(evidence_spans),
            "evidence_spans": evidence_spans,
            "fact_evidence": _text_list(asset.get("fact_evidence"), limit=6, max_len=240),
            "confidence": _confidence(asset.get("confidence")),
            "evidence_modality": str(asset.get("evidence_modality") or "visual"),
            "visual_evidence_span": str(asset.get("visual_evidence_span") or "")[:240],
        },
        "reuse_policy": _reuse_policy(asset),
        "asset_memory_policy": {
            "writes_fixed_asset": False,
            "included_in_context_before_confirmation": False,
            "requires_human_confirmation": True,
            "writes_long_term_memory": False,
        },
        "provider_policy": {
            "provider_calls_started": False,
            "provider_gate_required_for_enrichment": "AFS_ALLOW_REMOTE_VISION",
            "media_refs_required_before_provider_draft": True,
        },
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def _reuse_policy(asset: dict[str, Any]) -> dict[str, Any]:
    shot_refs = [str(item) for item in _list(asset.get("shot_refs"))[:24] if str(item)]
    is_cross_shot = len(shot_refs) >= 2
    return {
        "suggested_reuse_scope": "project_reuse_candidate" if is_cross_shot else "shot_local_candidate",
        "reason": "appears_across_multiple_shots" if is_cross_shot else "single_shot_evidence_only",
        "shot_ref_count": len(shot_refs),
        "requires_human_confirmation": True,
        "writes_fixed_asset": False,
        "promotion_blocked_by_default": True,
    }


def _reuse_scope_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"project_reuse_candidate": 0, "shot_local_candidate": 0}
    for candidate in candidates:
        scope = str((candidate.get("reuse_policy") or {}).get("suggested_reuse_scope") or "")
        if scope in counts:
            counts[scope] += 1
    return counts


def _visual_description_seed(asset_type: str, label: str, evidence_spans: list[dict[str, str]], asset: dict[str, Any]) -> str:
    rendered = render_asset_prompt_line(asset, max_facts=6)
    if rendered and "证据事实" in rendered:
        return rendered[:500]
    evidence = " ".join(span["text"] for span in evidence_spans[:2] if span.get("text")).strip()
    if asset_type == "character":
        fallback = f"{label} character appearance is pending human review."
    elif asset_type == "scene":
        fallback = f"{label} scene layout and lighting are pending human review."
    else:
        fallback = f"{label} prop appearance, material, and scale are pending human review."
    return (evidence or fallback)[:500]


def _evidence_spans(asset: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for span in _list(asset.get("evidence_spans"))[:12]:
        if not isinstance(span, dict):
            continue
        result.append(
            {
                "shot_id": str(span.get("shot_id") or ""),
                "source_span_id": str(span.get("source_span_id") or ""),
                "text": str(span.get("text") or "")[:240],
                "source": str(span.get("source") or "candidate"),
            }
        )
    return result


def _safe_id(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", str(value or "")).strip("_")[:96] or "asset"


def _confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return round(max(0.0, min(float(value), 1.0)), 3)
    return 0.6


def _role(asset_type: str) -> str:
    if asset_type == "character":
        return "story_character"
    if asset_type == "scene":
        return "scene_anchor"
    if asset_type == "prop":
        return "story_prop"
    return "asset"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text_list(value: Any, *, limit: int, max_len: int) -> list[str]:
    result: list[str] = []
    for item in _list(value):
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text[:max_len])
        if len(result) >= limit:
            break
    return result


__all__ = (
    "ALGORITHM_ID",
    "CANDIDATE_STAGE",
    "EVIDENCE_BOUNDARY",
    "FAILURE_MODES",
    "INPUT_CONTRACT",
    "NON_CLAIMS",
    "OUTPUT_CONTRACT",
    "SUPPORTED_ASSET_TYPES",
    "build_asset_card_candidates",
)
