from __future__ import annotations

from copy import deepcopy
from typing import Any

from agentflow.algorithms.asset_facts import build_asset_fact_profile, render_asset_prompt_line


def asset_graph_from_context_bundle(context_bundle: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_bundle, dict):
        return {}
    graph = _extract_asset_graph_from_mapping(context_bundle)
    return graph if graph else {}


def asset_graph_feedback_overlay_from_context_bundle(context_bundle: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_bundle, dict):
        return {}
    return _extract_feedback_overlay_from_mapping(context_bundle)


def asset_graph_from_context_subgraph(context_subgraph: Any) -> dict[str, Any]:
    if context_subgraph is None:
        return {}
    if isinstance(context_subgraph, dict):
        graph = _extract_asset_graph_from_mapping(context_subgraph)
        if graph:
            return graph
        nodes = context_subgraph.get("nodes")
    else:
        graph = _extract_asset_graph_from_mapping(_as_mapping(context_subgraph))
        if graph:
            return graph
        nodes = getattr(context_subgraph, "nodes", None)
    for node in _list(nodes):
        graph = _extract_asset_graph_from_mapping(_as_mapping(node))
        if graph:
            return graph
        params = _as_mapping(getattr(node, "node_parameters", None) if not isinstance(node, dict) else node.get("node_parameters"))
        graph = _extract_asset_graph_from_mapping(params)
        if graph:
            return graph
    return {}


def asset_graph_feedback_overlay_from_context_subgraph(context_subgraph: Any) -> dict[str, Any]:
    if context_subgraph is None:
        return {}
    root = _as_mapping(context_subgraph)
    overlay = _extract_feedback_overlay_from_mapping(root)
    if overlay:
        return overlay
    nodes = context_subgraph.get("nodes") if isinstance(context_subgraph, dict) else getattr(context_subgraph, "nodes", None)
    for node in _list(nodes):
        mapping = _as_mapping(node)
        overlay = _extract_feedback_overlay_from_mapping(mapping)
        if overlay:
            return overlay
        params = _as_mapping(getattr(node, "node_parameters", None) if not isinstance(node, dict) else node.get("node_parameters"))
        overlay = _extract_feedback_overlay_from_mapping(params)
        if overlay:
            return overlay
    return {}


def summarize_asset_graph_for_plan(
    asset_graph: dict[str, Any] | None,
    *,
    feedback_overlay: dict[str, Any] | None = None,
    max_assets: int = 8,
) -> dict[str, Any]:
    if not isinstance(asset_graph, dict) or not asset_graph:
        return _empty_summary()
    asset_graph = apply_asset_graph_feedback_overlay(asset_graph, feedback_overlay)
    if isinstance(asset_graph.get("locked_assets"), list):
        return _normalize_existing_summary(asset_graph, max_assets=max_assets)

    assets = _list(asset_graph.get("assets"))
    locked_assets = [_summarize_asset(asset) for asset in assets if isinstance(asset, dict) and str(asset.get("status") or "") != "rejected_by_user"]
    locked_assets = [asset for asset in locked_assets if asset.get("graph_asset_id") or asset.get("asset_id")][:max_assets]
    unsupported = _summarize_unsupported(asset_graph.get("unsupported_additions"))
    feedback = _summarize_feedback_decisions(asset_graph.get("asset_graph_feedback_overlay") or feedback_overlay)
    blocked_ids = _strings(asset_graph.get("blocked_graph_asset_ids"), limit=16)
    review_state = str(
        asset_graph.get("review_state")
        or ("needs_review_unsupported_addition" if unsupported else ("asset_graph_feedback_applied" if feedback else ("asset_graph_available" if locked_assets else "not_available")))
    )
    return {
        "artifact_type": "agentflow_asset_graph_context",
        "schema_version": "0.1.0",
        "source_artifact_type": str(asset_graph.get("artifact_type") or ""),
        "asset_count": _int(asset_graph.get("asset_count"), default=len(assets)),
        "selected_asset_count": len(locked_assets),
        "graph_asset_ids": [str(asset.get("graph_asset_id") or asset.get("asset_id") or "") for asset in locked_assets],
        "locked_assets": locked_assets,
        "unsupported_additions": unsupported,
        "feedback_decisions": feedback,
        "blocked_graph_asset_ids": blocked_ids,
        "review_state": review_state,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def format_asset_graph_prompt_lines(asset_graph_context: dict[str, Any] | None, *, max_assets: int = 6) -> str:
    if not isinstance(asset_graph_context, dict):
        return ""
    assets = _list(asset_graph_context.get("locked_assets"))[:max_assets]
    if not assets:
        return ""
    lines = ["Asset graph continuity:"]
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        label = str(asset.get("label") or asset.get("graph_asset_id") or "asset")
        graph_asset_id = str(asset.get("graph_asset_id") or asset.get("asset_id") or "")
        kind = "/".join(part for part in (str(asset.get("asset_type") or ""), str(asset.get("role") or "")) if part)
        rendered = render_asset_prompt_line(asset, max_facts=6)
        if rendered:
            lines.append(f"- {graph_asset_id} {rendered}")
            continue
        locks = "; ".join(_strings(asset.get("continuity_locks"), limit=4)) or "approved visual facts"
        avoids = "; ".join(_strings(asset.get("negative_locks"), limit=3)) or "unrequested changes"
        lines.append(f"- {graph_asset_id} {label} ({kind}): lock {locks}; avoid {avoids}")
    unsupported = _list(asset_graph_context.get("unsupported_additions"))
    if unsupported:
        additions = ", ".join(str(item.get("addition") or "") for item in unsupported[:4] if isinstance(item, dict))
        if additions:
            lines.append(f"- Review guard: upstream unsupported additions need review before becoming visual facts: {additions}")
    feedback = _list(asset_graph_context.get("feedback_decisions"))
    rejected = [item for item in feedback if isinstance(item, dict) and item.get("decision") == "reject"]
    if rejected:
        labels = ", ".join(str(item.get("label") or item.get("graph_asset_id") or "") for item in rejected[:4])
        if labels:
            lines.append(f"- Feedback guard: do not use rejected asset graph entries: {labels}")
    return "\n".join(line for line in lines if line.strip())


def build_asset_graph_feedback_overlay(feedback: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for event in _list(feedback) if isinstance(feedback, list) else ([feedback] if isinstance(feedback, dict) else []):
        if not isinstance(event, dict):
            continue
        if str(event.get("kind") or "") not in {"asset_graph_feedback", "studio_asset_graph_feedback"} and not isinstance(event.get("decisions"), list):
            continue
        for item in _list(event.get("decisions") or event.get("asset_decisions")):
            decision = _feedback_decision(item)
            if decision:
                decisions.append(decision)
    return {
        "artifact_type": "agentflow_asset_graph_feedback_overlay",
        "schema_version": "0.1.0",
        "decisions": decisions[:32],
        "review_state": "feedback_overlay_available" if decisions else "not_available",
        "feedback_is_memory": False,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def apply_asset_graph_feedback_overlay(asset_graph: dict[str, Any], feedback_overlay: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(asset_graph, dict):
        return {}
    overlay = _normalize_feedback_overlay(feedback_overlay)
    if not overlay.get("decisions"):
        return asset_graph
    graph = deepcopy(asset_graph)
    decisions = {str(item.get("graph_asset_id") or ""): item for item in _list(overlay.get("decisions")) if isinstance(item, dict)}
    blocked: list[str] = _strings(graph.get("blocked_graph_asset_ids"), limit=32)
    enriched_decisions: list[dict[str, Any]] = []
    for asset in _list(graph.get("assets")):
        if not isinstance(asset, dict):
            continue
        graph_asset_id = str(asset.get("graph_asset_id") or "")
        decision = decisions.get(graph_asset_id)
        if not decision:
            continue
        enriched = {**decision}
        if not enriched.get("label"):
            enriched["label"] = str(asset.get("label") or "")
        asset.setdefault("feedback_decisions", []).append(enriched)
        enriched_decisions.append(enriched)
        if enriched["decision"] == "confirm":
            asset["status"] = "confirmed_by_user"
            asset["review_state"] = "user_confirmed"
        elif enriched["decision"] == "lock":
            asset["status"] = "locked_by_user"
            asset["review_state"] = "user_locked"
        elif enriched["decision"] == "revise":
            asset["status"] = "needs_revision"
            asset["review_state"] = "user_revision_requested"
        elif enriched["decision"] == "reject":
            asset["status"] = "rejected_by_user"
            asset["review_state"] = "user_rejected"
            if graph_asset_id and graph_asset_id not in blocked:
                blocked.append(graph_asset_id)
            asset["negative_locks"] = _dedupe([*_strings(asset.get("negative_locks"), limit=16), f"do not use rejected asset {asset.get('label') or graph_asset_id}"])
        if enriched.get("label"):
            asset["label"] = str(enriched["label"])[:80]
        asset["continuity_locks"] = _dedupe([*_strings(asset.get("continuity_locks"), limit=16), *_strings(enriched.get("continuity_locks"), limit=8)])
        asset["negative_locks"] = _dedupe([*_strings(asset.get("negative_locks"), limit=16), *_strings(enriched.get("negative_locks"), limit=8)])
    graph["asset_graph_feedback_overlay"] = {**overlay, "decisions": enriched_decisions or overlay.get("decisions") or []}
    graph["blocked_graph_asset_ids"] = blocked
    graph["feedback_applied"] = True
    graph["review_state"] = "asset_graph_feedback_applied"
    graph["writes_long_term_memory"] = False
    graph["writes_company_kb"] = False
    return graph


def _extract_asset_graph_from_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    for key in ("asset_graph", "assetGraph", "asset_graph_channel", "assetGraphChannel"):
        value = mapping.get(key)
        if _looks_like_asset_graph(value):
            return value
    for key in ("asset_graph_context", "assetGraphContext"):
        value = mapping.get(key)
        if _looks_like_asset_graph_summary(value):
            return value
    for key in ("node_parameters", "runtime_context", "structured_shot", "structuredShot"):
        nested = mapping.get(key)
        if isinstance(nested, dict):
            graph = _extract_asset_graph_from_mapping(nested)
            if graph:
                return graph
    return {}


def _extract_feedback_overlay_from_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    for key in ("asset_graph_feedback_overlay", "assetGraphFeedbackOverlay"):
        value = mapping.get(key)
        if _looks_like_feedback_overlay(value):
            return _normalize_feedback_overlay(value)
    for key in ("asset_graph_feedback", "assetGraphFeedback", "feedback"):
        value = mapping.get(key)
        overlay = build_asset_graph_feedback_overlay(value if isinstance(value, (dict, list)) else None)
        if overlay.get("decisions"):
            return overlay
    for key in ("node_parameters", "runtime_context", "structured_shot", "structuredShot"):
        nested = mapping.get(key)
        if isinstance(nested, dict):
            overlay = _extract_feedback_overlay_from_mapping(nested)
            if overlay:
                return overlay
    return {}


def _looks_like_asset_graph(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("assets"), list)


def _looks_like_asset_graph_summary(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("locked_assets"), list)


def _looks_like_feedback_overlay(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("decisions"), list)


def _normalize_existing_summary(summary: dict[str, Any], *, max_assets: int) -> dict[str, Any]:
    assets = [_summarize_asset(asset) for asset in _list(summary.get("locked_assets")) if isinstance(asset, dict) and str(asset.get("status") or "") != "rejected_by_user"]
    assets = [asset for asset in assets if asset.get("graph_asset_id") or asset.get("asset_id")][:max_assets]
    unsupported = _summarize_unsupported(summary.get("unsupported_additions"))
    feedback = _summarize_feedback_decisions(summary.get("feedback_decisions") or summary.get("asset_graph_feedback_overlay"))
    return {
        "artifact_type": "agentflow_asset_graph_context",
        "schema_version": "0.1.0",
        "source_artifact_type": str(summary.get("source_artifact_type") or summary.get("artifact_type") or ""),
        "asset_count": _int(summary.get("asset_count"), default=len(assets)),
        "selected_asset_count": len(assets),
        "graph_asset_ids": [str(asset.get("graph_asset_id") or asset.get("asset_id") or "") for asset in assets],
        "locked_assets": assets,
        "unsupported_additions": unsupported,
        "feedback_decisions": feedback,
        "blocked_graph_asset_ids": _strings(summary.get("blocked_graph_asset_ids"), limit=16),
        "review_state": str(summary.get("review_state") or ("needs_review_unsupported_addition" if unsupported else ("asset_graph_feedback_applied" if feedback else "asset_graph_available"))),
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def _summarize_asset(asset: dict[str, Any]) -> dict[str, Any]:
    asset_type = str(asset.get("asset_type") or "asset")
    label = str(asset.get("label") or asset.get("title") or "")
    evidence = _evidence_text(asset)
    profile = asset.get("asset_fact_profile") if isinstance(asset.get("asset_fact_profile"), dict) else {}
    if not profile:
        profile = build_asset_fact_profile(
            asset_type=asset_type,
            label=label,
            evidence_text=evidence,
            source_text=str(asset.get("descriptive_signature") or asset.get("signature") or ""),
        )
    facts = asset.get("facts") if isinstance(asset.get("facts"), dict) else profile.get("facts")
    continuity = _strings(asset.get("continuity_locks"), limit=8) or _strings(profile.get("continuity_locks"), limit=8)
    negative = _strings(asset.get("negative_locks"), limit=8) or _strings(profile.get("negative_locks"), limit=8)
    return {
        "graph_asset_id": str(asset.get("graph_asset_id") or asset.get("asset_id") or ""),
        "asset_id": str(asset.get("asset_id") or asset.get("graph_asset_id") or ""),
        "asset_type": asset_type,
        "character_subtype": str(asset.get("character_subtype") or profile.get("character_subtype") or ""),
        "label": label,
        "role": str(asset.get("role") or asset.get("asset_type") or "asset"),
        "status": str(asset.get("status") or asset.get("review_state") or "candidate"),
        "confidence": _confidence(asset.get("confidence")),
        "shot_refs": _strings(asset.get("shot_refs"), limit=8),
        "evidence_text": evidence,
        "facts": facts if isinstance(facts, dict) else {},
        "fact_evidence": _strings(asset.get("fact_evidence") or profile.get("fact_evidence"), limit=6),
        "missing_fact_fields": _strings(asset.get("missing_fact_fields") or profile.get("missing_fact_fields"), limit=8),
        "continuity_locks": continuity,
        "negative_locks": negative,
    }


def _summarize_unsupported(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in _list(value)[:16]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "shot_id": str(item.get("shot_id") or ""),
                "addition": str(item.get("addition") or "")[:120],
                "source_span_id": str(item.get("source_span_id") or ""),
                "evidence_text": str(item.get("evidence_text") or "")[:240],
            }
        )
    return result


def _summarize_feedback_decisions(value: Any) -> list[dict[str, Any]]:
    overlay = _normalize_feedback_overlay(value) if isinstance(value, dict) else build_asset_graph_feedback_overlay(value if isinstance(value, list) else None)
    return [item for item in _list(overlay.get("decisions")) if isinstance(item, dict)][:32]


def _normalize_feedback_overlay(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return build_asset_graph_feedback_overlay(None)
    if value.get("artifact_type") != "agentflow_asset_graph_feedback_overlay":
        if isinstance(value.get("decisions"), list):
            decisions = [_feedback_decision(item) for item in _list(value.get("decisions"))]
            return {
                "artifact_type": "agentflow_asset_graph_feedback_overlay",
                "schema_version": "0.1.0",
                "decisions": [item for item in decisions if item][:32],
                "review_state": str(value.get("review_state") or "feedback_overlay_available"),
                "feedback_is_memory": False,
                "writes_long_term_memory": False,
                "writes_company_kb": False,
            }
        return build_asset_graph_feedback_overlay(value)
    decisions = [_feedback_decision(item) for item in _list(value.get("decisions"))]
    return {
        "artifact_type": "agentflow_asset_graph_feedback_overlay",
        "schema_version": "0.1.0",
        "decisions": [item for item in decisions if item][:32],
        "review_state": str(value.get("review_state") or "feedback_overlay_available"),
        "feedback_is_memory": False,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def _feedback_decision(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    graph_asset_id = str(item.get("graph_asset_id") or item.get("asset_id") or "").strip()
    decision = str(item.get("decision") or "").strip()
    if not graph_asset_id or decision not in {"confirm", "lock", "revise", "reject"}:
        return {}
    return {
        "graph_asset_id": graph_asset_id[:140],
        "decision": decision,
        "label": str(item.get("label") or "")[:80],
        "note": str(item.get("note") or item.get("reason") or "")[:240],
        "continuity_locks": _strings(item.get("continuity_locks") or item.get("lock_updates"), limit=8),
        "negative_locks": _strings(item.get("negative_locks") or item.get("avoid_updates"), limit=8),
    }


def _evidence_text(asset: dict[str, Any]) -> str:
    if asset.get("evidence_text"):
        return str(asset.get("evidence_text") or "")[:240]
    spans = _list(asset.get("evidence_spans"))
    texts = [str(span.get("text") or "").strip() for span in spans if isinstance(span, dict)]
    return " ".join(text for text in texts if text)[:240]


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {key: getattr(value, key) for key in dir(value) if not key.startswith("_") and not callable(getattr(value, key, None))}


def _empty_summary() -> dict[str, Any]:
    return {
        "artifact_type": "agentflow_asset_graph_context",
        "schema_version": "0.1.0",
        "source_artifact_type": "",
        "asset_count": 0,
        "selected_asset_count": 0,
        "graph_asset_ids": [],
        "locked_assets": [],
        "unsupported_additions": [],
        "feedback_decisions": [],
        "blocked_graph_asset_ids": [],
        "review_state": "not_available",
        "writes_long_term_memory": False,
        "writes_company_kb": False,
    }


def _strings(value: Any, *, limit: int) -> list[str]:
    result: list[str] = []
    for item in _list(value):
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text[:160])
        if len(result) >= limit:
            break
    return result


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return round(max(0.0, min(float(value), 1.0)), 3)
    return 0.0


def _int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = (
    "asset_graph_from_context_bundle",
    "asset_graph_feedback_overlay_from_context_bundle",
    "asset_graph_feedback_overlay_from_context_subgraph",
    "asset_graph_from_context_subgraph",
    "apply_asset_graph_feedback_overlay",
    "build_asset_graph_feedback_overlay",
    "format_asset_graph_prompt_lines",
    "summarize_asset_graph_for_plan",
)
