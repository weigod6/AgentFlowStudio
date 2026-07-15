import { assetIdFromRef } from "./asset-reference-summary.js";
import { handleCandidateCreatorAction } from "./candidate-selection-controller.js";
import { duplicateNode } from "./nodes.js";
import { fixNodeVisualAsset, handleNodeIntent, pollNodeVideoGeneration, startNodeGeneration, uploadNodeImage } from "./node-actions.js";
import { importScriptFileIntoTextNode } from "./script-breakdown.js";
import { openAssetDetailPopover } from "./panels/asset-detail-popover.js";
import { openDirectorShell } from "./panels/director-shell.js";
import { openNodeMenu } from "./panels/node-menu.js";
import { handleProductionDeliveryAction } from "./production-delivery-controller.js";

export function handleCanvasNodeClick(store, runtime, e) {
  const nodeEl = e.target.closest(".node");
  if (!nodeEl) return;
  const nodeId = nodeEl.dataset.nodeId;
  const node = store.get().nodes[nodeId];
  if (!node) return;
  const actionEl = e.target.closest("[data-action]");
  const action = actionEl?.dataset.action;
  if (!action) return;

  if (["candidate-select", "candidate-revise", "candidate-refresh"].includes(action)) {
    void handleCandidateCreatorAction(store, runtime, node, actionEl);
  } else if (action === "production-quality-approve" || action === "production-export" || action === "production-delivery-refresh") {
    void handleProductionDeliveryAction(store, runtime, node, actionEl);
  } else if (action === "intent") handleNodeIntent(store, runtime, node, actionEl.dataset.intent);
  else if (action === "open-director") openDirectorShell(store, node);
  else if (action === "asset-detail") openAssetDetailPopover(store, runtime, assetRefForAction(node, actionEl.dataset.assetId), actionEl);
  else if (["text", "script"].includes(node.type) && action === "upload") importScriptFileIntoTextNode(store, node);
  else if (action === "upload") uploadNodeImage(store, runtime, node);
  else if (action === "fix-visual-asset") fixNodeVisualAsset(store, runtime, node);
  else if (action === "open-generation-panel" || action === "continue-generate") {
    window.dispatchEvent(new CustomEvent("afs:studio-open-generation-panel", { detail: { node_id: node.id, node } }));
  } else if (action === "open-creation-process") {
    window.dispatchEvent(new CustomEvent("afs:studio-open-creation-process", { detail: { node_id: node.id, node } }));
  } else if (action === "content-card" || action === "video-asset-card-draft") {
    window.dispatchEvent(new CustomEvent("afs:video-asset-card-draft", { detail: { node_id: node.id, node } }));
  } else if (action === "run") startNodeGeneration(store, runtime, node);
  else if (action === "video-poll") pollNodeVideoGeneration(store, runtime, node);
  else if (action === "duplicate") duplicateNode(store, nodeId);
  else if (action === "toggle-collapse") {
    store.set((s) => { const n = s.nodes[nodeId]; if (n) n.collapsed = !n.collapsed; });
  } else if (action === "node-menu") {
    openNodeMenu(store, runtime, nodeId, actionEl);
  }
}

function assetRefForAction(node, assetId) {
  const assets = Array.isArray(node.params?.visualAssets) ? node.params.visualAssets : [];
  if (!assetId) return assets[0];
  return assets.find((asset) => assetIdFromRef(asset) === String(assetId)) || { asset_id: assetId };
}
