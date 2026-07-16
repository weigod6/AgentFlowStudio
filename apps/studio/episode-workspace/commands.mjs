function exactRef(value) {
  const ref = value?.ref || value;
  if (!ref?.entity_type || !ref?.entity_id || !ref?.version_id) throw new Error("缺少精确版本引用。");
  return { entity_type: ref.entity_type, entity_id: ref.entity_id, version_id: ref.version_id };
}

export function newCommandIdentity(prefix = "episode") {
  const random = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID().replaceAll("-", "")
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random}`.slice(0, 110);
}

export function commandTimestamp(evaluatedAt, offsetMs = 1) {
  const evaluated = Date.parse(evaluatedAt || "") || 0;
  return new Date(Math.max(Date.now(), evaluated + offsetMs)).toISOString();
}

export function buildShotReviewCommand(model, shot, decision = "approve") {
  const ref = exactRef(shot);
  const suffix = newCommandIdentity("ui");
  return {
    action: "shot.review",
    expected_aggregate_version: model.aggregateVersion,
    shot_ref: ref,
    decision,
    shot_version_id: `${ref.entity_id}.v-${suffix}`,
    decision_entity_id: `review-${ref.entity_id}-${suffix}`,
    decision_version_id: `review-${ref.entity_id}-${suffix}.v1`,
    created_at: commandTimestamp(model.evaluatedAt),
    note: decision === "approve" ? "创作者在故事板工作区批准此精确镜头版本。" : "创作者请求修改此精确镜头版本。",
  };
}

export function buildShotReassignCommand(model, shot, scene) {
  const shotRef = exactRef(shot);
  const suffix = newCommandIdentity("ui");
  return {
    action: "shot.reassign_scene",
    expected_aggregate_version: model.aggregateVersion,
    shot_ref: shotRef,
    scene_ref: exactRef(scene),
    new_version_id: `${shotRef.entity_id}.v-${suffix}`,
    created_at: commandTimestamp(model.evaluatedAt),
  };
}

export function buildCandidateSelectCommand(model, shot, candidate) {
  const shotRef = exactRef(shot);
  const suffix = newCommandIdentity("ui");
  return {
    action: "candidate.select",
    expected_aggregate_version: model.aggregateVersion,
    target_shot_ref: shotRef,
    candidate_ref: exactRef(candidate),
    purpose: "storyboard",
    selection_entity_id: `selection-${shotRef.entity_id}-${suffix}`,
    selection_version_id: `selection-${shotRef.entity_id}-${suffix}.v1`,
    created_at: commandTimestamp(model.evaluatedAt),
  };
}

export function buildSelectionReviewCommand(model, selection, decision = "approve") {
  const ref = exactRef(selection);
  const suffix = newCommandIdentity("ui");
  return {
    action: "selection.review",
    expected_aggregate_version: model.aggregateVersion,
    selection_ref: ref,
    decision,
    selection_version_id: `${ref.entity_id}.v-${suffix}`,
    decision_entity_id: `review-${ref.entity_id}-${suffix}`,
    decision_version_id: `review-${ref.entity_id}-${suffix}.v1`,
    created_at: commandTimestamp(model.evaluatedAt),
    note: decision === "approve" ? "创作者批准此精确选版。" : "创作者拒绝此精确选版。",
  };
}

export function buildSelectionLockCommand(model, selection) {
  const ref = exactRef(selection);
  const suffix = newCommandIdentity("ui");
  return {
    action: "selection.lock",
    expected_aggregate_version: model.aggregateVersion,
    selection_ref: ref,
    selection_version_id: `${ref.entity_id}.v-${suffix}`,
    decision_entity_id: `lock-${ref.entity_id}-${suffix}`,
    decision_version_id: `lock-${ref.entity_id}-${suffix}.v1`,
    created_at: commandTimestamp(model.evaluatedAt),
    note: "创作者锁定此精确选版。",
  };
}

export function buildSelectionUnlockCommand(model, selection) {
  const command = buildSelectionLockCommand(model, selection);
  return {
    ...command,
    action: "selection.unlock",
    decision_entity_id: command.decision_entity_id.replace("lock-", "unlock-"),
    decision_version_id: command.decision_version_id.replace("lock-", "unlock-"),
    note: "创作者重新打开此精确选版。",
  };
}

export function buildDeliveryUnlockCommand(model) {
  const ref = exactRef(model.delivery?.current_ref);
  const suffix = newCommandIdentity("ui");
  return {
    action: "delivery.unlock",
    expected_aggregate_version: model.aggregateVersion,
    delivery_ref: ref,
    delivery_version_id: `${ref.entity_id}.v-${suffix}`,
    decision_entity_id: `unlock-${ref.entity_id}-${suffix}`,
    decision_version_id: `unlock-${ref.entity_id}-${suffix}.v1`,
    created_at: commandTimestamp(model.evaluatedAt),
    note: "创作者重新打开此精确交付版本。",
  };
}

export function buildContinuityApplyCommand(model, shot, continuity, patch = {}) {
  const continuityRef = exactRef(continuity);
  const suffix = newCommandIdentity("ui");
  const selectedShotRefs = model.shots
    .filter((item) => item.continuity?.some((fact) => (
      fact.ref.entity_type === continuityRef.entity_type
      && fact.ref.entity_id === continuityRef.entity_id
      && fact.ref.version_id === continuityRef.version_id
    )))
    .map((item) => exactRef(item));
  if (!selectedShotRefs.some((ref) => ref.entity_id === exactRef(shot).entity_id)) {
    throw new Error("当前镜头不属于这项连续性修正范围。");
  }
  const plannedAt = commandTimestamp(model.evaluatedAt, 1);
  return {
    action: "continuity.apply",
    expected_aggregate_version: model.aggregateVersion,
    old_continuity_ref: continuityRef,
    new_version_id: `${continuityRef.entity_id}.v-${suffix}`,
    proposal_entity_id: `proposal-${continuityRef.entity_id}-${suffix}`,
    planned_at: plannedAt,
    applied_at: new Date(Date.parse(plannedAt) + 1).toISOString(),
    identity_baseline: patch.identity_baseline || continuity.identity_baseline,
    temporary_state: patch.temporary_state || continuity.temporary_state,
    prohibited_changes: patch.prohibited_changes || continuity.prohibited_changes,
    selected_shot_refs: selectedShotRefs,
  };
}

export function commandIdFor(action) {
  return newCommandIdentity(String(action || "episode-command").replaceAll(".", "-"));
}
