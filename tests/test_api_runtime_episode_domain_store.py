from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from apps.api.runtime_episode_domain_contract import (
    ProductionProjectAggregate,
    ProjectVersion,
    TenantScope,
)
from apps.api.runtime_episode_domain_store import (
    AggregateIdempotencyConflictError,
    AggregateIntegrityError,
    AggregateNotFoundError,
    AggregateRetiredError,
    AggregateScopeError,
    AggregateVersionConflictError,
    EpisodeDomainAggregateStore,
)


ORG_ID = "studio-team"
PROJECT_ID = "rainlight"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aggregate(
    aggregate_version: int,
    *,
    org_id: str = ORG_ID,
    project_id: str = PROJECT_ID,
    retired: bool = False,
    title: str = "雨灯失窃案",
) -> ProductionProjectAggregate:
    scope = TenantScope(org_id=org_id, project_id=project_id, actor_id="creator-1")
    projects = [
        ProjectVersion(
            entity_id=project_id,
            version_id=f"{project_id}-v1",
            revision=1,
            lifecycle_state="draft",
            content_digest=_digest(f"{project_id}:v1"),
            scope=scope,
            created_at="2026-07-15T08:00:00+00:00",
            title=title,
        )
    ]
    if retired:
        projects.append(
            ProjectVersion(
                entity_id=project_id,
                version_id=f"{project_id}-v2",
                revision=2,
                parent_version_id=f"{project_id}-v1",
                lifecycle_state="retired",
                content_digest=_digest(f"{project_id}:retired"),
                scope=scope,
                created_at="2026-07-15T09:00:00+00:00",
                title=title,
            )
        )
    return ProductionProjectAggregate(
        aggregate_version=aggregate_version,
        evaluated_at="2026-07-15T10:00:00+00:00",
        scope=scope,
        projects=tuple(projects),
    )


def test_atomic_roundtrip_and_exact_restart_recovery(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    aggregate = _aggregate(1)

    result = store.save(
        aggregate,
        expected_aggregate_version=0,
        idempotency_key="create-v1",
        payload_digest=_digest("create-v1"),
    )

    assert result.aggregate == aggregate
    assert result.replayed is False
    assert result.ledger_event_id
    assert result.aggregate_sha256 == _digest(
        json.dumps(
            aggregate.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    assert EpisodeDomainAggregateStore(tmp_path).load(
        org_id=ORG_ID,
        project_id=PROJECT_ID,
    ) == aggregate
    envelope = json.loads(store.snapshot_path(org_id=ORG_ID, project_id=PROJECT_ID).read_text(encoding="utf-8"))
    assert envelope["ledger_projection"]["event_count"] == 1
    assert envelope["ledger_projection"]["aggregate_version"] == 1
    assert envelope["ledger_projection"]["aggregate_sha256"] == envelope["aggregate_sha256"]
    assert envelope["outbox_records"][0]["event_id"] == envelope["ledger_events"][0]["event_id"]
    assert not list(store.snapshot_path(org_id=ORG_ID, project_id=PROJECT_ID).parent.glob("*.tmp"))


def test_snapshot_checksum_tamper_fails_closed(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        _aggregate(1),
        expected_aggregate_version=0,
        idempotency_key="create-v1",
        payload_digest=_digest("create-v1"),
    )
    path = store.snapshot_path(org_id=ORG_ID, project_id=PROJECT_ID)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["aggregate"]["projects"][0]["title"] = "被篡改"
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AggregateIntegrityError, match="checksum"):
        EpisodeDomainAggregateStore(tmp_path).load(org_id=ORG_ID, project_id=PROJECT_ID)


def test_compare_and_swap_requires_exact_current_and_next_versions(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        _aggregate(1),
        expected_aggregate_version=0,
        idempotency_key="create-v1",
        payload_digest=_digest("create-v1"),
    )

    with pytest.raises(AggregateVersionConflictError, match="expected 0, current 1"):
        store.save(
            _aggregate(2),
            expected_aggregate_version=0,
            idempotency_key="update-stale",
            payload_digest=_digest("update-stale"),
        )
    with pytest.raises(AggregateVersionConflictError, match="exactly one"):
        store.save(
            _aggregate(3),
            expected_aggregate_version=1,
            idempotency_key="update-skipped",
            payload_digest=_digest("update-skipped"),
        )

    saved = store.save(
        _aggregate(2),
        expected_aggregate_version=1,
        idempotency_key="update-v2",
        payload_digest=_digest("update-v2"),
    )
    assert saved.aggregate.aggregate_version == 2


def test_idempotent_replay_survives_restart_and_changed_payload_conflicts(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    original = store.save(
        _aggregate(1),
        expected_aggregate_version=0,
        idempotency_key="stable-create",
        payload_digest=_digest("same-payload"),
    )

    restarted = EpisodeDomainAggregateStore(tmp_path)
    replay = restarted.save(
        _aggregate(2, title="ignored replay input"),
        expected_aggregate_version=999,
        idempotency_key="stable-create",
        payload_digest=_digest("same-payload"),
    )

    assert replay.replayed is True
    assert replay.aggregate == original.aggregate
    assert replay.aggregate_sha256 == original.aggregate_sha256
    envelope = json.loads(store.snapshot_path(org_id=ORG_ID, project_id=PROJECT_ID).read_text(encoding="utf-8"))
    assert envelope["ledger_projection"]["event_count"] == 1
    with pytest.raises(AggregateIdempotencyConflictError, match="different payload"):
        restarted.save(
            _aggregate(2),
            expected_aggregate_version=1,
            idempotency_key="stable-create",
            payload_digest=_digest("changed-payload"),
        )


def test_parallel_same_version_has_one_winner(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        _aggregate(1),
        expected_aggregate_version=0,
        idempotency_key="create-v1",
        payload_digest=_digest("create-v1"),
    )
    barrier = Barrier(2)

    def write_candidate(candidate: str) -> str:
        barrier.wait(timeout=5)
        try:
            store.save(
                _aggregate(2, title=f"候选 {candidate}"),
                expected_aggregate_version=1,
                idempotency_key=f"candidate-{candidate}",
                payload_digest=_digest(candidate),
            )
        except AggregateVersionConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write_candidate, ("a", "b")))

    assert sorted(outcomes) == ["conflict", "saved"]
    assert store.load(org_id=ORG_ID, project_id=PROJECT_ID).aggregate_version == 2


def test_scope_is_project_isolated_and_unsafe_aliases_are_rejected(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    first = _aggregate(1, project_id="rainlight-a")
    second = _aggregate(1, project_id="rainlight-b")
    store.save(
        first,
        expected_aggregate_version=0,
        idempotency_key="create-a",
        payload_digest=_digest("create-a"),
    )
    store.save(
        second,
        expected_aggregate_version=0,
        idempotency_key="create-b",
        payload_digest=_digest("create-b"),
    )

    assert store.load(org_id=ORG_ID, project_id="rainlight-a") == first
    assert store.load(org_id=ORG_ID, project_id="rainlight-b") == second
    assert store.snapshot_path(org_id=ORG_ID, project_id="rainlight-a") != store.snapshot_path(
        org_id=ORG_ID,
        project_id="rainlight-b",
    )
    with pytest.raises(AggregateScopeError, match="aliases are rejected"):
        store.load(org_id=ORG_ID, project_id="rainlight-a/../rainlight-b")
    with pytest.raises(AggregateNotFoundError):
        store.load(org_id="another-team", project_id="rainlight-a")


def test_retired_project_allows_retirement_then_rejects_new_writes_but_replays(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        _aggregate(1),
        expected_aggregate_version=0,
        idempotency_key="create-v1",
        payload_digest=_digest("create-v1"),
    )
    retired = store.save(
        _aggregate(2, retired=True),
        expected_aggregate_version=1,
        idempotency_key="retire-v2",
        payload_digest=_digest("retire-v2"),
    )

    with pytest.raises(AggregateRetiredError, match="reject new"):
        store.save(
            _aggregate(3, retired=True),
            expected_aggregate_version=2,
            idempotency_key="post-retirement-write",
            payload_digest=_digest("post-retirement-write"),
        )
    replay = EpisodeDomainAggregateStore(tmp_path).save(
        _aggregate(99, retired=True),
        expected_aggregate_version=0,
        idempotency_key="retire-v2",
        payload_digest=_digest("retire-v2"),
    )
    assert replay.replayed is True
    assert replay.aggregate == retired.aggregate


def test_missing_snapshot_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(AggregateNotFoundError):
        EpisodeDomainAggregateStore(tmp_path).load(org_id=ORG_ID, project_id=PROJECT_ID)


def test_mutation_ledger_appends_and_rebuild_projection_matches_snapshot(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        _aggregate(1),
        expected_aggregate_version=0,
        idempotency_key="create-v1",
        payload_digest=_digest("create-v1"),
    )
    store.save(
        _aggregate(2),
        expected_aggregate_version=1,
        idempotency_key="update-v2",
        payload_digest=_digest("update-v2"),
    )

    path = store.snapshot_path(org_id=ORG_ID, project_id=PROJECT_ID)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert [item["sequence"] for item in envelope["ledger_events"]] == [1, 2]
    assert envelope["ledger_events"][1]["previous_event_digest"] == envelope["ledger_events"][0]["integrity_digest"]
    assert len(envelope["outbox_records"]) == 2
    assert envelope["ledger_projection"]["event_count"] == 2
    assert envelope["ledger_projection"]["aggregate_version"] == 2
    assert EpisodeDomainAggregateStore(tmp_path).load(org_id=ORG_ID, project_id=PROJECT_ID).aggregate_version == 2


def test_mutation_ledger_tamper_fails_closed(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        _aggregate(1),
        expected_aggregate_version=0,
        idempotency_key="create-v1",
        payload_digest=_digest("create-v1"),
    )
    path = store.snapshot_path(org_id=ORG_ID, project_id=PROJECT_ID)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["ledger_events"][0]["aggregate_version"] = 99
    body = {key: value for key, value in envelope.items() if key != "envelope_sha256"}
    envelope["envelope_sha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AggregateIntegrityError, match="event id"):
        EpisodeDomainAggregateStore(tmp_path).load(org_id=ORG_ID, project_id=PROJECT_ID)


def test_atomic_outbox_drift_fails_closed(tmp_path: Path) -> None:
    store = EpisodeDomainAggregateStore(tmp_path)
    store.save(
        _aggregate(1),
        expected_aggregate_version=0,
        idempotency_key="create-v1",
        payload_digest=_digest("create-v1"),
    )
    path = store.snapshot_path(org_id=ORG_ID, project_id=PROJECT_ID)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["outbox_records"] = []
    body = {key: value for key, value in envelope.items() if key != "envelope_sha256"}
    envelope["envelope_sha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AggregateIntegrityError, match="outbox"):
        EpisodeDomainAggregateStore(tmp_path).load(org_id=ORG_ID, project_id=PROJECT_ID)
