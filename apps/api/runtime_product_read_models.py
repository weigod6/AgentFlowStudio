from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from agentflow_studio.production.representative_episode_media import (
    RepresentativeEpisodeMediaError,
    revalidate_authoritative_media,
    safe_media_projection,
)
from apps.api.runtime_auth import RuntimeAuthStore
from apps.api.runtime_production_models import canonical_json_digest, checkpoint_digest
from apps.api.runtime_store import RuntimeStore


ROLE_LABELS = {
    "screenwriter": "编剧组",
    "storyboard": "分镜组",
    "art": "美术组",
    "director": "导演组",
    "continuity": "连贯性检查",
    "qa": "质量审核",
    "audio": "音频组",
    "edit": "后期组",
    "export": "交付组",
}
ROLE_ORDER = tuple(ROLE_LABELS)
LOCAL_RUNTIME_USER_ID = "local-runtime-user"

STAGES = (
    ("brief", "创作简报"),
    ("script", "剧本"),
    ("canon", "角色/场景设定"),
    ("storyboard", "分镜"),
    ("visual", "画面"),
    ("av", "视频/音频"),
    ("decision", "主创决策"),
    ("quality", "质量审核"),
    ("delivery", "交付"),
)


def register_runtime_product_read_model_routes(
    app: FastAPI,
    store: RuntimeStore,
    auth: RuntimeAuthStore,
) -> None:
    @app.get("/product/workspace-overview", tags=["product"])
    def workspace_overview(request: Request) -> dict[str, Any]:
        user_id = _require_user(auth, request)
        summaries = store.list_project_summaries()
        if auth.enabled():
            summaries = auth.filter_project_summaries(user_id, summaries)
        projects = [
            _project_overview(store, summary, user_id=user_id, owner_scoped=auth.enabled())
            for summary in summaries
        ]
        projects.sort(key=lambda item: (str(item.get("updated_at") or ""), item["project_id"]), reverse=True)
        return {
            "schema_version": "afs_product_workspace_overview.v0.1",
            "locale": "zh-CN",
            "workspace": {
                "label": "内容制作工作空间",
                "project_count": len(projects),
                "active_project_count": sum(item["status"] != "已交付" for item in projects),
            },
            "projects": projects,
            "decision_count": sum(item["decision_inbox"]["pending_count"] for item in projects),
            "blocked_count": sum(item["crew"]["blocked_count"] for item in projects),
        }

    @app.get("/projects/{project_id}/product-overview", tags=["product"])
    def project_overview(project_id: str, request: Request) -> dict[str, Any]:
        user_id = _require_project_owner(store, auth, request, project_id)
        summary = next(
            (item for item in store.list_project_summaries() if str(item.get("project_id") or "") == project_id),
            None,
        )
        if not summary:
            raise HTTPException(status_code=404, detail="项目不存在或已被移除。")
        return {
            "schema_version": "afs_product_project_overview.v0.1",
            "locale": "zh-CN",
            "project": _project_overview(store, summary, user_id=user_id, expanded=True, owner_scoped=auth.enabled()),
        }


def _require_user(auth: RuntimeAuthStore, request: Request) -> str:
    if not auth.enabled():
        return LOCAL_RUNTIME_USER_ID
    user_id = str(auth.require_user(request).get("user_id") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录。")
    return user_id


def _require_project_owner(
    store: RuntimeStore,
    auth: RuntimeAuthStore,
    request: Request,
    project_id: str,
) -> str:
    user_id = _require_user(auth, request)
    if (
        not project_id
        or store.is_project_deleted(project_id)
        or not store.project_manifest_path(project_id).is_file()
        or (auth.enabled() and not auth.user_can_access_project(user_id, project_id))
    ):
        raise HTTPException(status_code=403, detail="你没有访问该项目的权限。")
    return user_id


def _project_overview(
    store: RuntimeStore,
    summary: dict[str, Any],
    *,
    user_id: str,
    expanded: bool = False,
    owner_scoped: bool = True,
) -> dict[str, Any]:
    project_id = str(summary.get("project_id") or "")
    manifest = store.ensure_project_manifest(project_id)
    crew = _owned_crew(store, project_id, user_id, owner_scoped=owner_scoped)
    runs = [
        item for item in store.list_production_runs(project_id)
        if not owner_scoped or str(item.get("owner_user_id") or "") == user_id
    ]
    latest_run = max(runs, key=lambda item: str(item.get("updated_at") or ""), default={})
    studio_meta = _studio_meta(store, project_id)
    coverage = _canonical_coverage(store, project_id, latest_run)
    decisions = _decision_inbox(crew, latest_run)
    crew_summary = _crew_summary(crew)
    delivery = _delivery_summary(latest_run)
    jobs = _job_summary(store, project_id)
    stages = _stage_progress(manifest, crew, latest_run, coverage, decisions, delivery)
    completed = sum(item["state"] == "completed" for item in stages)
    progress = round((completed / len(stages)) * 100) if stages else 0
    result: dict[str, Any] = {
        "project_id": project_id,
        "name": str(studio_meta.get("projectName") or summary.get("goal") or "未命名项目"),
        "episode": _episode_label(latest_run),
        "status": _localized_project_status(str(summary.get("status") or ""), delivery),
        "updated_at": str(studio_meta.get("updated_at") or latest_run.get("updated_at") or ""),
        "progress_percent": progress,
        "current_stage": next((item["label"] for item in stages if item["state"] == "in_progress"), "待开始"),
        "next_action": _next_action(stages, decisions, crew_summary),
        "decision_inbox": decisions,
        "crew": crew_summary,
        "canonical_state": coverage,
        "delivery": delivery,
        "jobs": jobs,
    }
    if expanded:
        result["stages"] = stages
        result["recovery"] = {
            "reload_safe": True,
            "last_saved_at": str(studio_meta.get("saved_at") or latest_run.get("updated_at") or ""),
            "message": "项目状态来自已登录账户的持久化记录。",
        }
    return result


def _owned_crew(store: RuntimeStore, project_id: str, user_id: str, *, owner_scoped: bool = True) -> dict[str, Any]:
    try:
        crew = store.load_domain_crew(project_id)
    except KeyError:
        return {}
    if owner_scoped and str(crew.get("owner_user_id") or "") != user_id:
        return {}
    return crew


def _studio_meta(store: RuntimeStore, project_id: str) -> dict[str, Any]:
    path = store.projects_dir / project_id / "studio_state.json"
    if not path.is_file():
        return {}
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    return {
        "projectName": _safe_text(meta.get("projectName"), 100),
        "updated_at": _safe_text(meta.get("updated_at"), 80),
        "saved_at": _safe_text(payload.get("saved_at"), 80),
    }


def _decision_inbox(crew: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    conflicts = [item for item in crew.get("conflicts") or [] if isinstance(item, dict)]
    pending_conflicts = [item for item in conflicts if str(item.get("status") or "") not in {"resolved_by_creator", "closed"}]
    reconfirmations = [item for item in crew.get("propagation_reconfirmations") or [] if isinstance(item, dict)]
    pending_reconfirmations = [item for item in reconfirmations if item.get("reconfirmation_status") == "required_pending"]
    selected = run.get("selected_revision") if isinstance(run.get("selected_revision"), dict) else {}
    candidates = [item for item in run.get("candidates") or [] if isinstance(item, dict)]
    candidate_review_pending = bool(candidates and not selected)
    items = [
        {
            "kind": "creator_decision",
            "title": _safe_text(item.get("reason"), 120) or "创作冲突需要主创决定",
            "priority": "high",
            "action_label": "查看影响",
        }
        for item in pending_conflicts[:4]
    ]
    if candidate_review_pending:
        items.append({
            "kind": "candidate_review",
            "title": "候选版本等待主创审核",
            "priority": "normal",
            "action_label": "进入审核",
        })
    return {
        "pending_count": len(items),
        "reconfirmation_count": len(pending_reconfirmations),
        "items": items,
    }


def _crew_summary(crew: dict[str, Any]) -> dict[str, Any]:
    agents = [item for item in crew.get("agents") or [] if isinstance(item, dict)]
    tasks = [item for item in crew.get("tasks") or [] if isinstance(item, dict)]
    handoffs = [item for item in crew.get("handoffs") or [] if isinstance(item, dict)]
    blocked_states = {"blocked", "blocked_human", "revision_required", "reconfirmation_required"}
    blocked = [item for item in tasks if str(item.get("status") or "") in blocked_states]
    active = [item for item in tasks if str(item.get("status") or "") in {"ready", "claimed", "reconfirmation_required"}]
    role_by_agent = {str(item.get("agent_id") or ""): str(item.get("role") or "") for item in agents}
    agent_by_role = {
        str(item.get("role") or ""): item
        for item in agents
        if str(item.get("role") or "") in ROLE_LABELS
    }
    arbitrations = [item for item in crew.get("arbitrations") or [] if isinstance(item, dict)]
    latest_arbitration = arbitrations[-1] if arbitrations else {}
    latest_conflict_id = str(latest_arbitration.get("conflict_id") or "")
    approved_version_id = str(latest_arbitration.get("selected_version_id") or "")
    task_by_role: dict[str, dict[str, Any]] = {}
    for task in tasks:
        role = role_by_agent.get(str(task.get("assigned_agent_id") or ""), "")
        agent = agent_by_role.get(role)
        if not agent or task.get("assigned_agent_id") != agent.get("agent_id"):
            continue
        task_by_role[role] = task
    reconfirmation_by_role = {
        str(item.get("responsible_agent_role") or ""): item
        for item in crew.get("propagation_reconfirmations") or []
        if isinstance(item, dict)
        and latest_conflict_id
        and item.get("arbitration_conflict_id") == latest_conflict_id
        and str(item.get("responsible_agent_role") or "") in ROLE_LABELS
        and item.get("responsible_agent_id") == agent_by_role.get(str(item.get("responsible_agent_role") or ""), {}).get("agent_id")
    }
    activities = []
    for task in active[:6]:
        role = role_by_agent.get(str(task.get("assigned_agent_id") or ""), "")
        activities.append({
            "role": ROLE_LABELS.get(role, "制作助手"),
            "responsibility": _safe_text(task.get("objective"), 140) or "等待下一步制作任务",
            "state": _localized_task_status(str(task.get("status") or "")),
        })
    responsibilities = []
    for role in ROLE_ORDER:
        task = task_by_role.get(role)
        if not task:
            continue
        reconfirmation = reconfirmation_by_role.get(role)
        reconfirmed = bool(reconfirmation and reconfirmation.get("reconfirmation_status") == "reconfirmed")
        pending = bool(reconfirmation and reconfirmation.get("reconfirmation_status") == "required_pending")
        if reconfirmed:
            propagation_state = "已按批准版本重确认"
        elif pending:
            propagation_state = "等待责任人重确认"
        elif role == "screenwriter" and str(task.get("version_id") or ""):
            propagation_state = "主创决定后已恢复"
        else:
            propagation_state = "等待版本传播"
        responsibilities.append({
            "role": ROLE_LABELS[role],
            "responsibility": _safe_text(task.get("objective"), 140) or "等待下一步制作任务",
            "state": _localized_task_status(str(task.get("status") or "")),
            "approved_version": _localized_version(task.get("version_id")),
            "propagation_state": propagation_state,
            "reconfirmed": reconfirmed,
            "pending_reconfirmation": pending,
        })
    pending_reconfirmations = sum(item["pending_reconfirmation"] for item in responsibilities)
    reconfirmed_responsibilities = sum(item["reconfirmed"] for item in responsibilities)
    authoritative_version_complete = bool(
        approved_version_id
        and len(responsibilities) == 9
        and all(task_by_role[role].get("version_id") == approved_version_id for role in ROLE_ORDER)
    )
    return {
        "registered_role_count": len({item.get("role") for item in agents if item.get("role")}),
        "active_count": len(active),
        "blocked_count": len(blocked),
        "pending_handoff_count": sum(item.get("status") == "pending_receiver" for item in handoffs),
        "activities": activities,
        "episode_execution": {
            "role_count": len(responsibilities),
            "approved_version": _localized_version(approved_version_id),
            "pending_reconfirmation_count": pending_reconfirmations,
            "reconfirmed_count": reconfirmed_responsibilities,
            "propagation_complete": (
                latest_arbitration.get("propagation_complete") is True
                and authoritative_version_complete
                and reconfirmed_responsibilities == 8
            ),
            "responsibilities": responsibilities,
        },
    }


def _canonical_coverage(store: RuntimeStore, project_id: str, run: dict[str, Any]) -> dict[str, Any]:
    empty = {
        "status_label": "0/15",
        "episode_title": "",
        "episode_version_id": "",
        "package_sha256": "",
        "canon_digest": "",
        "checkpoint_version": 0,
        "duration_seconds": 0,
        "characters": 0,
        "scenes": 0,
        "shots": 0,
        "audio_items": 0,
        "character_versions": [],
        "scene_versions": [],
        "timeline": [],
        "audio": {
            "covered_shot_count": 0,
            "total_shot_count": 15,
            "pending_asset_count": 0,
            "all_audio_ready": False,
            "status": "尚未绑定",
        },
        "pending_media_count": 0,
        "all_assets_ready": False,
        "propagation_complete": False,
        "readiness": "尚未绑定本集制作规范",
    }
    checkpoint = run.get("checkpoint") if isinstance(run.get("checkpoint"), dict) else {}
    if not checkpoint or str(checkpoint.get("state_digest") or "") != checkpoint_digest(run):
        return empty
    binding = run.get("representative_episode_binding") if isinstance(run.get("representative_episode_binding"), dict) else {}
    counts = binding.get("counts") if isinstance(binding.get("counts"), dict) else {}
    readiness = binding.get("asset_readiness") if isinstance(binding.get("asset_readiness"), dict) else {}
    canon = binding.get("episode_canon") if isinstance(binding.get("episode_canon"), dict) else {}
    shots = [item for item in canon.get("shots") or [] if isinstance(item, dict)]
    characters = [item for item in canon.get("characters") or [] if isinstance(item, dict)]
    scenes = [item for item in canon.get("scenes") or [] if isinstance(item, dict)]
    audio = canon.get("audio") if isinstance(canon.get("audio"), dict) else {}
    if (
        not canon
        or str(binding.get("canon_digest") or "") != canonical_json_digest(canon)
        or int(counts.get("characters") or 0) != 3
        or int(counts.get("scenes") or 0) != 3
        or int(counts.get("shots") or 0) != 15
        or int(counts.get("audio_items") or 0) != 4
        or len(characters) != 3
        or len(scenes) != 3
        or len(shots) != 15
    ):
        return empty
    for index, shot in enumerate(shots, start=1):
        if (
            int(shot.get("ordinal") or 0) != index
            or str(shot.get("entity_id") or "") != f"shot-{index:03d}"
            or shot.get("start_seconds") != (index - 1) * 9
            or shot.get("end_seconds") != index * 9
        ):
            return empty
    scene_names = {
        str(item.get("entity_id") or ""): _safe_text(item.get("name"), 80)
        for item in scenes
    }
    character_names = {
        str(item.get("entity_id") or ""): _safe_text(item.get("name"), 80)
        for item in characters
    }
    timeline = []
    for shot in shots:
        media = shot.get("asset_readiness") if isinstance(shot.get("asset_readiness"), dict) else {}
        audio_coverage = shot.get("audio_coverage") if isinstance(shot.get("audio_coverage"), dict) else {}
        scene_ref = shot.get("scene_ref") if isinstance(shot.get("scene_ref"), dict) else {}
        dialogue = [item for item in shot.get("dialogue") or [] if isinstance(item, dict)]
        character_refs = [item for item in shot.get("character_refs") or [] if isinstance(item, dict)]
        timeline.append({
            "shot_number": int(shot["ordinal"]),
            "label": f"第 {int(shot['ordinal']):02d} 镜",
            "version_id": _safe_text(shot.get("current_approved_version_id"), 160),
            "start_seconds": int(shot["start_seconds"]),
            "end_seconds": int(shot["end_seconds"]),
            "scene": scene_names.get(str(scene_ref.get("entity_id") or ""), "场景待确认"),
            "characters": [
                character_names.get(str(item.get("entity_id") or ""), "角色待确认")
                for item in character_refs
            ],
            "visual_action": _safe_text(shot.get("visual_action"), 500),
            "dialogue": [
                {
                    "speaker": (
                        "旁白" if str(item.get("speaker_ref") or "") == "narrator"
                        else character_names.get(str(item.get("speaker_ref") or ""), "角色")
                    ),
                    "text": _safe_text(item.get("text"), 240),
                }
                for item in dialogue
            ],
            "camera": _safe_text(shot.get("camera"), 240),
            "motion": _safe_text(shot.get("motion"), 240),
            "continuity": _safe_text(shot.get("continuity_note"), 500),
            "media": {
                "required_count": int(media.get("required_count") or 0),
                "ready_count": int(media.get("ready_count") or 0),
                "pending_count": int(media.get("pending_media_count") or 0),
                "all_ready": media.get("all_required_assets_ready") is True,
                "status": "素材已齐" if media.get("all_required_assets_ready") is True else "素材待补齐",
            },
            "audio": {
                "covered": audio_coverage.get("covered") is True,
                "pending_asset_count": int(audio_coverage.get("pending_audio_asset_count") or 0),
                "status": "音频已齐" if audio_coverage.get("status") == "ready" else "音频待制作",
            },
        })
    audio_readiness = audio.get("readiness") if isinstance(audio.get("readiness"), dict) else {}
    result = {
        "status_label": "15/15",
        "episode_title": _safe_text(canon.get("episode_title"), 160),
        "episode_version_id": _safe_text(binding.get("episode_version_id"), 160),
        "package_sha256": _safe_text(binding.get("package_sha256"), 64),
        "canon_digest": _safe_text(binding.get("canon_digest"), 64),
        "checkpoint_version": int(checkpoint.get("version") or 0),
        "duration_seconds": int(canon.get("duration_seconds") or 0),
        "characters": int(counts.get("characters") or 0),
        "scenes": int(counts.get("scenes") or 0),
        "shots": int(counts.get("shots") or 0),
        "audio_items": int(counts.get("audio_items") or 0),
        "character_versions": [
            {
                "name": _safe_text(item.get("name"), 80),
                "version_id": _safe_text(item.get("current_approved_version_id"), 160),
                "continuity": [_safe_text(value, 240) for value in item.get("continuity_constraints") or []],
            }
            for item in characters
        ],
        "scene_versions": [
            {
                "name": _safe_text(item.get("name"), 80),
                "version_id": _safe_text(item.get("current_approved_version_id"), 160),
                "continuity": [_safe_text(value, 240) for value in item.get("style_constraints") or []],
            }
            for item in scenes
        ],
        "timeline": timeline,
        "audio": {
            "covered_shot_count": len(audio.get("coverage_shot_refs") or []),
            "total_shot_count": 15,
            "pending_asset_count": int(audio_readiness.get("pending_count") or 0),
            "all_audio_ready": audio_readiness.get("all_audio_ready") is True,
            "status": "音频已齐" if audio_readiness.get("all_audio_ready") is True else "音频待制作",
        },
        "pending_media_count": int(readiness.get("pending_media_count") or 0),
        "all_assets_ready": readiness.get("all_assets_ready") is True,
        "propagation_complete": binding.get("propagation_complete") is True,
        "readiness": "制作素材已齐" if readiness.get("all_assets_ready") is True else "制作素材待补齐",
    }
    media = run.get("representative_episode_media")
    media_projection = safe_media_projection(None)
    if isinstance(media, dict):
        media_root = (
            store.production_run_path(project_id, str(run.get("run_id") or "")).parent
            / "representative_episode_media"
        )
        try:
            revalidate_authoritative_media(binding, media, media_root)
        except RepresentativeEpisodeMediaError:
            media_projection = {
                **safe_media_projection(None),
                "status": "blocked",
                "continuity_status": "blocked",
            }
        else:
            media_projection = safe_media_projection(media)
    result["media_delivery"] = media_projection
    if media_projection.get("accepted_count") == 25:
        result["pending_media_count"] = 0
        result["all_assets_ready"] = True
        result["readiness"] = "25/25 制作素材已接纳"
        result["audio"] = {
            "covered_shot_count": 15,
            "total_shot_count": 15,
            "pending_asset_count": 0,
            "all_audio_ready": True,
            "status": "音频已齐",
        }
        for item in result["timeline"]:
            item["media"] = {
                "required_count": int(item["media"]["required_count"]),
                "ready_count": int(item["media"]["required_count"]),
                "pending_count": 0,
                "all_ready": True,
                "status": "素材已齐",
            }
            item["audio"] = {
                "covered": True,
                "pending_asset_count": 0,
                "status": "音频已齐",
            }
    return result


def _delivery_summary(run: dict[str, Any]) -> dict[str, Any]:
    reviews = [item for item in run.get("quality_reviews") or [] if isinstance(item, dict)]
    exports = [item for item in run.get("exports") or [] if isinstance(item, dict)]
    selected = run.get("selected_revision") if isinstance(run.get("selected_revision"), dict) else {}
    approved = bool(reviews and str(reviews[-1].get("decision") or reviews[-1].get("status") or "") in {"approve", "approved", "passed"})
    return {
        "candidate_selected": bool(selected),
        "quality_reviewed": bool(reviews),
        "quality_approved": approved,
        "export_ready": bool(selected and approved),
        "delivered": bool(exports),
        "export_count": len(exports),
        "message": "已生成交付记录" if exports else "等待质量审核与主创批准" if selected else "等待选择候选版本",
    }


def _job_summary(store: RuntimeStore, project_id: str) -> dict[str, Any]:
    jobs = store.list_project_jobs(project_id)
    running = sum(str(item.get("status") or "") in {"queued", "running", "polling"} for item in jobs)
    failed = sum(str(item.get("status") or "") in {"failed", "cancelled"} for item in jobs)
    return {
        "total_count": len(jobs),
        "running_count": running,
        "attention_count": failed,
        "cost_observability": "unavailable",
        "cost_message": "供应商成本以可核对账单为准。",
    }


def _stage_progress(
    manifest: dict[str, Any],
    crew: dict[str, Any],
    run: dict[str, Any],
    coverage: dict[str, Any],
    decisions: dict[str, Any],
    delivery: dict[str, Any],
) -> list[dict[str, str]]:
    tasks = [item for item in crew.get("tasks") or [] if isinstance(item, dict)]
    actions = {str(item.get("action") or ""): str(item.get("status") or "") for item in tasks}
    completed_actions = {key for key, status in actions.items() if status == "completed"}
    started_actions = set(actions)
    candidate_ready = bool(run.get("candidates"))
    # A project name is not a completed creative brief. New projects should
    # start at the brief instead of reporting invented progress.
    brief_evidence = bool(
        manifest.get("source_assets")
        or manifest.get("content_cards")
        or tasks
        or run
    )
    completed = {
        "brief": brief_evidence,
        "script": "script.write" in completed_actions,
        "canon": bool(coverage["characters"] or coverage["scenes"]),
        "storyboard": "storyboard.compose" in completed_actions or coverage["shots"] > 0,
        "visual": candidate_ready,
        "av": "edit.assemble" in completed_actions,
        "decision": decisions["pending_count"] == 0 and delivery["candidate_selected"],
        "quality": delivery["quality_reviewed"],
        "delivery": delivery["delivered"],
    }
    started = {
        "script": "script.write" in started_actions,
        "canon": "art.create" in started_actions,
        "storyboard": "storyboard.compose" in started_actions,
        "visual": candidate_ready,
        "av": bool({"audio.produce", "edit.assemble"} & started_actions),
        "decision": decisions["pending_count"] > 0 or delivery["candidate_selected"],
        "quality": delivery["quality_reviewed"],
        "delivery": delivery["export_ready"],
    }
    first_open_seen = False
    result = []
    for key, label in STAGES:
        if completed.get(key):
            state = "completed"
        elif started.get(key) or not first_open_seen:
            state = "in_progress"
            first_open_seen = True
        else:
            state = "not_started"
        result.append({"key": key, "label": label, "state": state})
    return result


def _next_action(stages: list[dict[str, str]], decisions: dict[str, Any], crew: dict[str, Any]) -> str:
    if decisions["pending_count"]:
        return f"处理 {decisions['pending_count']} 项主创决策"
    if crew["blocked_count"]:
        return f"解除 {crew['blocked_count']} 项制作阻塞"
    current = next((item for item in stages if item["state"] == "in_progress"), None)
    return f"继续{current['label']}" if current else "检查交付准备度"


def _episode_label(run: dict[str, Any]) -> str:
    binding = run.get("representative_episode_binding") if isinstance(run.get("representative_episode_binding"), dict) else {}
    return _safe_text(binding.get("episode_title"), 100) or "第 01 集"


def _localized_project_status(status: str, delivery: dict[str, Any]) -> str:
    if delivery["delivered"]:
        return "已交付"
    return {
        "completed": "已完成",
        "in_progress": "制作中",
        "blocked": "有阻塞",
        "draft": "准备中",
    }.get(status, "制作中")


def _localized_task_status(status: str) -> str:
    return {
        "ready": "待开始",
        "claimed": "进行中",
        "completed": "已完成",
        "revision_required": "待修改",
        "reconfirmation_required": "待重新确认",
    }.get(status, "待处理")


def _localized_version(value: Any) -> str:
    text = _safe_text(value, 160)
    marker = text.rsplit("-v", 1)
    if len(marker) == 2 and marker[1].isdigit():
        return f"第 {int(marker[1])} 版"
    return "当前批准版本"


def _safe_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


__all__ = ("register_runtime_product_read_model_routes",)
