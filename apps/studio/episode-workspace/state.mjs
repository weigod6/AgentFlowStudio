export const PROJECTION_SCHEMA = "afs_episode_workspace_projection.v0.1";
export const AGGREGATE_SCHEMA = "afs_episode_production_aggregate.v0.1";
export const MODES = Object.freeze(["storyboard", "review", "delivery"]);
export const FILTERS = Object.freeze(["all", "blocking", "needs_review", "rework"]);

function exactRefKey(ref) {
  if (!ref || !ref.entity_type || !ref.entity_id || !ref.version_id) return "";
  return `${ref.entity_type}:${ref.entity_id}:${ref.version_id}`;
}

function sameExactRef(left, right) {
  return exactRefKey(left) !== "" && exactRefKey(left) === exactRefKey(right);
}

function latestVersions(records = []) {
  const heads = new Map();
  for (const record of records) {
    const current = heads.get(record.entity_id);
    if (!current || Number(record.revision) > Number(current.revision)) heads.set(record.entity_id, record);
  }
  return [...heads.values()];
}

function assertProjection(payload) {
  if (payload?.schema_version !== PROJECTION_SCHEMA) throw new Error("工作区数据版本不一致。");
  if (payload?.aggregate?.schema_version !== AGGREGATE_SCHEMA) throw new Error("项目事实版本不一致。");
  if (!payload.workspace || !Array.isArray(payload.workspace.scenes) || !Array.isArray(payload.workspace.shots)) {
    throw new Error("工作区数据不完整。");
  }
  const aggregate = payload.aggregate;
  const shotRefs = new Set(aggregate.shots.map(exactRefKey));
  const sceneRefs = new Set(aggregate.scenes.map(exactRefKey));
  for (const scene of payload.workspace.scenes) {
    if (!sceneRefs.has(exactRefKey(scene.ref))) throw new Error("场景展示数据与项目事实不一致。");
  }
  for (const shot of payload.workspace.shots) {
    if (!shotRefs.has(exactRefKey(shot.ref))) throw new Error("镜头展示数据与项目事实不一致。");
    if (!sceneRefs.has(exactRefKey(shot.scene_ref))) throw new Error("镜头场景引用不可用。");
  }
  const nextAction = payload.workspace.next_action;
  const nextShotRef = nextAction?.shot_ref || nextAction?.subject_ref;
  if (nextAction && !shotRefs.has(exactRefKey(nextShotRef))) {
    throw new Error("建议下一步没有对应到有效镜头。");
  }
  return payload;
}

export function buildWorkspaceModel(payload) {
  assertProjection(payload);
  const workspace = payload.workspace;
  const projects = latestVersions(payload.aggregate.projects);
  const episodes = latestVersions(payload.aggregate.episodes);
  const project = projects.find((item) => item.entity_id === payload.aggregate.scope.project_id) || projects[0];
  const episode = episodes.find((item) => sameExactRef(item, workspace.episode_ref)) || episodes[0];
  const scenes = [...workspace.scenes].sort((a, b) => a.sequence - b.sequence);
  const shots = [...workspace.shots].sort((a, b) => a.sequence - b.sequence);
  return Object.freeze({
    aggregateVersion: payload.aggregate.aggregate_version,
    evaluatedAt: payload.aggregate.evaluated_at,
    project: {
      title: project?.title || "未命名项目",
      policy: project?.data_policy || {},
    },
    episode: { title: episode?.title || "未命名单集", ref: workspace.episode_ref },
    scenes,
    shots,
    nextAction: workspace.next_action,
    recovery: workspace.recovery || null,
    truth: workspace.truth || {},
    delivery: workspace.delivery || {},
    evidenceEnvironment: workspace.evidence_environment || null,
  });
}

export function createInitialUiState(model, savedState = null) {
  const saved = savedState && sameExactRef(savedState.episode_ref, model.episode.ref) ? savedState : null;
  const suggestedRef = model.nextAction?.shot_ref || model.nextAction?.subject_ref;
  const suggestedShot = model.shots.find((shot) => sameExactRef(shot.ref, suggestedRef));
  const savedShot = model.shots.find((shot) => sameExactRef(shot.ref, saved?.active_shot_ref));
  const initialShot = savedShot || suggestedShot || model.shots[0] || null;
  const recoveredMode = MODES.includes(saved?.mode) ? saved.mode : "storyboard";
  const pendingCommand = saved?.pending_command && typeof saved.pending_command === "object"
    ? saved.pending_command
    : null;
  return Object.freeze({
    mode: recoveredMode,
    activeShotKey: exactRefKey(initialShot?.ref),
    nextShotKey: exactRefKey(suggestedShot?.ref),
    sceneFilterKey: "all",
    statusFilter: "all",
    inspectorSection: String(saved?.inspector_section || "overview"),
    focusedControl: String(saved?.focused_control || ""),
    scrollTop: Math.max(0, Number(saved?.scroll_top || 0)),
    pendingIdempotencyKey: String(pendingCommand?.idempotency_key || ""),
    pendingCommand,
  });
}

export function inspectShot(state, shotRef) {
  return Object.freeze({ ...state, activeShotKey: exactRefKey(shotRef), inspectorSection: "overview" });
}

export function selectMode(state, mode) {
  if (!MODES.includes(mode)) return state;
  return Object.freeze({ ...state, mode });
}

export function selectSceneFilter(state, sceneRef) {
  return Object.freeze({ ...state, sceneFilterKey: sceneRef === "all" ? "all" : exactRefKey(sceneRef) });
}

export function selectStatusFilter(state, filter) {
  if (!FILTERS.includes(filter)) return state;
  return Object.freeze({ ...state, statusFilter: filter });
}

export function activeShot(model, state) {
  return model.shots.find((shot) => exactRefKey(shot.ref) === state.activeShotKey) || null;
}

export function nextShot(model, state) {
  return model.shots.find((shot) => exactRefKey(shot.ref) === state.nextShotKey) || null;
}

export function updateUiRecovery(state, patch = {}) {
  return Object.freeze({ ...state, ...patch });
}

export function focusIfAvailable(target, options = { preventScroll: true }) {
  if (!target || typeof target.focus !== "function") return false;
  target.focus(options);
  return true;
}

export function retainPendingCommandAfterFailure(kind, commandDispatched = true) {
  const failure = String(kind || "");
  if (failure === "invalid") return false;
  if (failure === "stale") return !commandDispatched;
  return commandDispatched && ["server", "auth"].includes(failure);
}

export function episodeWorkspaceState(model, state) {
  const active = activeShot(model, state);
  return {
    schema_version: "afs_episode_workspace_ui.v0.1",
    episode_ref: model.episode.ref,
    active_shot_ref: active?.ref || null,
    mode: state.mode,
    focused_control: state.focusedControl || "",
    inspector_section: state.inspectorSection || "overview",
    scroll_top: Math.max(0, Number(state.scrollTop || 0)),
    pending_idempotency_key: state.pendingIdempotencyKey || "",
    pending_command: state.pendingCommand || null,
  };
}

export function mergeEpisodeWorkspaceState(studioState, model, state) {
  return {
    ...(studioState && typeof studioState === "object" ? studioState : {}),
    episode_workspace: episodeWorkspaceState(model, state),
  };
}

export function visibleShots(model, state) {
  return model.shots.filter((shot) => {
    if (state.sceneFilterKey !== "all" && exactRefKey(shot.scene_ref) !== state.sceneFilterKey) return false;
    if (state.statusFilter === "blocking" && !shot.blocking) return false;
    if (state.statusFilter === "needs_review" && shot.review_state !== "needs_review") return false;
    if (state.statusFilter === "rework" && shot.production_state !== "rework") return false;
    return true;
  });
}

export function groupShotsByScene(model, shots) {
  return model.scenes.map((scene) => ({
    scene,
    shots: shots.filter((shot) => sameExactRef(shot.scene_ref, scene.ref)),
  })).filter((group) => group.shots.length > 0);
}

export function availableAction(shot, actionName) {
  const action = shot?.allowed_actions?.find((item) => item.action === actionName);
  if (!action) return { enabled: false, reason: "当前状态不允许这项操作。", blockedBy: [] };
  const blockedBy = Array.isArray(action.blocked_by) ? action.blocked_by : [];
  return {
    enabled: action.enabled === true && blockedBy.length === 0,
    reason: action.reason || (blockedBy.length ? "请先完成前置问题。" : ""),
    blockedBy,
  };
}

export function shouldShowCurrentVersusNext(state) {
  return state.activeShotKey !== state.nextShotKey;
}

export function stateSummary(model, state) {
  const active = activeShot(model, state);
  const next = nextShot(model, state);
  return {
    active_sequence: active?.sequence ?? null,
    next_sequence: next?.sequence ?? null,
    mode: state.mode,
    visible_shot_count: visibleShots(model, state).length,
    current_differs_from_next: shouldShowCurrentVersusNext(state),
  };
}

export { exactRefKey, sameExactRef };
