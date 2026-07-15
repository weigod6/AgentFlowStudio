import {
  assetCardFieldsForType,
  assetCardText,
  assetCardTypeLabel,
  normalizeAssetCardDraft,
} from "../asset-card-drafts.js";
import { assetCardUserAdjustmentText, assetImageRatio } from "../asset-card-image-prompts.js";
import {
  ASSET_REFERENCE_MODES,
  assetReferenceMode,
  buildAssetCardRevisionState,
} from "../asset-revision-references.js";
import { startNodeGeneration } from "../node-actions.js";
import { el, showModal } from "../overlay.js";

export function openAssetCardPanel(store, nodeId, runtime = null) {
  const node = store.get().nodes[nodeId];
  const draft = node?.params?.assetCardDraft;
  if (!node || !draft) return;
  const modal = el("div", "modal-card visual-asset-panel asset-card-panel");
  render(modal, normalizeAssetCardDraft(draft), assetReferenceMode(node));
  const close = showModal(modal);

  modal.addEventListener("click", async (event) => {
    const action = event.target?.dataset?.action;
    if (action === "close") {
      close();
      return;
    }
    if (action === "save") {
      saveAssetCard(store, nodeId, collect(modal, draft), currentDraft(store, nodeId, draft), selectedReferenceMode(modal));
      await store.flushRuntimeSave?.();
      close();
    }
    if (action === "save-regenerate") {
      saveAssetCard(store, nodeId, collect(modal, draft), currentDraft(store, nodeId, draft), selectedReferenceMode(modal));
      await store.flushRuntimeSave?.();
      const fresh = store.get().nodes[nodeId];
      if (fresh) startNodeGeneration(store, runtime, fresh);
      close();
    }
  });
}

function render(modal, draft, mode) {
  const fields = assetCardFieldsForType(draft.asset_type);
  const currentMode = mode || ASSET_REFERENCE_MODES.LOCALIZED_EDIT;
  modal.innerHTML = `
    <div class="modal-head">
      <div>
        <div class="eyebrow">候选资产卡</div>
        <h3>编辑${assetCardTypeLabel(draft.asset_type, draft.character_subtype)}</h3>
      </div>
      <button class="icon-btn" data-action="close" title="关闭">×</button>
    </div>
    <label class="va-row">名称<input data-field="label" value="${escapeAttr(draft.label)}"></label>
    <label class="va-row">一句话签名<input data-field="signature" value="${escapeAttr(draft.signature)}"></label>
    <div class="va-section-label">特征卡 <small>确认固定前只作为草稿，不进入关键帧约束</small></div>
    ${fields.map(([key, label]) => `
      <label class="va-row va-feature">${label}<input data-card="${escapeAttr(key)}" value="${escapeAttr(draft.feature_card?.[key] || "")}"></label>
    `).join("")}
    <div class="va-section-label">不可变锁定项 <small>每行一条，固定后进入生成约束</small></div>
    <textarea data-field="negative_locks" rows="4">${escapeHtml((draft.negative_locks || []).join("\n"))}</textarea>
    <div class="va-section-label">参考图用途 <small>决定保存并生成时如何使用上传/连接的参考图</small></div>
    <div class="va-type-row va-reference-mode-row">
      <button class="va-type${currentMode === ASSET_REFERENCE_MODES.LOCALIZED_EDIT ? " active" : ""}" data-reference-mode="${ASSET_REFERENCE_MODES.LOCALIZED_EDIT}" type="button" title="按参考图稳定身份，只修改资产卡里改变的细节">局部修订</button>
      <button class="va-type${currentMode === ASSET_REFERENCE_MODES.ORIGINALIZE_IP_SAFE ? " active" : ""}" data-reference-mode="${ASSET_REFERENCE_MODES.ORIGINALIZE_IP_SAFE}" type="button" title="只提取灵感方向，重新设计为原创资产以降低 IP 风险">原创重生</button>
    </div>
    <div class="va-section-label">来源</div>
    <div class="draft-status">${escapeHtml(draft.source_shot_id || "未标记")} · ${escapeHtml(draft.source || "local")}</div>
    <div class="draft-status">保存后生成会重新绘制整张资产图；局部图像编辑未开放，需要 image-edit/mask 能力。</div>
    <div class="modal-actions">
      <button class="ghost-btn" data-action="close">取消</button>
      <button class="primary-btn" data-action="save">保存资产卡</button>
      <button class="primary-btn" data-action="save-regenerate">保存并重新生成资产图</button>
    </div>
  `;
  for (const button of modal.querySelectorAll("[data-reference-mode]")) {
    button.addEventListener("click", () => {
      for (const peer of modal.querySelectorAll("[data-reference-mode]")) peer.classList.toggle("active", peer === button);
    });
  }
}

function collect(modal, prior) {
  const card = {};
  for (const input of modal.querySelectorAll("[data-card]")) {
    const value = String(input.value || "").trim();
    if (value) card[input.dataset.card] = value;
  }
  return normalizeAssetCardDraft({
    ...prior,
    label: field(modal, "label"),
    signature: field(modal, "signature"),
    feature_card: card,
    negative_locks: field(modal, "negative_locks").split(/\r?\n/),
    updated_at: new Date().toISOString(),
  });
}

function saveAssetCard(store, nodeId, draft, previousDraft, referenceMode) {
  store.set((s) => {
    const node = s.nodes[nodeId];
    if (!node) return;
    node.params.assetReferenceMode = referenceMode || ASSET_REFERENCE_MODES.LOCALIZED_EDIT;
    node.params.assetCardDraft = draft;
    node.params.assetCardRevision = buildAssetCardRevisionState(node, previousDraft, draft);
    node.params.asset_prep = {
      ...(node.params.asset_prep || {}),
      status: "card_ready",
      asset_ref: draft.source_asset_ref || node.params.asset_prep?.asset_ref || null,
    };
    node.prompt = assetCardUserAdjustmentText(node);
    if (node.prompt) {
      node.params.assetCardDraft.user_edited_text = node.prompt;
      node.params.assetCardDraft.updated_by_user = true;
    }
    node.content = assetCardText(draft);
    node.title = `${assetCardTypeLabel(draft.asset_type, draft.character_subtype)} · @${draft.label}`;
    node.status = "complete";
    node.params.spec = {
      ...(node.params.spec || {}),
      ratio: assetImageRatio(draft.asset_type),
      count: 1,
    };
  });
}

function selectedReferenceMode(modal) {
  return String(modal.querySelector("[data-reference-mode].active")?.dataset?.referenceMode || ASSET_REFERENCE_MODES.LOCALIZED_EDIT);
}

function currentDraft(store, nodeId, fallback) {
  return normalizeAssetCardDraft(store.get().nodes[nodeId]?.params?.assetCardDraft || fallback);
}

function field(root, name) {
  return String(root.querySelector(`[data-field="${name}"]`)?.value || "").trim();
}

function escapeHtml(value) {
  return String(value || "").replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]),
  );
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
