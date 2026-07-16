from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from apps.api.runtime_episode_domain_contract import (
    EpisodeVersion,
    ProjectDataPolicy,
    ProjectVersion,
    ProductionProjectAggregate,
    SceneVersion,
    SeriesVersion,
    ShotVersion,
    TenantScope,
)
from apps.api.runtime_episode_domain_store import (
    AggregateNotFoundError,
    AggregateSaveResult,
    EpisodeDomainAggregateStore,
)
from apps.api.runtime_store import RuntimeStore


STUDIO_EPISODE_PROJECT_TYPE = "studio_episode_production"
STUDIO_CREATOR_AUTHORING_PROJECT_TYPE = "studio_creator_authoring"
BOOTSTRAP_EPISODE_ID = "episode-001"
BOOTSTRAP_EPISODE_VERSION_ID = "episode-001-v1"
BOOTSTRAP_SCENE_ID = "scene-001"
BOOTSTRAP_SCENE_VERSION_ID = "scene-001-v1"
BOOTSTRAP_CREATED_AT = "2026-07-15T00:00:00+00:00"


@dataclass(frozen=True)
class EpisodeBootstrapResult:
    aggregate: ProductionProjectAggregate
    created: bool
    replayed: bool

    @property
    def workspace_entry(self) -> dict[str, str]:
        return {
            "episode_id": BOOTSTRAP_EPISODE_ID,
            "episode_version_id": BOOTSTRAP_EPISODE_VERSION_ID,
            "href": (
                f"/studio/episode-workspace/?project={self.aggregate.scope.project_id}"
                f"&episode={BOOTSTRAP_EPISODE_ID}&version={BOOTSTRAP_EPISODE_VERSION_ID}"
            ),
        }


@dataclass(frozen=True)
class CreatorAuthoringBootstrapResult:
    aggregate: ProductionProjectAggregate
    created: bool
    replayed: bool

    @property
    def workspace_entry(self) -> dict[str, str]:
        return {
            "href": (
                f"/studio/episode-workspace/?project={self.aggregate.scope.project_id}"
            ),
        }


def should_bootstrap_episode_project(project_type: str) -> bool:
    return str(project_type or "").strip() == STUDIO_EPISODE_PROJECT_TYPE


def should_bootstrap_creator_authoring_project(project_type: str) -> bool:
    return str(project_type or "").strip() == STUDIO_CREATOR_AUTHORING_PROJECT_TYPE


def ensure_empty_creator_bootstrap(
    store: RuntimeStore,
    *,
    scope: TenantScope,
    title: str,
    idempotency_key: str = "project-create-creator-bootstrap-v1",
) -> CreatorAuthoringBootstrapResult:
    aggregate_store = EpisodeDomainAggregateStore(store.root)
    try:
        aggregate = aggregate_store.load(org_id=scope.org_id, project_id=scope.project_id)
    except AggregateNotFoundError:
        aggregate = build_empty_creator_aggregate(scope=scope, title=title)
        result = _save_bootstrap(aggregate_store, aggregate, idempotency_key=idempotency_key)
        return CreatorAuthoringBootstrapResult(
            aggregate=result.aggregate,
            created=not result.replayed,
            replayed=result.replayed,
        )
    return CreatorAuthoringBootstrapResult(aggregate=aggregate, created=False, replayed=True)


def build_empty_creator_aggregate(
    *,
    scope: TenantScope,
    title: str,
) -> ProductionProjectAggregate:
    safe_title = _safe_title(title)
    project = ProjectVersion(
        **_fact(scope, "project", scope.project_id),
        title=safe_title,
        summary="",
        creative_intent="",
        ip_profile="",
        data_policy=ProjectDataPolicy(
            visibility="private",
            training_use="denied_by_default",
            product_improvement_use="denied_by_default",
            export_enabled=True,
            deletion_enabled=True,
        ),
    )
    return ProductionProjectAggregate(
        aggregate_version=1,
        evaluated_at=BOOTSTRAP_CREATED_AT,
        scope=scope,
        projects=(project,),
    )


def ensure_minimal_episode_bootstrap(
    store: RuntimeStore,
    *,
    scope: TenantScope,
    title: str,
    idempotency_key: str = "project-create-episode-bootstrap-v1",
) -> EpisodeBootstrapResult:
    aggregate_store = EpisodeDomainAggregateStore(store.root)
    try:
        aggregate = aggregate_store.load(org_id=scope.org_id, project_id=scope.project_id)
    except AggregateNotFoundError:
        aggregate = build_minimal_episode_aggregate(scope=scope, title=title)
        result = _save_bootstrap(aggregate_store, aggregate, idempotency_key=idempotency_key)
        return EpisodeBootstrapResult(
            aggregate=result.aggregate,
            created=not result.replayed,
            replayed=result.replayed,
        )
    return EpisodeBootstrapResult(aggregate=aggregate, created=False, replayed=True)


def build_minimal_episode_aggregate(
    *,
    scope: TenantScope,
    title: str,
) -> ProductionProjectAggregate:
    safe_title = _safe_title(title)
    project = ProjectVersion(
        **_fact(scope, "project", scope.project_id),
        title=safe_title,
        data_policy=ProjectDataPolicy(
            visibility="private",
            training_use="denied_by_default",
            product_improvement_use="denied_by_default",
            export_enabled=True,
            deletion_enabled=True,
        ),
    )
    series = SeriesVersion(
        **_fact(scope, "series", "series-001"),
        project_ref=project.as_ref(),
        title=safe_title,
    )
    episode = EpisodeVersion(
        **_fact(scope, "episode", BOOTSTRAP_EPISODE_ID),
        series_ref=series.as_ref(),
        title="第一集",
    )
    scene = SceneVersion(
        **_fact(scope, "scene", BOOTSTRAP_SCENE_ID),
        episode_ref=episode.as_ref(),
        sequence=1,
        title="开场",
    )
    shots = tuple(
        ShotVersion(
            **_fact(scope, "shot", f"shot-{index:03d}"),
            scene_ref=scene.as_ref(),
            sequence=index,
            duration_seconds=3,
        )
        for index in range(1, 4)
    )
    return ProductionProjectAggregate(
        aggregate_version=1,
        evaluated_at=BOOTSTRAP_CREATED_AT,
        scope=scope,
        projects=(project,),
        series=(series,),
        episodes=(episode,),
        scenes=(scene,),
        shots=shots,
    )


def _save_bootstrap(
    aggregate_store: EpisodeDomainAggregateStore,
    aggregate: ProductionProjectAggregate,
    *,
    idempotency_key: str,
) -> AggregateSaveResult:
    return aggregate_store.save(
        aggregate,
        expected_aggregate_version=0,
        idempotency_key=idempotency_key,
        payload_digest=_digest(
            {
                "operation": "project_create_minimal_episode_bootstrap",
                "aggregate": aggregate.model_dump(mode="json"),
            }
        ),
    )


def _fact(scope: TenantScope, entity_type: str, entity_id: str) -> dict[str, Any]:
    version_id = (
        BOOTSTRAP_EPISODE_VERSION_ID
        if entity_id == BOOTSTRAP_EPISODE_ID
        else BOOTSTRAP_SCENE_VERSION_ID
        if entity_id == BOOTSTRAP_SCENE_ID
        else f"{entity_id}-v1"
    )
    return {
        "entity_id": entity_id,
        "version_id": version_id,
        "revision": 1,
        "parent_version_id": None,
        "lifecycle_state": "draft",
        "review_state": "not_requested",
        "content_digest": _digest(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "version_id": version_id,
                "scope": scope.model_dump(mode="json"),
            }
        ),
        "scope": scope,
        "created_at": BOOTSTRAP_CREATED_AT,
    }


def _safe_title(value: str) -> str:
    return str(value or "").strip()[:180] or "未命名项目"


def _digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = (
    "BOOTSTRAP_EPISODE_ID",
    "BOOTSTRAP_EPISODE_VERSION_ID",
    "STUDIO_CREATOR_AUTHORING_PROJECT_TYPE",
    "STUDIO_EPISODE_PROJECT_TYPE",
    "CreatorAuthoringBootstrapResult",
    "EpisodeBootstrapResult",
    "build_empty_creator_aggregate",
    "build_minimal_episode_aggregate",
    "ensure_empty_creator_bootstrap",
    "ensure_minimal_episode_bootstrap",
    "should_bootstrap_creator_authoring_project",
    "should_bootstrap_episode_project",
)
