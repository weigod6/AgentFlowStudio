from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.runtime_auth import RuntimeAuthStore
from apps.api.runtime_episode_authoring_service import (
    AuthoringReferenceError,
    AuthoringScopeError,
    AuthoringStateError,
    AuthoringVersionConflictError,
    diff_shot_versions,
    preview_shot_restore,
    preview_shot_revision,
)
from apps.api.runtime_episode_domain_contract import SAFE_ID, EntityVersionRef, TenantScope
from apps.api.runtime_episode_domain_routes import LOCAL_ACTOR_ID, LOCAL_ORG_ID
from apps.api.runtime_episode_domain_store import (
    AggregateIntegrityError,
    AggregateNotFoundError,
    AggregateScopeError,
    EpisodeDomainAggregateStore,
    EpisodeDomainStoreError,
)
from apps.api.runtime_episode_workspace_projection import (
    EpisodeWorkspaceProjectionError,
    WorkspaceProjectionReferenceError,
    build_creator_authoring_projection,
    build_episode_workspace_projection,
)
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_store import RuntimeStore


_SAFE_ID_RE = re.compile(SAFE_ID, re.ASCII)


class EpisodeWorkspaceReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    aggregate: dict[str, Any]
    workspace: dict[str, Any]


class CreatorAuthoringWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    aggregate_version: int
    project: dict[str, Any]
    story_bibles: list[dict[str, Any]]
    series: list[dict[str, Any]]
    arcs: list[dict[str, Any]]
    episodes: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    shots: list[dict[str, Any]]
    reference_assets: list[dict[str, Any]]
    reference_sets: list[dict[str, Any]]
    counts: dict[str, int]
    provider_dispatch_count: int


class ShotCreativePreviewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    creative_intent: str | None = Field(default=None, max_length=4000)
    duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    reference_set_ref: EntityVersionRef | None = None


class ShotImpactPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_aggregate_version: int = Field(ge=1, strict=True)
    shot_ref: EntityVersionRef
    changes: ShotCreativePreviewPatch


class ShotRestorePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_aggregate_version: int = Field(ge=1, strict=True)
    historical_ref: EntityVersionRef
    current_ref: EntityVersionRef


class ShotVersionDiffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_ref: EntityVersionRef
    right_ref: EntityVersionRef


class ShotImpactPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregate_version: int
    shot_ref: EntityVersionRef
    direct_affected_refs: tuple[EntityVersionRef, ...]
    transitive_affected_refs: tuple[EntityVersionRef, ...]
    protected_refs: tuple[EntityVersionRef, ...]
    stale_candidate_refs: tuple[EntityVersionRef, ...]
    stale_review_refs: tuple[EntityVersionRef, ...]
    estimated_follow_up: int
    proposed_changes: dict[str, Any]
    preview_digest: str


class ShotVersionDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[str, dict[str, Any]]


def register_runtime_episode_workspace_routes(
    app: FastAPI,
    store: RuntimeStore,
    auth: RuntimeAuthStore,
) -> None:
    aggregate_store = EpisodeDomainAggregateStore(store.root)

    @app.get(
        "/projects/{project_id}/creator-workspace",
        response_model=CreatorAuthoringWorkspaceResponse,
        summary="Read Creator Authoring Workspace",
    )
    def get_creator_authoring_workspace(
        project_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _, aggregate = _load_authoring_aggregate(
            store,
            auth,
            aggregate_store,
            request,
            project_id,
        )
        try:
            return build_creator_authoring_projection(aggregate)
        except EpisodeWorkspaceProjectionError as exc:
            _raise_workspace_error(
                project_id,
                status_code=500,
                error="creator_workspace_integrity_failed",
                message="Creator workspace could not be projected safely.",
                stage="creator_workspace_projection",
                cause=exc,
            )

    @app.get(
        "/projects/{project_id}/episodes/{episode_id}/versions/{episode_version_id}/workspace",
        response_model=EpisodeWorkspaceReadResponse,
    )
    def get_episode_workspace(
        project_id: str,
        episode_id: str,
        episode_version_id: str,
        request: Request,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        if any(
            _SAFE_ID_RE.fullmatch(value) is None
            for value in (episode_id, episode_version_id)
        ):
            _raise_workspace_error(
                project_id,
                status_code=422,
                error="episode_workspace_identity_invalid",
                message="Episode identity is invalid.",
                stage="episode_workspace_scope",
            )
        try:
            aggregate = aggregate_store.load(org_id=scope.org_id, project_id=project_id)
        except AggregateNotFoundError as exc:
            _raise_store_error(exc, project_id=project_id)
        except EpisodeDomainStoreError as exc:
            _raise_store_error(exc, project_id=project_id)
        if aggregate.scope != scope:
            _raise_workspace_error(
                project_id,
                status_code=500,
                error="episode_workspace_integrity_failed",
                message="Episode workspace state failed its identity check.",
                stage="episode_workspace_read",
            )
        try:
            return build_episode_workspace_projection(
                aggregate,
                episode_ref=EntityVersionRef(
                    entity_type="episode",
                    entity_id=episode_id,
                    version_id=episode_version_id,
                ),
            )
        except WorkspaceProjectionReferenceError as exc:
            _raise_workspace_error(
                project_id,
                status_code=409,
                error="episode_workspace_reference_conflict",
                message="Episode workspace references changed. Reload the latest episode state.",
                stage="episode_workspace_projection",
                retryable=True,
                cause=exc,
            )
        except EpisodeWorkspaceProjectionError as exc:
            _raise_workspace_error(
                project_id,
                status_code=500,
                error="episode_workspace_integrity_failed",
                message="Episode workspace could not be projected safely.",
                stage="episode_workspace_projection",
                cause=exc,
            )

    @app.post(
        "/projects/{project_id}/episode-production-aggregate/shot-impact-preview",
        response_model=ShotImpactPreviewResponse,
        summary="Preview Shot Revision Impact",
    )
    def get_shot_impact_preview(
        project_id: str,
        body: ShotImpactPreviewRequest,
        request: Request,
    ) -> dict[str, Any]:
        scope, aggregate = _load_authoring_aggregate(
            store,
            auth,
            aggregate_store,
            request,
            project_id,
        )
        try:
            preview = preview_shot_revision(
                aggregate,
                scope=scope,
                expected_aggregate_version=body.expected_aggregate_version,
                shot_ref=body.shot_ref,
                proposed_changes={
                    name: getattr(body.changes, name)
                    for name in body.changes.model_fields_set
                },
            )
        except (AuthoringScopeError, AuthoringVersionConflictError, AuthoringReferenceError, AuthoringStateError) as exc:
            _raise_authoring_read_error(exc, project_id=project_id)
        return {
            "aggregate_version": preview.aggregate_version,
            "shot_ref": preview.shot_ref,
            "direct_affected_refs": preview.direct_affected_refs,
            "transitive_affected_refs": preview.transitive_affected_refs,
            "protected_refs": preview.protected_refs,
            "stale_candidate_refs": preview.stale_candidate_refs,
            "stale_review_refs": preview.stale_review_refs,
            "estimated_follow_up": preview.estimated_follow_up,
            "proposed_changes": preview.proposed_changes,
            "preview_digest": preview.preview_digest,
        }

    @app.post(
        "/projects/{project_id}/episode-production-aggregate/shot-restore-preview",
        response_model=ShotImpactPreviewResponse,
        summary="Preview Shot Restore Impact",
    )
    def get_shot_restore_preview(
        project_id: str,
        body: ShotRestorePreviewRequest,
        request: Request,
    ) -> dict[str, Any]:
        scope, aggregate = _load_authoring_aggregate(
            store,
            auth,
            aggregate_store,
            request,
            project_id,
        )
        try:
            preview = preview_shot_restore(
                aggregate,
                scope=scope,
                expected_aggregate_version=body.expected_aggregate_version,
                historical_ref=body.historical_ref,
                current_ref=body.current_ref,
            )
        except (AuthoringScopeError, AuthoringVersionConflictError, AuthoringReferenceError, AuthoringStateError) as exc:
            _raise_authoring_read_error(exc, project_id=project_id)
        return {
            "aggregate_version": preview.aggregate_version,
            "shot_ref": preview.shot_ref,
            "direct_affected_refs": preview.direct_affected_refs,
            "transitive_affected_refs": preview.transitive_affected_refs,
            "protected_refs": preview.protected_refs,
            "stale_candidate_refs": preview.stale_candidate_refs,
            "stale_review_refs": preview.stale_review_refs,
            "estimated_follow_up": preview.estimated_follow_up,
            "proposed_changes": preview.proposed_changes,
            "preview_digest": preview.preview_digest,
        }

    @app.post(
        "/projects/{project_id}/episode-production-aggregate/shot-version-diff",
        response_model=ShotVersionDiffResponse,
        summary="Read Creator-friendly Shot Version Diff",
    )
    def get_shot_version_diff(
        project_id: str,
        body: ShotVersionDiffRequest,
        request: Request,
    ) -> dict[str, Any]:
        scope, aggregate = _load_authoring_aggregate(
            store,
            auth,
            aggregate_store,
            request,
            project_id,
        )
        try:
            changes = diff_shot_versions(
                aggregate,
                scope=scope,
                left_ref=body.left_ref,
                right_ref=body.right_ref,
            )
        except (AuthoringScopeError, AuthoringReferenceError) as exc:
            _raise_authoring_read_error(exc, project_id=project_id)
        return {"changes": changes}


def _require_project_scope(
    store: RuntimeStore,
    auth: RuntimeAuthStore,
    request: Request,
    project_id: str,
) -> TenantScope:
    if _SAFE_ID_RE.fullmatch(project_id) is None:
        _raise_workspace_error(
            project_id,
            status_code=422,
            error="episode_workspace_project_id_invalid",
            message="Project identity is invalid.",
            stage="episode_workspace_scope",
        )
    if (
        store.is_project_deleted(project_id)
        or not store.project_manifest_path(project_id).is_file()
    ):
        _raise_workspace_error(
            project_id,
            status_code=404,
            error="project_not_found",
            message="Project was not found.",
            stage="episode_workspace_scope",
        )
    try:
        manifest = store.ensure_project_manifest(project_id)
    except (OSError, ValueError) as exc:
        _raise_workspace_error(
            project_id,
            status_code=422,
            error="project_manifest_invalid",
            message="Project data is invalid.",
            stage="episode_workspace_scope",
            cause=exc,
        )
    if str(manifest.get("project_id") or "") != project_id:
        _raise_workspace_error(
            project_id,
            status_code=422,
            error="project_manifest_identity_mismatch",
            message="Project identity does not match its stored record.",
            stage="episode_workspace_scope",
        )
    if not auth.enabled():
        return TenantScope(
            org_id=LOCAL_ORG_ID,
            project_id=project_id,
            actor_id=LOCAL_ACTOR_ID,
        )
    user = auth.require_user(request)
    user_id = str(user.get("user_id") or "")
    if _SAFE_ID_RE.fullmatch(user_id) is None or not auth.user_can_access_project(
        user_id, project_id
    ):
        _raise_workspace_error(
            project_id,
            status_code=403,
            error="project_access_denied",
            message="Project access is denied.",
            stage="episode_workspace_scope",
        )
    return TenantScope(org_id=user_id, project_id=project_id, actor_id=user_id)


def _load_authoring_aggregate(
    store: RuntimeStore,
    auth: RuntimeAuthStore,
    aggregate_store: EpisodeDomainAggregateStore,
    request: Request,
    project_id: str,
) -> tuple[TenantScope, Any]:
    scope = _require_project_scope(store, auth, request, project_id)
    try:
        aggregate = aggregate_store.load(org_id=scope.org_id, project_id=project_id)
    except EpisodeDomainStoreError as exc:
        _raise_store_error(exc, project_id=project_id)
    if aggregate.scope != scope:
        _raise_workspace_error(
            project_id,
            status_code=500,
            error="episode_workspace_integrity_failed",
            message="Creator workspace state failed its identity check.",
            stage="episode_authoring_read",
        )
    return scope, aggregate


def _raise_authoring_read_error(exc: Exception, *, project_id: str) -> None:
    if isinstance(exc, AuthoringScopeError):
        status_code = 403
        error = "episode_authoring_scope_mismatch"
        message = "Creator workspace access is not allowed."
    elif isinstance(exc, AuthoringVersionConflictError):
        status_code = 409
        error = "episode_authoring_version_conflict"
        message = "Creator facts changed. Reload before continuing."
    elif isinstance(exc, AuthoringReferenceError):
        status_code = 409
        error = "episode_authoring_reference_conflict"
        message = "A referenced creator fact is missing or no longer current."
    else:
        status_code = 422
        error = "episode_authoring_state_conflict"
        message = "The requested creator preview is not valid."
    _raise_workspace_error(
        project_id,
        status_code=status_code,
        error=error,
        message=message,
        stage="episode_authoring_read",
        retryable=status_code == 409,
        cause=exc,
    )


def _raise_store_error(exc: EpisodeDomainStoreError, *, project_id: str) -> None:
    if isinstance(exc, AggregateNotFoundError):
        _raise_workspace_error(
            project_id,
            status_code=404,
            error="episode_aggregate_not_found",
            message="Episode production state was not found.",
            stage="episode_workspace_read",
            cause=exc,
        )
    if isinstance(exc, AggregateScopeError):
        _raise_workspace_error(
            project_id,
            status_code=403,
            error="episode_aggregate_scope_mismatch",
            message="Episode production state does not belong to this project scope.",
            stage="episode_workspace_scope",
            cause=exc,
        )
    if isinstance(exc, AggregateIntegrityError):
        _raise_workspace_error(
            project_id,
            status_code=500,
            error="episode_workspace_integrity_failed",
            message="Episode production state failed its integrity check.",
            stage="episode_workspace_read",
            cause=exc,
        )
    _raise_workspace_error(
        project_id,
        status_code=500,
        error="episode_workspace_store_failed",
        message="Episode workspace state could not be processed.",
        stage="episode_workspace_read",
        cause=exc,
    )


def _raise_workspace_error(
    project_id: str,
    *,
    status_code: int,
    error: str,
    message: str,
    stage: str,
    retryable: bool = False,
    cause: Exception | None = None,
) -> None:
    detail = safe_error_detail(
        error,
        message=message,
        project_id=project_id,
        action="episode_workspace",
        stage=stage,
        retryable=retryable,
    )
    exception = HTTPException(status_code=status_code, detail=detail)
    if cause is None:
        raise exception
    raise exception from cause


__all__ = (
    "CreatorAuthoringWorkspaceResponse",
    "EpisodeWorkspaceReadResponse",
    "ShotImpactPreviewRequest",
    "ShotImpactPreviewResponse",
    "ShotVersionDiffRequest",
    "ShotVersionDiffResponse",
    "register_runtime_episode_workspace_routes",
)
