import {
  assetCardFieldsForType,
  assetCardTypeLabel,
  normalizeAssetCardDraft,
} from "./asset-card-drafts.js";

const MAX_REVISION_REFERENCES = 4;
export const ASSET_REFERENCE_MODES = {
  LOCALIZED_EDIT: "localized_edit",
  ORIGINALIZE_IP_SAFE: "originalize_ip_safe",
};

export function buildAssetCardRevisionState(node, previousDraft, nextDraft) {
  const prior = normalizeAssetCardDraft(previousDraft || {});
  const next = normalizeAssetCardDraft(nextDraft || {});
  const mode = assetReferenceMode(node);
  const references = revisionReferenceAssets(node);
  const changed = changedAssetCardFields(prior, next);
  return {
    schema_version: "afs_asset_card_revision.v0.1",
    mode: references.length ? mode : "text_only_revision",
    asset_type: next.asset_type,
    asset_label: next.label,
    reference_assets: references,
    changed_fields: changed,
    preserve_locks: (next.negative_locks || []).map(cleanText).filter(Boolean).slice(0, 12),
    created_at: new Date().toISOString(),
  };
}

export function buildUserAssetCardRevisionState(node, draft, instruction, requestedMode = "") {
  const card = normalizeAssetCardDraft(draft || {});
  const mode = normalizeAssetReferenceMode(requestedMode || node?.params?.assetReferenceMode);
  const references = revisionReferenceAssets(node);
  const text = cleanText(instruction).slice(0, 500);
  return {
    schema_version: "afs_asset_card_revision.v0.1",
    mode: references.length ? mode : "text_only_revision",
    asset_type: card.asset_type,
    asset_label: card.label,
    reference_assets: references,
    changed_fields: text ? [{
      field: "user_instruction",
      label: "用户调整要求",
      from: "",
      to: text,
    }] : [],
    preserve_locks: (card.negative_locks || []).map(cleanText).filter(Boolean).slice(0, 12),
    created_at: new Date().toISOString(),
  };
}

export function normalizeAssetReferenceMode(value) {
  const raw = String(value || "").trim();
  const text = raw.toLowerCase();
  if ([
    "originalize",
    "originalize_ip_safe",
    "reference_guided_original_rebirth",
    "original_rebirth",
    "ip_safe_rebirth",
    "ip_risk_reduction",
    "inspiration_reference",
    "style_reference_only",
  ].includes(text)) return ASSET_REFERENCE_MODES.ORIGINALIZE_IP_SAFE;
  if (["image_guided_partial_revision", "partial_revision", "localized_edit", "text_only_revision"].includes(text)) {
    return ASSET_REFERENCE_MODES.LOCALIZED_EDIT;
  }
  if (/原创|降\s*ip|降低\s*ip|去\s*ip|灵感参考|重新设计|redesign|do not copy|copyright safe/i.test(raw)) {
    return ASSET_REFERENCE_MODES.ORIGINALIZE_IP_SAFE;
  }
  return ASSET_REFERENCE_MODES.LOCALIZED_EDIT;
}

export function assetReferenceMode(node) {
  return normalizeAssetReferenceMode(node?.params?.assetReferenceMode || node?.params?.assetCardRevision?.mode);
}

export function canUseAssetReferenceMode(node) {
  if (node?.type !== "image" && node?.type !== "video") return false;
  if (node?.params?.assetCardDraft) return true;
  return nodeImageUploads(node).some((item) => cleanAssetId(item?.asset_id || item?.assetId))
    || nodeVisualAssets(node).some((asset) => imageRefsFromVisualAsset(asset).length > 0)
    || Boolean(node?.params?.firstFrameImageAssetId || node?.params?.lastFrameImageAssetId);
}

export function isOriginalizeAssetReferenceMode(value) {
  return normalizeAssetReferenceMode(value) === ASSET_REFERENCE_MODES.ORIGINALIZE_IP_SAFE;
}

export function assetCardRevisionImageRefs(node) {
  const revision = node?.params?.assetCardRevision;
  const refs = Array.isArray(revision?.reference_assets) ? revision.reference_assets : [];
  return dedupe(refs.map((item) => cleanAssetId(item?.asset_id || item?.assetId))).slice(0, MAX_REVISION_REFERENCES);
}

export function assetCardNodeUploadImageRefs(node) {
  return dedupe(nodeImageUploads(node)
    .filter(isUserUploadedReferenceImage)
    .map((item) => cleanAssetId(item?.asset_id || item?.assetId)))
    .slice(0, MAX_REVISION_REFERENCES);
}

export function assetCardReferenceImageRefs(node) {
  return dedupe([
    ...assetCardRevisionImageRefs(node),
    ...assetCardNodeUploadImageRefs(node),
  ]).slice(0, MAX_REVISION_REFERENCES);
}

export function safeAssetCardRevisionSnapshot(revision) {
  if (!revision || typeof revision !== "object") return null;
  const references = Array.isArray(revision.reference_assets) ? revision.reference_assets : [];
  const changed = Array.isArray(revision.changed_fields) ? revision.changed_fields : [];
  return {
    schema_version: "afs_asset_card_revision.v0.1",
    mode: normalizeAssetReferenceMode(revision.mode),
    asset_type: String(revision.asset_type || "").slice(0, 40),
    asset_label: cleanText(revision.asset_label).slice(0, 80),
    reference_assets: references.map((item, index) => ({
      asset_id: cleanAssetId(item?.asset_id || item?.assetId),
      role: cleanText(item?.role || referenceRole(item, index)).slice(0, 80),
      priority: index + 1,
    })).filter((item) => item.asset_id).slice(0, MAX_REVISION_REFERENCES),
    changed_fields: changed.map((item) => ({
      field: cleanText(item?.field).slice(0, 80),
      label: cleanText(item?.label).slice(0, 80),
      from: cleanText(item?.from).slice(0, 240),
      to: cleanText(item?.to).slice(0, 240),
    })).filter((item) => item.field && item.to).slice(0, 12),
    preserve_locks: (Array.isArray(revision.preserve_locks) ? revision.preserve_locks : [])
      .map(cleanText)
      .filter(Boolean)
      .slice(0, 12),
  };
}

export function assetCardRevisionPromptSupplement(node) {
  const revision = safeAssetCardRevisionSnapshot(node?.params?.assetCardRevision);
  if (!revision) return "";
  const hasRefs = revision.reference_assets.length > 0;
  const changes = revision.changed_fields
    .map((item) => `${item.label || item.field}: ${item.from || "unspecified"} -> ${item.to}`)
    .join("; ");
  if (isOriginalizeAssetReferenceMode(revision.mode)) {
    return originalizeRevisionPromptSupplement(revision, hasRefs, changes);
  }
  const editPolicy = revisionEditPolicyLines(revision);
  const locks = revision.preserve_locks.length ? `Card locks to preserve: ${revision.preserve_locks.join("; ")}` : "";
  const typeLabel = assetCardTypeLabel(revision.asset_type, revision.character_subtype);
  return [
    `${typeLabel} revision mode: ${hasRefs ? "image-guided partial revision" : "text-only revision with drift risk"}.`,
    hasRefs
      ? `Use the provided reference image(s) as the primary visual source of truth for @${revision.asset_label || "asset"}; reference #1 has highest priority and should dominate identity, layout, proportions, camera distance, view grid, and non-edited details.`
      : "No prior reference image is available, so preserve the card locks as strictly as possible.",
    "Treat this as localized image editing, not text-to-image redesign. The asset card text is secondary evidence except for the explicit edited fields.",
    "The changed fields are the only editable delta; keep every unrelated visual feature anchored to the previous reference image.",
    "Revision strength: conservative low-change pass. The result should read as the same previous reference sheet after one art-director edit, not a fresh redesign.",
    "This is not a new asset design. Preserve the same subject identity, silhouette, body proportions, head shape, limb structure, view layout, camera distance, neutral background, palette family, and all non-edited details.",
    changes ? `Apply only the changed asset-card details: ${changes}.` : "No explicit card-field delta is recorded; regenerate conservatively from the current card.",
    editPolicy.length ? `Field-specific edit policy:\n- ${editPolicy.join("\n- ")}` : "",
    "If a material detail changes, change only the surface/material treatment while keeping the original anatomy, scale, turnaround sheet structure, and mechanical/organic construction relationships.",
    "Do not turn the subject into a toy, chibi, mascot, cute round-head robot, unrelated character, or different proportion system unless the asset card explicitly asks for that.",
    locks,
  ].filter(Boolean).join("\n");
}

function originalizeRevisionPromptSupplement(revision, hasRefs, changes) {
  const typeLabel = assetCardTypeLabel(revision.asset_type, revision.character_subtype);
  const locks = revision.preserve_locks.length ? `Safety constraints to respect without copying IP: ${revision.preserve_locks.join("; ")}` : "";
  return [
    `${typeLabel} reference transformation mode: originalize / IP-risk reduction.`,
    hasRefs
      ? `Use the provided reference image(s) as inspiration and visual evidence only for @${revision.asset_label || "asset"}; do not treat reference #1 as identity, costume, layout, pose, logo, or exact proportion truth.`
      : "No prior reference image is available, so build a new original asset from the current card and user intent.",
    "Goal: create a new reusable production asset that keeps only high-level concept, broad material mood, functional role, palette relationship, and genre direction.",
    "Must redesign recognizable identity: change face/head details, silhouette, costume system, distinctive markings, logo-like shapes, exact weapon/object geometry, and iconic scene composition.",
    "Do not copy or closely imitate a known character, franchise look, trademark, signature outfit, exact pose, exact layout, or uniquely recognizable IP cue.",
    changes ? `Use these user/card directions as transformation goals, not literal copy locks: ${changes}.` : "If no explicit delta is recorded, transform the reference into a fresh original design with lower recognizability.",
    "Output should feel like a newly art-directed asset inspired by the reference category, not the same previous reference sheet after a small edit.",
    locks,
  ].filter(Boolean).join("\n");
}

function revisionReferenceAssets(node) {
  const refs = [];
  const mode = assetReferenceMode(node);
  for (const item of [...nodeImageUploads(node)].reverse()) {
    const assetId = cleanAssetId(item.asset_id || item.assetId);
    if (!assetId) continue;
    refs.push({
      asset_id: assetId,
      role: referenceRoleForMode(item, refs.length, mode),
      source: cleanText(item.source_kind || item.sourceKind || item.role || "node_upload").slice(0, 80),
      priority: refs.length + 1,
    });
    if (refs.length >= MAX_REVISION_REFERENCES) return refs;
  }
  for (const item of [...nodeVisualAssets(node)].reverse()) {
    for (const assetId of imageRefsFromVisualAsset(item)) {
      if (!assetId) continue;
      refs.push({
        asset_id: assetId,
        role: referenceRoleForMode(item, refs.length, mode),
        source: "fixed_visual_asset",
        priority: refs.length + 1,
      });
      if (refs.length >= MAX_REVISION_REFERENCES) return dedupeReferenceAssets(refs);
    }
  }
  return dedupeReferenceAssets(refs);
}

function changedAssetCardFields(previousDraft, nextDraft) {
  const fields = assetCardFieldsForType(nextDraft.asset_type);
  const changed = [];
  for (const [key, label] of fields) {
    const from = cleanText(previousDraft.feature_card?.[key]);
    const to = cleanText(nextDraft.feature_card?.[key]);
    if (from === to || !to) continue;
    changed.push({ field: key, label, from, to });
  }
  if (cleanText(previousDraft.signature) !== cleanText(nextDraft.signature)) {
    changed.unshift({
      field: "signature",
      label: "一句话签名",
      from: cleanText(previousDraft.signature),
      to: cleanText(nextDraft.signature),
    });
  }
  return changed.slice(0, 12);
}

function revisionEditPolicyLines(revision) {
  const lines = [];
  for (const item of revision.changed_fields || []) {
    const field = cleanText(item.field);
    const target = cleanText(item.to).toLowerCase();
    if (field === "user_instruction") {
      if (/cap|hat|baseball|鸭舌帽|帽/u.test(target)) {
        lines.push("User-instruction clothing scope: add only the requested hat/headwear as an outer accessory; preserve face identity, hair silhouette where visible, body, outfit, view layout, palette family, and all non-edited details.");
      }
      if (/scar|疤|伤疤|刀疤|面部.*痕|脸.*痕|左边面部|左脸/u.test(target)) {
        lines.push("User-instruction facial-mark scope: add exactly the requested scar/mark only where requested; preserve face identity, eyes, brow, hair, expression, costume, body, props, and all view positions.");
      }
      if (/plush|fur|furry|fabric|毛绒|绒|布料|织物/u.test(target)) {
        lines.push("User-instruction material scope: change only the named surface/material treatment while preserving the same frame, proportions, silhouette, turnaround layout, and non-edited construction details.");
      }
    }
    if (field === "wardrobe") {
      lines.push("Wardrobe edit scope: add the requested clothing as an outer garment layer only; do not redesign the head, face screen, eye shape, ear side modules, neck, chest core, mechanical limbs, hands, feet, body scale, or turnaround-sheet layout.");
      lines.push("Keep the robot body visible at uncovered neck/chest/hands/legs unless the card explicitly asks to fully hide it; clothing must not convert the robot into a human, child, monk, mascot, or different character archetype.");
    }
    if (field === "appearance") {
      lines.push("Appearance edit scope: change only the named surface/recognizable detail; keep the same identity, adult/humanoid scale, head-to-body ratio, joint layout, limb length, camera distance, and sheet composition.");
      if (/plush|fur|furry|fabric|毛绒|绒|布料|织物/u.test(target)) {
        lines.push("Plush/fabric material must read as a surface covering on the same existing robot frame, not as a cute toy, chibi body, stuffed doll, or new rounded robot design.");
      }
      if (/scar|疤|伤疤|刀疤|面部.*痕|脸.*痕|左边面部|左脸/u.test(target)) {
        lines.push("Facial-mark edit scope: add exactly the requested scar/mark on the subject's left side of the face only; preserve face identity, eyes, brow, hair, expression, costume, armor, body, weapons/props, and all view positions.");
        lines.push("Make the scar visible in the front half-body close-up and consistent on matching front/side views; do not add extra scars, wounds, blood, different facial structure, or a new character design.");
      }
    }
    if (field === "palette") {
      lines.push("Palette edit scope: change colors only; preserve form, materials, proportions, clothing cut, lighting direction, and reference sheet layout.");
    }
    if (field === "demeanor") {
      lines.push("Demeanor edit scope: change expression or mood only; preserve geometry, outfit, material, palette, and view layout.");
    }
  }
  return dedupe(lines).slice(0, 8);
}

function nodeImageUploads(node) {
  return Array.isArray(node?.params?.uploads) ? node.params.uploads : [];
}

function isUserUploadedReferenceImage(item) {
  const role = cleanText(item?.role || item?.source_kind || item?.sourceKind || item?.kind).toLowerCase();
  return ["reference_image", "uploaded_reference", "user_reference_image"].includes(role);
}

function nodeVisualAssets(node) {
  return Array.isArray(node?.params?.visualAssets) ? node.params.visualAssets : [];
}

function imageRefsFromVisualAsset(asset) {
  const refs = Array.isArray(asset?.image_asset_refs)
    ? asset.image_asset_refs
    : Array.isArray(asset?.source_image_asset_refs)
      ? asset.source_image_asset_refs
      : [];
  return refs.map(cleanAssetId).filter(Boolean);
}

function referenceRole(item, index) {
  const role = cleanText(item?.role || item?.asset_type || "");
  if (index === 0) return "identity_layout_anchor";
  if (/scene/i.test(role)) return "scene_context_reference";
  if (/prop/i.test(role)) return "prop_detail_reference";
  if (/style/i.test(role)) return "style_reference";
  return "secondary_identity_reference";
}

function referenceRoleForMode(item, index, mode) {
  if (isOriginalizeAssetReferenceMode(mode)) {
    if (index === 0) return "inspiration_reference";
    return "secondary_inspiration_reference";
  }
  return index === 0 ? "identity_layout_anchor" : referenceRole(item, index);
}

function dedupeReferenceAssets(items) {
  const seen = new Set();
  const result = [];
  for (const item of items) {
    if (!item.asset_id || seen.has(item.asset_id)) continue;
    seen.add(item.asset_id);
    result.push({ ...item, priority: result.length + 1 });
  }
  return result.slice(0, MAX_REVISION_REFERENCES);
}

function dedupe(values) {
  const result = [];
  for (const value of values) {
    if (value && !result.includes(value)) result.push(value);
  }
  return result;
}

function cleanAssetId(value) {
  const text = cleanText(value);
  if (!text || /[\\/]/.test(text) || /(api_key|bearer|signed_url|token)/i.test(text)) return "";
  return text.slice(0, 120);
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}
