import { assetCardTypeLabel } from "./asset-card-drafts.js";
import { assetImagePrompt, assetPromptSupplementFromNode } from "./asset-card-image-prompts.js";
import { assetCardRevisionPromptSupplement } from "./asset-revision-references.js";

export function assetCardPromptText(node) {
  const draft = node.params?.assetCardDraft;
  if (!draft || node.params?.nodeRole !== "asset_card_draft") return "";
  const generated = assetImagePrompt(draft);
  const revision = assetCardRevisionPromptSupplement(node);
  const manual = assetPromptSupplementFromNode(node);
  const typeLabel = assetCardTypeLabel(draft.asset_type, draft.character_subtype);
  const guard = [
    `${typeLabel}生成模式：只生成 @${String(draft.label || "").replace(/^@+/, "") || "资产"} 的可复用视觉参考，不生成完整分镜关键帧。`,
    "不得把上游分镜直接画成单张剧情插画；必须输出可审查、可固定、后续可复用的角色/场景/道具参考图。",
    draft.asset_type === "scene" ? "场景资产必须是同一空间的多角度环境参考图：广角、反向、俯瞰/空间布局、光影材质细节；不得加入角色主体，除非资产卡明确要求比例参考。" : "",
    draft.asset_type === "character" ? "角色资产必须按固定布局输出：正面半身特写 + 全身正面居中 + 左侧面全身 + 背面全身；以身份、结构、材质、比例和辨识点为主，背景保持中性，不带武器、手持道具或背景物体。" : "",
    draft.asset_type === "prop" ? "道具资产必须是单一道具的正面、侧面、俯视和局部结构/材质参考；以单体外观、材质、比例和使用状态为主，背景保持简洁。" : "",
  ].filter(Boolean).join("\n");
  return [generated, revision, manual, guard].filter(Boolean).join("\n");
}

export function safeAssetCardSnapshot(draft) {
  return {
    asset_type: String(draft.asset_type || "").slice(0, 40),
    label: String(draft.label || "").replace(/^@+/, "").slice(0, 80),
    status: String(draft.status || "").slice(0, 40),
    signature: String(draft.signature || "").slice(0, 180),
  };
}
