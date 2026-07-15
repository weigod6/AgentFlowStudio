import { safeError, setNodeError } from "./node-action-utils.js";
import { ensureShotAssetPrepNodesForScriptNode } from "./shot-asset-nodes.js";
import { createKeyframeNodesForStoryboard } from "./storyboard-keyframes.js";
import { structuredShotFromSegment } from "./structured-shot.js";

export async function identifyScriptAssets(store, runtime, node) {
  const fresh = store.get().nodes[node.id] || node;
  const structuredShot = await plannedStructuredShot(store, runtime, fresh);
  if (!structuredShot) return [];
  const created = ensureShotAssetPrepNodesForScriptNode(store, fresh, { structuredShot, replaceExisting: true });
  if (!created.length) {
    setNodeError(store, fresh.id, "当前分镜没有识别到可拆出的角色、场景或道具资产。");
  }
  return created;
}

export function createStoryboardKeyframeLayer(store, node) {
  const fresh = store.get().nodes[node.id] || node;
  return createKeyframeNodesForStoryboard(store, fresh);
}

async function plannedStructuredShot(store, runtime, node) {
  const fresh = store.get().nodes[node.id] || node;
  const scriptText = currentScriptText(fresh);
  const localShot = currentTextMatchesStructuredShot(fresh.params?.structuredShot, scriptText)
    ? fresh.params.structuredShot
    : structuredShotFromSegment(scriptText, Number(fresh.params?.scriptSegmentIndex || 1));
  if (!runtime?.planShotAssets) return localShot;
  try {
    store.set((s) => {
      const target = s.nodes[fresh.id];
      if (!target) return;
      target.params.assetPrepState = { status: "planning", updated_at: new Date().toISOString() };
    }, { history: false });
    const payload = await runtime.planShotAssets({
      node_id: fresh.id,
      shot: localShot || {},
      script_text: scriptText,
      generated_at: new Date().toISOString(),
    });
    const assetRefs = Array.isArray(payload?.asset_refs) ? payload.asset_refs : [];
    if (!assetRefs.length) {
      setNodeError(store, fresh.id, "资产规划没有返回可用的角色、场景或道具资产；请检查登录状态与 Runtime 服务后重试。");
      return null;
    }
    return {
      ...(localShot || {}),
      shot_id: localShot?.shot_id || `shot_${String(fresh.params?.scriptSegmentIndex || 1).padStart(2, "0")}`,
      index: localShot?.index || Number(fresh.params?.scriptSegmentIndex || 1),
      description: scriptText || localShot?.description || "",
      source_text: scriptText || localShot?.source_text || "",
      asset_refs: assetRefs,
    };
  } catch (error) {
    setNodeError(store, fresh.id, `资产规划暂时不可用：${safeError(error)}。请检查登录状态与 Runtime 服务后重试。`);
    return null;
  }
}

function currentScriptText(node) {
  return String(node?.content || node?.prompt || "").trim();
}

function currentTextMatchesStructuredShot(shot, scriptText) {
  if (!shot || typeof shot !== "object") return false;
  if (!scriptText) return true;
  const sourceText = String(shot.source_text || shot.description || "").trim();
  return sourceText === scriptText || sourceText.includes(scriptText) || scriptText.includes(sourceText);
}
