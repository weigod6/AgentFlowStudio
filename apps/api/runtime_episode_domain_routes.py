from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.runtime_auth import RuntimeAuthStore
from apps.api.runtime_episode_domain_contract import (
    SAFE_ID,
    AssetCandidateVersion,
    DeliveryVersion,
    ProductionProjectAggregate,
    SafeArtifactRef,
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
    EpisodeDomainStoreError,
)
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_store import RuntimeStore


LOCAL_ORG_ID = "local-runtime"
LOCAL_ACTOR_ID = "local-creator"
_SAFE_ID_RE = re.compile(SAFE_ID, re.ASCII)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
_UNC_PATH_RE = re.compile(
    r"(?<![:\\/])(?:\\\\|//)[^\\/\s]+[\\/][^\s]+",
)
_POSIX_PRIVATE_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9._~:/-])"
    r"/(?:home|Users|root|opt|var|tmp|private|mnt|srv|etc|run|test|data|workspace)"
    r"(?:/|\\)",
    re.IGNORECASE,
)
_FILE_URI_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])file:(?://|[\\/]|[A-Za-z]:[\\/])",
    re.IGNORECASE,
)
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_URL_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")
_URL_CANDIDATE_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*://|//|(?:\.\.?/|/)?"
    r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]{1,5})?/)"
    r"[^\s<>\"']+"
)
_SIGNED_QUERY_KEYS = frozenset(
    {
        "access-token",
        "authorization",
        "auth-token",
        "awsaccesskeyid",
        "credential",
        "googleaccessid",
        "jwt",
        "key-pair-id",
        "sig",
        "signature",
        "signed-token",
        "token",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-signature",
        "x-goog-credential",
        "x-goog-security-token",
        "x-goog-signature",
        "x-ms-signature",
        "x-ms-token",
    }
)


class EpisodeSafeArtifactRef(SafeArtifactRef):
    pass


class EpisodeAssetCandidateVersion(AssetCandidateVersion):
    artifact_ref: EpisodeSafeArtifactRef | None = None


class EpisodeDeliveryVersion(DeliveryVersion):
    preview_artifact_ref: EpisodeSafeArtifactRef | None = None
    export_artifact_refs: tuple[EpisodeSafeArtifactRef, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )


class EpisodeProductionProjectAggregate(ProductionProjectAggregate):
    asset_candidates: tuple[EpisodeAssetCandidateVersion, ...] = Field(
        default_factory=tuple,
        max_length=65536,
    )
    deliveries: tuple[EpisodeDeliveryVersion, ...] = Field(
        default_factory=tuple,
        max_length=4096,
    )


class EpisodeAggregateReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_aggregate_version: int = Field(ge=0, strict=True)
    aggregate: EpisodeProductionProjectAggregate


class EpisodeAggregateReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregate: EpisodeProductionProjectAggregate
    aggregate_version: int = Field(ge=1, strict=True)


class EpisodeAggregateWriteResponse(EpisodeAggregateReadResponse):
    replayed: bool


IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=160,
        pattern=SAFE_ID,
    ),
]


def register_runtime_episode_domain_routes(
    app: FastAPI,
    store: RuntimeStore,
    auth: RuntimeAuthStore,
) -> None:
    aggregate_store = EpisodeDomainAggregateStore(store.root)

    @app.get(
        "/projects/{project_id}/episode-production-aggregate",
        response_model=EpisodeAggregateReadResponse,
    )
    def get_episode_production_aggregate(
        project_id: str,
        request: Request,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        try:
            aggregate = aggregate_store.load(org_id=scope.org_id, project_id=project_id)
        except EpisodeDomainStoreError as exc:
            _raise_store_error(exc, request=request, project_id=project_id)
        if aggregate.scope != scope:
            _raise_api_error(
                request,
                project_id,
                status_code=500,
                error="episode_aggregate_integrity_failed",
                message="Episode production state failed its identity check.",
                stage="episode_aggregate_read",
            )
        aggregate_payload = _safe_aggregate_payload(
            aggregate,
            request=request,
            project_id=project_id,
            status_code=500,
            stage="episode_aggregate_read",
        )
        return {
            "aggregate": aggregate_payload,
            "aggregate_version": aggregate.aggregate_version,
        }

    @app.put(
        "/projects/{project_id}/episode-production-aggregate",
        response_model=EpisodeAggregateWriteResponse,
        summary="Bootstrap Episode Production Aggregate",
        description=(
            "Creates aggregate version 1 from expected version 0. Later mutations "
            "must use the typed /commands endpoint; whole-aggregate replacement is "
            "rejected after bootstrap."
        ),
    )
    def replace_episode_production_aggregate(
        project_id: str,
        body: EpisodeAggregateReplaceRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        aggregate = ProductionProjectAggregate.model_validate(
            body.aggregate.model_dump(mode="json")
        )
        scope = _require_project_scope(store, auth, request, project_id)
        _require_exact_aggregate_scope(aggregate, scope, request=request)
        if body.expected_aggregate_version != 0 or aggregate.aggregate_version != 1:
            _raise_api_error(
                request,
                project_id,
                status_code=409,
                error="episode_aggregate_bootstrap_only",
                message=(
                    "Whole-episode replacement is only available for the initial "
                    "bootstrap. Use a typed episode command for later changes."
                ),
                stage="episode_aggregate_bootstrap",
            )
        _safe_aggregate_payload(
            aggregate,
            request=request,
            project_id=project_id,
            status_code=422,
            stage="episode_aggregate_validation",
        )
        payload_digest = _mutation_digest(
            project_id=project_id,
            expected_aggregate_version=body.expected_aggregate_version,
            aggregate=aggregate,
        )
        try:
            result = aggregate_store.save(
                aggregate,
                expected_aggregate_version=body.expected_aggregate_version,
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
            )
        except EpisodeDomainStoreError as exc:
            _raise_store_error(exc, request=request, project_id=project_id)
        aggregate_payload = _safe_aggregate_payload(
            result.aggregate,
            request=request,
            project_id=project_id,
            status_code=500,
            stage="episode_aggregate_write",
        )
        return {
            "aggregate": aggregate_payload,
            "aggregate_version": result.aggregate.aggregate_version,
            "replayed": result.replayed,
        }


def _require_project_scope(
    store: RuntimeStore,
    auth: RuntimeAuthStore,
    request: Request,
    project_id: str,
) -> TenantScope:
    if _SAFE_ID_RE.fullmatch(project_id) is None:
        _raise_api_error(
            request,
            project_id,
            status_code=422,
            error="episode_aggregate_project_id_invalid",
            message="Project identity is invalid.",
            stage="episode_aggregate_scope",
        )
    if store.is_project_deleted(project_id) or not store.project_manifest_path(project_id).is_file():
        _raise_api_error(
            request,
            project_id,
            status_code=404,
            error="project_not_found",
            message="Project was not found.",
            stage="episode_aggregate_scope",
        )
    try:
        manifest = store.ensure_project_manifest(project_id)
    except (OSError, ValueError) as exc:
        _raise_api_error(
            request,
            project_id,
            status_code=422,
            error="project_manifest_invalid",
            message="Project data is invalid.",
            stage="episode_aggregate_scope",
            cause=exc,
        )
    if str(manifest.get("project_id") or "") != project_id:
        _raise_api_error(
            request,
            project_id,
            status_code=422,
            error="project_manifest_identity_mismatch",
            message="Project identity does not match its stored record.",
            stage="episode_aggregate_scope",
        )
    if not auth.enabled():
        return TenantScope(
            org_id=LOCAL_ORG_ID,
            project_id=project_id,
            actor_id=LOCAL_ACTOR_ID,
        )
    user = auth.require_user(request)
    user_id = str(user.get("user_id") or "")
    if _SAFE_ID_RE.fullmatch(user_id) is None or not auth.user_can_access_project(user_id, project_id):
        _raise_api_error(
            request,
            project_id,
            status_code=403,
            error="project_access_denied",
            message="Project access is denied.",
            stage="episode_aggregate_scope",
        )
    # Runtime auth currently has personal project ownership, not a separate
    # organization object. Binding the personal org and actor to the verified
    # owner id is the only identity mapping the current Runtime can prove.
    return TenantScope(org_id=user_id, project_id=project_id, actor_id=user_id)


def _require_exact_aggregate_scope(
    aggregate: ProductionProjectAggregate,
    expected: TenantScope,
    *,
    request: Request,
) -> None:
    if aggregate.scope.project_id != expected.project_id:
        _raise_api_error(
            request,
            expected.project_id,
            status_code=409,
            error="episode_aggregate_project_mismatch",
            message="Aggregate project identity does not match the request path.",
            stage="episode_aggregate_scope",
        )
    if aggregate.scope.org_id != expected.org_id or aggregate.scope.actor_id != expected.actor_id:
        _raise_api_error(
            request,
            expected.project_id,
            status_code=403,
            error="episode_aggregate_scope_mismatch",
            message="Aggregate tenant or actor identity does not match the authenticated project owner.",
            stage="episode_aggregate_scope",
        )


def _mutation_digest(
    *,
    project_id: str,
    expected_aggregate_version: int,
    aggregate: ProductionProjectAggregate,
) -> str:
    payload = {
        "operation": "replace_episode_production_aggregate",
        "project_id": project_id,
        "expected_aggregate_version": expected_aggregate_version,
        "aggregate": aggregate.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _safe_aggregate_payload(
    aggregate: ProductionProjectAggregate,
    *,
    request: Request,
    project_id: str,
    status_code: int,
    stage: str,
) -> dict[str, Any]:
    payload = aggregate.model_dump(mode="json")
    try:
        _reject_unsafe_projection(payload)
    except ValueError as exc:
        _raise_api_error(
            request,
            project_id,
            status_code=status_code,
            error=(
                "episode_aggregate_unsafe_payload"
                if status_code < 500
                else "episode_aggregate_integrity_failed"
            ),
            message="Episode production state contains unsafe private data.",
            stage=stage,
            cause=exc,
        )
    return payload


def _reject_unsafe_projection(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _reject_unsafe_projection(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe_projection(item)
        return
    if not isinstance(value, str):
        return
    decoded = _decoded_string(value).strip()
    if (
        _WINDOWS_ABSOLUTE_PATH_RE.search(decoded)
        or _UNC_PATH_RE.search(decoded)
        or decoded.startswith("/")
        or _POSIX_PRIVATE_ABSOLUTE_PATH_RE.search(decoded)
        or _FILE_URI_RE.search(decoded)
        or _url_has_signed_credentials(decoded)
    ):
        raise ValueError("unsafe projection string")


def _decoded_string(value: str) -> str:
    decoded = value
    for _ in range(3):
        candidate = unquote(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    return decoded


def _url_has_signed_credentials(value: str) -> bool:
    for match in _URL_CANDIDATE_RE.finditer(value):
        candidate = match.group(0)
        if "?" not in candidate:
            continue
        prefix, query = candidate.split("?", 1)
        if not _looks_like_url(prefix):
            continue
        query = query.split("#", 1)[0]
        for parameter in re.split(r"[&;]", query):
            raw_key = parameter.split("=", 1)[0]
            key = _decoded_string(raw_key).strip().casefold().replace("_", "-")
            if key in _SIGNED_QUERY_KEYS:
                return True
    return False


def _looks_like_url(prefix: str) -> bool:
    if not prefix or any(character.isspace() for character in prefix):
        return False
    return bool(
        _URL_SCHEME_RE.match(prefix)
        or prefix.startswith(("./", "../", "/", "//"))
        or "/" in prefix
        or _URL_HOST_RE.fullmatch(prefix)
    )


def _raise_store_error(
    exc: EpisodeDomainStoreError,
    *,
    request: Request,
    project_id: str,
) -> None:
    if isinstance(exc, AggregateNotFoundError):
        _raise_api_error(
            request,
            project_id,
            status_code=404,
            error="episode_aggregate_not_found",
            message="Episode production state was not found.",
            stage="episode_aggregate_read",
            cause=exc,
        )
    if isinstance(exc, AggregateScopeError):
        _raise_api_error(
            request,
            project_id,
            status_code=403,
            error="episode_aggregate_scope_mismatch",
            message="Episode production state does not belong to this project scope.",
            stage="episode_aggregate_scope",
            cause=exc,
        )
    if isinstance(exc, AggregateIdempotencyConflictError):
        _raise_api_error(
            request,
            project_id,
            status_code=409,
            error="episode_aggregate_idempotency_conflict",
            message="This idempotency key was already used for a different mutation.",
            stage="episode_aggregate_write",
            cause=exc,
        )
    if isinstance(exc, AggregateVersionConflictError):
        _raise_api_error(
            request,
            project_id,
            status_code=409,
            error="episode_aggregate_version_conflict",
            message="Episode production state changed. Reload it before retrying.",
            stage="episode_aggregate_write",
            retryable=True,
            cause=exc,
        )
    if isinstance(exc, AggregateRetiredError):
        _raise_api_error(
            request,
            project_id,
            status_code=409,
            error="episode_aggregate_retired",
            message="Retired episode production state cannot be changed.",
            stage="episode_aggregate_write",
            cause=exc,
        )
    if isinstance(exc, AggregateIntegrityError):
        _raise_api_error(
            request,
            project_id,
            status_code=500,
            error="episode_aggregate_integrity_failed",
            message="Episode production state failed its integrity check.",
            stage="episode_aggregate_read",
            cause=exc,
        )
    _raise_api_error(
        request,
        project_id,
        status_code=500,
        error="episode_aggregate_store_failed",
        message="Episode production state could not be processed.",
        stage="episode_aggregate_store",
        cause=exc,
    )


def _raise_api_error(
    request: Request,
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
        action="episode_aggregate",
        stage=stage,
        retryable=retryable,
    )
    exception = HTTPException(status_code=status_code, detail=detail)
    if cause is None:
        raise exception
    raise exception from cause


__all__ = (
    "EpisodeAggregateReadResponse",
    "EpisodeAggregateReplaceRequest",
    "EpisodeAggregateWriteResponse",
    "EpisodeAssetCandidateVersion",
    "EpisodeDeliveryVersion",
    "EpisodeProductionProjectAggregate",
    "EpisodeSafeArtifactRef",
    "LOCAL_ACTOR_ID",
    "LOCAL_ORG_ID",
    "register_runtime_episode_domain_routes",
)
