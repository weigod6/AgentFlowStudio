from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentflow.harness.json_io import exclusive_file_lock
from apps.api.runtime_episode_domain_contract import (
    SAFE_ID,
    SHA256,
    ProductionProjectAggregate,
)


STORE_SCHEMA_VERSION = "afs_episode_production_aggregate_store.v0.1"
MUTATION_LEDGER_SCHEMA_VERSION = "afs_episode_aggregate_mutation_ledger.v0.1"
OUTBOX_SCHEMA_VERSION = "afs_episode_aggregate_outbox.v0.1"
_SAFE_ID_RE = re.compile(SAFE_ID, re.ASCII)
_SHA256_RE = re.compile(SHA256, re.ASCII)


class EpisodeDomainStoreError(RuntimeError):
    """Base class for fail-closed aggregate store errors."""


class AggregateNotFoundError(EpisodeDomainStoreError):
    pass


class AggregateIntegrityError(EpisodeDomainStoreError):
    pass


class AggregateScopeError(EpisodeDomainStoreError):
    pass


class AggregateVersionConflictError(EpisodeDomainStoreError):
    pass


class AggregateIdempotencyConflictError(EpisodeDomainStoreError):
    pass


class AggregateRetiredError(EpisodeDomainStoreError):
    pass


@dataclass(frozen=True)
class AggregateSaveResult:
    aggregate: ProductionProjectAggregate
    replayed: bool
    aggregate_sha256: str
    ledger_event_id: str | None = None


class EpisodeDomainAggregateStore:
    """Atomic file snapshot store for one production aggregate per project.

    Paths are derived from hashes rather than sanitized identifiers. The original
    tenant and project ids remain inside the checksummed envelope and are checked
    on every read, so path aliases cannot silently cross a project boundary.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def snapshot_path(self, *, org_id: str, project_id: str) -> Path:
        safe_org = _validated_id(org_id, field="org_id")
        safe_project = _validated_id(project_id, field="project_id")
        return (
            self.root
            / "episode_aggregates"
            / _id_digest(safe_org)
            / f"{_id_digest(safe_project)}.json"
        )

    def load(self, *, org_id: str, project_id: str) -> ProductionProjectAggregate:
        path = self.snapshot_path(org_id=org_id, project_id=project_id)
        with exclusive_file_lock(_lock_path(path)):
            envelope = self._read_envelope(
                path,
                expected_org_id=org_id,
                expected_project_id=project_id,
            )
        return _aggregate_from_envelope(envelope)

    def save(
        self,
        aggregate: ProductionProjectAggregate,
        *,
        expected_aggregate_version: int,
        idempotency_key: str,
        payload_digest: str,
    ) -> AggregateSaveResult:
        if not isinstance(expected_aggregate_version, int) or isinstance(
            expected_aggregate_version,
            bool,
        ) or expected_aggregate_version < 0:
            raise AggregateVersionConflictError(
                "expected_aggregate_version must be a non-negative integer"
            )
        key = _validated_id(idempotency_key, field="idempotency_key")
        digest = _validated_sha256(payload_digest, field="payload_digest")
        incoming = ProductionProjectAggregate.model_validate(aggregate)
        org_id = _validated_id(incoming.scope.org_id, field="org_id")
        project_id = _validated_id(incoming.scope.project_id, field="project_id")
        path = self.snapshot_path(org_id=org_id, project_id=project_id)

        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(_lock_path(path)):
            existing: dict[str, Any] | None = None
            if path.exists():
                existing = self._read_envelope(
                    path,
                    expected_org_id=org_id,
                    expected_project_id=project_id,
                )
                receipt = existing["idempotency_records"].get(key)
                if receipt is not None:
                    if receipt["payload_digest"] != digest:
                        raise AggregateIdempotencyConflictError(
                            "idempotency key was already used with a different payload"
                        )
                    replayed = _aggregate_from_payload(
                        receipt["result_aggregate"],
                        expected_org_id=org_id,
                        expected_project_id=project_id,
                    )
                    replayed_sha = _aggregate_digest(receipt["result_aggregate"])
                    if replayed_sha != receipt["result_sha256"]:
                        raise AggregateIntegrityError(
                            "idempotency replay aggregate checksum does not match"
                        )
                    return AggregateSaveResult(
                        aggregate=replayed,
                        replayed=True,
                        aggregate_sha256=replayed_sha,
                    )

            current_version = int(existing["aggregate_version"]) if existing else 0
            if expected_aggregate_version != current_version:
                raise AggregateVersionConflictError(
                    f"aggregate version conflict: expected {expected_aggregate_version}, "
                    f"current {current_version}"
                )
            if incoming.aggregate_version != current_version + 1:
                raise AggregateVersionConflictError(
                    "incoming aggregate_version must advance the stored version by exactly one"
                )
            if existing is not None and _project_is_retired(_aggregate_from_envelope(existing)):
                raise AggregateRetiredError("retired projects reject new aggregate mutations")

            aggregate_payload = incoming.model_dump(mode="json")
            aggregate_sha = _aggregate_digest(aggregate_payload)
            records = dict(existing["idempotency_records"]) if existing else {}
            records[key] = {
                "payload_digest": digest,
                "result_sha256": aggregate_sha,
                "result_aggregate": aggregate_payload,
            }
            ledger_events = list(existing.get("ledger_events") or []) if existing else []
            outbox_records = list(existing.get("outbox_records") or []) if existing else []
            previous_digest = (
                str(ledger_events[-1]["integrity_digest"])
                if ledger_events
                else None
            )
            ledger_event = _ledger_event(
                scope={"org_id": org_id, "project_id": project_id},
                sequence=len(ledger_events) + 1,
                aggregate_version=incoming.aggregate_version,
                idempotency_key=key,
                payload_digest=digest,
                result_sha256=aggregate_sha,
                previous_event_digest=previous_digest,
            )
            ledger_events.append(ledger_event)
            outbox_records.append(_outbox_record(ledger_event))
            body: dict[str, Any] = {
                "schema_version": STORE_SCHEMA_VERSION,
                "scope": {"org_id": org_id, "project_id": project_id},
                "aggregate_version": incoming.aggregate_version,
                "aggregate_sha256": aggregate_sha,
                "aggregate": aggregate_payload,
                "idempotency_records": records,
                "ledger_events": ledger_events,
                "outbox_records": outbox_records,
                "ledger_projection": {
                    "schema_version": "afs_episode_aggregate_projection.v0.1",
                    "aggregate_version": incoming.aggregate_version,
                    "aggregate_sha256": aggregate_sha,
                    "event_count": len(ledger_events),
                    "last_event_digest": ledger_event["integrity_digest"],
                    "ledger_digest": _json_digest(ledger_events),
                },
            }
            envelope = {**body, "envelope_sha256": _json_digest(body)}
            _atomic_write_json(path, envelope)
            return AggregateSaveResult(
                aggregate=incoming,
                replayed=False,
                aggregate_sha256=aggregate_sha,
                ledger_event_id=ledger_event["event_id"],
            )

    def _read_envelope(
        self,
        path: Path,
        *,
        expected_org_id: str,
        expected_project_id: str,
    ) -> dict[str, Any]:
        if not path.exists():
            raise AggregateNotFoundError("aggregate snapshot does not exist")
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AggregateIntegrityError("aggregate snapshot is unreadable") from exc
        if not isinstance(envelope, dict):
            raise AggregateIntegrityError("aggregate snapshot envelope must be an object")
        if envelope.get("schema_version") != STORE_SCHEMA_VERSION:
            raise AggregateIntegrityError("aggregate snapshot schema version is unsupported")
        checksum = envelope.get("envelope_sha256")
        if not isinstance(checksum, str):
            raise AggregateIntegrityError("aggregate snapshot envelope checksum is missing")
        body = {key: value for key, value in envelope.items() if key != "envelope_sha256"}
        if _json_digest(body) != checksum:
            raise AggregateIntegrityError("aggregate snapshot envelope checksum does not match")
        scope = envelope.get("scope")
        if scope != {"org_id": expected_org_id, "project_id": expected_project_id}:
            raise AggregateScopeError("aggregate snapshot scope does not match requested project")
        payload = envelope.get("aggregate")
        if not isinstance(payload, dict):
            raise AggregateIntegrityError("aggregate snapshot payload is missing")
        if _aggregate_digest(payload) != envelope.get("aggregate_sha256"):
            raise AggregateIntegrityError("aggregate payload checksum does not match")
        aggregate = _aggregate_from_payload(
            payload,
            expected_org_id=expected_org_id,
            expected_project_id=expected_project_id,
        )
        if aggregate.aggregate_version != envelope.get("aggregate_version"):
            raise AggregateIntegrityError("aggregate version does not match its envelope")
        records = envelope.get("idempotency_records")
        if not isinstance(records, dict):
            raise AggregateIntegrityError("idempotency records are missing")
        for key, receipt in records.items():
            _validated_id(key, field="stored idempotency_key", integrity=True)
            if not isinstance(receipt, dict):
                raise AggregateIntegrityError("idempotency receipt must be an object")
            _validated_sha256(
                receipt.get("payload_digest"),
                field="stored payload_digest",
                integrity=True,
            )
            _validated_sha256(
                receipt.get("result_sha256"),
                field="stored result_sha256",
                integrity=True,
            )
            result_payload = receipt.get("result_aggregate")
            if not isinstance(result_payload, dict):
                raise AggregateIntegrityError("idempotency receipt aggregate is missing")
            if _aggregate_digest(result_payload) != receipt["result_sha256"]:
                raise AggregateIntegrityError("idempotency receipt checksum does not match")
            result = _aggregate_from_payload(
                result_payload,
                expected_org_id=expected_org_id,
                expected_project_id=expected_project_id,
            )
            if result.aggregate_version > aggregate.aggregate_version:
                raise AggregateIntegrityError(
                    "idempotency receipt cannot be newer than the current aggregate"
                )
        _validate_ledger_and_outbox(
            envelope,
            expected_org_id=expected_org_id,
            expected_project_id=expected_project_id,
            aggregate_version=aggregate.aggregate_version,
            aggregate_sha256=str(envelope.get("aggregate_sha256") or ""),
        )
        return envelope


def _aggregate_from_envelope(envelope: dict[str, Any]) -> ProductionProjectAggregate:
    return ProductionProjectAggregate.model_validate(envelope["aggregate"])


def _aggregate_from_payload(
    payload: dict[str, Any],
    *,
    expected_org_id: str,
    expected_project_id: str,
) -> ProductionProjectAggregate:
    try:
        aggregate = ProductionProjectAggregate.model_validate(payload)
    except ValidationError as exc:
        raise AggregateIntegrityError("aggregate payload violates the frozen contract") from exc
    if (
        aggregate.scope.org_id != expected_org_id
        or aggregate.scope.project_id != expected_project_id
    ):
        raise AggregateScopeError("aggregate payload scope does not match its storage scope")
    return aggregate


def _project_is_retired(aggregate: ProductionProjectAggregate) -> bool:
    latest = max(aggregate.projects, key=lambda project: project.revision)
    return latest.lifecycle_state == "retired"


def _validated_id(value: Any, *, field: str, integrity: bool = False) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        error = AggregateIntegrityError if integrity else AggregateScopeError
        raise error(f"{field} must be an exact safe identifier; aliases are rejected")
    return value


def _validated_sha256(value: Any, *, field: str, integrity: bool = False) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        error = AggregateIntegrityError if integrity else AggregateIdempotencyConflictError
        raise error(f"{field} must be a lowercase SHA-256 digest")
    return value


def _id_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aggregate_digest(payload: dict[str, Any]) -> str:
    return _json_digest(payload)


def _json_digest(payload: Any) -> str:
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _ledger_event(
    *,
    scope: dict[str, str],
    sequence: int,
    aggregate_version: int,
    idempotency_key: str,
    payload_digest: str,
    result_sha256: str,
    previous_event_digest: str | None,
) -> dict[str, Any]:
    base = {
        "schema_version": MUTATION_LEDGER_SCHEMA_VERSION,
        "scope": scope,
        "event_type": "aggregate_saved",
        "sequence": sequence,
        "aggregate_version": aggregate_version,
        "idempotency_key": idempotency_key,
        "payload_digest": payload_digest,
        "result_sha256": result_sha256,
        "previous_event_digest": previous_event_digest,
    }
    event_id = _json_digest(base)
    return {
        **base,
        "event_id": event_id,
        "integrity_digest": _json_digest({**base, "event_id": event_id}),
    }


def _outbox_record(event: dict[str, Any]) -> dict[str, Any]:
    base = {
        "schema_version": OUTBOX_SCHEMA_VERSION,
        "scope": event["scope"],
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "aggregate_version": event["aggregate_version"],
        "payload_digest": event["payload_digest"],
        "result_sha256": event["result_sha256"],
        "status": "pending",
    }
    return {**base, "outbox_id": _json_digest(base)}


def _validate_ledger_and_outbox(
    envelope: dict[str, Any],
    *,
    expected_org_id: str,
    expected_project_id: str,
    aggregate_version: int,
    aggregate_sha256: str,
) -> None:
    ledger_events = envelope.get("ledger_events")
    outbox_records = envelope.get("outbox_records")
    projection = envelope.get("ledger_projection")
    if ledger_events is None and outbox_records is None and projection is None:
        return
    if not isinstance(ledger_events, list):
        raise AggregateIntegrityError("aggregate mutation ledger is missing")
    if not isinstance(outbox_records, list):
        raise AggregateIntegrityError("aggregate outbox records are missing")
    if not isinstance(projection, dict):
        raise AggregateIntegrityError("aggregate ledger projection is missing")
    if len(outbox_records) != len(ledger_events):
        raise AggregateIntegrityError("aggregate outbox does not match mutation ledger")
    expected_scope = {"org_id": expected_org_id, "project_id": expected_project_id}
    previous_digest: str | None = None
    event_digests: list[str] = []
    for index, event in enumerate(ledger_events, start=1):
        if not isinstance(event, dict):
            raise AggregateIntegrityError("aggregate ledger event must be an object")
        if event.get("schema_version") != MUTATION_LEDGER_SCHEMA_VERSION:
            raise AggregateIntegrityError("aggregate ledger event schema version is unsupported")
        if event.get("scope") != expected_scope:
            raise AggregateScopeError("aggregate ledger event scope does not match requested project")
        if event.get("sequence") != index:
            raise AggregateIntegrityError("aggregate ledger sequence is not contiguous")
        if event.get("previous_event_digest") != previous_digest:
            raise AggregateIntegrityError("aggregate ledger previous digest does not match")
        _validated_id(event.get("idempotency_key"), field="ledger idempotency_key", integrity=True)
        _validated_sha256(event.get("payload_digest"), field="ledger payload_digest", integrity=True)
        _validated_sha256(event.get("result_sha256"), field="ledger result_sha256", integrity=True)
        body = {
            key: value
            for key, value in event.items()
            if key not in {"event_id", "integrity_digest"}
        }
        event_id = event.get("event_id")
        integrity_digest = event.get("integrity_digest")
        if _json_digest(body) != event_id:
            raise AggregateIntegrityError("aggregate ledger event id does not match")
        if _json_digest({**body, "event_id": event_id}) != integrity_digest:
            raise AggregateIntegrityError("aggregate ledger event checksum does not match")
        if int(event.get("aggregate_version") or 0) > aggregate_version:
            raise AggregateIntegrityError("aggregate ledger event cannot exceed current aggregate version")
        previous_digest = str(integrity_digest)
        event_digests.append(previous_digest)
    for index, outbox in enumerate(outbox_records):
        if not isinstance(outbox, dict):
            raise AggregateIntegrityError("aggregate outbox record must be an object")
        event = ledger_events[index]
        if outbox.get("schema_version") != OUTBOX_SCHEMA_VERSION:
            raise AggregateIntegrityError("aggregate outbox schema version is unsupported")
        if outbox.get("scope") != expected_scope:
            raise AggregateScopeError("aggregate outbox scope does not match requested project")
        if outbox.get("event_id") != event["event_id"]:
            raise AggregateIntegrityError("aggregate outbox event id does not match mutation ledger")
        if outbox.get("payload_digest") != event["payload_digest"]:
            raise AggregateIntegrityError("aggregate outbox payload digest does not match mutation ledger")
        if outbox.get("result_sha256") != event["result_sha256"]:
            raise AggregateIntegrityError("aggregate outbox result digest does not match mutation ledger")
        base = {key: value for key, value in outbox.items() if key != "outbox_id"}
        if _json_digest(base) != outbox.get("outbox_id"):
            raise AggregateIntegrityError("aggregate outbox checksum does not match")
    if projection.get("schema_version") != "afs_episode_aggregate_projection.v0.1":
        raise AggregateIntegrityError("aggregate ledger projection schema version is unsupported")
    if projection.get("aggregate_version") != aggregate_version:
        raise AggregateIntegrityError("aggregate ledger projection version does not match")
    if projection.get("aggregate_sha256") != aggregate_sha256:
        raise AggregateIntegrityError("aggregate ledger projection aggregate checksum does not match")
    if projection.get("event_count") != len(ledger_events):
        raise AggregateIntegrityError("aggregate ledger projection event count does not match")
    if projection.get("last_event_digest") != (event_digests[-1] if event_digests else None):
        raise AggregateIntegrityError("aggregate ledger projection last event digest does not match")
    if projection.get("ledger_digest") != _json_digest(ledger_events):
        raise AggregateIntegrityError("aggregate ledger projection digest does not match")


def _lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = (
    "AggregateIdempotencyConflictError",
    "AggregateIntegrityError",
    "AggregateNotFoundError",
    "AggregateRetiredError",
    "AggregateSaveResult",
    "AggregateScopeError",
    "AggregateVersionConflictError",
    "EpisodeDomainAggregateStore",
    "EpisodeDomainStoreError",
    "STORE_SCHEMA_VERSION",
)
