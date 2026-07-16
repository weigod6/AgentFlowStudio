from __future__ import annotations

import json
from typing import Any

from apps.api.runtime_studio_state_assets import sanitize_assets
from apps.api.runtime_studio_state_creator_authoring import sanitize_creator_authoring
from apps.api.runtime_studio_state_episode_workspace import sanitize_episode_workspace
from apps.api.runtime_studio_state_params import SAFE_NODE_PARAM_KEYS, sanitize_node_params
from apps.api.runtime_studio_state_preview import LOCAL_PATH_PATTERN, safe_node_preview_url, safe_preview_url
from apps.api.runtime_store import safe_id


FORBIDDEN_STUDIO_KEYS = {
    "api_key",
    "secret",
    "token",
    "cookie",
    "authorization",
    "provider_config",
    "signed_url",
    "provider_raw",
    "provider_response",
    "raw_response",
    "media_bytes",
    "trace",
    "knowledge_weights",
    "hidden_memory",
}
ALLOWED_PUBLIC_SAFETY_KEYS = {
    "no_credentialed_url",
    "no_local_path",
    "no_media_bytes",
    "no_provider_raw",
    "no_secrets",
    "trace_summary",
}
PRUNED_RUNTIME_PARAM_KEYS = {
    "lastContextBundle",
    "lastKeyframeSourceEvidenceTrace",
    "temporaryLockOverrides",
    "feedbackOverlayDecisions",
}


def sanitize_studio_state(value: dict[str, Any], *, project_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("studio state must be an object")
    _reject_forbidden_known_surfaces(value)
    sanitized = {
        "meta": _meta(value.get("meta")),
        "viewport": _viewport(value.get("viewport")),
        "nodes": _nodes(value.get("nodes"), project_id=project_id),
        "edges": _edges(value.get("edges")),
        "order": _order(value.get("order"), value.get("nodes")),
        "assets": sanitize_assets(
            value.get("assets"),
            project_id=project_id,
            text=_text,
            preview_url=safe_preview_url,
        ),
        "episode_workspace": sanitize_episode_workspace(
            value.get("episode_workspace"),
            text=_text,
            number=_number,
            reject_forbidden=_reject_forbidden,
        ),
        "creator_authoring": sanitize_creator_authoring(
            value.get("creator_authoring"),
            text=_text,
            reject_forbidden=_reject_forbidden,
        ),
        "production": _production(value.get("production")),
    }
    _reject_forbidden(sanitized)
    return sanitized


def _meta(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    return {
        "projectName": _text(data.get("projectName"), "未命名项目", 80),
        "canvasName": _text(data.get("canvasName"), "画布 1", 80),
        "seq": _number(data.get("seq"), 1),
        "updated_at": _text(data.get("updated_at"), "", 80),
    }


def _viewport(value: Any) -> dict[str, float]:
    data = value if isinstance(value, dict) else {}
    return {
        "x": _number(data.get("x"), 0),
        "y": _number(data.get("y"), 0),
        "scale": max(0.18, min(2.6, _number(data.get("scale"), 1))),
    }


def _nodes(value: Any, *, project_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    source = value if isinstance(value, dict) else {}
    for raw_id, node in source.items():
        if not isinstance(node, dict):
            continue
        node_id = safe_id(str(raw_id))
        node_type = _text(node.get("type"), "text", 40)
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        safe_params = _node_params(params, project_id=project_id)
        preview_url = safe_node_preview_url(node.get("previewUrl"), node_type=node_type, project_id=project_id)
        safe_node = {
            "id": node_id,
            "type": node_type,
            "title": _text(node.get("title"), "未命名节点", 120),
            "x": _number(node.get("x"), 0),
            "y": _number(node.get("y"), 0),
            "w": _number(node.get("w"), 280),
            "h": _number(node.get("h"), 280),
            "prompt": _text(node.get("prompt"), "", 4000),
            "content": _text(node.get("content"), "", 8000),
            "params": _jsonable(safe_params),
            "status": _text(node.get("status"), "idle", 40),
            "result": _text(node.get("result"), "", 4000),
            "collapsed": bool(node.get("collapsed")),
        }
        if preview_url:
            safe_node["previewUrl"] = preview_url
        result[node_id] = safe_node
    return result


def _edges(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    source = value if isinstance(value, dict) else {}
    for raw_id, edge in source.items():
        if not isinstance(edge, dict):
            continue
        edge_id = safe_id(str(raw_id))
        relation = _text(edge.get("relation_type") or edge.get("relationType"), "generation", 40)
        if relation not in {"generation", "director", "reference"}:
            relation = "generation"
        result[edge_id] = {
            "id": edge_id,
            "from": safe_id(str(edge.get("from", ""))),
            "to": safe_id(str(edge.get("to", ""))),
            "relation_type": relation,
        }
    return result


def _node_params(value: dict[str, Any], *, project_id: str | None = None) -> dict[str, Any]:
    return sanitize_node_params(
        value,
        project_id=project_id,
        text=_text,
        number=_number,
        preview_url=safe_preview_url,
    )


def _order(value: Any, nodes: Any) -> list[str]:
    if isinstance(value, list):
        return [safe_id(str(item)) for item in value]
    if isinstance(nodes, dict):
        return [safe_id(str(item)) for item in nodes]
    return []


def _production(value: Any) -> dict[str, Any]:
    # Production authority is reconstructed from the authenticated project ledger
    # by the route. A Studio snapshot never persists client-supplied authority.
    return {}


def _reject_forbidden(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    if LOCAL_PATH_PATTERN.search(serialized):
        raise ValueError("studio state contains local path or runtime artifact path")
    _reject_forbidden_recursive(value)


def _reject_forbidden_recursive(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_forbidden_studio_key(str(key)):
                raise ValueError(f"studio state contains forbidden field: {key}")
            _reject_forbidden_recursive(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_forbidden_recursive(item)
        return
    if isinstance(value, str):
        lowered = value.lower()
        for key in FORBIDDEN_STUDIO_KEYS:
            if key in lowered:
                raise ValueError(f"studio state contains forbidden field: {key}")


def _is_forbidden_studio_key(key: str) -> bool:
    lowered = key.lower()
    normalized = "".join(ch for ch in lowered if ch.isalnum())
    if lowered in ALLOWED_PUBLIC_SAFETY_KEYS or normalized in {
        "".join(ch for ch in item if ch.isalnum()) for item in ALLOWED_PUBLIC_SAFETY_KEYS
    }:
        return False
    for forbidden in FORBIDDEN_STUDIO_KEYS:
        forbidden_norm = "".join(ch for ch in forbidden.lower() if ch.isalnum())
        if lowered == forbidden or normalized == forbidden_norm:
            return True
        if forbidden in lowered or forbidden_norm in normalized:
            return True
    return False


def _reject_forbidden_known_surfaces(value: dict[str, Any]) -> None:
    nodes = value.get("nodes")
    if isinstance(nodes, dict):
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            safe_preview_url(node.get("previewUrl"))
            node_shallow = {key: item for key, item in node.items() if key not in {"params", "previewUrl"}}
            _reject_forbidden(node_shallow)
            _reject_node_params(node.get("params"))
    assets = value.get("assets")
    if isinstance(assets, list):
        for item in assets:
            if isinstance(item, dict):
                _reject_forbidden(item)


def _reject_node_params(params: Any) -> None:
    if not isinstance(params, dict):
        return
    for key, item in params.items():
        if key in PRUNED_RUNTIME_PARAM_KEYS:
            continue
        if _is_forbidden_studio_key(str(key)):
            raise ValueError(f"studio state contains forbidden field: {key}")
        if key in {"keyframeConstraints", "keyframeLocalEditDraft"}:
            continue
        if key in SAFE_NODE_PARAM_KEYS:
            _reject_forbidden(item)


def _text(value: Any, fallback: str, max_length: int) -> str:
    text = str(value if value is not None else fallback)
    if LOCAL_PATH_PATTERN.search(text):
        raise ValueError("studio state contains local path or runtime artifact path")
    return text[:max_length]


def _number(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _jsonable(value: Any) -> Any:
    dumped = json.dumps(value, ensure_ascii=False)
    _reject_forbidden(value)
    return json.loads(dumped)


__all__ = ("sanitize_studio_state",)
