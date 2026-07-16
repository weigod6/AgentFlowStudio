from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter, ValidationError

from apps.api.runtime_episode_command_routes import EpisodeCommandRequest
from apps.api.runtime_store import safe_id


_EPISODE_COMMAND_ADAPTER = TypeAdapter(EpisodeCommandRequest)


def sanitize_episode_workspace(
    value: Any,
    *,
    text: Callable[[Any, str, int], str],
    number: Callable[[Any, float], float],
    reject_forbidden: Callable[[Any], None],
) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    if not data:
        return {}
    mode = text(data.get("mode"), "storyboard", 24)
    if mode not in {"storyboard", "review", "delivery"}:
        mode = "storyboard"
    pending_key = _safe_identity(
        data.get("pending_idempotency_key"), allow_empty=True
    )
    pending_command = _pending_episode_command(
        data.get("pending_command"), reject_forbidden=reject_forbidden
    )
    if pending_command is not None:
        if pending_key and pending_key != pending_command["idempotency_key"]:
            raise ValueError("episode workspace pending command identity mismatch")
        pending_key = pending_command["idempotency_key"]
    elif pending_key:
        raise ValueError("episode workspace pending identity requires an exact command payload")
    return {
        "schema_version": "afs_episode_workspace_ui.v0.1",
        "episode_ref": _exact_ref(data.get("episode_ref"), "episode", text=text),
        "active_shot_ref": _exact_ref(data.get("active_shot_ref"), "shot", text=text),
        "mode": mode,
        "focused_control": text(data.get("focused_control"), "", 120),
        "inspector_section": text(data.get("inspector_section"), "overview", 80),
        "scroll_top": max(0, number(data.get("scroll_top"), 0)),
        "pending_idempotency_key": pending_key,
        "pending_command": pending_command,
    }


def _pending_episode_command(
    value: Any, *, reject_forbidden: Callable[[Any], None]
) -> dict[str, Any] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, dict) or set(value) != {"idempotency_key", "payload"}:
        raise ValueError("episode workspace pending command envelope is invalid")
    key = _safe_identity(value.get("idempotency_key"))
    try:
        payload = _EPISODE_COMMAND_ADAPTER.validate_python(value.get("payload"))
    except ValidationError as exc:
        raise ValueError("episode workspace pending command payload is invalid") from exc
    serialized = payload.model_dump(mode="json")
    reject_forbidden(serialized)
    return {"idempotency_key": key, "payload": serialized}


def _exact_ref(
    value: Any, expected_type: str, *, text: Callable[[Any, str, int], str]
) -> dict[str, str] | None:
    data = value if isinstance(value, dict) else {}
    if not data:
        return None
    entity_type = text(data.get("entity_type"), "", 40)
    if entity_type != expected_type:
        raise ValueError("episode workspace exact reference has an invalid entity type")
    return {
        "entity_type": entity_type,
        "entity_id": _safe_identity(data.get("entity_id")),
        "version_id": _safe_identity(data.get("version_id")),
    }


def _safe_identity(value: Any, *, allow_empty: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw and allow_empty:
        return ""
    if not raw or safe_id(raw) != raw:
        raise ValueError("episode workspace identity is invalid")
    return raw


__all__ = ("sanitize_episode_workspace",)
