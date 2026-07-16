from __future__ import annotations

from typing import Any

from apps.api.runtime_artifacts import keyframe_generation_artifacts
from apps.api.runtime_creator_golden_trial_common import DispatchNextRequest, digest, object_id
from apps.api.runtime_episode_domain_contract import (
    AssetCandidateVersion,
    EntityVersionRef,
    ProductionProjectAggregate,
    SafeArtifactRef,
    TenantScope,
)
from apps.api.runtime_episode_domain_store import (
    AggregateNotFoundError,
    EpisodeDomainAggregateStore,
    EpisodeDomainStoreError,
)
from apps.api.runtime_generated_image_assets import register_generated_image_asset
from apps.api.runtime_jobs import runtime_job
from apps.api.runtime_keyframe_routes import _candidate_records, _candidate_previews
from apps.api.runtime_keyframes import KEYFRAME_NON_CLAIMS, build_keyframe_generation
from apps.api.runtime_models import KeyframeGenerationRequest
from apps.api.runtime_store import RuntimeStore, safe_id


def dispatch_image_keyframe(
    store: RuntimeStore,
    project_id: str,
    scope: TenantScope,
    *,
    shot_id: str,
    body: DispatchNextRequest,
    provider_attempt_id: str,
) -> dict[str, Any]:
    job_id = store.new_job_id("creator_golden_keyframe", project_id)
    output_dir = store.run_dir(project_id, job_id)
    prompt = shot_prompt(shot_id, body)
    request = KeyframeGenerationRequest(
        node_id=f"creator-golden-{safe_id(shot_id)}",
        prompt_text=prompt,
        optimized_prompt=prompt,
        target_platform="short_video",
        style="cinematic",
        aspect_ratio=body.aspect_ratio,
        candidate_count=body.candidate_count,
        provider_service_id=safe_id(body.provider_service_id),
        node_parameters={"disable_provider_retry": True, "stop_loss": "single_provider_attempt_per_shot"},
        generated_at=body.generated_at,
    )
    result = build_keyframe_generation(
        store,
        project_id,
        request,
        output_dir,
        request_id=provider_attempt_id,
        client_request_id=provider_attempt_id,
    )
    artifacts = keyframe_generation_artifacts(store, output_dir)
    status = str(result.get("status") or "blocked")
    job = runtime_job(job_id, project_id, "creator_golden_keyframe", status, artifacts=artifacts)
    public_job = store.write_job(job)
    records = _candidate_records(
        store,
        project_id,
        job_id,
        result.get("provider_outputs") or [],
    )
    candidate_previews = _candidate_previews(project_id, job_id, records)
    selected_artifact_ref = None
    reusable_image_assets: list[dict[str, Any]] = []
    for item in records:
        try:
            registered = register_generated_image_asset(
                store,
                project_id,
                source_node_id=request.node_id,
                source_job_id=job_id,
                source_candidate_id=item["candidate_id"],
                image_path=item["path"],
                source_candidate_digest=item["sha256"],
                source_candidate_status=item["status"],
            )
        except ValueError:
            continue
        reusable_image_assets.append(registered["asset"])
        if selected_artifact_ref is None:
            selected_artifact_ref = {
                "artifact_id": registered["artifact"]["artifact_id"],
                "artifact_type": "image_keyframe",
                "content_digest": item["sha256"],
            }
    return {
        "status": status,
        "job_id": public_job["job_id"],
        "provider_gate": result.get("provider_gate") or {},
        "provider_calls_started": bool(result.get("provider_calls_started")),
        "safe_manifest": result.get("safe_manifest") or {},
        "artifacts": artifacts,
        "candidate_previews": candidate_previews,
        "reusable_image_assets": reusable_image_assets,
        "selected_artifact_ref": selected_artifact_ref,
        "non_claims": KEYFRAME_NON_CLAIMS,
        "scope_actor": scope.actor_id,
    }


def write_episode_candidate(
    store: RuntimeStore,
    scope: TenantScope,
    *,
    shot_id: str,
    artifact_ref: SafeArtifactRef,
    job_id: str,
    created_at: str,
    idempotency_key: str,
) -> dict[str, Any]:
    aggregate_store = EpisodeDomainAggregateStore(store.root)
    try:
        aggregate = aggregate_store.load(org_id=scope.org_id, project_id=scope.project_id)
    except AggregateNotFoundError:
        raise
    target = latest_shot_ref(aggregate, shot_id)
    candidate_entity_id = object_id("trial-candidate", job_id, shot_id)
    for candidate in aggregate.asset_candidates:
        if candidate.entity_id == candidate_entity_id:
            return {
                "status": "replayed",
                "recoverable": False,
                "aggregate_version": aggregate.aggregate_version,
                "candidate_ref": candidate.as_ref().model_dump(mode="json"),
                "target_ref": target.model_dump(mode="json"),
                "control_provenance_status": "not_written_by_adapter_cache",
            }
    candidate = AssetCandidateVersion(
        entity_id=candidate_entity_id,
        version_id=f"{candidate_entity_id}-v1",
        revision=1,
        parent_version_id=None,
        lifecycle_state="candidate",
        review_state="needs_review",
        content_digest=digest(
            {
                "operation": "creator_golden_trial_episode_candidate",
                "target_ref": target.model_dump(mode="json"),
                "artifact_ref": artifact_ref.model_dump(mode="json"),
                "job_id": job_id,
            }
        ),
        scope=scope,
        created_at=created_at,
        target_ref=target,
        artifact_ref=artifact_ref,
        job_id=job_id,
        job_state="succeeded",
        control_provenance=None,
    )
    payload = aggregate.model_dump(mode="python")
    payload.update(
        {
            "aggregate_version": aggregate.aggregate_version + 1,
            "evaluated_at": created_at,
            "asset_candidates": (*aggregate.asset_candidates, candidate),
        }
    )
    updated = ProductionProjectAggregate.model_validate(payload)
    result = aggregate_store.save(
        updated,
        expected_aggregate_version=aggregate.aggregate_version,
        idempotency_key=idempotency_key,
        payload_digest=digest(
            {
                "operation": "creator_golden_trial_episode_candidate",
                "candidate": candidate.model_dump(mode="json"),
                "expected_aggregate_version": aggregate.aggregate_version,
            }
        ),
    )
    written = next(item for item in result.aggregate.asset_candidates if item.entity_id == candidate_entity_id)
    return {
        "status": "written" if not result.replayed else "replayed",
        "recoverable": False,
        "aggregate_version": result.aggregate.aggregate_version,
        "candidate_ref": written.as_ref().model_dump(mode="json"),
        "target_ref": target.model_dump(mode="json"),
        "human_review_state": "needs_review",
        "control_provenance_status": "not_written_by_adapter_cache",
    }


def latest_shot_ref(aggregate: ProductionProjectAggregate, shot_id: str) -> EntityVersionRef:
    matches = [shot for shot in aggregate.shots if shot.entity_id == shot_id]
    if not matches:
        raise EpisodeDomainStoreError(f"shot not found: {shot_id}")
    return max(matches, key=lambda item: item.revision).as_ref()


def shot_prompt(shot_id: str, body: DispatchNextRequest) -> str:
    shot_index = int(str(shot_id).rsplit("-", 1)[-1])
    beats = {
        1: "A creator enters a compact near-future production room and sees an episode map on a wall display.",
        2: "The director reviews three aligned visual frames, marking continuity issues with calm hand gestures.",
        3: "The production lead packages the approved candidate into a clean delivery board with cost and provenance visible.",
    }
    return (
        f"Creator-led AI-native production center, shot {shot_index}. "
        f"{beats.get(shot_index, beats[1])} "
        "Cinematic but readable, consistent character, clear production workspace, no text overlays."
    )


__all__ = (
    "dispatch_image_keyframe",
    "latest_shot_ref",
    "shot_prompt",
    "write_episode_candidate",
)
