from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from apps.api.runtime_episode_domain_contract import EntityVersionRef
from apps.api.runtime_store import safe_id


_STABLE_REF_RE = re.compile(
    r"^(?:project|series|story_bible|arc|episode|scene|shot|reference_asset|reference_set):"
    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$"
)


def sanitize_creator_authoring(
    value: Any,
    *,
    text: Callable[[Any, str, int], str],
    reject_forbidden: Callable[[Any], None],
) -> dict[str, Any]:
    """Persist UI recovery state without accepting canonical content facts."""

    data = value if isinstance(value, dict) else {}
    if not data:
        return {}
    allowed = {
        "mode",
        "schema_version",
        "selected_episode",
        "selected_shot",
        "selected_section",
        "mobile_inspector_open",
        "technical_open",
        "pending_command",
        "pending_failure",
    }
    if set(data) - allowed:
        raise ValueError("creator authoring state contains domain or unknown fields")
    if data.get("schema_version") not in (None, "afs_creator_authoring_ui.v0.1"):
        raise ValueError("creator authoring state schema is invalid")
    mode = text(data.get("mode"), "storyboard", 24)
    if mode not in {"storyboard", "canvas"}:
        raise ValueError("creator authoring mode is invalid")
    pending = _pending_command(data.get("pending_command"), reject_forbidden=reject_forbidden)
    return {
        "schema_version": "afs_creator_authoring_ui.v0.1",
        "mode": mode,
        "selected_episode": _stable_ref(data.get("selected_episode"), allow_empty=True),
        "selected_shot": _stable_ref(data.get("selected_shot"), allow_empty=True),
        "selected_section": _stable_ref(data.get("selected_section"), allow_empty=True),
        "mobile_inspector_open": data.get("mobile_inspector_open") is True,
        "technical_open": data.get("technical_open") is True,
        "pending_command": pending,
        "pending_failure": text(data.get("pending_failure"), "", 500),
    }


def _pending_command(
    value: Any,
    *,
    reject_forbidden: Callable[[Any], None],
) -> dict[str, Any] | None:
    if value in (None, {}):
        return None
    expected_keys = {"schema_version", "idempotency_key", "command", "status"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("creator authoring pending command envelope is invalid")
    if value.get("schema_version") != "afs_creator_pending_command.v0.1":
        raise ValueError("creator authoring pending command schema is invalid")
    key = str(value.get("idempotency_key") or "").strip()
    if not key or safe_id(key) != key:
        raise ValueError("creator authoring idempotency key is invalid")
    status = str(value.get("status") or "")
    if status not in {"pending", "failed"}:
        raise ValueError("creator authoring pending command status is invalid")
    serialized = _creator_command(value.get("command"))
    reject_forbidden(serialized)
    return {
        "schema_version": "afs_creator_pending_command.v0.1",
        "idempotency_key": key,
        "command": serialized,
        "status": status,
    }


def _creator_command(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("creator authoring pending command payload is invalid")
    action = value.get("action")
    required: dict[str, set[str]] = {
        "authoring.create": {
            "action", "expected_aggregate_version", "entity_id", "version_id", "created_at", "entity"
        },
        "authoring.revise": {
            "action", "expected_aggregate_version", "target_ref", "new_version_id", "created_at", "changes"
        },
        "authoring.reorder": {
            "action", "expected_aggregate_version", "ordered_refs", "new_version_ids", "created_at"
        },
        "shot.revise_intent": {
            "action", "expected_aggregate_version", "shot_ref", "new_version_id", "created_at", "changes",
            "preview_digest", "confirmed_direct_refs", "confirmed_transitive_refs", "confirmed_protected_refs",
        },
        "shot.restore": {
            "action", "expected_aggregate_version", "historical_ref", "current_ref", "new_version_id",
            "created_at", "preview_digest", "confirmed_direct_refs", "confirmed_transitive_refs",
            "confirmed_protected_refs",
        },
    }
    expected = required.get(str(action or ""))
    if expected is None or set(value) != expected:
        raise ValueError("creator authoring pending command shape is invalid")
    version = value.get("expected_aggregate_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("creator authoring pending command version is invalid")
    for key in ("entity_id", "version_id", "new_version_id"):
        if key in value and safe_id(str(value[key] or "")) != value[key]:
            raise ValueError("creator authoring pending command identity is invalid")
    for key in ("target_ref", "shot_ref", "historical_ref", "current_ref"):
        if key in value:
            _exact_ref(value[key])
    for key in ("ordered_refs", "confirmed_direct_refs", "confirmed_transitive_refs", "confirmed_protected_refs"):
        if key in value:
            refs = value[key]
            if not isinstance(refs, list):
                raise ValueError("creator authoring pending command reference set is invalid")
            for ref in refs:
                _exact_ref(ref)
    return dict(value)


def _exact_ref(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"entity_type", "entity_id", "version_id"}:
        raise ValueError("creator authoring exact reference is invalid")
    try:
        EntityVersionRef.model_validate(value)
    except ValueError:
        raise ValueError("creator authoring exact reference type is invalid")
    for key in ("entity_id", "version_id"):
        raw = str(value.get(key) or "")
        if not raw or safe_id(raw) != raw:
            raise ValueError("creator authoring exact reference identity is invalid")


def _stable_ref(value: Any, *, allow_empty: bool) -> str:
    raw = str(value or "").strip()
    if not raw and allow_empty:
        return ""
    if _STABLE_REF_RE.fullmatch(raw) is None:
        raise ValueError("creator authoring stable reference is invalid")
    return raw


__all__ = ("sanitize_creator_authoring",)
