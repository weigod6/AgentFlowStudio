from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException

from apps.api.runtime_asset_profile_plan import attach_asset_profiles, build_asset_profile_plan
from apps.api.runtime_asset_graph import attach_graph_asset_ids_to_refs, build_asset_graph
from apps.api.runtime_asset_extraction import principal_asset_refs_with_diagnostics
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_models import ShotAssetPlanRequest
from apps.api.runtime_store import RuntimeStore, reject_unsafe_payload
from apps.api.runtime_storyboard_local import (
    local_storyboard_shots,
    normalize_asset_ref,
    structured_shot,
)


ASSET_PLAN_NON_CLAIMS = [
    "not human acceptance",
    "not fixed asset memory",
    "not generated media",
    "not provider smoke",
]

GENERIC_CHARACTER_LABELS = {"主角", "角色", "人物"}
GENERIC_SCENE_LABELS = {"主要场景", "场景"}


def register_runtime_shot_asset_plan_routes(app: FastAPI, store: RuntimeStore) -> None:
    @app.post("/projects/{project_id}/shot-asset-plans")
    def shot_asset_plan(project_id: str, request: ShotAssetPlanRequest) -> dict[str, Any]:
        try:
            store.ensure_project_manifest(project_id)
            return build_shot_asset_plan(project_id, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=safe_error_detail("invalid_shot_asset_plan")) from exc


def build_shot_asset_plan(project_id: str, request: ShotAssetPlanRequest) -> dict[str, Any]:
    text = _source_text(request)
    shot = request.shot if isinstance(request.shot, dict) else {}
    inferred_shot = _structured_from_request(shot, text)
    authoritative = _authoritative_shot(shot)
    if authoritative:
        refs = _authoritative_refs(shot.get("asset_refs"))
        dropped_refs = list(shot.get("dropped_asset_ref_diagnostics") or [])
    else:
        refs = _normalized_refs(shot.get("asset_refs"), text)
        refs.extend(_normalized_refs(inferred_shot.get("asset_refs"), text))
        refs.extend(_normalized_refs(request.existing_assets, text))
        refs = _apply_global_context(refs, request.script_text or text, text)
        refs = _remove_generic_when_specific(refs)
        refs = _dedupe_refs(refs)
        refs = [_with_evidence(ref, text) for ref in refs]
        refs, dropped_refs = principal_asset_refs_with_diagnostics(refs)
    graph_shot = _graph_shot(shot, inferred_shot, refs, text, dropped_refs)
    asset_graph = build_asset_graph([graph_shot], source_text=request.script_text or text, graph_source="shot_asset_plan")
    refs = attach_graph_asset_ids_to_refs(refs, asset_graph)
    asset_profile_plan = build_asset_profile_plan(refs, text)
    refs = attach_asset_profiles(refs, text)
    safe_manifest = {
        "artifact_type": "agentflow_shot_asset_plan_safe_manifest",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "node_id": request.node_id,
        "status": "verified_asset_plan" if authoritative else "local_asset_plan",
        "asset_refs_authoritative": authoritative,
        "provider_calls_started": False,
        "raw_provider_response_stored": False,
        "generated_media_bytes_stored": False,
        "asset_nodes_created": False,
        "asset_ref_count": len(refs),
        "asset_profile_count": len(asset_profile_plan),
        "asset_graph_asset_count": int(asset_graph.get("asset_count") or 0),
        "unsupported_addition_count": len(asset_graph.get("unsupported_additions") or []),
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": ASSET_PLAN_NON_CLAIMS,
    }
    payload = {
        "project_id": project_id,
        "node_id": request.node_id,
        "asset_refs": refs,
        "asset_profile_plan": asset_profile_plan,
        "asset_graph": asset_graph,
        "asset_nodes_created": False,
        "safe_manifest": safe_manifest,
        "writes_long_term_memory": False,
        "writes_company_kb": False,
        "non_claims": ASSET_PLAN_NON_CLAIMS,
    }
    reject_unsafe_payload(safe_manifest)
    reject_unsafe_payload(asset_graph)
    reject_unsafe_payload(payload)
    return payload


def _graph_shot(
    shot: dict[str, Any],
    inferred_shot: dict[str, Any],
    refs: list[dict[str, Any]],
    text: str,
    dropped_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        **inferred_shot,
        **{key: value for key, value in shot.items() if key in {"shot_id", "index", "description", "source_text", "source_span", "unsupported_additions"}},
        "asset_refs": refs,
        "dropped_asset_ref_diagnostics": list(dropped_refs or []),
        "source_text": str(shot.get("source_text") or inferred_shot.get("source_text") or text),
    }


def _structured_from_request(shot: dict[str, Any], text: str) -> dict[str, Any]:
    if isinstance(shot.get("asset_refs"), list) and shot.get("description"):
        return shot
    index = _safe_int(shot.get("index")) or _safe_int(_field(text, "镜号")) or 1
    description = str(shot.get("description") or _field(text, "画面描述") or text).strip()
    return structured_shot(description, index)


def _authoritative_shot(shot: dict[str, Any]) -> bool:
    return bool(
        shot.get("asset_refs_authoritative") is True
        or str(shot.get("asset_ref_authority") or "") == "runtime_provider_verified_v2"
    )


def _authoritative_refs(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        asset_type = str(item.get("asset_type") or "").strip()
        label = str(item.get("label") or item.get("display_name") or "").strip()[:80]
        entity_id = str(item.get("entity_id") or "").strip()
        if asset_type not in {"character", "scene", "prop"} or not label:
            continue
        key = entity_id or f"{asset_type}\x1f{label}"
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                **item,
                "label": label,
                "display_name": str(item.get("display_name") or label)[:80],
                "asset_type": asset_type,
                "entity_id": entity_id,
                "status": str(item.get("status") or "verified_candidate"),
                "source": str(item.get("source") or "provider_verified_v2"),
                "modality_gate_status": "accepted",
                "verification_status": "verified",
            }
        )
    return result


def _apply_global_context(refs: list[dict[str, Any]], script_text: str, shot_text: str) -> list[dict[str, Any]]:
    combined = "\n".join(part for part in [script_text, shot_text] if part)
    if not combined:
        return refs
    global_refs: list[dict[str, Any]] = []
    for shot in local_storyboard_shots(combined):
        global_refs.extend(_normalized_refs(shot.get("asset_refs"), combined))
    if not any(ref.get("asset_type") == "character" for ref in refs):
        refs.extend(ref for ref in global_refs if ref.get("asset_type") == "character")
    if not any(ref.get("asset_type") == "scene" for ref in refs):
        refs.extend(ref for ref in global_refs if ref.get("asset_type") == "scene")
    if not any(ref.get("asset_type") == "prop" for ref in refs):
        refs.extend(ref for ref in global_refs if ref.get("asset_type") == "prop")
    return refs


def _normalized_refs(items: Any, context: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for index, item in enumerate(items if isinstance(items, list) else []):
        ref = normalize_asset_ref(item, index, context)
        if ref:
            refs.append(ref)
    return refs


def _remove_generic_when_specific(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    has_named_character = any(
        ref.get("asset_type") == "character" and ref.get("label") not in GENERIC_CHARACTER_LABELS
        for ref in refs
    )
    has_named_scene = any(
        ref.get("asset_type") == "scene" and ref.get("label") not in GENERIC_SCENE_LABELS
        for ref in refs
    )
    cleaned: list[dict[str, Any]] = []
    for ref in refs:
        if has_named_character and ref.get("asset_type") == "character" and ref.get("label") in GENERIC_CHARACTER_LABELS:
            continue
        if has_named_scene and ref.get("asset_type") == "scene" and ref.get("label") in GENERIC_SCENE_LABELS:
            continue
        cleaned.append(ref)
    return cleaned


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        asset_type = str(ref.get("asset_type") or "")
        label = str(ref.get("label") or "").strip()
        if asset_type not in {"character", "scene", "prop"} or not label:
            continue
        key = (asset_type, label)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "label": label,
                "asset_id": str(ref.get("asset_id") or f"candidate:{asset_type}:{_slug(label)}"),
                "asset_type": asset_type,
                "status": str(ref.get("status") or "candidate"),
                "source": str(ref.get("source") or "local_asset_plan"),
                "scope": str(ref.get("scope") or "shot_tree"),
                "confidence": ref.get("confidence") if isinstance(ref.get("confidence"), (int, float)) else 0.72,
            }
        )
    return result[:12]


def _with_evidence(ref: dict[str, Any], text: str) -> dict[str, Any]:
    evidence = _evidence_for_label(text, str(ref.get("label") or ""))
    return {**ref, "evidence_text": evidence or str(text or "").strip()[:240]}


def _evidence_for_label(text: str, label: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*", clean) if part.strip()]
    for sentence in sentences:
        if label and label in sentence:
            return sentence[:240]
    for sentence in sentences:
        if re.search(r"山巅|山脊|石台|战场|云海|城市|街道|雨夜|屋顶|天台|金箍棒|钢爪", sentence):
            return sentence[:240]
    return sentences[0][:240] if sentences else clean[:240]


def _source_text(request: ShotAssetPlanRequest) -> str:
    shot = request.shot if isinstance(request.shot, dict) else {}
    parts = [
        str(shot.get("description") or ""),
        str(shot.get("source_text") or ""),
        str(request.script_text or ""),
    ]
    return "\n".join(part for part in parts if part.strip()).strip()


def _field(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*[：:]\s*(.+)", str(text or ""))
    return match.group(1).strip() if match else ""


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).lower()[:32] or "asset"


__all__ = (
    "ASSET_PLAN_NON_CLAIMS",
    "build_shot_asset_plan",
    "register_runtime_shot_asset_plan_routes",
)
