from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agentflow.harness.json_io import write_json
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_store import RuntimeStore, read_json, safe_id, storage_path_token


SCHEMA_VERSION = "afs_runtime_submit_idempotency.v0.1"
VOLATILE_REQUEST_FIELDS = {"generated_at"}


@dataclass(frozen=True)
class SubmitIdempotencyReservation:
    state: Literal["reserved", "replay", "conflict", "pending"]
    project_id: str
    action: str
    stable_request_id: str
    fingerprint: str
    ledger_dir: Path
    ledger: dict[str, Any]
    response: dict[str, Any] | None = None

    @property
    def is_reserved(self) -> bool:
        return self.state == "reserved"


def begin_submit_idempotency(
    store: RuntimeStore,
    *,
    project_id: str,
    action: str,
    request: Any,
    client_request_id: str = "",
    request_id: str = "",
) -> SubmitIdempotencyReservation:
    fingerprint = submit_request_fingerprint(request)
    stable_request_id = stable_submit_request_id(client_request_id, fingerprint)
    ledger_dir = _ledger_dir(store, project_id, action, stable_request_id)
    ledger_path = ledger_dir / "ledger.json"
    response_path = ledger_dir / "response.json"
    ledger_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Directory creation is the atomic reservation primitive for the local Runtime model.
        ledger_dir.mkdir()
    except FileExistsError:
        try:
            ledger = read_json(ledger_path)
        except (FileNotFoundError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            ledger = {}
        existing_fingerprint = str(ledger.get("fingerprint") or "")
        if existing_fingerprint and existing_fingerprint != fingerprint:
            return SubmitIdempotencyReservation(
                state="conflict",
                project_id=project_id,
                action=action,
                stable_request_id=stable_request_id,
                fingerprint=fingerprint,
                ledger_dir=ledger_dir,
                ledger=ledger,
            )
        if response_path.exists():
            return SubmitIdempotencyReservation(
                state="replay",
                project_id=project_id,
                action=action,
                stable_request_id=stable_request_id,
                fingerprint=fingerprint,
                ledger_dir=ledger_dir,
                ledger=ledger,
                response=read_json(response_path),
            )
        return SubmitIdempotencyReservation(
            state="pending",
            project_id=project_id,
            action=action,
            stable_request_id=stable_request_id,
            fingerprint=fingerprint,
            ledger_dir=ledger_dir,
            ledger=ledger,
        )

    ledger = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "action": action,
        "stable_request_id": stable_request_id,
        "fingerprint": fingerprint,
        "status": "reserved",
        "job_id": "",
        "provider_calls_started": False,
        "request_id": request_id,
        "client_request_id": client_request_id,
        "created_at": _now(),
        "updated_at": _now(),
    }
    write_json(ledger_path, ledger)
    return SubmitIdempotencyReservation(
        state="reserved",
        project_id=project_id,
        action=action,
        stable_request_id=stable_request_id,
        fingerprint=fingerprint,
        ledger_dir=ledger_dir,
        ledger=ledger,
    )


def complete_submit_idempotency(
    reservation: SubmitIdempotencyReservation,
    *,
    job_id: str,
    response: dict[str, Any],
    provider_calls_started: bool,
) -> None:
    if not reservation.is_reserved:
        return
    response_path = reservation.ledger_dir / "response.json"
    write_json(response_path, response)
    ledger = {
        **reservation.ledger,
        "status": "completed",
        "job_id": job_id,
        "provider_calls_started": bool(provider_calls_started),
        "response_sha256": _json_digest(response),
        "updated_at": _now(),
    }
    write_json(reservation.ledger_dir / "ledger.json", ledger)


def abort_submit_idempotency(reservation: SubmitIdempotencyReservation | None) -> None:
    if reservation is None or not reservation.is_reserved:
        return
    shutil.rmtree(reservation.ledger_dir, ignore_errors=True)


def submit_idempotency_error_detail(
    reservation: SubmitIdempotencyReservation,
    *,
    request_id: str = "",
    client_request_id: str = "",
    node_id: str = "",
) -> dict[str, Any]:
    conflict = reservation.state == "conflict"
    existing_job_id = str(reservation.ledger.get("job_id") or "")
    detail = safe_error_detail(
        "idempotency_conflict" if conflict else "idempotency_request_in_progress",
        detail_code="idempotency_conflict" if conflict else "idempotency_request_in_progress",
        message=(
            "Submit request id was reused with a different payload."
            if conflict
            else "Submit request is still reserved and has no completed response yet."
        ),
        user_action=(
            "Use a new client request id for changed generation parameters."
            if conflict
            else "Retry the same request after the first submit returns."
        ),
        request_id=request_id,
        client_request_id=client_request_id,
        project_id=reservation.project_id,
        node_id=node_id,
        action=reservation.action,
        stage="idempotency",
        status="conflict" if conflict else "blocked",
        retryable=not conflict,
        details={
            "provider_calls_started": False,
            "stable_request_id": reservation.stable_request_id,
            "existing_job_id": existing_job_id,
            "existing_fingerprint": str(reservation.ledger.get("fingerprint") or ""),
            "incoming_fingerprint": reservation.fingerprint,
            "idempotency_scope": f"{reservation.project_id}/{reservation.action}",
        },
    )
    detail["provider_calls_started"] = False
    return detail


def submit_request_fingerprint(request: Any) -> str:
    if hasattr(request, "model_dump"):
        payload = request.model_dump(mode="json", by_alias=True)
    elif isinstance(request, dict):
        payload = dict(request)
    else:
        payload = dict(getattr(request, "__dict__", {}))
    return _json_digest(_stable_payload(payload))


def stable_submit_request_id(client_request_id: str, fingerprint: str) -> str:
    cleaned = safe_id(str(client_request_id or "").strip())
    if cleaned and cleaned != "item":
        return cleaned[:120]
    return f"fingerprint-{fingerprint[:32]}"


def _stable_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_payload(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in VOLATILE_REQUEST_FIELDS
        }
    if isinstance(value, list):
        return [_stable_payload(item) for item in value]
    return value


def _ledger_dir(store: RuntimeStore, project_id: str, action: str, stable_request_id: str) -> Path:
    return (
        store.root
        / "submit_idempotency"
        / storage_path_token(project_id, max_len=28)
        / storage_path_token(action, max_len=28)
        / storage_path_token(stable_request_id, max_len=24)
    )


def _json_digest(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = (
    "SubmitIdempotencyReservation",
    "abort_submit_idempotency",
    "begin_submit_idempotency",
    "complete_submit_idempotency",
    "stable_submit_request_id",
    "submit_idempotency_error_detail",
    "submit_request_fingerprint",
)
