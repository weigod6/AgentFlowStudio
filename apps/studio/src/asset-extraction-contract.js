const ASSET_TYPES = new Set(["character", "scene", "prop"]);
const GENERIC_CHARACTER_LABELS = new Set(["人", "人物", "主角", "角色", "主体"]);
const GENERIC_SCENE_LABELS = new Set(["场景", "主要场景"]);
const PRONOUN_LABELS = new Set(["他", "她", "它", "他们", "她们", "ta", "they", "he", "she"]);

const AUDIO_ONLY_TERMS = [
  "城市噪音",
  "城市环境底噪",
  "环境底噪",
  "底噪",
  "噪音",
  "环境音",
  "ambience",
  "ambient",
  "city noise",
  "distant city noise",
  "audio",
  "sound",
  "black screen",
];
const CITY_TERMS = ["城市", "city", "街道", "street", "road"];
const VISUAL_CITY_TERMS = [
  "rain-night city street",
  "city street",
  "skyline",
  "building",
  "buildings",
  "neon",
  "wet road",
  "visible lights",
  "rooftop",
  "雨夜",
  "街道",
  "屋顶",
  "天际线",
  "建筑",
  "高楼",
  "霓虹",
  "湿路",
  "路面",
  "灯光",
];
const KNOWN_CHARACTER_NAMES = ["唐僧", "白骨精", "孙悟空", "猪八戒", "沙僧", "金刚狼", "林晚"];
const VISUAL_CHARACTER_TERMS = [
  "walks",
  "runs",
  "face",
  "coat",
  "hand",
  "站",
  "走",
  "奔跑",
  "穿",
  "低头",
  "手部",
  "展开",
  "外套",
  "侧脸",
  "女孩",
  "林晚",
  "机器人",
  "唐僧",
  "白骨精",
  "孙悟空",
  "猪八戒",
];

export function normalizeAssetExtractionRefs(assetRefs, options = {}) {
  const context = cleanText(options.context || "");
  const includeInferred = Boolean(options.includeInferred);
  const candidates = Array.isArray(assetRefs) ? assetRefs.filter((item) => item && typeof item === "object") : [];
  if (includeInferred) {
    const specificTypes = specificAssetTypes(candidates);
    candidates.push(...inferredAssetRefs(context).filter((item) => !specificTypes.has(item.asset_type)));
  }
  const accepted = [];
  const dropped = [];
  const seen = new Set();
  const droppedSeen = new Set();
  candidates.forEach((candidate, index) => {
    const { ref, diagnostic } = normalizeAssetRefForContract(candidate, index, context);
    if (ref) {
      const key = `${ref.asset_type}:${ref.display_name}`;
      if (!seen.has(key)) {
        seen.add(key);
        accepted.push(ref);
      }
    } else if (diagnostic) {
      const key = `${diagnostic.asset_type}:${diagnostic.display_name}:${diagnostic.reason}`;
      if (!droppedSeen.has(key)) {
        droppedSeen.add(key);
        dropped.push(diagnostic);
      }
    }
  });
  return { asset_refs: accepted, dropped_asset_ref_diagnostics: dropped };
}

export function normalizeAssetRefForContract(asset, index = 0, context = "") {
  const assetType = ASSET_TYPES.has(asset?.asset_type) ? asset.asset_type : "character";
  const rawLabel = cleanLabel(asset?.display_name || asset?.label || asset?.name || "");
  if (!rawLabel) return { ref: null, diagnostic: null };
  const evidence = cleanText(asset?.evidence_text || asset?.visual_evidence_span || context);
  const contextText = cleanText(context || evidence);
  let displayName = rawLabel;
  let provisionalName = Boolean(asset?.provisional_name);
  let nameSource = String(asset?.name_source || asset?.source || "candidate");

  if (assetType === "scene" && isAudioOnlyCityReference(rawLabel, evidence, contextText)) {
    return { ref: null, diagnostic: diagnostic(rawLabel, assetType, "audio_only_non_visual_city_reference", evidence || contextText) };
  }
  if (assetType === "character" && PRONOUN_LABELS.has(rawLabel)) {
    return { ref: null, diagnostic: diagnostic(rawLabel, assetType, "ambiguous_alias_not_auto_merged", evidence || contextText) };
  }
  if (assetType === "character" && GENERIC_CHARACTER_LABELS.has(rawLabel)) {
    const provisional = provisionalCharacterName(contextText);
    if (!provisional) return { ref: null, diagnostic: diagnostic(rawLabel, assetType, "unresolved_generic_character", evidence || contextText) };
    displayName = provisional;
    provisionalName = true;
    nameSource = "visual_context_provisional";
  }
  if (assetType === "scene" && GENERIC_SCENE_LABELS.has(rawLabel)) {
    const sceneName = visualSceneName(contextText);
    if (!sceneName) return { ref: null, diagnostic: diagnostic(rawLabel, assetType, "unresolved_generic_scene", evidence || contextText) };
    displayName = sceneName;
    provisionalName = true;
    nameSource = "visual_context_provisional";
  }

  const visualSpan = visualEvidenceSpan(contextText, evidence, displayName, assetType);
  if (assetType === "scene" && hasAudioOnlyTerms(evidence || contextText) && !visualSpan) {
    return { ref: null, diagnostic: diagnostic(rawLabel, assetType, "audio_only_non_visual_reference", evidence || contextText) };
  }
  const acceptedVisualSpan = visualSpan || (assetType === "scene" ? (evidence || contextText).slice(0, 240) : "");
  return {
    ref: {
      label: displayName,
      display_name: displayName,
      asset_id: String(asset?.asset_id || `candidate:${assetType}:${slug(displayName)}`),
      graph_asset_id: String(asset?.graph_asset_id || asset?.graphAssetId || ""),
      asset_type: assetType,
      status: String(asset?.status || "candidate"),
      source: String(asset?.source || "candidate"),
      scope: String(asset?.scope || "shot_tree"),
      confidence: confidence(asset?.confidence, provisionalName),
      evidence_text: (acceptedVisualSpan || evidence || contextText).slice(0, 240),
      descriptive_signature: cleanText(asset?.descriptive_signature || asset?.signature || acceptedVisualSpan || evidence || contextText).slice(0, 240),
      evidence_modality: "visual",
      visual_evidence_span: acceptedVisualSpan,
      modality_gate_status: "accepted",
      name_source: nameSource,
      provisional_name: provisionalName,
      ...profileFields(asset),
    },
    diagnostic: null,
  };
}

function profileFields(asset) {
  const profilePlan = plainObject(asset?.profile_plan);
  const assetFactProfile = plainObject(asset?.asset_fact_profile);
  const factProfile = plainObject(asset?.fact_profile);
  const facts = plainObject(asset?.facts);
  return {
    character_subtype: cleanText(
      asset?.character_subtype
        || profilePlan?.character_subtype
        || assetFactProfile?.character_subtype
        || factProfile?.character_subtype
        || "",
    ),
    profile_plan: profilePlan || undefined,
    asset_fact_profile: assetFactProfile || undefined,
    fact_profile: factProfile || undefined,
    facts: facts || undefined,
    continuity_locks: stringList(asset?.continuity_locks),
    negative_locks: stringList(asset?.negative_locks),
    fact_evidence: stringList(asset?.fact_evidence),
    missing_fact_fields: stringList(asset?.missing_fact_fields),
  };
}

function plainObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function stringList(value) {
  return Array.isArray(value) ? value.map((item) => cleanText(item)).filter(Boolean).slice(0, 12) : undefined;
}

function inferredAssetRefs(context) {
  const refs = [];
  for (const name of namedCharacters(context)) refs.push({ label: name, asset_type: "character", source: "candidate", evidence_text: context });
  const sceneName = visualSceneName(context);
  if (sceneName) refs.push({ label: sceneName, asset_type: "scene", source: "candidate", evidence_text: context });
  return refs;
}

function specificAssetTypes(candidates) {
  const result = new Set();
  for (const item of candidates) {
    const assetType = ASSET_TYPES.has(item?.asset_type) ? item.asset_type : "character";
    const label = cleanLabel(item?.display_name || item?.label || item?.name || "");
    if (label && !GENERIC_CHARACTER_LABELS.has(label) && !GENERIC_SCENE_LABELS.has(label) && !PRONOUN_LABELS.has(label)) result.add(assetType);
  }
  return result;
}

function namedCharacters(text) {
  const names = [];
  const relationRe = /([\u4e00-\u9fffA-Za-z0-9·]{2,12})(?:大战|对决|迎娶|娶了|娶|嫁给|爱上|遇见|面对|追击|追杀|营救|守护)([\u4e00-\u9fffA-Za-z0-9·]{2,12})/gu;
  for (const match of text.matchAll(relationRe)) {
    names.push(trimCharacterName(match[1]), trimCharacterName(match[2]));
  }
  for (const item of knownCharactersInSourceOrder(text)) names.push(item);
  if (/\bLin\s+Wan\b/i.test(text)) names.push("Lin Wan");
  if (text.includes("女孩")) names.push("女孩");
  if (text.includes("机器人")) names.push("机器人");
  if (/\bfuture robot\b|\brobot\b/i.test(text)) names.push("Future Robot");
  return [...new Set(names)];
}

function knownCharactersInSourceOrder(text) {
  return KNOWN_CHARACTER_NAMES
    .map((name) => ({ name, index: text.indexOf(name) }))
    .filter((item) => item.index >= 0)
    .sort((a, b) => a.index - b.index)
    .map((item) => item.name);
}

function trimCharacterName(value) {
  return String(value || "")
    .replace(/^(以|把|将|当|用|和|与|及|、)+/, "")
    .replace(/^.*(?:是|讲述|关于|围绕)/, "")
    .replace(/(为核心|为主题|为主|展开|对决|战斗|格斗|碰撞).*$/, "")
    .replace(/(但是|但|却|旁观|观战|从旁).*$/, "")
    .trim();
}

function provisionalCharacterName(text) {
  const names = namedCharacters(text);
  if (names.length) return names[0];
  if (["红色外套", "侧脸", "霓虹"].some((term) => text.includes(term))) return "红色外套人物";
  if (/robot/i.test(text) || text.includes("机器人")) return /robot/i.test(text) ? "Future Robot" : "机器人";
  if (hasVisualCharacterContext(text)) return "可见人物";
  return "";
}

function visualSceneName(text) {
  if (hasNegatedVisualContext(text)) return "";
  const lower = text.toLowerCase();
  if (lower.includes("rain-night city street")) return "rain-night city street";
  if (lower.includes("city street") || (lower.includes("street") && lower.includes("city"))) return "city street";
  if (lower.includes("rooftop") && lower.includes("city")) return "city rooftop";
  if (text.includes("雨夜") && (text.includes("城市") || text.includes("街道"))) return "雨夜城市街道";
  if (text.includes("城市") && text.includes("屋顶")) return "城市屋顶";
  if (text.includes("城市") && ["街道", "天际线", "建筑", "高楼", "霓虹", "湿路", "路面", "灯光"].some((term) => text.includes(term))) return "城市街道";
  return "";
}

function visualEvidenceSpan(context, evidence, displayName, assetType) {
  const source = cleanText(context || evidence);
  if (!source) return "";
  const candidates = source.split(/(?<=[。！？.!?])\s*/).map((item) => item.trim()).filter(Boolean);
  const sentences = candidates.length ? candidates : [source];
  const direct = sentences.find((sentence) => displayName && sentence.includes(displayName));
  if (direct) return direct.slice(0, 240);
  if (assetType === "scene") return (sentences.find(hasVisualCityContext) || "").slice(0, 240);
  if (assetType === "character") return (sentences.find(hasVisualCharacterContext) || "").slice(0, 240);
  if (assetType === "prop") return sentences[0].slice(0, 240);
  return "";
}

function isAudioOnlyCityReference(label, evidence, context) {
  const text = `${label} ${evidence} ${context}`;
  return hasCityTerms(text) && hasAudioOnlyTerms(text) && !hasVisualCityContext(text);
}

function hasCityTerms(text) {
  const lower = String(text || "").toLowerCase();
  return CITY_TERMS.some((term) => (isAscii(term) ? lower.includes(term) : text.includes(term)));
}

function hasAudioOnlyTerms(text) {
  const lower = String(text || "").toLowerCase();
  return AUDIO_ONLY_TERMS.some((term) => (isAscii(term) ? lower.includes(term) : text.includes(term)));
}

function hasVisualCityContext(text) {
  if (hasNegatedVisualContext(text)) return false;
  const lower = String(text || "").toLowerCase();
  return VISUAL_CITY_TERMS.some((term) => (isAscii(term) ? lower.includes(term) : text.includes(term)));
}

function hasVisualCharacterContext(text) {
  if (hasNegatedVisualContext(text)) return false;
  const lower = String(text || "").toLowerCase();
  return VISUAL_CHARACTER_TERMS.some((term) => (isAscii(term) ? lower.includes(term) : text.includes(term)));
}

function hasNegatedVisualContext(text) {
  const lower = String(text || "").toLowerCase();
  return ["没有可见", "不可见", "无可见", "没有画面", "no visible", "not visible", "black screen"].some((term) => (isAscii(term) ? lower.includes(term) : text.includes(term)));
}

function diagnostic(label, assetType, reason, evidence) {
  return {
    label,
    display_name: label,
    asset_type: assetType,
    reason,
    evidence_text: cleanText(evidence).slice(0, 240),
    evidence_modality: hasAudioOnlyTerms(evidence) ? "audio" : "textual",
    modality_gate_status: "held",
  };
}

function confidence(value, provisionalName) {
  return Number.isFinite(value) ? Math.max(0, Math.min(Number(value), 1)) : provisionalName ? 0.72 : 0.82;
}

function cleanLabel(value) {
  return String(value || "").replace(/^[\s@]+|[\s，。；:：.!?！？]+$/g, "").trim().slice(0, 40);
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function slug(value) {
  return String(value || "").replace(/[^0-9A-Za-z\u4e00-\u9fff]+/g, "").toLowerCase().slice(0, 48) || "asset";
}

function isAscii(value) {
  return /^[\x00-\x7F]+$/.test(value);
}
