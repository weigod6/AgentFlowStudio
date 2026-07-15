import { promptPlaceholder } from "./nodes.js";
import { MODELS_BY_NODE_TYPE, findModel, isRemoteVideoModel, videoCapabilitiesForVideoModel } from "./presets/models.js";
import {
  VIDEO_RATIOS, VIDEO_RESOLUTIONS,
  videoSpecLabel,
} from "./presets/specs.js";
import {
  clampVideoDurationLabel,
  videoCapabilitiesFromNode,
  videoDurationOptions,
} from "./presets/video-capabilities.js";
import { showPopover, el } from "./overlay.js";
import { openOptimizer } from "./optimizer.js";
import { openGalleryModal } from "./panels/gallery-modal.js";
import { canRunNodeGeneration, pollNodeVideoGeneration, startNodeGeneration } from "./node-actions.js";
import { icon } from "./icons.js";
import { barSignature, bindBarResizePositioning, positionBar, structureSignature } from "./prompt-bar-position.js";
import { flashTooltip, updateNode } from "./prompt-bar-actions.js";
import { openExpandEditor } from "./prompt-bar-expand.js";
import { bindAssetMentionSuggestions } from "./mention-suggestions.js";
import { assetCardPromptPlaceholder, assetCardUserAdjustmentText } from "./asset-card-image-prompts.js";
import {
  ASSET_REFERENCE_MODES,
  assetReferenceMode,
  canUseAssetReferenceMode,
  buildUserAssetCardRevisionState,
} from "./asset-revision-references.js";
import {
  expandTextIdeaToScript,
  importScriptFileIntoTextNode,
  splitTextNodeToStoryboardNodes,
} from "./script-breakdown.js";

const PROMPT_NODE_TYPES = new Set(["text", "image", "video", "video_merge", "audio", "script", "director", "library"]);

export function renderPromptBar(state, store, runtime) {
  const layer = document.getElementById("prompt-bar-layer");
  const selectedId = state.selection.nodeIds.length === 1 ? state.selection.nodeIds[0] : null;
  const node = selectedId ? state.nodes[selectedId] : null;
  const show = node && PROMPT_NODE_TYPES.has(node.type) && (node.type === "script" || !node.content || state.ui?.promptBarNodeId === node.id);

  let bar = layer.querySelector(".prompt-bar");
  if (!show) {
    if (bar) bar.remove();
    return;
  }

  const signature = barSignature(state, node);
  if (!bar || bar.dataset.signature !== signature) {
    if (bar && bar.dataset.nodeId === node.id && isPromptTextEditing(bar) && bar.dataset.structure === structureSignature(node)) {
      bar.dataset.signature = signature;
      positionBar(bar, state, node);
      return;
    }
    const next = buildBar(store, runtime, node);
    next.dataset.signature = signature;
    next.dataset.structure = structureSignature(node);
    if (bar) bar.replaceWith(next);
    else layer.appendChild(next);
    bar = next;
  }
  syncPromptBarState(bar, node);
  positionBar(bar, state, node);
}

function isPromptTextEditing(bar) {
  const active = document.activeElement;
  return Boolean(bar.contains(active) && ["TEXTAREA", "INPUT"].includes(active?.tagName));
}

function buildBar(store, runtime, node) {
  const bar = el("div", "prompt-bar");
  bar.dataset.nodeId = node.id;
  const p = node.params || {};

  if ((p.attachments || []).length) {
    const chips = el("div", "attach-chips");
    for (const att of p.attachments) {
      const chip = el("button", "attach-chip");
      chip.innerHTML = icon("text", 14);
      chip.title = att.label || att.id;
      chip.appendChild(el("span", "badge", "1"));
      chips.appendChild(chip);
    }
    bar.appendChild(chips);
  }

  if (canUseAssetReferenceMode(node)) {
    bar.appendChild(buildAssetReferenceModeTabs(store, node));
  }

  const textarea = document.createElement("textarea");
  textarea.placeholder = promptTextPlaceholder(node);
  textarea.value = promptTextValue(node);
  textarea.addEventListener("input", () => {
    store.set((s) => {
      s.ui.promptBarNodeId = node.id;
      const n = s.nodes[node.id];
      if (!n) return;
      n.prompt = textarea.value;
      if (n.params?.assetCardDraft) {
        n.params.assetCardDraft.user_edited_text = textarea.value;
        n.params.assetCardDraft.updated_by_user = Boolean(textarea.value.trim());
        if (textarea.value.trim()) {
          n.params.assetCardRevision = buildUserAssetCardRevisionState(
            n,
            n.params.assetCardDraft,
            textarea.value,
            n.params.assetReferenceMode,
          );
        } else if (usesPromptBarAssetCardRevision(n.params.assetCardRevision)) {
          delete n.params.assetCardRevision;
        }
      } else if (n.type === "text" || n.type === "script") {
        n.content = textarea.value;
        if (String(n.params?.scriptExpansionSourceIdea || "").trim() !== textarea.value.trim()) {
          delete n.params.scriptExpansionSourceIdea;
          delete n.params.scriptExpansionState;
          if (n.params.scriptInputMode === "idea_expanded_script" || n.params.scriptInputMode === "idea_expanded_script_fallback") {
            delete n.params.scriptInputMode;
          }
        }
      }
      delete n.params.lastOptimizedPromptPlain;
    }, { history: false });
  });
  textarea.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      if (!canRunNodeGeneration(node) || (node.type === "video" && !isRemoteVideoModel(node.params?.model))) {
        flashTooltip(textarea, "当前节点不支持直接生成，请使用该节点的专用操作。");
        return;
      }
      runPromptBarGeneration(store, runtime, node);
    }
  });
  bindAssetMentionSuggestions(textarea, store, node.id);
  bar.appendChild(textarea);

  const expand = el("button", "expand-btn");
  expand.innerHTML = icon("expand", 14);
  expand.title = "放大编辑";
  expand.addEventListener("click", () => openExpandEditor(store, runtime, node));
  bar.appendChild(expand);

  bar.appendChild(buildBottomRow(store, runtime, node, textarea));
  bindBarResizePositioning(bar, store, node.id);
  syncPromptBarState(bar, node);
  return bar;
}

function buildAssetReferenceModeTabs(store, node) {
  const wrap = el("div", "mode-tabs asset-reference-mode-tabs");
  const current = assetReferenceMode(node);
  const modes = [
    [ASSET_REFERENCE_MODES.LOCALIZED_EDIT, "局部修订", "按参考图稳定身份，只修改你写明的细节"],
    [ASSET_REFERENCE_MODES.ORIGINALIZE_IP_SAFE, "原创重生", "只提取灵感方向，重新设计以降低 IP 风险"],
  ];
  for (const [mode, label, title] of modes) {
    const tab = el("button", `mode-tab${current === mode ? " active" : ""}`, label);
    tab.type = "button";
    tab.title = title;
    tab.dataset.mode = mode;
    tab.setAttribute("aria-pressed", current === mode ? "true" : "false");
    tab.addEventListener("click", () => {
      store.set((s) => {
        const n = s.nodes[node.id];
        if (!n) return;
        n.params.assetReferenceMode = mode;
        if (n.params.assetCardDraft && String(n.prompt || n.params.assetCardDraft.user_edited_text || "").trim()) {
          n.params.assetCardRevision = buildUserAssetCardRevisionState(
            n,
            n.params.assetCardDraft,
            n.prompt || n.params.assetCardDraft.user_edited_text,
            mode,
          );
        } else if (n.params.assetCardRevision) {
          n.params.assetCardRevision.mode = mode;
        }
        delete n.params.lastOptimizedPromptPlain;
      }, { history: false });
      syncAssetReferenceModeTabs(wrap, mode);
    });
    wrap.appendChild(tab);
  }
  return wrap;
}

function buildToolChips(store, node, defs) {
  const wrap = el("div", "tool-chips");
  for (const [label, iconName, onClick] of defs) {
    const chip = el("button", "tool-chip");
    chip.innerHTML = `<span class="tc-icon">${icon(iconName, 14)}</span><span>${label}</span>`;
    if (label === "特效" && node.params.effect) chip.classList.add("active");
    chip.addEventListener("click", onClick);
    wrap.appendChild(chip);
  }
  return wrap;
}

function buildBottomRow(store, runtime, node, textarea) {
  const row = el("div", "bar-row");
  const p = node.params;
  const model = findModel(node.type, p.model);

  const modelBtn = el("button", "bar-select");
  modelBtn.innerHTML = `<span class="sel-icon">${icon("sparkle1", 13)}</span><span>${model.name}</span><span class="caret">▾</span>`;
  modelBtn.addEventListener("click", () => openModelPopover(store, node, modelBtn));
  row.appendChild(modelBtn);

  if (node.type === "video" || node.type === "video_merge") {
    const specBtn = el("button", "bar-select");
    specBtn.innerHTML = `<span class="sel-icon">${icon("frames", 13)}</span><span>${videoSpecLabel(p.spec)}</span><span class="caret">▾</span>`;
    specBtn.addEventListener("click", () => openVideoSpecPopover(store, node, specBtn));
    row.appendChild(specBtn);

    const motionBtn = el("button", `bar-tool${p.motion ? " active" : ""}`);
    motionBtn.innerHTML = `${icon("filmcam", 14)}<span>${p.motion || "运镜"}</span>`;
    motionBtn.addEventListener("click", () => openGalleryModal(store, "motions", node.id));
    row.appendChild(motionBtn);
  }

  if (node.type === "text") {
    row.appendChild(textAction("upload", "导入剧本", () => importScriptFileIntoTextNode(store, node, textarea)));
    row.appendChild(textAction("sparkles", "扩写剧本", () => expandTextIdeaToScript(store, runtime, node, textarea)));
    row.appendChild(textAction("frames", "拆分分镜", () => {
      splitTextNodeToStoryboardNodes(store, node, runtime).then((created) => {
        const current = store.get().nodes[node.id];
        if (!created.length && current?.params?.storyboardBreakdownState?.status !== "failed") {
          flashTooltip(textarea, "先输入或导入剧本");
        }
      });
    }));
  }

  row.appendChild(el("span", "row-spacer"));

  const optimizeBtn = el("button", "bar-tool optimize-btn");
  optimizeBtn.dataset.action = "optimize-prompt";
  optimizeBtn.innerHTML = `${icon("sparkles", 14)}<span>优化</span>`;
  optimizeBtn.title = "优化提示词";
  optimizeBtn.addEventListener("click", () => {
    if (!optimizableNodeText(store.get().nodes[node.id], textarea).trim()) {
      flashTooltip(optimizeBtn, "先输入提示词");
      return;
    }
    openOptimizer(store, runtime, node.id, optimizeBtn, textarea);
  });
  row.appendChild(optimizeBtn);

  const send = el("button", "send-btn");
  const canVideo = node.type === "video" && isRemoteVideoModel(node.params?.model);
  const shouldPollVideo = canVideo && node.status === "generating" && Boolean(node.params?.lastVideoJobId);
  send.innerHTML = shouldPollVideo ? icon("retry", 15) : icon("arrowUp", 15);
  const canSend = node.type === "image" || canVideo;
  if (node.type === "video" && node.params?.videoRevision?.enabled) send.title = "提交视频重生成尝试；不是局部编辑";
  else if (canSend) send.title = "生成";
  else if (node.type === "video") send.title = "当前视频模型不支持直接生成";
  else send.title = "当前节点不支持直接生成，请使用该节点的专用操作";
  if (shouldPollVideo) send.title = "继续轮询";
  send.disabled = !canSend;
  send.addEventListener("click", () => runPromptBarGeneration(store, runtime, node));
  row.appendChild(send);

  return row;
}

function textAction(iconName, label, onClick) {
  const button = el("button", "bar-tool text-script-tool");
  button.innerHTML = `${icon(iconName, 14)}<span>${label}</span>`;
  button.title = label;
  button.addEventListener("click", onClick);
  return button;
}

function optimizableNodeText(node, textarea = null) {
  const currentInput = String(textarea?.value || "").trim();
  if (currentInput) return currentInput;
  if (!node) return "";
  if (isTextContentNode(node)) return String(node.content || node.prompt || "");
  return String(node.prompt || node.content || "");
}

function isTextContentNode(node) {
  return node.type === "text" || node.type === "script";
}

export function syncPromptBarState(bar, node) {
  const task = activePromptTaskProgress(node);
  const running = Boolean(task);
  bar.classList.toggle("optimizing", running);
  syncAssetReferenceModeTabs(bar, assetReferenceMode(node));
  const textarea = bar.querySelector("textarea");
  if (textarea) {
    textarea.classList.toggle("prompt-shimmer", running);
    const expectedPrompt = promptTextValue(node);
    if (!isPromptTextEditing(bar) && textarea.value !== expectedPrompt) {
      textarea.value = expectedPrompt;
    }
    const expectedPlaceholder = promptTextPlaceholder(node);
    if (textarea.placeholder !== expectedPlaceholder) {
      textarea.placeholder = expectedPlaceholder;
    }
  }
  const optimizeBtn = bar.querySelector('[data-action="optimize-prompt"]');
  if (optimizeBtn) {
    optimizeBtn.classList.toggle("busy", running);
    optimizeBtn.disabled = running;
    const label = optimizeBtn.querySelector("span");
    if (label) label.textContent = running ? promptTaskLabel(task) : "优化";
  }
}

function syncAssetReferenceModeTabs(root, activeMode) {
  if (!root?.querySelectorAll) return;
  for (const tab of root.querySelectorAll(".asset-reference-mode-tabs [data-mode]")) {
    const active = tab.dataset.mode === activeMode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-pressed", active ? "true" : "false");
  }
}

function activePromptTaskProgress(node) {
  const tasks = [
    { label: "优化", state: node.params?.promptOptimizationState },
    { label: "扩写", state: node.params?.scriptExpansionState },
    { label: "拆分", state: node.params?.storyboardBreakdownState },
  ];
  return tasks.find((item) => item.state?.status === "running") || null;
}

function promptTaskLabel(task) {
  const percent = Number(task?.state?.percent);
  if (Number.isFinite(percent)) return `${task.label} ${Math.max(0, Math.min(100, Math.round(percent)))}%`;
  return `${task?.label || "处理"}中`;
}

function promptTextValue(node) {
  if (node?.params?.assetCardDraft) return assetCardUserAdjustmentText(node);
  if (isTextContentNode(node)) return node?.content || node?.prompt || "";
  return node?.prompt || node?.content || "";
}

function promptTextPlaceholder(node) {
  const p = node?.params || {};
  if (p.assetCardDraft) return assetCardPromptPlaceholder(p.assetCardDraft.asset_type);
  return promptPlaceholder(node?.type, p.spec?.mode);
}

function usesPromptBarAssetCardRevision(revision) {
  return Array.isArray(revision?.changed_fields)
    && revision.changed_fields.some((item) => item?.field === "user_instruction");
}

function runPromptBarGeneration(store, runtime, node) {
  const fresh = store.get().nodes[node.id] || node;
  if (fresh.type === "video" && fresh.status === "generating" && fresh.params?.lastVideoJobId) {
    pollNodeVideoGeneration(store, runtime, fresh);
    return;
  }
  if (!canRunNodeGeneration(fresh)) return;
  startNodeGeneration(store, runtime, fresh);
}

function openModelPopover(store, node, anchor) {
  const models = MODELS_BY_NODE_TYPE[node.type] || [];
  const pop = el("div");
  pop.style.minWidth = "270px";
  for (const m of models) {
    const item = el("button", `menu-item${node.params.model === m.id ? " selected" : ""}`);
    item.innerHTML = `<span class="mi-icon">${icon("sparkle1", 13)}</span><span>${m.name}${m.desc ? `<span class="mi-sub">${m.desc}</span>` : ""}</span><span class="mi-meta">${m.eta}</span>`;
    item.addEventListener("click", () => {
      updateNode(store, node.id, (n) => {
        n.params.model = m.id;
        if (n.type === "video") {
          const capabilities = videoCapabilitiesForVideoModel(m.id);
          n.params.spec = {
            ...(n.params.spec || {}),
            duration: clampVideoDurationLabel(n.params.spec?.duration || "5s", capabilities),
          };
        }
      });
      close();
    });
    pop.appendChild(item);
  }
  const close = showPopover(anchor, pop, { place: "top" });
}

function openVideoSpecPopover(store, node, anchor) {
  const pop = el("div", "spec-pop");
  const videoCapabilities = videoCapabilitiesFromNode(node, videoCapabilitiesForVideoModel(node.params?.model));
  pop.appendChild(specSection("比例", VIDEO_RATIOS, node.params.spec.ratio, (v) =>
    updateNode(store, node.id, (n) => { n.params.spec.ratio = v; })));
  pop.appendChild(specSection("分辨率", VIDEO_RESOLUTIONS, node.params.spec.resolution, (v) =>
    updateNode(store, node.id, (n) => { n.params.spec.resolution = v; })));
  pop.appendChild(specSection("时长", videoDurationOptions(videoCapabilities), node.params.spec.duration, (v) =>
    updateNode(store, node.id, (n) => { n.params.spec.duration = v; })));
  showPopover(anchor, pop, { place: "top" });
}

function specSection(label, options, current, onPick, ratio = false) {
  const section = el("div", "spec-section");
  section.appendChild(el("div", "spec-label", label));
  const wrap = el("div", "spec-options");
  for (const opt of options) {
    const value = typeof opt === "object" ? opt.value : opt;
    const text = typeof opt === "object" ? opt.label : opt;
    const disabled = Boolean(typeof opt === "object" && opt.disabled);
    const btn = el("button", `spec-opt${ratio ? " ratio" : ""}${current === value ? " active" : ""}`, text);
    btn.disabled = disabled;
    if (disabled) btn.title = opt.reason || "unsupported";
    btn.addEventListener("click", () => {
      if (disabled) return;
      onPick(value);
      for (const sib of wrap.children) sib.classList.toggle("active", sib === btn);
    });
    wrap.appendChild(btn);
  }
  section.appendChild(wrap);
  return section;
}
