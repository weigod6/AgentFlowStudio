from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Iterable, TypeVar

from pydantic import ValidationError

from apps.api.runtime_episode_domain_contract import (
    AgentProposal,
    ContinuityStateVersion,
    EntityVersionRef,
    ProductionProjectAggregate,
    ShotVersion,
    TenantScope,
    is_lifecycle_transition_allowed,
)


class ContinuityServiceError(ValueError):
    """A continuity command cannot be applied to the supplied project facts."""


@dataclass(frozen=True)
class ContinuityChangePlan:
    """Creator-visible impact derived from exact latest facts.

    ``affected_shot_refs`` is the complete predicted pre-change impact set. It
    deliberately remains separate from the successor refs an apply operation
    later records in ``AgentProposal.applied_refs``.
    """

    scope: TenantScope
    expected_aggregate_version: int
    old_continuity_ref: EntityVersionRef
    proposed_continuity: ContinuityStateVersion
    proposal_entity_id: str
    affected_shot_refs: tuple[EntityVersionRef, ...]
    unaffected_shot_refs: tuple[EntityVersionRef, ...]


def plan_change(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    old_continuity_ref: EntityVersionRef,
    new_version_id: str,
    proposal_entity_id: str,
    created_at: str,
    identity_baseline: tuple[str, ...] | None = None,
    temporary_state: tuple[str, ...] | None = None,
    prohibited_changes: tuple[str, ...] | None = None,
) -> ContinuityChangePlan:
    """Plan an append-only continuity successor and derive its full impact."""

    _guard_command(aggregate, scope, expected_aggregate_version)
    old = _require_current_continuity(aggregate, old_continuity_ref)
    if old.lifecycle_state in ("locked", "retired"):
        raise ContinuityServiceError(
            "locked or retired continuity requires an explicit upstream transition"
        )
    if old.lifecycle_state != "candidate" and not is_lifecycle_transition_allowed(
        old.lifecycle_state,
        "candidate",
    ):
        raise ContinuityServiceError(
            "continuity cannot create a candidate successor from its current lifecycle"
        )
    if any(
        item.entity_id == old.entity_id and item.version_id == new_version_id
        for item in aggregate.continuity_states
    ):
        raise ContinuityServiceError(
            "new continuity version id must be unused in this entity history"
        )
    if any(item.entity_id == proposal_entity_id for item in aggregate.agent_proposals):
        raise ContinuityServiceError("proposal entity id already exists")

    affected, unaffected = _derive_impact(aggregate, old_continuity_ref)
    if not affected:
        raise ContinuityServiceError("continuity change has no currently affected shots")

    proposed_identity = (
        old.identity_baseline if identity_baseline is None else identity_baseline
    )
    proposed_temporary = old.temporary_state if temporary_state is None else temporary_state
    proposed_prohibited = (
        old.prohibited_changes if prohibited_changes is None else prohibited_changes
    )
    semantic_payload = {
        "subject_type": old.subject_type,
        "subject_id": old.subject_id,
        "identity_baseline": proposed_identity,
        "temporary_state": proposed_temporary,
        "prohibited_changes": proposed_prohibited,
        # Exact-version selections cannot silently transfer to a successor.
        "approved_asset_selection_refs": (),
    }
    if (
        proposed_identity == old.identity_baseline
        and proposed_temporary == old.temporary_state
        and proposed_prohibited == old.prohibited_changes
        and not old.approved_asset_selection_refs
    ):
        raise ContinuityServiceError("continuity change must alter a semantic field")

    try:
        proposed = ContinuityStateVersion(
            entity_id=old.entity_id,
            version_id=new_version_id,
            revision=old.revision + 1,
            parent_version_id=old.version_id,
            lifecycle_state="candidate",
            review_state="needs_review",
            content_digest=_semantic_digest(semantic_payload),
            scope=scope,
            created_at=created_at,
            source_refs=old.source_refs,
            subject_type=old.subject_type,
            subject_id=old.subject_id,
            identity_baseline=proposed_identity,
            temporary_state=proposed_temporary,
            prohibited_changes=proposed_prohibited,
            approved_asset_selection_refs=(),
        )
        # Validate the deterministic operation ref now, before the plan leaves
        # the service boundary.
        _proposal_ref(proposal_entity_id, 1)
    except ValidationError as exc:
        raise ContinuityServiceError(str(exc)) from exc

    _require_later(created_at, old.created_at, "continuity successor")
    return ContinuityChangePlan(
        scope=scope,
        expected_aggregate_version=expected_aggregate_version,
        old_continuity_ref=old_continuity_ref,
        proposed_continuity=proposed,
        proposal_entity_id=proposal_entity_id,
        affected_shot_refs=affected,
        unaffected_shot_refs=unaffected,
    )


def apply_change(
    aggregate: ProductionProjectAggregate,
    plan: ContinuityChangePlan,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    selected_shot_refs: tuple[EntityVersionRef, ...],
    created_at: str,
) -> ProductionProjectAggregate:
    """Apply a creator-selected subset and record explicit operation membership."""

    _guard_plan(aggregate, plan, scope, expected_aggregate_version)
    _require_later(created_at, plan.proposed_continuity.created_at, "continuity apply")
    selected = _require_selected_subset(aggregate, plan, selected_shot_refs)
    proposal_ref = _proposal_ref(plan.proposal_entity_id, 1)
    proposed_ref = plan.proposed_continuity.as_ref()

    successors = tuple(
        _shot_successor(
            shot,
            all_shots=aggregate.shots,
            old_ref=plan.old_continuity_ref,
            new_ref=proposed_ref,
            source_proposal_ref=proposal_ref,
            created_at=created_at,
        )
        for shot in selected
    )
    proposal = AgentProposal(
        entity_id=plan.proposal_entity_id,
        version_id=proposal_ref.version_id,
        revision=1,
        lifecycle_state="draft",
        review_state="not_requested",
        content_digest="0" * 64,
        scope=scope,
        created_at=created_at,
        target_ref=proposed_ref,
        # Never replace this creator-visible prediction with selected or
        # successor refs. Apply re-derived the complete set above.
        impact_refs=plan.affected_shot_refs,
        applied_refs=tuple(shot.as_ref() for shot in successors),
        action="replace_continuity_ref",
        decision_state="executed",
    )
    proposal = proposal.model_copy(
        update={"content_digest": _proposal_content_digest(proposal)}
    )
    return _validated_aggregate(
        aggregate,
        aggregate_version=aggregate.aggregate_version + 1,
        evaluated_at=created_at,
        continuity_states=(*aggregate.continuity_states, plan.proposed_continuity),
        shots=(*aggregate.shots, *successors),
        agent_proposals=(*aggregate.agent_proposals, proposal),
    )


def reject_change(
    aggregate: ProductionProjectAggregate,
    plan: ContinuityChangePlan,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
) -> ProductionProjectAggregate:
    """Reject a plan without appending or changing any aggregate fact."""

    _guard_plan(aggregate, plan, scope, expected_aggregate_version)
    return aggregate


def undo_change(
    aggregate: ProductionProjectAggregate,
    *,
    scope: TenantScope,
    expected_aggregate_version: int,
    proposal_ref: EntityVersionRef,
    created_at: str,
) -> ProductionProjectAggregate:
    """Atomically restore every exact successor owned by one executed proposal."""

    _guard_command(aggregate, scope, expected_aggregate_version)
    executed = _require_current_proposal(aggregate, proposal_ref)
    if executed.decision_state != "executed":
        raise ContinuityServiceError("only an exact executed proposal can be undone")
    if executed.action != "replace_continuity_ref":
        raise ContinuityServiceError("proposal is not a continuity ref replacement")
    if not executed.applied_refs:
        raise ContinuityServiceError("executed proposal has no durable applied membership")

    target = _require_ref(
        aggregate.continuity_states,
        executed.target_ref,
        "continuity_state",
    )
    if target.parent_version_id is None:
        raise ContinuityServiceError("executed continuity target has no prior exact version")
    prior_ref = EntityVersionRef(
        entity_type="continuity_state",
        entity_id=target.entity_id,
        version_id=target.parent_version_id,
    )
    prior = _require_ref(aggregate.continuity_states, prior_ref, "continuity_state")
    if prior.scope != scope or target.scope != scope:
        raise ContinuityServiceError("continuity history does not share exact command scope")

    # This is the atomic preflight. Every explicitly owned successor must still
    # be the exact latest shot head. A later P2 operation blocks all of P1 undo.
    owned = _require_current_shots(aggregate, executed.applied_refs)
    for shot in owned:
        if shot.source_proposal_ref != executed.as_ref():
            raise ContinuityServiceError(
                "applied shot does not backlink the exact executed proposal"
            )
        if shot.continuity_refs.count(executed.target_ref) != 1:
            raise ContinuityServiceError(
                "applied shot does not contain exactly one proposal continuity ref"
            )

    undo_ref = _proposal_ref(executed.entity_id, executed.revision + 1)
    if any(item.version_id == undo_ref.version_id for item in aggregate.agent_proposals):
        raise ContinuityServiceError("undo proposal version already exists")
    restored = tuple(
        _shot_successor(
            shot,
            all_shots=aggregate.shots,
            old_ref=executed.target_ref,
            new_ref=prior_ref,
            source_proposal_ref=undo_ref,
            created_at=created_at,
        )
        for shot in owned
    )
    undo = executed.model_copy(
        update={
            "version_id": undo_ref.version_id,
            "revision": executed.revision + 1,
            "parent_version_id": executed.version_id,
            "target_ref": prior_ref,
            "impact_refs": executed.applied_refs,
            "applied_refs": tuple(shot.as_ref() for shot in restored),
            "decision_state": "undone",
            "content_digest": "0" * 64,
            "created_at": created_at,
        }
    )
    undo = undo.model_copy(update={"content_digest": _proposal_content_digest(undo)})
    _require_later(created_at, executed.created_at, "proposal undo")
    return _validated_aggregate(
        aggregate,
        aggregate_version=aggregate.aggregate_version + 1,
        evaluated_at=created_at,
        shots=(*aggregate.shots, *restored),
        agent_proposals=(*aggregate.agent_proposals, undo),
    )


def _guard_plan(
    aggregate: ProductionProjectAggregate,
    plan: ContinuityChangePlan,
    scope: TenantScope,
    expected_aggregate_version: int,
) -> None:
    _guard_command(aggregate, scope, expected_aggregate_version)
    if plan.scope != scope:
        raise ContinuityServiceError("continuity plan scope does not match command scope")
    if plan.expected_aggregate_version != expected_aggregate_version:
        raise ContinuityServiceError("continuity plan was derived from another aggregate version")
    try:
        _proposal_ref(plan.proposal_entity_id, 1)
    except ValidationError as exc:
        raise ContinuityServiceError("continuity plan has an invalid proposal identity") from exc
    old = _require_current_continuity(aggregate, plan.old_continuity_ref)
    affected, unaffected = _derive_impact(aggregate, plan.old_continuity_ref)
    if plan.affected_shot_refs != affected or plan.unaffected_shot_refs != unaffected:
        raise ContinuityServiceError("continuity plan impact boundary is changed or incomplete")
    if any(item.entity_id == plan.proposal_entity_id for item in aggregate.agent_proposals):
        raise ContinuityServiceError("proposal entity id already exists")

    proposed = plan.proposed_continuity
    if (
        proposed.identity_baseline == old.identity_baseline
        and proposed.temporary_state == old.temporary_state
        and proposed.prohibited_changes == old.prohibited_changes
        and not old.approved_asset_selection_refs
    ):
        raise ContinuityServiceError("continuity change must alter a semantic field")
    expected_digest = _semantic_digest(
        {
            "subject_type": proposed.subject_type,
            "subject_id": proposed.subject_id,
            "identity_baseline": proposed.identity_baseline,
            "temporary_state": proposed.temporary_state,
            "prohibited_changes": proposed.prohibited_changes,
            "approved_asset_selection_refs": (),
        }
    )
    if (
        proposed.scope != scope
        or proposed.entity_id != old.entity_id
        or proposed.revision != old.revision + 1
        or proposed.parent_version_id != old.version_id
        or proposed.lifecycle_state != "candidate"
        or proposed.review_state != "needs_review"
        or proposed.subject_type != old.subject_type
        or proposed.subject_id != old.subject_id
        or proposed.source_refs != old.source_refs
        or proposed.approved_asset_selection_refs
        or proposed.content_digest != expected_digest
    ):
        raise ContinuityServiceError(
            "continuity plan successor does not match the exact fact history"
        )
    if any(item.as_ref() == proposed.as_ref() for item in aggregate.continuity_states):
        raise ContinuityServiceError("proposed continuity version already exists")
    _require_later(proposed.created_at, old.created_at, "continuity successor")


def _guard_command(
    aggregate: ProductionProjectAggregate,
    scope: TenantScope,
    expected_aggregate_version: int,
) -> None:
    try:
        # Revalidate model_copy/model_construct inputs so corrupted explicit
        # membership fails closed before service logic observes it.
        ProductionProjectAggregate.model_validate(aggregate.model_dump(mode="python"))
    except ValidationError as exc:
        raise ContinuityServiceError("aggregate violates the episode fact contract") from exc
    if scope != aggregate.scope:
        raise ContinuityServiceError(
            "tenant, project, and actor scope must match the aggregate"
        )
    if expected_aggregate_version != aggregate.aggregate_version:
        raise ContinuityServiceError("stale aggregate version")


def _derive_impact(
    aggregate: ProductionProjectAggregate,
    old_ref: EntityVersionRef,
) -> tuple[tuple[EntityVersionRef, ...], tuple[EntityVersionRef, ...]]:
    latest = _latest_by_entity(aggregate.shots)
    if any(shot.continuity_refs.count(old_ref) > 1 for shot in latest):
        raise ContinuityServiceError(
            "latest shot contains duplicate exact continuity references"
        )
    affected = tuple(shot.as_ref() for shot in latest if old_ref in shot.continuity_refs)
    unaffected = tuple(shot.as_ref() for shot in latest if old_ref not in shot.continuity_refs)
    return affected, unaffected


def _require_selected_subset(
    aggregate: ProductionProjectAggregate,
    plan: ContinuityChangePlan,
    selected_refs: tuple[EntityVersionRef, ...],
) -> tuple[ShotVersion, ...]:
    if not selected_refs:
        raise ContinuityServiceError("apply requires at least one selected shot")
    if len(selected_refs) != len(set(selected_refs)):
        raise ContinuityServiceError("selected shot refs must be unique")
    selected_set = set(selected_refs)
    affected_set = set(plan.affected_shot_refs)
    if not selected_set.issubset(affected_set):
        raise ContinuityServiceError("selected shot is outside the derived impact boundary")
    latest = {shot.as_ref(): shot for shot in _latest_by_entity(aggregate.shots)}
    if any(ref not in latest for ref in selected_set):
        raise ContinuityServiceError("selected shot ref is not an exact current shot version")
    # Preserve the service-derived order rather than caller order.
    return tuple(latest[ref] for ref in plan.affected_shot_refs if ref in selected_set)


def _require_current_continuity(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
) -> ContinuityStateVersion:
    item = _require_ref(aggregate.continuity_states, ref, "continuity_state")
    latest = {value.entity_id: value for value in _latest_by_entity(aggregate.continuity_states)}
    if latest[item.entity_id].as_ref() != ref:
        raise ContinuityServiceError("continuity ref is not the exact current version")
    return item


def _require_current_proposal(
    aggregate: ProductionProjectAggregate,
    ref: EntityVersionRef,
) -> AgentProposal:
    item = _require_ref(aggregate.agent_proposals, ref, "agent_proposal")
    latest = {value.entity_id: value for value in _latest_by_entity(aggregate.agent_proposals)}
    if latest[item.entity_id].as_ref() != ref:
        raise ContinuityServiceError("proposal ref is not the exact current version")
    return item


def _require_current_shots(
    aggregate: ProductionProjectAggregate,
    refs: tuple[EntityVersionRef, ...],
) -> tuple[ShotVersion, ...]:
    if len(refs) != len(set(refs)):
        raise ContinuityServiceError("shot refs must be unique")
    latest = {shot.as_ref(): shot for shot in _latest_by_entity(aggregate.shots)}
    result: list[ShotVersion] = []
    for ref in refs:
        if ref.entity_type != "shot" or ref not in latest:
            raise ContinuityServiceError("shot ref is not an exact current shot version")
        result.append(latest[ref])
    return tuple(result)


T = TypeVar("T")


def _require_ref(items: Iterable[T], ref: EntityVersionRef, expected_type: str) -> T:
    if ref.entity_type != expected_type:
        raise ContinuityServiceError(f"reference must target {expected_type}")
    for item in items:
        if getattr(item, "as_ref")() == ref:
            return item
    raise ContinuityServiceError("exact version ref does not resolve inside the aggregate")


def _latest_by_entity(items: Iterable[T]) -> tuple[T, ...]:
    latest: dict[str, T] = {}
    for item in items:
        entity_id = getattr(item, "entity_id")
        previous = latest.get(entity_id)
        if previous is None or getattr(item, "revision") > getattr(previous, "revision"):
            latest[entity_id] = item
    return tuple(latest[key] for key in sorted(latest))


def _proposal_ref(entity_id: str, revision: int) -> EntityVersionRef:
    return EntityVersionRef(
        entity_type="agent_proposal",
        entity_id=entity_id,
        version_id=f"{entity_id}.v{revision}",
    )


def _shot_successor(
    shot: ShotVersion,
    *,
    all_shots: tuple[ShotVersion, ...],
    old_ref: EntityVersionRef,
    new_ref: EntityVersionRef,
    source_proposal_ref: EntityVersionRef,
    created_at: str,
) -> ShotVersion:
    _require_later(created_at, shot.created_at, "shot successor")
    if shot.continuity_refs.count(old_ref) != 1:
        raise ContinuityServiceError(
            "shot must contain exactly one continuity ref being replaced"
        )
    version_id = f"{shot.entity_id}.v{shot.revision + 1}"
    if any(
        item.entity_id == shot.entity_id and item.version_id == version_id
        for item in all_shots
    ):
        raise ContinuityServiceError("canonical shot successor version already exists")
    continuity_refs = tuple(new_ref if ref == old_ref else ref for ref in shot.continuity_refs)
    digest = _semantic_digest(
        {
            # Business content only. Operation ownership is represented by the
            # explicit source_proposal_ref/applied_refs edge, never this digest.
            "scene_ref": shot.scene_ref,
            "sequence": shot.sequence,
            "duration_seconds": shot.duration_seconds,
            "continuity_refs": continuity_refs,
            "source_refs": shot.source_refs,
        }
    )
    return shot.model_copy(
        update={
            "version_id": version_id,
            "revision": shot.revision + 1,
            "parent_version_id": shot.version_id,
            "lifecycle_state": "candidate",
            "review_state": "needs_review",
            "content_digest": digest,
            "created_at": created_at,
            "continuity_refs": continuity_refs,
            "source_proposal_ref": source_proposal_ref,
        }
    )


def _proposal_content_digest(proposal: AgentProposal) -> str:
    return _semantic_digest(
        {
            "target_ref": proposal.target_ref,
            "impact_refs": proposal.impact_refs,
            "applied_refs": proposal.applied_refs,
            "action": proposal.action,
            "decision_state": proposal.decision_state,
        }
    )


def _validated_aggregate(
    aggregate: ProductionProjectAggregate,
    **updates: object,
) -> ProductionProjectAggregate:
    try:
        return ProductionProjectAggregate(**{**aggregate.model_dump(), **updates})
    except ValidationError as exc:
        raise ContinuityServiceError(str(exc)) from exc


def _semantic_digest(value: object) -> str:
    def default(item: object) -> object:
        if hasattr(item, "model_dump"):
            return getattr(item, "model_dump")(mode="json")
        if isinstance(item, tuple):
            return list(item)
        raise TypeError(f"cannot encode {type(item)!r}")

    encoded = json.dumps(
        value,
        default=default,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_later(value: str, previous: str, label: str) -> None:
    try:
        current_time = datetime.fromisoformat(value)
        previous_time = datetime.fromisoformat(previous)
    except ValueError as exc:
        raise ContinuityServiceError(f"{label} timestamp must be ISO-8601") from exc
    if current_time.tzinfo is None or previous_time.tzinfo is None:
        raise ContinuityServiceError(f"{label} timestamp must include a timezone")
    if current_time <= previous_time:
        raise ContinuityServiceError(f"{label} timestamp must be later than its parent")


__all__ = (
    "ContinuityChangePlan",
    "ContinuityServiceError",
    "apply_change",
    "plan_change",
    "reject_change",
    "undo_change",
)
