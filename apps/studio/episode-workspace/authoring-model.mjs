export function refKey(ref) {
  return ref ? `${ref.entity_type}:${ref.entity_id}:${ref.version_id}` : "";
}

export function stableKey(ref) {
  return ref ? `${ref.entity_type}:${ref.entity_id}` : "";
}

export function sameStableRef(left, right) {
  return Boolean(left && right && left.entity_type === right.entity_type && left.entity_id === right.entity_id);
}

export function createAuthoringUi(model, saved = {}) {
  const episodes = model.episodes || [];
  const firstEpisode = episodes[0] || null;
  const selectedEpisode = episodes.find((item) => stableKey(item.ref) === saved.selected_episode) || firstEpisode;
  const shots = shotsForEpisode(model, selectedEpisode?.ref);
  const selectedShot = shots.find((item) => stableKey(item.ref) === saved.selected_shot) || shots[0] || null;
  return {
    mode: saved.mode === "canvas" ? "canvas" : "storyboard",
    selectedEpisode: selectedEpisode?.ref || null,
    selectedShot: selectedShot?.ref || null,
    selectedSection: saved.selected_section || stableKey(model.project?.ref),
    mobileInspectorOpen: saved.mobile_inspector_open === true,
    technicalOpen: saved.technical_open === true,
    pendingCommand: saved.pending_command || null,
    pendingFailure: saved.pending_failure || "",
  };
}

export function uiPreference(ui) {
  return {
    mode: ui.mode,
    selected_episode: stableKey(ui.selectedEpisode),
    selected_shot: stableKey(ui.selectedShot),
    selected_section: ui.selectedSection,
    mobile_inspector_open: ui.mobileInspectorOpen,
    technical_open: ui.technicalOpen,
    pending_command: ui.pendingCommand,
    pending_failure: ui.pendingFailure,
  };
}

export function currentEpisode(model, ui) {
  return model.episodes.find((item) => sameStableRef(item.ref, ui.selectedEpisode)) || model.episodes[0] || null;
}

export function scenesForEpisode(model, episodeRef) {
  return (model.scenes || [])
    .filter((scene) => sameStableRef(scene.episode_ref, episodeRef))
    .sort((left, right) => left.sequence - right.sequence);
}

export function shotsForScene(model, sceneRef) {
  return (model.shots || [])
    .filter((shot) => sameStableRef(shot.scene_ref, sceneRef))
    .sort((left, right) => left.sequence - right.sequence);
}

export function shotsForEpisode(model, episodeRef) {
  const sceneIds = new Set(scenesForEpisode(model, episodeRef).map((scene) => scene.ref.entity_id));
  return (model.shots || [])
    .filter((shot) => sceneIds.has(shot.scene_ref.entity_id))
    .sort((left, right) => left.sequence - right.sequence);
}

export function currentShot(model, ui) {
  return (model.shots || []).find((item) => sameStableRef(item.ref, ui.selectedShot)) || null;
}

export function approvedReferenceSets(model) {
  return (model.reference_sets || []).filter((item) => item.approval_state === "approved");
}

export function validPendingEnvelope(value) {
  if (!value || typeof value !== "object") return false;
  if (value.schema_version !== "afs_creator_pending_command.v0.1") return false;
  if (Object.keys(value).sort().join(",") !== "command,idempotency_key,schema_version,status") return false;
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$/.test(String(value.idempotency_key || ""))) return false;
  if (!value.command || typeof value.command !== "object") return false;
  if (!String(value.command.action || "").includes(".")) return false;
  if (!Number.isInteger(value.command.expected_aggregate_version)) return false;
  return value.status === "pending" || value.status === "failed";
}
