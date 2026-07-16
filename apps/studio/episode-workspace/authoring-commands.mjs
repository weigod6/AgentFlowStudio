function token(prefix) {
  const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
    || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random.slice(0, 18)}`;
}

export function commandIdentity(action) {
  return token(action.replaceAll(".", "-"));
}

export function versionIdentity(entityId, revisionHint = "next") {
  return `${entityId}-${revisionHint}-${token("v").slice(-12)}`;
}

export function stableIdentity(kind) {
  return token(kind.replaceAll("_", "-"));
}

export function createEntityCommand(model, entityType, entityId, entity) {
  return {
    action: "authoring.create",
    expected_aggregate_version: model.aggregate_version,
    entity_id: entityId,
    version_id: versionIdentity(entityId, "v1"),
    created_at: new Date().toISOString(),
    entity: { entity_type: entityType, ...entity },
  };
}

export function reviseEntityCommand(model, targetRef, changes) {
  return {
    action: "authoring.revise",
    expected_aggregate_version: model.aggregate_version,
    target_ref: targetRef,
    new_version_id: versionIdentity(targetRef.entity_id),
    created_at: new Date().toISOString(),
    changes: { entity_type: targetRef.entity_type, ...changes },
  };
}

export function reorderCommand(model, orderedRefs) {
  return {
    action: "authoring.reorder",
    expected_aggregate_version: model.aggregate_version,
    ordered_refs: orderedRefs,
    new_version_ids: orderedRefs.map((ref) => versionIdentity(ref.entity_id, "order")),
    created_at: new Date().toISOString(),
  };
}

export function reviseShotCommand(model, shot, changes, preview) {
  return {
    action: "shot.revise_intent",
    expected_aggregate_version: model.aggregate_version,
    shot_ref: shot.ref,
    new_version_id: versionIdentity(shot.ref.entity_id),
    created_at: new Date().toISOString(),
    changes,
    preview_digest: preview.preview_digest,
    confirmed_direct_refs: preview.direct_affected_refs,
    confirmed_transitive_refs: preview.transitive_affected_refs,
    confirmed_protected_refs: preview.protected_refs,
  };
}

export function restoreShotCommand(model, shot, historicalRef, preview) {
  return {
    action: "shot.restore",
    expected_aggregate_version: model.aggregate_version,
    historical_ref: historicalRef,
    current_ref: shot.ref,
    new_version_id: versionIdentity(shot.ref.entity_id, "restore"),
    created_at: new Date().toISOString(),
    preview_digest: preview.preview_digest,
    confirmed_direct_refs: preview.direct_affected_refs,
    confirmed_transitive_refs: preview.transitive_affected_refs,
    confirmed_protected_refs: preview.protected_refs,
  };
}
