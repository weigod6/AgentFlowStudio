import {
  assetCardDraftFromRef,
  assetCardText,
  assetCardTypeLabel,
} from "./asset-card-drafts.js";
import { assetAutoBindingGraph, nodeReferenceStackForGraphBoundAssets } from "./asset-auto-binding-refs.js";
import { assetImageRatio } from "./asset-card-image-prompts.js";
import { createNode, connect } from "./nodes.js";
import { refineStructuredShotAssets, structuredShotFromSegment } from "./structured-shot.js";

const MAX_ASSET_PREP_NODES_PER_SHOT = 4;

export function createShotAssetPrepNodes(store, scriptNodeId, structuredShot, x, y, options = {}) {
  const refs = Array.isArray(structuredShot?.asset_refs) ? structuredShot.asset_refs : [];
  const created = [];
  const bindingGraph = assetAutoBindingGraph(options.assetAutoBindingGraph);
  refs.slice(0, MAX_ASSET_PREP_NODES_PER_SHOT).forEach((asset, index) => {
    const reusableId = reusableAssetCardNodeId(store.get(), asset, scriptNodeId);
    if (reusableId) {
      connect(store, scriptNodeId, reusableId);
      created.push(reusableId);
      return;
    }
    const draft = assetCardDraftFromRef(asset, structuredShot, { sourceScriptNodeId: scriptNodeId });
    const assetNode = createNode(store, "image", x, y + index * 150);
    applyAssetDraftToNode(store, assetNode.id, draft, structuredShot, scriptNodeId, asset, bindingGraph);
    connect(store, scriptNodeId, assetNode.id);
    created.push(assetNode.id);
  });
  return created;
}

export function ensureShotAssetPrepNodesForScriptNode(store, scriptNode, options = {}) {
  const fresh = store.get().nodes[scriptNode.id] || scriptNode;
  if (!fresh) return [];
  const existing = existingShotAssetCardNodeIds(store.get(), fresh.id);
  if (existing.length && !options.replaceExisting) return existing;
  if (existing.length && options.replaceExisting) {
    removeShotAssetCardNodes(store, existing);
  }
  const context = fresh.content || fresh.prompt || "";
  const structuredShot = options.structuredShot
    ? refineStructuredShotAssets(options.structuredShot, context)
    : fresh.params?.structuredShot
    ? refineStructuredShotAssets(fresh.params.structuredShot, context)
    : structuredShotFromSegment(context, Number(fresh.params?.scriptSegmentIndex || 1));
  const bindingGraph = assetAutoBindingGraph(
    options.assetAutoBindingGraph
    || fresh.params?.assetAutoBindingGraph
    || fresh.params?.storyboardBreakdown?.assetAutoBindingGraph,
  );
  const created = createShotAssetPrepNodes(store, fresh.id, structuredShot, fresh.x + fresh.w + 160, fresh.y, {
    assetAutoBindingGraph: bindingGraph,
  });
  store.set((s) => {
    const node = s.nodes[fresh.id];
    if (!node) return;
    node.params.structuredShot = structuredShot;
    node.params.shotAssetRefs = structuredShot.asset_refs;
    if (bindingGraph) node.params.assetAutoBindingGraph = bindingGraph;
    node.params.assetPrepState = {
      status: created.length ? "card_ready" : "no_assets_detected",
      downstream_node_ids: created,
      updated_at: new Date().toISOString(),
    };
  });
  linkMatchingStoryboardShotsToAssetCards(store, fresh, created);
  return created;
}

export function createManualShotAssetNode(store, scriptNode, assetType, label = "") {
  const fresh = store.get().nodes[scriptNode.id] || scriptNode;
  if (!fresh) return null;
  const safeType = ["character", "scene", "prop"].includes(assetType) ? assetType : "character";
  const fallback = safeType === "scene" ? "新增场景" : safeType === "prop" ? "新增道具" : "新增角色";
  const context = fresh.content || fresh.prompt || "";
  const structuredShot = fresh.params?.structuredShot
    ? refineStructuredShotAssets(fresh.params.structuredShot, context)
    : structuredShotFromSegment(context, Number(fresh.params?.scriptSegmentIndex || 1));
  const asset = {
    label: label || fallback,
    asset_type: safeType,
    asset_id: `manual:${safeType}:${Date.now()}`,
    status: "candidate",
    source: "manual",
  };
  const draft = assetCardDraftFromRef(asset, structuredShot, { sourceScriptNodeId: fresh.id });
  const yOffset = existingShotAssetCardNodeIds(store.get(), fresh.id).length * 150;
  const assetNode = createNode(store, "image", fresh.x + fresh.w + 160, fresh.y + yOffset);
  applyAssetDraftToNode(store, assetNode.id, draft, structuredShot, fresh.id, asset);
  connect(store, fresh.id, assetNode.id);
  store.set((s) => {
    const node = s.nodes[fresh.id];
    if (!node) return;
    const structured = node.params.structuredShot || structuredShot;
    node.params.structuredShot = {
      ...structured,
      asset_refs: appendAssetRef(structured.asset_refs, asset),
    };
    node.params.shotAssetRefs = appendAssetRef(node.params.shotAssetRefs || [], asset);
    node.params.assetPrepState = {
      status: "card_ready",
      downstream_node_ids: existingShotAssetCardNodeIds(s, fresh.id),
      updated_at: new Date().toISOString(),
    };
  });
  linkManualAssetToMatchingStoryboardShots(store, fresh, asset, assetNode.id);
  return assetNode.id;
}

export function existingShotAssetCardNodeIds(state, scriptNodeId) {
  return Object.values(state.nodes || {})
    .filter((node) => node?.params?.assetCardDraft?.source_script_node_id === scriptNodeId)
    .map((node) => node.id);
}

function reusableAssetCardNodeId(state, asset, scriptNodeId = "") {
  const key = assetKey(asset);
  if (!key) return "";
  for (const node of Object.values(state.nodes || {})) {
    if (!node || node.id === scriptNodeId || node.params?.assetCardDraft?.source_script_node_id === scriptNodeId) continue;
    const draft = node.params?.assetCardDraft;
    const prepRef = node.params?.asset_prep?.asset_ref;
    if (assetKey(draft) === key || assetKey(prepRef) === key) return node.id;
  }
  return "";
}

function linkMatchingStoryboardShotsToAssetCards(store, sourceScriptNode, assetNodeIds) {
  if (!sourceScriptNode?.params?.sourceTextNodeId || !assetNodeIds.length) return;
  const state = store.get();
  const assetNodeByKey = new Map();
  for (const assetNodeId of assetNodeIds) {
    const assetNode = state.nodes?.[assetNodeId];
    const key = assetKey(assetNode?.params?.assetCardDraft || assetNode?.params?.asset_prep?.asset_ref);
    if (key) assetNodeByKey.set(key, assetNodeId);
  }
  if (!assetNodeByKey.size) return;
  for (const node of Object.values(state.nodes || {})) {
    if (!node || node.id === sourceScriptNode.id || node.type !== "script") continue;
    if (node.params?.sourceTextNodeId !== sourceScriptNode.params.sourceTextNodeId) continue;
    const context = node.content || node.prompt || "";
    const siblingShot = node.params?.structuredShot
      ? refineStructuredShotAssets(node.params.structuredShot, context)
      : structuredShotFromSegment(context, Number(node.params?.scriptSegmentIndex || 1));
    const matchedIds = [];
    for (const asset of siblingShot.asset_refs || []) {
      const assetNodeId = assetNodeByKey.get(assetKey(asset));
      if (!assetNodeId) continue;
      connect(store, node.id, assetNodeId);
      matchedIds.push(assetNodeId);
    }
    if (!matchedIds.length) continue;
    store.set((s) => {
      const fresh = s.nodes[node.id];
      if (!fresh) return;
      const existing = Array.isArray(fresh.params?.assetPrepState?.downstream_node_ids)
        ? fresh.params.assetPrepState.downstream_node_ids
        : [];
      fresh.params.structuredShot = siblingShot;
      fresh.params.shotAssetRefs = siblingShot.asset_refs;
      fresh.params.assetPrepState = {
        status: "linked_existing_assets",
        downstream_node_ids: [...new Set([...existing, ...matchedIds])],
        updated_at: new Date().toISOString(),
      };
    });
  }
}

function linkManualAssetToMatchingStoryboardShots(store, sourceScriptNode, asset, assetNodeId) {
  if (!sourceScriptNode?.params?.sourceTextNodeId || !assetNodeId) return;
  const label = String(asset?.label || "").replace(/^@+/, "").trim();
  if (!label) return;
  const state = store.get();
  for (const node of Object.values(state.nodes || {})) {
    if (!node || node.id === sourceScriptNode.id || node.type !== "script") continue;
    if (node.params?.sourceTextNodeId !== sourceScriptNode.params.sourceTextNodeId) continue;
    const context = node.content || node.prompt || "";
    if (!contextMentionsAsset(context, label)) continue;
    connect(store, node.id, assetNodeId);
    store.set((s) => {
      const fresh = s.nodes[node.id];
      if (!fresh) return;
      const siblingShot = fresh.params?.structuredShot
        ? refineStructuredShotAssets(fresh.params.structuredShot, context)
        : structuredShotFromSegment(context, Number(fresh.params?.scriptSegmentIndex || 1));
      fresh.params.structuredShot = {
        ...siblingShot,
        asset_refs: appendAssetRef(siblingShot.asset_refs, asset),
      };
      fresh.params.shotAssetRefs = appendAssetRef(fresh.params.shotAssetRefs || siblingShot.asset_refs || [], asset);
      const existing = Array.isArray(fresh.params?.assetPrepState?.downstream_node_ids)
        ? fresh.params.assetPrepState.downstream_node_ids
        : [];
      fresh.params.assetPrepState = {
        status: "linked_existing_assets",
        downstream_node_ids: [...new Set([...existing, assetNodeId])],
        updated_at: new Date().toISOString(),
      };
    });
  }
}

function appendAssetRef(refs, asset) {
  const next = Array.isArray(refs) ? refs.slice() : [];
  const key = assetKey(asset);
  if (!key || next.some((item) => assetKey(item) === key)) return next;
  return [...next, asset];
}

function contextMentionsAsset(context, label) {
  const source = String(context || "");
  if (!source || !label) return false;
  return source.includes(label) || source.includes(`@${label}`);
}

function assetKey(asset) {
  const label = String(asset?.label || asset?.display_name || "").replace(/^@+/, "").trim().toLowerCase();
  const type = String(asset?.asset_type || "").trim().toLowerCase();
  return label && type ? `${type}:${label}` : "";
}

function removeShotAssetCardNodes(store, nodeIds) {
  const removal = new Set(nodeIds);
  store.set((s) => {
    for (const id of removal) delete s.nodes[id];
    s.order = s.order.filter((id) => !removal.has(id));
    for (const [edgeId, edge] of Object.entries(s.edges || {})) {
      if (removal.has(edge.from) || removal.has(edge.to)) delete s.edges[edgeId];
    }
    for (const group of Object.values(s.groups || {})) {
      group.nodeIds = group.nodeIds.filter((id) => !removal.has(id));
    }
    for (const groupId of Object.keys(s.groups || {})) {
      if (!s.groups[groupId].nodeIds.length) delete s.groups[groupId];
    }
  });
}

function applyAssetDraftToNode(store, nodeId, draft, structuredShot, scriptNodeId, asset, bindingGraph = null) {
  store.set((s) => {
    const node = s.nodes[nodeId];
    if (!node) return;
    const referenceStack = nodeReferenceStackForGraphBoundAssets(bindingGraph, { asset_refs: [asset] }, nodeId);
    node.title = `${assetCardTypeLabel(draft.asset_type, draft.character_subtype)} · @${draft.label}`;
    node.prompt = "";
    node.content = assetCardText(draft);
    node.status = "complete";
    node.h = Math.max(230, Math.min(340, 170 + Object.keys(draft.feature_card || {}).length * 18));
    node.params.nodeRole = "asset_card_draft";
    node.params.assetCardDraft = draft;
    node.params.spec = {
      ...(node.params.spec || {}),
      ratio: assetImageRatio(draft.asset_type),
      count: 1,
    };
    node.params.asset_prep = {
      status: "card_ready",
      source_script_node_id: scriptNodeId,
      source_shot_id: structuredShot.shot_id,
      asset_ref: asset,
      asset_card_id: draft.card_id,
    };
    node.params.assetPrepState = {
      status: "card_ready",
      source_script_node_id: scriptNodeId,
      source_shot_id: structuredShot.shot_id,
    };
    if (bindingGraph) node.params.assetAutoBindingGraph = bindingGraph;
    if (referenceStack) node.params.nodeReferenceStack = referenceStack;
  });
}
