from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from agentflow.harness.json_io import exclusive_file_lock, write_json
from apps.api.runtime_auth import RuntimeAuthStore
from apps.api.runtime_errors import safe_exception_detail
from apps.api.runtime_production_runs import resolve_project_studio_binding
from apps.api.runtime_store import RuntimeStore, read_json, reject_unsafe_payload, safe_id
from apps.api.runtime_studio_state_sanitizer import sanitize_studio_state


class StudioStateRequest(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict)
    expected_version: str = Field(default="", max_length=120)


def register_runtime_studio_state_routes(app: FastAPI, store: RuntimeStore, auth: RuntimeAuthStore) -> None:
    @app.get("/projects/{project_id}/studio-state")
    def get_studio_state(project_id: str, request: Request) -> dict[str, Any]:
        store.ensure_project_manifest(project_id)
        production_binding = _project_production_binding(store, auth, request, project_id)
        path = _state_path(store, project_id)
        if not path.exists():
            return {
                "project_id": project_id,
                "source": "empty",
                "state": {"production": production_binding} if production_binding else None,
                "state_version": "",
                "saved_at": "",
            }
        try:
            payload = read_json(path)
            reject_unsafe_payload(payload)
            raw_state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
            state = sanitize_studio_state(raw_state, project_id=project_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(
                status_code=409,
                detail=safe_exception_detail(exc, "studio_state_recovery_required"),
            ) from exc
        state = {**state, "production": production_binding}
        return {
            "project_id": project_id,
            "source": "runtime",
            "state": state,
            "state_version": _payload_version(payload),
            "saved_at": str(payload.get("saved_at", "")),
        }

    @app.put("/projects/{project_id}/studio-state")
    def put_studio_state(project_id: str, body: StudioStateRequest, request: Request) -> dict[str, Any]:
        store.ensure_project_manifest(project_id)
        try:
            state = sanitize_studio_state(body.state, project_id=project_id)
            state["production"] = _project_production_binding(store, auth, request, project_id)
            reject_unsafe_payload(state)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=safe_exception_detail(exc, "invalid_studio_state"),
            ) from exc

        path = _state_path(store, project_id)
        expected_version = str(body.expected_version or "").strip()
        strict_cas = "creator_authoring" in body.state or bool(expected_version)
        lock_path = path.with_suffix(".lock")
        try:
            with exclusive_file_lock(lock_path):
                current_version = _current_state_version(path, project_id=project_id)
                if strict_cas and expected_version != current_version:
                    raise HTTPException(status_code=409, detail="studio state version conflict")

                saved_at = datetime.now(timezone.utc).isoformat()
                state_version = _state_version(saved_at)
                payload = {
                    "artifact_type": "afs_studio_state",
                    "schema_version": "0.2.0",
                    "project_id": project_id,
                    "saved_at": saved_at,
                    "state_version": state_version,
                    "state": state,
                    "does_not_store_secrets": True,
                    "does_not_store_private_asset_bytes": True,
                }
                write_json(path, payload)
        except HTTPException:
            raise
        except (ValueError, OSError) as exc:
            raise HTTPException(
                status_code=409,
                detail=safe_exception_detail(exc, "studio_state_recovery_required"),
            ) from exc
        return {
            "project_id": project_id,
            "source": "runtime",
            "saved": True,
            "state": state,
            "state_version": state_version,
            "saved_at": saved_at,
        }


def _state_path(store: RuntimeStore, project_id: str):
    return store.projects_dir / safe_id(project_id) / "studio_state.json"


def _project_production_binding(
    store: RuntimeStore,
    auth: RuntimeAuthStore,
    request: Request,
    project_id: str,
) -> dict[str, Any]:
    manifest = store.ensure_project_manifest(project_id)
    if manifest.get("project_type") == "studio_creator_authoring":
        return {}
    owner_user_id = None
    if auth.enabled():
        user = auth.require_user(request)
        owner_user_id = str(user.get("user_id") or "")
        if not owner_user_id or not auth.user_can_access_project(owner_user_id, project_id):
            raise HTTPException(status_code=403, detail="project access denied")
    return resolve_project_studio_binding(store, project_id, owner_user_id=owner_user_id)


def _current_state_version(path, *, project_id: str) -> str:
    if not path.exists():
        return ""
    payload = read_json(path)
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    sanitize_studio_state(state, project_id=project_id)
    version = _payload_version(payload)
    if not version:
        raise ValueError("studio state has no recoverable version")
    return version


def _payload_version(payload: dict[str, Any]) -> str:
    return str(payload.get("state_version") or payload.get("saved_at") or "")


def _state_version(saved_at: str) -> str:
    return f"studio_state:{safe_id(saved_at)}"


__all__ = ("register_runtime_studio_state_routes", "sanitize_studio_state")
