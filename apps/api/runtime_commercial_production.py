from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agentflow.harness.json_io import exclusive_file_lock, write_json
from apps.api.runtime_auth import RuntimeAuthStore
from apps.api.runtime_episode_domain_contract import SAFE_ID, TenantScope
from apps.api.runtime_episode_domain_routes import _require_project_scope
from apps.api.runtime_errors import safe_error_detail
from apps.api.runtime_store import RuntimeStore, read_json, reject_unsafe_payload, safe_id


COMMERCIAL_STATE_SCHEMA = "afs_commercial_production_slice.v0.1"
COMMERCIAL_PROJECTION_SCHEMA = "afs_commercial_production_projection.v0.1"


IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=160, pattern=SAFE_ID),
]


class CommercialProductionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SampleCreateRequest(CommercialProductionModel):
    expected_version: int = Field(default=0, ge=0, strict=True)
    title: str = Field(default="雾港异闻录", min_length=1, max_length=120)
    created_at: str = Field(default="2026-07-15T00:00:00+00:00", min_length=1, max_length=64)


class StageGateLockRequest(CommercialProductionModel):
    expected_version: int = Field(ge=1, strict=True)
    gate_id: str = Field(default="storyboard-scope-lock", pattern=SAFE_ID)
    note: str = Field(default="锁定第一集故事板生产范围。", max_length=240)
    created_at: str = Field(default="2026-07-15T00:00:00+00:00", min_length=1, max_length=64)


class LocalRewriteRequest(CommercialProductionModel):
    expected_version: int = Field(ge=1, strict=True)
    target_shot_id: str = Field(pattern=SAFE_ID)
    replacement_beat: str = Field(min_length=1, max_length=400)
    reason: str = Field(default="局部镜头节奏调整", max_length=240)
    created_at: str = Field(default="2026-07-15T00:00:00+00:00", min_length=1, max_length=64)


def register_runtime_commercial_production_routes(
    app: FastAPI,
    store: RuntimeStore,
    auth: RuntimeAuthStore,
) -> None:
    @app.get("/projects/{project_id}/commercial-production")
    def get_commercial_production(project_id: str, request: Request) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        with _commercial_lock(store, project_id):
            state = _read_state(store, project_id)
            if state is None:
                return {"production": _empty_projection(scope)}
            _require_state_scope(state, scope, request=request, project_id=project_id)
            return {"production": _projection(state)}

    @app.post("/projects/{project_id}/commercial-production/sample")
    def create_commercial_sample(
        project_id: str,
        body: SampleCreateRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        digest = _payload_digest(scope, body.model_dump(mode="json"))
        with _commercial_lock(store, project_id):
            state = _read_state(store, project_id)
            replay = _idempotency_replay_or_conflict(state, idempotency_key, digest, request, project_id)
            if replay is not None:
                return replay
            if state is not None or body.expected_version != 0:
                _raise_commercial_error(
                    request,
                    project_id,
                    status_code=409,
                    error="commercial_production_already_exists",
                    message="Commercial production sample already exists for this project.",
                    stage="sample",
                )
            state = _sample_state(scope, title=body.title, created_at=_stamp(body.created_at))
            response = {"production": _projection(state), "replayed": False}
            _record_idempotency(state, idempotency_key, digest, response)
            _write_state(store, project_id, state)
            return response

    @app.post("/projects/{project_id}/commercial-production/stage-gate/lock")
    def lock_stage_gate(
        project_id: str,
        body: StageGateLockRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        digest = _payload_digest(scope, body.model_dump(mode="json"))
        with _commercial_lock(store, project_id):
            state = _load_required_state(store, project_id, scope, request)
            replay = _idempotency_replay_or_conflict(state, idempotency_key, digest, request, project_id)
            if replay is not None:
                return replay
            _require_version(state, body.expected_version, request, project_id, "stage_gate_lock")
            locked_refs = _scope_refs(state)
            gate = state["stage_gates"]["storyboard_scope_lock"]
            gate.update(
                {
                    "gate_id": body.gate_id,
                    "status": "locked",
                    "locked_at": _stamp(body.created_at),
                    "note": body.note,
                    "locked_refs": locked_refs,
                    "scope_digest": _digest(locked_refs),
                    "recoverable": True,
                }
            )
            _advance(state, _stamp(body.created_at))
            response = {"production": _projection(state), "replayed": False}
            _record_idempotency(state, idempotency_key, digest, response)
            _write_state(store, project_id, state)
            return response

    @app.post("/projects/{project_id}/commercial-production/revision-requests/local-rewrite")
    def request_local_rewrite(
        project_id: str,
        body: LocalRewriteRequest,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        scope = _require_project_scope(store, auth, request, project_id)
        digest = _payload_digest(scope, body.model_dump(mode="json"))
        with _commercial_lock(store, project_id):
            state = _load_required_state(store, project_id, scope, request)
            replay = _idempotency_replay_or_conflict(state, idempotency_key, digest, request, project_id)
            if replay is not None:
                return replay
            _require_version(state, body.expected_version, request, project_id, "local_rewrite")
            gate = state["stage_gates"]["storyboard_scope_lock"]
            if gate.get("status") != "locked":
                _raise_commercial_error(
                    request,
                    project_id,
                    status_code=409,
                    error="commercial_stage_gate_not_locked",
                    message="Lock the storyboard scope before requesting a local rewrite.",
                    stage="local_rewrite",
                )
            shot = _find_one(state["shots"], "shot_id", body.target_shot_id, request, project_id)
            scene = _find_one(state["scenes"], "scene_id", shot["scene_id"], request, project_id)
            before_digest = _protected_digest(state, body.target_shot_id)
            previous_ref = _shot_ref(shot)
            shot["revision"] = int(shot["revision"]) + 1
            shot["version_id"] = f"{shot['shot_id']}-v{shot['revision']}"
            shot["beat"] = body.replacement_beat
            shot["review_state"] = "needs_review"
            shot["content_digest"] = _digest(
                {
                    "shot_id": shot["shot_id"],
                    "revision": shot["revision"],
                    "beat": shot["beat"],
                    "asset_refs": shot["asset_refs"],
                }
            )
            after_digest = _protected_digest(state, body.target_shot_id)
            request_id = f"urr-{len(state['revision_requests']) + 1:03d}"
            impact = {
                "request_id": request_id,
                "status": "applied_to_selected_scope",
                "reason": body.reason,
                "target_ref": _shot_ref(shot),
                "previous_target_ref": previous_ref,
                "selected_refs": {
                    "episode_id": state["selected_episode_id"],
                    "scene_id": scene["scene_id"],
                    "shot_id": shot["shot_id"],
                },
                "protected_ref_counts": _protected_counts(state, body.target_shot_id),
                "protected_digest_before": before_digest,
                "protected_digest_after": after_digest,
                "protected_digest_equal": before_digest == after_digest,
                "created_at": _stamp(body.created_at),
            }
            state["revision_requests"].append(impact)
            gate["last_revision_request_id"] = request_id
            _advance(state, _stamp(body.created_at))
            response = {"production": _projection(state), "revision_request": impact, "replayed": False}
            _record_idempotency(state, idempotency_key, digest, response)
            _write_state(store, project_id, state)
            return response


def _empty_projection(scope: TenantScope) -> dict[str, Any]:
    return {
        "schema_version": COMMERCIAL_PROJECTION_SCHEMA,
        "status": "empty",
        "version": 0,
        "scope": scope.model_dump(mode="json"),
        "next_action": "bootstrap_sample",
        "provider_dispatch_count": 0,
        "non_claims": _non_claims(),
    }


def _projection(state: dict[str, Any]) -> dict[str, Any]:
    episodes = list(state["episodes"])
    selected_episode_id = str(state["selected_episode_id"])
    selected_scenes = [item for item in state["scenes"] if item["episode_id"] == selected_episode_id]
    selected_scene_ids = {item["scene_id"] for item in selected_scenes}
    selected_shots = [item for item in state["shots"] if item["scene_id"] in selected_scene_ids]
    return {
        "schema_version": COMMERCIAL_PROJECTION_SCHEMA,
        "status": "ready",
        "version": state["version"],
        "updated_at": state["updated_at"],
        "scope": state["scope"],
        "mode": state["mode"],
        "hierarchy": state["hierarchy"],
        "episodes": episodes,
        "selected_episode_id": selected_episode_id,
        "scenes": selected_scenes,
        "shots": selected_shots,
        "storyboard": {
            "default_mode": True,
            "scene_count": len(selected_scenes),
            "shot_count": len(selected_shots),
            "locked": state["stage_gates"]["storyboard_scope_lock"]["status"] == "locked",
        },
        "canvas": {
            "optional_mode": True,
            "node_count": len(selected_scenes) + len(selected_shots) + len(state["assets"]),
            "source": "same_commercial_production_state",
        },
        "assets": state["assets"],
        "reference_sets": state["reference_sets"],
        "creative_profiles": state["creative_profiles"],
        "production_recipe": state["production_recipe"],
        "stage_gates": state["stage_gates"],
        "revision_requests": state["revision_requests"],
        "production_control": state["production_control"],
        "acceptance": {
            "runnable_checks": [
                "bootstrap_sample",
                "lock_storyboard_scope",
                "local_rewrite_selected_shot",
                "prove_unselected_refs_unchanged",
            ],
            "provider_dispatch_count": state["production_control"]["provider_dispatch_count"],
        },
        "non_claims": _non_claims(),
    }


def _sample_state(scope: TenantScope, *, title: str, created_at: str) -> dict[str, Any]:
    episodes = [
        {"episode_id": "ep-001", "sequence": 1, "title": "雾港来信", "logline": "失踪编剧留下能改写城市记忆的分镜本。", "outline_status": "selected"},
        {"episode_id": "ep-002", "sequence": 2, "title": "塔顶的白鹤", "logline": "主角追查旧电视塔与白鹤目击线索。", "outline_status": "planned"},
        {"episode_id": "ep-003", "sequence": 3, "title": "反向放映", "logline": "团队发现反派用候选镜头污染真实证词。", "outline_status": "planned"},
    ]
    scenes = [
        ("scene-001", 1, "雨夜码头", "建立失踪案与城市气质。"),
        ("scene-002", 2, "旧电视塔内", "发现关键道具并确认追踪方向。"),
        ("scene-003", 3, "屋顶白鹤", "角色与动物线索同框，确认连续性。"),
        ("scene-004", 4, "剪辑室反证", "锁定下一集的返工范围。"),
    ]
    shots: list[dict[str, Any]] = []
    shot_assets = {
        "scene-001": ["asset-human-lin", "asset-location-harbor"],
        "scene-002": ["asset-human-lin", "asset-prop-storyboard", "asset-location-tower"],
        "scene-003": ["asset-human-lin", "asset-animal-crane", "asset-location-roof"],
        "scene-004": ["asset-human-lin", "asset-prop-storyboard"],
    }
    beats = [
        "远景：雨幕中的雾港码头，霓虹映在积水里。",
        "中景：林澈翻开失踪编剧寄来的分镜本。",
        "特写：纸页边缘出现旧电视塔的坐标。",
        "跟拍：林澈冲入雨夜，码头灯光被风吹乱。",
        "广角：旧电视塔大厅布满被弃置的监视器。",
        "近景：分镜本自动翻到一页空白镜头。",
        "特写：红色铅笔在纸上自行画出白鹤轮廓。",
        "推镜：塔顶门缝透出冷白色的光。",
        "仰拍：白鹤停在屋顶信号架上。",
        "双人中景：林澈与白鹤隔着风声对视。",
        "插入：白鹤脚环刻着失踪编剧的编号。",
        "横移：远处广告屏短暂播放未拍摄过的画面。",
        "内景：剪辑室里候选镜头按时间线排开。",
        "特写：林澈把红色铅笔封入透明证物袋。",
        "对照：已锁镜头与异常镜头并排显示。",
        "收束：团队只批准返工第 6 镜，其余镜头保持锁定。",
    ]
    for index, beat in enumerate(beats, start=1):
        scene_id = scenes[(index - 1) // 4][0]
        shot_id = f"shot-{index:03d}"
        shot = {
            "shot_id": shot_id,
            "scene_id": scene_id,
            "sequence": index,
            "version_id": f"{shot_id}-v1",
            "revision": 1,
            "duration_seconds": 5,
            "beat": beat,
            "asset_refs": shot_assets[scene_id],
            "recipe_override": "shot-closeup-suspense" if index in {3, 6, 7, 11, 14} else "",
            "review_state": "draft",
        }
        shot["content_digest"] = _digest({"shot_id": shot_id, "beat": beat, "asset_refs": shot["asset_refs"]})
        shots.append(shot)
    scene_payloads = [
        {"scene_id": scene_id, "episode_id": "ep-001", "sequence": seq, "title": name, "purpose": purpose}
        for scene_id, seq, name, purpose in scenes
    ]
    assets = _sample_assets()
    state = {
        "schema_version": COMMERCIAL_STATE_SCHEMA,
        "version": 1,
        "created_at": created_at,
        "updated_at": created_at,
        "scope": scope.model_dump(mode="json"),
        "mode": {"primary": "storyboard", "optional": "canvas", "same_fact_source": True},
        "hierarchy": {
            "project_id": scope.project_id,
            "project_title": title,
            "ip_title": "雾港异闻录",
            "story_bible": {"bible_id": "bible-story-v1", "status": "draft", "facts": ["雾港靠影像记忆维持城市秩序。", "分镜本只能改写未锁定镜头。"]},
            "world_bible": {"bible_id": "bible-world-v1", "status": "draft", "facts": ["旧电视塔是第一季的高频场景。", "白鹤是异常记忆的视觉信号。"]},
            "arc": {"arc_id": "arc-001", "title": "失踪编剧与反向放映"},
            "volume": {"volume_id": "vol-001", "title": "雾港篇"},
        },
        "episodes": episodes,
        "selected_episode_id": "ep-001",
        "scenes": scene_payloads,
        "shots": shots,
        "assets": assets,
        "reference_sets": _reference_sets(assets),
        "creative_profiles": _creative_profiles(),
        "production_recipe": _production_recipe(),
        "stage_gates": {
            "storyboard_scope_lock": {
                "gate_id": "storyboard-scope-lock",
                "status": "unlocked",
                "locked_refs": [],
                "scope_digest": "",
                "recoverable": True,
            }
        },
        "revision_requests": [],
        "production_control": {
            "provider_dispatch_count": 0,
            "queue": {"status": "idle", "max_parallel_tasks": 2, "running": 0, "retryable": True},
            "cost": {"basis": "internal_estimate_only", "estimated_units": 0, "actual_cost_status": "not_claimed"},
            "observability": {"event_count": 1, "last_event": "sample.created"},
            "rollback": {"available": True, "scope": "state_versioned_file_with_idempotent_receipts"},
        },
        "idempotency_records": {},
    }
    return state


def _sample_assets() -> list[dict[str, Any]]:
    rows = [
        ("asset-human-lin", "human", "林澈", 0.96, "creator_brief"),
        ("asset-animal-crane", "animal", "白鹤", 0.88, "story_bible"),
        ("asset-location-harbor", "scene_location", "雾港码头", 0.92, "world_bible"),
        ("asset-location-tower", "scene_location", "旧电视塔", 0.94, "world_bible"),
        ("asset-location-roof", "scene_location", "电视塔屋顶", 0.82, "scene_outline"),
        ("asset-prop-storyboard", "prop", "红色分镜本", 0.9, "creator_brief"),
    ]
    assets: list[dict[str, Any]] = []
    for asset_id, asset_type, name, confidence, source in rows:
        assets.append(
            {
                "asset_entity_id": asset_id,
                "type": asset_type,
                "name": name,
                "recognition": {"confidence": confidence, "source_refs": [source], "needs_human_confirmation": confidence < 0.9},
                "base_identity": {"version_id": f"{asset_id}-base-v1", "locked": False, "facts": [f"{name} 的全局身份不得被镜头局部状态覆盖。"]},
                "episode_variant": {"version_id": f"{asset_id}-ep001-v1", "episode_id": "ep-001", "facts": [f"{name} 在第一集使用冷青色光影方案。"]},
                "reference_set_id": f"refset-{asset_id}",
                "generated_candidates": [{"candidate_id": f"cand-{asset_id}-001", "status": "deterministic_placeholder"}],
                "approved_version": {"version_id": f"{asset_id}-approved-v1", "status": "pending_review"},
                "shot_local_state_policy": "shot-local state cannot mutate base identity",
            }
        )
    return assets


def _reference_sets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "reference_set_id": asset["reference_set_id"],
            "asset_entity_id": asset["asset_entity_id"],
            "status": "first_class_pending_review",
            "reference_assets": [{"reference_asset_id": f"{asset['asset_entity_id']}-ref-001", "kind": "safe_manifest_ref"}],
        }
        for asset in assets
    ]


def _creative_profiles() -> list[dict[str, Any]]:
    return [
        {"profile_id": "profile-project-noir", "scope": "project", "label": "雾港悬疑", "inherits_from": "", "visible_cards": ["类型：都市悬疑", "叙事：证据推进", "画面：冷青霓虹"], "raw_prompt_exposed": False},
        {"profile_id": "profile-episode-rain", "scope": "episode", "label": "第一集雨夜", "inherits_from": "profile-project-noir", "visible_cards": ["镜头：慢推与插入特写", "动作：克制", "声音：雨声占位"], "raw_prompt_exposed": False},
        {"profile_id": "profile-scene-tower", "scope": "scene", "label": "电视塔段落", "inherits_from": "profile-episode-rain", "visible_cards": ["空间：垂直压迫", "负面约束：不改变角色身份"], "raw_prompt_exposed": False},
        {"profile_id": "shot-closeup-suspense", "scope": "shot", "label": "关键物特写", "inherits_from": "profile-scene-tower", "visible_cards": ["景别：近景/特写", "用途：局部重生成"], "raw_prompt_exposed": False},
    ]


def _production_recipe() -> dict[str, Any]:
    return {
        "recipe_id": "recipe-commercial-v1",
        "version_id": "recipe-commercial-v1.0",
        "inheritance_order": ["project", "episode", "scene", "shot"],
        "cards": {
            "genre": "都市悬疑漫剧",
            "narrative_grammar": "证据链推进，每场只解一个问题",
            "shot_language": "可审片的 5 秒镜头，优先稳定构图",
            "visual_style": "冷青霓虹 + 雨夜反光",
            "motion_audio": "镜头运动与声音只是方案，不触发生成",
            "negative_constraints": "不得漂移角色身份、场景基线和未选镜头",
            "provider_adapter": "closed until explicit provider gate",
        },
        "raw_prompt_exposed": False,
    }


def _commercial_lock(store: RuntimeStore, project_id: str):
    path = _state_path(store, project_id).with_name("commercial_production.transaction.lock")
    return exclusive_file_lock(path)


def _state_path(store: RuntimeStore, project_id: str) -> Path:
    return store.projects_dir / safe_id(project_id) / "commercial_production.json"


def _read_state(store: RuntimeStore, project_id: str) -> dict[str, Any] | None:
    path = _state_path(store, project_id)
    if not path.is_file():
        return None
    state = read_json(path)
    if state.get("schema_version") != COMMERCIAL_STATE_SCHEMA:
        raise ValueError("commercial production state schema is unsupported")
    return state


def _write_state(store: RuntimeStore, project_id: str, state: dict[str, Any]) -> None:
    if state.get("schema_version") != COMMERCIAL_STATE_SCHEMA:
        raise ValueError("commercial production state schema is unsupported")
    if state.get("scope", {}).get("project_id") != project_id:
        raise ValueError("commercial production state scope mismatch")
    reject_unsafe_payload(state)
    write_json(_state_path(store, project_id), state)


def _load_required_state(
    store: RuntimeStore,
    project_id: str,
    scope: TenantScope,
    request: Request,
) -> dict[str, Any]:
    state = _read_state(store, project_id)
    if state is None:
        _raise_commercial_error(
            request,
            project_id,
            status_code=404,
            error="commercial_production_not_found",
            message="Create the commercial production sample before running this command.",
            stage="load",
        )
    _require_state_scope(state, scope, request=request, project_id=project_id)
    return state


def _require_state_scope(
    state: dict[str, Any],
    expected: TenantScope,
    *,
    request: Request,
    project_id: str,
) -> None:
    if state.get("scope") != expected.model_dump(mode="json"):
        _raise_commercial_error(
            request,
            project_id,
            status_code=403,
            error="commercial_production_scope_mismatch",
            message="Commercial production state does not belong to this project owner.",
            stage="scope",
        )


def _require_version(state: dict[str, Any], expected: int, request: Request, project_id: str, stage: str) -> None:
    current = int(state.get("version") or 0)
    if expected != current:
        _raise_commercial_error(
            request,
            project_id,
            status_code=409,
            error="commercial_production_version_conflict",
            message=f"Commercial production version conflict: expected {expected}, current {current}.",
            stage=stage,
            retryable=True,
        )


def _idempotency_replay_or_conflict(
    state: dict[str, Any] | None,
    key: str,
    digest: str,
    request: Request,
    project_id: str,
) -> dict[str, Any] | None:
    if state is None:
        return None
    receipt = (state.get("idempotency_records") or {}).get(key)
    if receipt is None:
        return None
    if receipt.get("payload_digest") != digest:
        _raise_commercial_error(
            request,
            project_id,
            status_code=409,
            error="commercial_production_idempotency_conflict",
            message="Idempotency key was already used with a different command payload.",
            stage="idempotency",
        )
    response = deepcopy(receipt.get("response") or {})
    response["replayed"] = True
    return response


def _record_idempotency(state: dict[str, Any], key: str, digest: str, response: dict[str, Any]) -> None:
    state.setdefault("idempotency_records", {})[key] = {
        "payload_digest": digest,
        "response": deepcopy(response),
    }


def _scope_refs(state: dict[str, Any]) -> list[dict[str, str]]:
    refs = []
    for collection, key in (
        ("episodes", "episode_id"),
        ("scenes", "scene_id"),
        ("shots", "shot_id"),
        ("assets", "asset_entity_id"),
        ("creative_profiles", "profile_id"),
    ):
        refs.extend({"entity_type": collection[:-1], "entity_id": item[key]} for item in state[collection])
    refs.append({"entity_type": "recipe", "entity_id": state["production_recipe"]["recipe_id"]})
    return refs


def _protected_digest(state: dict[str, Any], selected_shot_id: str) -> str:
    protected = {
        "episodes": [item for item in state["episodes"] if item["episode_id"] != state["selected_episode_id"]],
        "scenes": state["scenes"],
        "shots": [item for item in state["shots"] if item["shot_id"] != selected_shot_id],
        "assets": state["assets"],
        "reference_sets": state["reference_sets"],
        "production_recipe": state["production_recipe"],
    }
    return _digest(protected)


def _protected_counts(state: dict[str, Any], selected_shot_id: str) -> dict[str, int]:
    return {
        "episodes": len([item for item in state["episodes"] if item["episode_id"] != state["selected_episode_id"]]),
        "scenes": len(state["scenes"]),
        "shots": len([item for item in state["shots"] if item["shot_id"] != selected_shot_id]),
        "assets": len(state["assets"]),
    }


def _shot_ref(shot: dict[str, Any]) -> dict[str, Any]:
    return {"entity_type": "shot", "entity_id": shot["shot_id"], "version_id": shot["version_id"]}


def _find_one(items: list[dict[str, Any]], field: str, value: str, request: Request, project_id: str) -> dict[str, Any]:
    for item in items:
        if item.get(field) == value:
            return item
    _raise_commercial_error(
        request,
        project_id,
        status_code=404,
        error="commercial_production_ref_not_found",
        message="The requested production reference was not found.",
        stage="reference",
    )


def _advance(state: dict[str, Any], stamp: str) -> None:
    state["version"] = int(state["version"]) + 1
    state["updated_at"] = stamp
    state["production_control"]["observability"]["event_count"] = int(
        state["production_control"]["observability"].get("event_count") or 0
    ) + 1


def _payload_digest(scope: TenantScope, payload: dict[str, Any]) -> str:
    return _digest({"scope": scope.model_dump(mode="json"), "payload": payload})


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _stamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=safe_error_detail("invalid_timestamp", message="Timestamp must be ISO-8601.")) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _non_claims() -> list[str]:
    return [
        "structure_verification_only",
        "provider_smoke_not_run",
        "generated_media_qa_not_claimed",
        "human_acceptance_not_claimed",
        "business_validation_not_claimed",
        "durable_rule_promotion_not_claimed",
    ]


def _raise_commercial_error(
    request: Request,
    project_id: str,
    *,
    status_code: int,
    error: str,
    message: str,
    stage: str,
    retryable: bool = False,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=safe_error_detail(
            error,
            message=message,
            project_id=project_id,
            request_id=getattr(request.state, "request_id", ""),
            client_request_id=getattr(request.state, "client_request_id", ""),
            action="commercial_production",
            stage=stage,
            retryable=retryable,
        ),
    )


__all__ = ("register_runtime_commercial_production_routes",)
