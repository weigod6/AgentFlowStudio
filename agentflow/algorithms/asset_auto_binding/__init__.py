from __future__ import annotations

import re
from typing import Any


ALGORITHM_ID = "afs.asset_auto_binding.v0.1"
INPUT_CONTRACT = "candidate asset graph, fixed visual assets, project id"
OUTPUT_CONTRACT = "safe reversible binding graph with suggestions, established graph relationships, and blocked candidates"
FAILURE_MODES = (
    "low_confidence_candidate",
    "missing_candidate_evidence",
    "missing_fixed_source_evidence",
    "ambiguous_fixed_asset_match",
    "unsupported_additions_require_review",
    "irreversible_binding_rejected",
)
EVIDENCE_BOUNDARY = "deterministic graph binding only; no provider call, media QA, human acceptance, or memory promotion"

ASSET_AUTO_BINDING_SCHEMA_VERSION = "0.1.0"
MIN_BINDING_CONFIDENCE = 0.82
NON_CLAIMS = [
    "not provider smoke",
    "not generated media QA",
    "not human acceptance",
    "not fixed asset promotion",
    "not durable memory promotion",
    "not business validation",
]


def build_asset_auto_binding_graph(
    *,
    project_id: str,
    asset_graph: dict[str, Any],
    fixed_visual_assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fixed_assets = [item for item in _list(fixed_visual_assets) if isinstance(item, dict)]
    candidate_assets = [item for item in _list(asset_graph.get("assets")) if isinstance(item, dict)]
    fixed_index = _fixed_asset_index(fixed_assets)
    graph_reasons = _graph_block_reasons(asset_graph)
    suggestions: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for candidate in candidate_assets:
        block_reasons = [*graph_reasons, *_candidate_block_reasons(candidate)]
        matches = fixed_index.get(_asset_key(candidate), [])
        if not matches:
            block_reasons.append("no_fixed_asset_match")
        elif len(matches) > 1:
            block_reasons.append("ambiguous_fixed_asset_match")
        else:
            block_reasons.extend(_fixed_asset_block_reasons(matches[0]))

        if block_reasons:
            blocked.append(_blocked_candidate(candidate, block_reasons))
            continue

        suggestion = _binding_suggestion(project_id, candidate, matches[0])
        suggestions.append(suggestion)
        relationships.append(_relationship_from_suggestion(suggestion))

    return {
        "artifact_type": "agentflow_asset_auto_binding_graph",
        "schema_version": ASSET_AUTO_BINDING_SCHEMA_VERSION,
        "algorithm_id": ALGORITHM_ID,
        "graph_stage": "asset_auto_binding_reversible_graph",
        "binding_policy": {
            "min_confidence": MIN_BINDING_CONFIDENCE,
            "match_rule": "exact_asset_type_and_normalized_label",
            "explainability_required": True,
            "reversibility_required": True,
            "fail_closed": True,
        },
        "summary": {
            "project_id": str(project_id or ""),
            "candidate_asset_count": len(candidate_assets),
            "fixed_visual_asset_count": len(fixed_assets),
            "suggested_binding_count": len(suggestions),
            "established_binding_count": len(relationships),
            "blocked_candidate_count": len(blocked),
            "provider_calls_started": False,
            "writes_long_term_memory": False,
            "writes_company_kb": False,
        },
        "binding_suggestions": suggestions,
        "relationships": relationships,
        "blocked_candidates": blocked,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": NON_CLAIMS,
    }


def _binding_suggestion(project_id: str, candidate: dict[str, Any], fixed: dict[str, Any]) -> dict[str, Any]:
    graph_asset_id = str(candidate.get("graph_asset_id") or "")
    fixed_asset_id = str(fixed.get("asset_id") or "")
    binding_id = f"binding:{_safe_id(graph_asset_id)}:{_safe_id(fixed_asset_id)}"
    source_evidence = _safe_source_evidence(fixed.get("source_evidence"))
    return {
        "binding_id": binding_id,
        "binding_state": "bound",
        "binding_decision": "auto_established",
        "project_id": str(project_id or ""),
        "graph_asset_id": graph_asset_id,
        "fixed_visual_asset_id": fixed_asset_id,
        "asset_type": str(candidate.get("asset_type") or ""),
        "label": str(candidate.get("label") or "")[:80],
        "confidence": _confidence(candidate.get("confidence")),
        "explainability": {
            "matched_fields": ["asset_type", "label", "candidate_evidence", "fixed_source_evidence"],
            "candidate_evidence_span_count": len(_list(candidate.get("evidence_spans"))),
            "fixed_source_evidence_available": bool(source_evidence),
            "explanation": "Exact asset type and label match with candidate evidence and fixed source evidence.",
        },
        "lineage_refs": {
            "candidate_graph_asset_id": graph_asset_id,
            "fixed_visual_asset_id": fixed_asset_id,
            "fixed_source_node_id": str(fixed.get("source_node_id") or ""),
            "source_human_gate_id": str(source_evidence.get("source_human_gate_id") or ""),
            "source_asset_card_candidate_id": str(source_evidence.get("source_asset_card_candidate_id") or ""),
        },
        "reversal_plan": {
            "reversible": True,
            "action": "unbind",
            "restores_binding_state": "unbound",
            "preserve_lineage": True,
            "destructive_asset_write": False,
        },
        "safety_boundary": {
            "provider_calls_started": False,
            "writes_long_term_memory": False,
            "writes_company_kb": False,
            "human_acceptance_claimed": False,
        },
    }


def _relationship_from_suggestion(suggestion: dict[str, Any]) -> dict[str, Any]:
    return {
        "relationship_type": "asset_auto_binding_established",
        "from_node_id": f"asset:{suggestion['graph_asset_id']}",
        "to_node_id": f"fixed_asset:{_safe_id(str(suggestion['fixed_visual_asset_id']))}",
        "binding_id": suggestion["binding_id"],
        "binding_state": suggestion["binding_state"],
        "confidence": suggestion["confidence"],
        "explainable": True,
        "reversible": True,
        "undo_action": "unbind",
        "source": ALGORITHM_ID,
    }


def _blocked_candidate(candidate: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    unique_reasons = [reason for index, reason in enumerate(reasons) if reason and reason not in reasons[:index]]
    return {
        "graph_asset_id": str(candidate.get("graph_asset_id") or ""),
        "asset_type": str(candidate.get("asset_type") or ""),
        "label": str(candidate.get("label") or "")[:80],
        "confidence": _confidence(candidate.get("confidence")),
        "binding_state": "blocked",
        "block_reasons": unique_reasons,
    }


def _graph_block_reasons(asset_graph: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if asset_graph.get("artifact_type") != "agentflow_asset_graph":
        reasons.append("invalid_asset_graph_contract")
    if _list(asset_graph.get("unsupported_additions")):
        reasons.append("unsupported_additions_require_review")
    if _list(asset_graph.get("merge_candidates")):
        reasons.append("merge_candidates_require_review")
    if asset_graph.get("writes_long_term_memory") is True or asset_graph.get("writes_company_kb") is True:
        reasons.append("unsafe_graph_write_claim")
    return reasons


def _candidate_block_reasons(candidate: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    reasons.extend(
        str(item).strip()
        for item in _list(candidate.get("auto_binding_block_reasons"))
        if str(item).strip()
    )
    if not str(candidate.get("graph_asset_id") or ""):
        reasons.append("missing_graph_asset_id")
    if _confidence(candidate.get("confidence")) < MIN_BINDING_CONFIDENCE:
        reasons.append("low_confidence_candidate")
    if not _list(candidate.get("evidence_spans")):
        reasons.append("missing_candidate_evidence")
    if not str(candidate.get("asset_type") or "") or not str(candidate.get("label") or ""):
        reasons.append("missing_asset_type_or_label")
    return reasons


def _fixed_asset_block_reasons(fixed: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if fixed.get("status") != "fixed":
        reasons.append("fixed_asset_not_fixed")
    if not str(fixed.get("asset_id") or ""):
        reasons.append("missing_fixed_asset_id")
    if not _safe_source_evidence(fixed.get("source_evidence")):
        reasons.append("missing_fixed_source_evidence")
    return reasons


def _fixed_asset_index(fixed_assets: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for asset in fixed_assets:
        key = _asset_key(asset)
        if key == ("", ""):
            continue
        index.setdefault(key, []).append(asset)
    return index


def _asset_key(asset: dict[str, Any]) -> tuple[str, str]:
    return (str(asset.get("asset_type") or ""), _normalized_label(asset.get("label")))


def _safe_source_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keys = (
        "source_contract",
        "source_human_gate_id",
        "source_asset_card_candidate_id",
        "source_stage",
        "result_asset_status",
        "provider_calls_started",
        "generated_media_claimed",
        "human_creative_acceptance_claimed",
        "business_validation_claimed",
    )
    return {key: value.get(key) for key in keys if key in value and value.get(key) not in (None, "", [], {})}


def _normalized_label(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def _safe_id(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.:-]+", "_", str(value or "")).strip("_")[:120] or "item"


def _confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return round(max(0.0, min(float(value), 1.0)), 3)
    return 0.0


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


__all__ = (
    "ALGORITHM_ID",
    "ASSET_AUTO_BINDING_SCHEMA_VERSION",
    "EVIDENCE_BOUNDARY",
    "FAILURE_MODES",
    "INPUT_CONTRACT",
    "MIN_BINDING_CONFIDENCE",
    "NON_CLAIMS",
    "OUTPUT_CONTRACT",
    "build_asset_auto_binding_graph",
)
