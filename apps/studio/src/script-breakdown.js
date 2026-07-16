import { createNode, connect } from "./nodes.js";
import { assetAutoBindingGraph, nodeReferenceStackForGraphBoundAssets } from "./asset-auto-binding-refs.js";
import { buildOptimizationRequest, normalizeOptimization } from "./optimizer-contract.js";
import { formatRuntimeError } from "./runtime-error-utils.js";
import { SCRIPT_UPLOAD_ACCEPT, readScriptFileText, safeFileName, scriptFileExtension } from "./script-file-import.js";
import { normalizeShotAssetRefsWithDiagnostics, refineStructuredShotAssets, structuredShotFromSegment, structuredShotText } from "./structured-shot.js";

const SHOT_MARKER_RE = /^\s*(第?\s*\d+\s*[镜幕场]|镜头\s*\d+|分镜\s*\d+|场景\s*\d+|scene\s*\d+|shot\s*\d+)/i;
const STORYBOARD_PLACEHOLDER_RE = /(推进主体|展示变化|收束结果|保留下一步生成关键帧所需的信息)/;
const SCRIPT_OPTIMIZER_LABEL_RE = /^\s*(意图|角色\/主体|人物\/主体|主体|场景\/美术|动作\/情节|镜头\/构图|灯光|运动\/时间推进|连续性|负面约束|Intent|Subject\/Character|Scene\/Production Design|Action\/Beat|Camera\/Framing|Lighting|Motion\/Temporal Progression|Continuity|Negative Constraints)\s*[：:]/i;
const SCRIPT_WRAPPER_RE = /(请把下面的一句话扩写成正式短视频剧本正文|输出要求|原始想法|script_expansion_contract|formal_script_before_storyboard_breakdown|storyboard_placeholder_outline|source_idea)/i;
const SCRIPT_TEMPLATE_FILLER_RE = /(推进主体|展示变化|收束结果|主角或核心物体|核心物体|保留下一步拆分分镜|Primary character|Primary scene)/;
const EXPANDED_SCRIPT_MODES = new Set(["idea_expanded_script", "idea_expanded_script_fallback"]);

export function importScriptFileIntoTextNode(store, node, textarea = null) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = SCRIPT_UPLOAD_ACCEPT;
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const text = await readScriptFileText(file);
      updateTextNode(store, node.id, text, {
        title: trimFileTitle(file.name),
        scriptInputMode: "full_script_upload",
        scriptImportState: {
          status: "complete",
          file_name: safeFileName(file.name),
          file_type: scriptFileExtension(file.name),
          completed_at: new Date().toISOString(),
        },
      });
      if (textarea) textarea.value = text;
    } catch (error) {
      setScriptImportError(store, node.id, safeScriptImportError(error));
    }
  }, { once: true });
  input.click();
}

export async function expandTextIdeaToScript(store, runtime, node, textarea = null) {
  const fresh = store.get().nodes[node.id] || node;
  const idea = scriptExpansionSourceIdea(fresh);
  if (!idea) return;
  setScriptExpansionState(store, fresh.id, "running", idea);
  textarea?.classList?.add("prompt-shimmer");
  try {
    const request = buildOptimizationRequest(store.get(), {
      ...fresh,
      type: "script",
      prompt: formalScriptExpansionPrompt(idea),
    });
    request.node_type = "script";
    request.generation_target = "script";
    request.node_parameters = {
      ...(request.node_parameters || {}),
      script_expansion_contract: "formal_script_before_storyboard_breakdown",
      script_generation_mode: "idea_to_script",
      source_idea: idea.slice(0, 600),
      forbidden_output: "storyboard_placeholder_outline",
      llm_provider: "prompt_optimizer",
      llm_model: "prompt-optimizer",
      remote_optimizer_required: true,
    };
    const response = runtime?.optimizePrompt ? await runtime.optimizePrompt(request) : null;
    const outcome = response ? normalizeOptimization(response, request) : null;
    const script = normalizeExpandedScript(outcome?.plain || outcome?.optimized, idea);
    updateTextNode(store, fresh.id, script, {
      scriptInputMode: "idea_expanded_script",
      scriptExpansionSourceIdea: idea.slice(0, 600),
      scriptExpansionState: { status: "complete", percent: 100, completed_at: new Date().toISOString() },
    });
    if (textarea) textarea.value = script;
  } catch (error) {
    if (runtime?.optimizePrompt) {
      setScriptExpansionError(store, fresh.id, error);
      return;
    }
    const script = draftScriptFromIdea(idea);
    updateTextNode(store, fresh.id, script, {
      scriptInputMode: "idea_expanded_script_fallback",
      scriptExpansionSourceIdea: idea.slice(0, 600),
      scriptExpansionState: { status: "fallback", percent: 100, completed_at: new Date().toISOString() },
    });
    if (textarea) textarea.value = script;
  } finally {
    textarea?.classList?.remove("prompt-shimmer");
  }
}

export async function splitTextNodeToStoryboardNodes(store, node, runtime = null) {
  const fresh = store.get().nodes[node.id] || node;
  const source = String(fresh.content || fresh.prompt || "").trim();
  if (!source) return [];
  setStoryboardBreakdownState(store, fresh.id, "running");
  const breakdown = await loadStoryboardBreakdown(store, runtime, fresh, source);
  const shots = breakdown.shots;
  if (!shots.length) return [];
  const createdIds = [];
  const x = fresh.x + fresh.w + 180;
  const bindingGraph = assetAutoBindingGraph(breakdown.asset_auto_binding_graph);
  for (const [index, shot] of shots.entries()) {
    const allowLocalAssetInference = breakdown.mode === "local_fallback";
    const structuredShot = refineStructuredShotAssets(
      normalizeStoryboardShot(shot, index + 1),
      "",
      { inferMissingAssets: allowLocalAssetInference },
    );
    const shotText = structuredShotText(structuredShot);
    const shotNode = createNode(store, "script", x, fresh.y + index * 230);
    const referenceStack = nodeReferenceStackForGraphBoundAssets(bindingGraph, structuredShot, shotNode.id);
    store.set((s) => {
      const target = s.nodes[shotNode.id];
      if (!target) return;
      target.title = `分镜 ${String(index + 1).padStart(2, "0")}`;
      target.prompt = shotText;
      target.content = shotText;
      target.status = "complete";
      target.h = Math.max(220, Math.min(360, 112 + Math.ceil(shotText.length / 36) * 15));
      target.params.sourceTextNodeId = fresh.id;
      target.params.scriptSegmentIndex = index + 1;
      target.params.structuredShot = structuredShot;
      target.params.shotAssetRefs = structuredShot.asset_refs;
      if (bindingGraph) target.params.assetAutoBindingGraph = bindingGraph;
      if (referenceStack) target.params.nodeReferenceStack = referenceStack;
      target.params.assetPrepState = {
        status: "pending_user_review",
        downstream_node_ids: [],
        updated_at: new Date().toISOString(),
      };
    });
    connect(store, fresh.id, shotNode.id);
    createdIds.push(shotNode.id);
  }
  store.set((s) => {
    const sourceNode = s.nodes[fresh.id];
    if (!sourceNode) return;
    const fallbackNotice = storyboardFallbackNotice(breakdown);
    sourceNode.params.storyboardBreakdown = {
      status: "shots_ready_for_review",
      mode: breakdown.mode,
      shot_count: createdIds.length,
      downstream_node_ids: createdIds,
      asset_node_ids: [],
      asset_nodes_created: false,
      provider_calls_started: Boolean(breakdown.provider_calls_started),
      assetCardCandidates: breakdown.asset_card_candidates || null,
      assetCardCandidateArtifactId: breakdown.artifacts?.asset_card_candidates?.artifact_id || "",
      productionGraph: breakdown.production_graph || null,
      productionGraphArtifactId: breakdown.artifacts?.production_graph_snapshot?.artifact_id || "",
      assetAutoBindingGraph: bindingGraph,
      assetAutoBindingGraphArtifactId: breakdown.artifacts?.asset_auto_binding_graph?.artifact_id || "",
      fallback_visible_to_user: Boolean(fallbackNotice),
      fallback_reason: fallbackNotice?.reason || "",
      fallback_message: fallbackNotice?.message || "",
      discard_reason: breakdown.discard_reason || breakdown.safe_manifest?.discard_reason || "",
      updated_at: new Date().toISOString(),
    };
    sourceNode.params.storyboardBreakdownState = {
      status: fallbackNotice ? "fallback" : "complete",
      percent: 100,
      label: "分镜拆解",
      message: fallbackNotice?.message || "",
      completed_at: new Date().toISOString(),
    };
    if (fallbackNotice) {
      sourceNode.params.generationPolicyStatus = "needs_attention";
      sourceNode.params.generationStatusDetail = "分镜拆解已生成本地保守结果，但未证明 LLM provider 正常完成。";
      sourceNode.params.generationBlockedReason = fallbackNotice.message;
      sourceNode.params.generationNextAction = "检查 LLM gate/provider 配置；继续资产识别前请人工复核分镜主体、资产和对白。";
    } else {
      delete sourceNode.params.generationPolicyStatus;
      delete sourceNode.params.generationStatusDetail;
      delete sourceNode.params.generationBlockedReason;
      delete sourceNode.params.generationNextAction;
    }
    sourceNode.status = "complete";
  });
  return createdIds;
}

export function splitScriptIntoShots(source) {
  const text = String(source || "").trim();
  if (!text) return [];
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const marked = splitByMarkedLines(lines);
  if (marked.length > 1) return marked;
  const paragraphs = text.split(/\n\s*\n/).map(cleanSegment).filter(Boolean);
  if (paragraphs.length > 1) return paragraphs.slice(0, 24);
  return sentenceChunks(text).slice(0, 24);
}

function splitByMarkedLines(lines) {
  const chunks = [];
  let current = [];
  for (const line of lines) {
    if (SHOT_MARKER_RE.test(line) && current.length) {
      chunks.push(cleanSegment(current.join("\n")));
      current = [];
    }
    current.push(line);
  }
  if (current.length) chunks.push(cleanSegment(current.join("\n")));
  return chunks.filter(Boolean);
}

function sentenceChunks(text) {
  const sentences = text
    .split(/(?<=[。！？!?；;])\s*/)
    .map(cleanSegment)
    .filter(Boolean);
  if (!sentences.length) return [text];
  const targetCount = Math.max(1, Math.min(Math.max(Math.ceil(sentences.length / 2), Math.ceil(text.length / 180)), sentences.length, 12));
  return balancedSentenceChunks(sentences, targetCount);
}

function balancedSentenceChunks(sentences, targetCount) {
  const chunks = [];
  for (let index = 0; index < targetCount; index += 1) {
    const start = Math.round(index * sentences.length / targetCount);
    const end = Math.round((index + 1) * sentences.length / targetCount);
    const chunk = sentences.slice(start, end).join("");
    if (chunk) chunks.push(chunk);
  }
  return chunks;
}

function scriptExpansionSourceIdea(node) {
  const current = String(node?.prompt || node?.content || "").trim();
  const stored = String(node?.params?.scriptExpansionSourceIdea || "").trim();
  if (stored && EXPANDED_SCRIPT_MODES.has(node?.params?.scriptInputMode)) return stored;
  return current;
}

function normalizeExpandedScript(value, idea) {
  const text = String(value || "").trim();
  if (!text || looksLikeInvalidScriptBody(text)) return draftScriptFromIdea(idea);
  return text;
}

function looksLikeInvalidScriptBody(text) {
  return looksLikeStoryboardPlaceholder(text)
    || looksLikePromptWrapperEcho(text)
    || looksLikeOptimizerSectionOutput(text)
    || SCRIPT_TEMPLATE_FILLER_RE.test(text);
}

function looksLikeStoryboardPlaceholder(text) {
  const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const markerCount = lines.filter((line) => SHOT_MARKER_RE.test(line)).length;
  return markerCount >= 3 && STORYBOARD_PLACEHOLDER_RE.test(text);
}

function looksLikePromptWrapperEcho(text) {
  return SCRIPT_WRAPPER_RE.test(String(text || ""));
}

function looksLikeOptimizerSectionOutput(text) {
  const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return lines.filter((line) => SCRIPT_OPTIMIZER_LABEL_RE.test(line)).length >= 2;
}

function draftScriptFromIdea(idea) {
  const clean = cleanSegment(idea);
  const profile = fallbackStoryProfile(clean);
  return [
    `片名：《${profile.title}》`,
    "",
    `${profile.protagonist}出现在${profile.scene}。这一刻看似只是${clean}，但${profile.mood}的气氛已经压在画面里：${profile.atmosphere}。`,
    "",
    `时间缓慢推进，${profile.name}先是保持原来的状态，随后被一个细小异常打断。${profile.actionProgression}，让观众意识到这不是静止的概念展示，而是一段正在发生的故事。`,
    "",
    `转折来自${profile.turnTrigger}。${profile.discovery}，${profile.name}必须在继续停留和立刻行动之间做出反应。`,
    "",
    `结尾停在${profile.endingImage}。${profile.endingHook}，为下一步分镜拆分留下清楚的人物、场景、动作和悬念。`,
  ].join("\n");
}

function fallbackStoryProfile(clean) {
  const lower = clean.toLowerCase();
  if (clean.includes("睡") || lower.includes("sleep")) {
    return {
      title: "沉睡的门铃",
      name: "沈眠",
      protagonist: "主角沈眠独自躺在清晨还没亮透的出租屋里",
      scene: "一间窗帘半掩、只剩空调低声的房间",
      mood: "安静、压抑又带一点悬疑",
      atmosphere: "灰蓝色天光爬上床沿，床头旧闹钟停在六点十七分",
      actionProgression: "她在梦中翻身，手指碰到枕边一张被折起的车票，呼吸忽然变得急促",
      turnTrigger: "门外三下很轻的敲门声",
      discovery: "门缝下没有人影，只有一枚还带着雨水的钥匙被慢慢推了进来",
      endingImage: "沈眠坐起身握住钥匙、望向门口的背影",
      endingHook: "门外传来一个熟悉却不该出现的声音，低声叫出她的名字",
    };
  }
  if (clean.includes("机器人") || lower.includes("robot")) {
    return {
      title: "屋顶星光协议",
      name: "遥星R-17",
      protagonist: "未来机器人遥星R-17站在风声很低的屋顶边缘",
      scene: "远离城市中心的乡村屋顶平台",
      mood: "孤独、沉静又带着诗意",
      atmosphere: "屋檐下的旧灯泡微微摇晃，星光落在金属外壳上",
      actionProgression: "它抬起头校准星图，却发现胸口的旧信号灯第一次亮起",
      turnTrigger: "天空中一颗本不该移动的星点",
      discovery: "星点传回的不是坐标，而是一段来自多年以前的人类童声",
      endingImage: "遥星R-17把手伸向夜空、信号灯持续闪烁的剪影",
      endingHook: "那段童声问它是否还记得回家的路",
    };
  }
  const compact = clean.replace(/[，。！？；、,.!?;:：\s]+/g, "");
  return {
    title: (compact || "未完成的信号").slice(0, 12),
    name: "林澈",
    protagonist: "主角林澈站在一处被晨光切开的安静空间里",
    scene: "兼具现实细节和故事压力的室内场景",
    mood: "克制、微妙又暗含转折",
    atmosphere: "桌面上的小物件、窗外的声音和人物的停顿共同压低节奏",
    actionProgression: "他先试图维持平静，随后被一个与原本状态不相符的细节吸引",
    turnTrigger: "一件原本不该移动的物品突然改变位置",
    discovery: "那个细节把普通瞬间变成需要选择的故事节点",
    endingImage: "林澈停在光影交界处、回望身后的动作",
    endingHook: "他发现自己并不是这个场景里唯一清醒的人",
  };
}

function formalScriptExpansionPrompt(idea) {
  return [
    "请把下面的一句话扩写成正式短视频剧本正文，而不是分镜列表。",
    "输出要求：",
    "- 先给片名，再给连续叙事正文。",
    "- 明确角色、场景、情绪、动作变化和结尾。",
    "- 不要输出“分镜 01/02/03/04”，不要写占位句，不要写“推进主体/展示变化/收束结果”。",
    "- 文字要能在下一步再拆分成分镜。",
    "",
    `原始想法：${cleanSegment(idea)}`,
  ].join("\n");
}

async function loadStoryboardBreakdown(store, runtime, node, source) {
  if (runtime?.breakdownStoryboard) {
    try {
      const payload = await runtime.breakdownStoryboard({
        node_id: node.id,
        script_text: source,
        target_platform: "short_video",
        style: "cinematic",
        node_parameters: {
          llm_provider: node.params?.llm_provider || node.params?.llmProvider || "prompt_optimizer",
        },
        generated_at: new Date().toISOString(),
      });
      const shots = normalizeStoryboardShotList(payload?.shots);
      if (shots.length) {
        return {
          shots,
          mode: payload?.safe_manifest?.status || "runtime_storyboard_breakdown",
          provider_calls_started: Boolean(payload?.provider_calls_started),
          provider_gate: payload?.provider_gate || payload?.safe_manifest?.provider_gate || null,
          safe_manifest: payload?.safe_manifest || null,
          fallback_visible_to_user: Boolean(payload?.fallback_visible_to_user || payload?.safe_manifest?.fallback_visible_to_user),
          fallback_reason: payload?.fallback_reason || payload?.safe_manifest?.fallback_reason || "",
          fallback_message: payload?.fallback_message || payload?.safe_manifest?.fallback_message || "",
          discard_reason: payload?.safe_manifest?.discard_reason || "",
          asset_card_candidates: payload?.asset_card_candidates || null,
          production_graph: payload?.production_graph || null,
          asset_auto_binding_graph: payload?.asset_auto_binding_graph || null,
          artifacts: payload?.artifacts || {},
        };
      }
      setStoryboardBreakdownError(store, node.id, new Error("分镜拆解服务未返回可用分镜。"));
      return { shots: [], mode: "runtime_failed", provider_calls_started: Boolean(payload?.provider_calls_started) };
    } catch (error) {
      setStoryboardBreakdownError(store, node.id, error);
      return { shots: [], mode: "runtime_failed", provider_calls_started: false };
    }
  }
  return {
    shots: splitScriptIntoShots(source).map((segment, index) => structuredShotFromSegment(segment, index + 1)),
    mode: "local_fallback",
    provider_calls_started: false,
    fallback_visible_to_user: true,
    fallback_reason: "runtime_unavailable",
    fallback_message: "Runtime 分镜服务不可用，已使用浏览器本地保守拆分；结果需要人工复核。",
  };
}

function storyboardFallbackNotice(breakdown) {
  const manifest = breakdown?.safe_manifest || {};
  const visible = Boolean(
    breakdown?.fallback_visible_to_user
      || manifest.fallback_visible_to_user
      || breakdown?.mode === "local_fallback",
  );
  if (!visible) return null;
  const reason = String(breakdown?.fallback_reason || manifest.fallback_reason || fallbackReasonFromMode(breakdown?.mode) || "");
  const message = String(
    breakdown?.fallback_message
      || manifest.fallback_message
      || fallbackMessageForReason(reason, breakdown?.discard_reason || manifest.discard_reason),
  );
  return { reason, message };
}

function fallbackReasonFromMode(mode) {
  return mode === "local_fallback" ? "local_fallback" : "";
}

function fallbackMessageForReason(reason, discardReason = "") {
  if (reason === "llm_gate_blocked") return "LLM gate 未开启，已使用本地保守分镜；结果需要人工复核后再继续资产识别。";
  if (reason === "provider_call_failed") return "LLM provider 调用失败，已使用本地保守分镜；请检查服务配置或稍后重试。";
  if (reason === "provider_output_discarded") {
    return `LLM 输出未被采用，已回退到本地保守分镜；${discardReason ? `原因：${discardReason}` : "原因：provider 输出未通过结构化校验"}。`;
  }
  if (reason === "runtime_unavailable") return "Runtime 分镜服务不可用，已使用浏览器本地保守拆分；结果需要人工复核。";
  return "已使用本地保守分镜；继续前请人工复核分镜主体、资产和对白。";
}

function normalizeStoryboardShotList(value) {
  if (!Array.isArray(value)) return [];
  return value.map((shot, index) => normalizeStoryboardShot(shot, index + 1));
}

function normalizeStoryboardShot(shot, fallbackIndex) {
  if (typeof shot === "string") return structuredShotFromSegment(shot, fallbackIndex);
  const source = String(shot?.source_text || shot?.description || "").trim();
  const fallback = structuredShotFromSegment(source, fallbackIndex);
  const hasExplicitAssetRefs = Array.isArray(shot?.asset_refs);
  const normalizedAssets = hasExplicitAssetRefs
    ? normalizeShotAssetRefsWithDiagnostics(
        shot.asset_refs.map((asset, index) => normalizeAssetRef(asset, index)).filter(Boolean),
        source || String(shot?.description || ""),
      )
    : { asset_refs: fallback.asset_refs, dropped_asset_ref_diagnostics: fallback.dropped_asset_ref_diagnostics || [] };
  const assetRefs = normalizedAssets.asset_refs || [];
  const droppedAssetRefDiagnostics = [
    ...(Array.isArray(shot?.dropped_asset_ref_diagnostics) ? shot.dropped_asset_ref_diagnostics : []),
    ...((normalizedAssets && normalizedAssets.dropped_asset_ref_diagnostics) || []),
  ];
  return {
    ...fallback,
    shot_id: String(shot?.shot_id || fallback.shot_id),
    index: Number(shot?.index || fallbackIndex),
    duration: String(shot?.duration || fallback.duration),
    description: String(shot?.description || fallback.description),
    shot_size: String(shot?.shot_size || fallback.shot_size),
    light_atmosphere: String(shot?.light_atmosphere || fallback.light_atmosphere),
    camera_motion: String(shot?.camera_motion || fallback.camera_motion),
    dialogue: String(shot?.dialogue || fallback.dialogue),
    sound: String(shot?.sound || fallback.sound),
    asset_refs: assetRefs,
    dropped_asset_ref_diagnostics: droppedAssetRefDiagnostics,
    source_text: source || fallback.source_text,
  };
}

function normalizeAssetRef(asset, index) {
  if (!asset || typeof asset !== "object") return null;
  const label = String(asset.display_name || asset.label || asset.name || "").trim().slice(0, 40);
  if (!label) return null;
  const type = ["character", "scene", "prop"].includes(asset.asset_type) ? asset.asset_type : "character";
  return {
    label,
    display_name: String(asset.display_name || label),
    asset_id: String(asset.asset_id || `candidate:${type}:${index + 1}`),
    graph_asset_id: String(asset.graph_asset_id || asset.graphAssetId || ""),
    asset_type: type,
    status: String(asset.status || "candidate"),
    source: String(asset.source || "llm"),
    descriptive_signature: String(asset.descriptive_signature || ""),
    evidence_modality: String(asset.evidence_modality || ""),
    visual_evidence_span: String(asset.visual_evidence_span || ""),
    modality_gate_status: String(asset.modality_gate_status || ""),
    name_source: String(asset.name_source || ""),
    provisional_name: Boolean(asset.provisional_name),
  };
}

function updateTextNode(store, nodeId, prompt, params = {}) {
  store.set((s) => {
    const node = s.nodes[nodeId];
    if (!node) return;
    node.prompt = prompt;
    node.content = prompt;
    node.status = "complete";
    Object.assign(node.params, params);
    if (params.title) node.title = params.title;
  });
}

function setScriptExpansionState(store, nodeId, status, visibleText = "") {
  store.set((s) => {
    const node = s.nodes[nodeId];
    if (!node) return;
    node.params.scriptExpansionState = {
      status,
      percent: status === "running" ? 18 : 100,
      label: "剧本扩写",
      started_at: new Date().toISOString(),
    };
    if (status === "running" && visibleText) {
      node.content = node.content || visibleText;
      node.prompt = visibleText;
      node.status = "generating";
    }
  }, { history: false, persist: false });
}

function setScriptExpansionError(store, nodeId, error) {
  const message = formatRuntimeError(error, "剧本扩写失败。");
  store.set((s) => {
    const node = s.nodes[nodeId];
    if (!node) return;
    node.status = "error";
    node.params.scriptExpansionState = {
      status: "failed",
      percent: 100,
      label: "剧本扩写",
      message,
      completed_at: new Date().toISOString(),
    };
    node.params.generationPolicyStatus = "needs_attention";
    node.params.generationStatusDetail = "剧本扩写未完成。";
    node.params.generationBlockedReason = message;
    node.params.generationNextAction = "确认生成服务可用后重试剧本扩写。";
  });
}

function setStoryboardBreakdownState(store, nodeId, status, message = "") {
  store.set((s) => {
    const node = s.nodes[nodeId];
    if (!node) return;
    node.params.storyboardBreakdownState = {
      status,
      message,
      percent: status === "running" ? 22 : 100,
      label: "分镜拆解",
      updated_at: new Date().toISOString(),
    };
    if (status === "running") node.status = "generating";
  }, { history: false, persist: false });
}

function setStoryboardBreakdownError(store, nodeId, error) {
  const message = formatRuntimeError(error, "分镜拆解失败，请检查生成服务配置或稍后重试。");
  store.set((s) => {
    const node = s.nodes[nodeId];
    if (!node) return;
    node.status = "error";
    node.params.storyboardBreakdownState = {
      status: "failed",
      percent: 100,
      label: "分镜拆解",
      message,
      completed_at: new Date().toISOString(),
    };
    node.params.generationPolicyStatus = "needs_attention";
    node.params.generationStatusDetail = "分镜拆解未完成。";
    node.params.generationBlockedReason = message;
    node.params.generationNextAction = "确认 Runtime 分镜拆解服务可用后重试。";
  });
}

function setScriptImportError(store, nodeId, message) {
  store.set((s) => {
    const node = s.nodes[nodeId];
    if (!node) return;
    node.status = "error";
    node.result = `剧本导入失败：${message}`;
    node.params.scriptImportState = {
      status: "error",
      message,
      completed_at: new Date().toISOString(),
    };
  }, { history: false });
}

function cleanSegment(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function trimFileTitle(name) {
  const value = String(name || "").replace(/\.(txt|md|markdown|docx?|pptx?)$/i, "").trim();
  return value ? `剧本：${value.slice(0, 24)}` : "剧本文本";
}

function safeScriptImportError(error) {
  const message = error instanceof Error ? error.message : String(error || "无法读取文件");
  return message.replace(/Bearer\s+\S+/gi, "Bearer <redacted>").slice(0, 160);
}
