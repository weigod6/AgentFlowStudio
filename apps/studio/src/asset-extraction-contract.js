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
const ANIMAL_REFERENCE_TERMS = [
  "拉布拉多",
  "金毛",
  "边牧",
  "柯基",
  "哈士奇",
  "柴犬",
  "奶狗",
  "幼犬",
  "小狗",
  "狗狗",
  "橘猫",
  "狸花猫",
  "黑猫",
  "白猫",
  "小猫",
  "猫咪",
  "猫",
  "狗",
  "犬",
];
const PROP_REFERENCE_TERMS = [
  "荧光绿网球",
  "网球",
  "红绳",
  "牵引绳",
  "狗绳",
  "毛线团",
  "项圈",
  "断绳",
  "断戟",
  "青铜虎符",
  "虎符",
  "竹简",
  "军旗",
  "残旗",
  "旧军籍册",
  "军籍册",
  "试卷",
  "草稿纸",
  "寻狗启事",
  "启事",
  "手机",
  "照片",
  "信件",
  "信封",
  "金箍棒",
  "钢爪",
  "地图",
  "钥匙",
];
const GENERIC_PROP_NOUN_TERMS = [
  "数学试卷",
  "试卷",
  "草稿纸",
  "纸张",
  "启事",
  "寻狗启事",
  "照片",
  "信件",
  "信封",
  "手机",
  "钥匙",
  "地图",
  "竹简",
  "虎符",
  "军旗",
  "残旗",
  "旗",
  "断戟",
  "戟",
  "剑",
  "刀",
  "枪",
  "弓",
  "棍",
  "棒",
  "网球",
  "球",
  "红绳",
  "牵引绳",
  "狗绳",
  "绳",
  "毛线团",
  "纸盒",
  "纸箱",
  "项圈",
  "断绳",
  "香炉",
  "面包",
  "耳机线",
  "雨伞",
  "伞",
];
const KEY_PROP_ACTION_TERMS = [
  "手持",
  "死攥",
  "攥",
  "握",
  "拿",
  "捧",
  "叼",
  "吐",
  "顶",
  "勾",
  "勾着",
  "拾起",
  "翻转",
  "展开",
  "散开",
  "露出",
  "震颤",
  "嗡鸣",
  "照亮",
  "反射",
  "检查",
  "查看",
  "写着",
  "批注",
  "锁定",
  "递",
  "掏出",
  "撑着",
  "放在",
  "压住",
];
const ACTION_FRAGMENT_LABEL_TERMS = [
  "挣脱",
  "转身",
  "轻巧",
  "跃下",
  "落地",
  "掏出",
  "本能",
  "悬停",
  "低头",
  "抬头",
  "回头",
  "侧身",
  "伸手",
  "抬手",
  "咬牙",
];
const BODY_PART_LABEL_TERMS = [
  "右眼",
  "左眼",
  "瞳孔",
  "眼睛",
  "指尖",
  "手指",
  "指节",
  "爪子",
  "耳朵",
  "鼻尖",
  "鼻头",
  "喉结",
  "下颌",
  "肩",
  "手腕",
  "后颈",
];
const NON_CHARACTER_LABEL_TERMS = [
  "手机",
  "屏幕",
  "试卷",
  "草稿",
  "启事",
  "断戟",
  "虎符",
  "竹简",
  "军旗",
  "残旗",
];

export function normalizeAssetExtractionRefs(assetRefs, options = {}) {
  const context = cleanText(options.context || "");
  const includeInferred = Boolean(options.includeInferred);
  const candidates = Array.isArray(assetRefs) ? assetRefs.filter((item) => item && typeof item === "object") : [];
  if (includeInferred) {
    candidates.push(...inferredAssetRefs(context));
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
  return { asset_refs: dropSubsumedAssetRefs(accepted), dropped_asset_ref_diagnostics: dropped };
}

export function normalizeAssetRefForContract(asset, index = 0, context = "") {
  let assetType = ASSET_TYPES.has(asset?.asset_type) ? asset.asset_type : "character";
  const rawLabel = cleanLabel(asset?.display_name || asset?.label || asset?.name || "");
  if (!rawLabel) return { ref: null, diagnostic: null };
  const evidence = cleanText(asset?.evidence_text || asset?.visual_evidence_span || context);
  const contextText = cleanText(context || evidence);
  let displayName = rawLabel;
  let provisionalName = Boolean(asset?.provisional_name);
  let nameSource = String(asset?.name_source || asset?.source || "candidate");

  if (assetType === "character" && looksLikePropReference(rawLabel, evidence, contextText)) {
    assetType = "prop";
    displayName = cleanPropLabel(rawLabel) || rawLabel;
  }
  else if (assetType === "prop" && looksLikePropPhraseLabel(displayName)) displayName = cleanPropLabel(displayName) || displayName;
  if (assetType === "scene" && isAudioOnlyCityReference(rawLabel, evidence, contextText)) {
    return { ref: null, diagnostic: diagnostic(rawLabel, assetType, "audio_only_non_visual_city_reference", evidence || contextText) };
  }
  if (assetType === "character" && PRONOUN_LABELS.has(rawLabel)) {
    return { ref: null, diagnostic: diagnostic(rawLabel, assetType, "ambiguous_alias_not_auto_merged", evidence || contextText) };
  }
  if (assetType === "character" && looksLikeActionFragmentLabel(rawLabel)) {
    return { ref: null, diagnostic: diagnostic(rawLabel, assetType, "action_fragment_not_asset", evidence || contextText) };
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
  for (const name of namedAnimalCharacters(context)) {
    refs.push({ label: name, asset_type: "character", character_subtype: "animal", source: "grounded_mention", evidence_text: context });
  }
  const sceneName = visualSceneName(context);
  if (sceneName) refs.push({ label: sceneName, asset_type: "scene", source: "candidate", evidence_text: context });
  for (const name of visualPropNames(context)) {
    refs.push({ label: name, asset_type: "prop", status: "prop_relevant", source: "grounded_mention", evidence_text: context });
  }
  return dropSubsumedAssetRefs(refs);
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
  for (const item of actionBoundCharacterNames(text)) names.push(item);
  if (/\bLin\s+Wan\b/i.test(text)) names.push("Lin Wan");
  if (text.includes("女孩")) names.push("女孩");
  if (text.includes("机器人")) names.push("机器人");
  if (/\bfuture robot\b|\brobot\b/i.test(text)) names.push("Future Robot");
  return [...new Set(names)];
}

function actionBoundCharacterNames(text) {
  const names = [];
  const source = String(text || "");
  const actionRe = /(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{2,4}?)(?=单膝|双膝|抬头|低头|转身|侧身|回头|凝视|望向|看向|站|蹲|跪|坐|走|跑|追|冲|跃|扑|伸手|抬手|握|攥|死攥|拿|捧|抱|咬牙|喉结|瞳孔|肩|右臂|左臂|指节|手指|下颌|呼吸|开口|呛出|怔住|愣住)/gu;
  for (const match of source.matchAll(actionRe)) {
    const name = trimCharacterName(match[1]);
    if (looksLikeCharacterName(name)) names.push(name);
  }
  return [...new Set(names)];
}

function looksLikeCharacterName(value) {
  const clean = String(value || "").trim();
  if (!clean || GENERIC_CHARACTER_LABELS.has(clean) || GENERIC_SCENE_LABELS.has(clean) || PRONOUN_LABELS.has(clean)) return false;
  if (["暴雨", "泥浆", "古战场", "战场", "城墙", "城垛", "雷声", "雨声", "镜头", "画面", "远处", "血色", "残旗", "军旗", "断戟", "虎符", "竹简", "试卷", "草稿", "启事"].some((term) => clean.includes(term))) return false;
  if (looksLikeActionFragmentLabel(clean)) return false;
  return /^[\u4e00-\u9fff]{2,4}$/.test(clean);
}

function looksLikeActionFragmentLabel(value) {
  const clean = String(value || "").trim();
  if (!clean) return false;
  if (/^(?:他|她|它|这|那|其|我|你)/.test(clean)) return true;
  if (ACTION_FRAGMENT_LABEL_TERMS.some((term) => clean.includes(term))) return true;
  if (BODY_PART_LABEL_TERMS.some((term) => clean.includes(term))) return true;
  if (NON_CHARACTER_LABEL_TERMS.some((term) => clean.includes(term))) return true;
  return false;
}

function looksLikePropPhraseLabel(value) {
  const clean = String(value || "").trim();
  if (!clean) return false;
  if (/^(?:他|她|它|这|那|其|我|你)/.test(clean)) return true;
  return [
    "掏出",
    "叼着",
    "吐出",
    "吐在",
    "捧着",
    "拿着",
    "握着",
    "攥着",
    "撑着",
    "拾起",
    "翻转",
    "露出",
    "放在",
    "压住",
    "勾着",
  ].some((term) => clean.includes(term));
}

function namedAnimalCharacters(text) {
  const source = String(text || "");
  const names = [];
  const quotedAliasRe = /(?:拉布拉多|金毛|边牧|柯基|哈士奇|柴犬|奶狗|幼犬|小狗|狗狗|橘猫|狸花猫|黑猫|白猫|小猫|猫咪|猫|狗|犬)[“"]([\u4e00-\u9fffA-Za-z0-9·]{1,8})[”"]/gu;
  for (const match of source.matchAll(quotedAliasRe)) names.push(match[1]);
  const breedRe = /((?:黑色|白色|灰色|棕色|黄色|金色|橘色|灰白相间|黑白相间)?(?:拉布拉多|金毛|边牧|柯基|哈士奇|贵宾犬|萨摩耶|柴犬)(?:幼崽|幼犬)?)/gu;
  for (const match of source.matchAll(breedRe)) names.push(match[1]);
  const longerSpeciesPresent = ["拉布拉多", "金毛", "边牧", "柯基", "哈士奇", "柴犬", "奶狗", "幼犬", "小狗", "橘猫", "狸花猫", "黑猫", "白猫", "小猫"].some((term) => source.includes(term));
  for (const term of ["奶狗", "幼犬", "小狗", "狗狗", "橘猫", "狸花猫", "黑猫", "白猫", "小猫", "猫咪", "猫", "狗", "犬"]) {
    if (source.includes(term) && (term.length > 1 || !longerSpeciesPresent)) names.push(term);
  }
  return [...new Set(names)].filter(Boolean);
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
  const grounded = groundedSceneName(text);
  if (grounded) return grounded;
  if (lower.includes("rain-night city street")) return "rain-night city street";
  if (lower.includes("city street") || (lower.includes("street") && lower.includes("city"))) return "city street";
  if (lower.includes("rooftop") && lower.includes("city")) return "city rooftop";
  if (text.includes("雨夜") && (text.includes("城市") || text.includes("街道"))) return "雨夜城市街道";
  if (text.includes("城市") && text.includes("屋顶")) return "城市屋顶";
  if (text.includes("城市") && ["街道", "天际线", "建筑", "高楼", "霓虹", "湿路", "路面", "灯光"].some((term) => text.includes(term))) return "城市街道";
  return "";
}

function groundedSceneName(text) {
  const source = String(text || "");
  if (source.includes("古战场")) return "古战场";
  if (source.includes("老城区巷口")) return "老城区巷口";
  if (source.includes("斜坡草甸")) return "斜坡草甸";
  if (/山巅|山脊|云海/.test(source) && source.includes("战场")) return "山巅石台战场";
  if (source.includes("战场")) return "战场";
  const sceneRe = /([\u4e00-\u9fffA-Za-z0-9·]{0,10}(?:校门口|巷口|窄巷|巷子|公园长椅旁|公园长椅|公园|草甸|草坪|厨房|房间|屋顶|楼顶|天台|城墙|城垛|街道|走廊|宫殿|庭院|广场|餐厅|山洞|洞口|洞内))/gu;
  for (const match of source.matchAll(sceneRe)) {
    const label = cleanSceneLabel(match[1]);
    if (label) return label;
  }
  return "";
}

function cleanSceneLabel(value) {
  const clean = String(value || "")
    .replace(/^(?:在|从|向|朝|远处|路对面|空荡|焦黑|破碎|湿漉漉|梧桐树影斑驳的)+/, "")
    .replace(/^.*(?:站在|坐在|蹲在|躺在|停在|来到|走进|冲向|落在|映着|在)/, "")
    .replace(/(?:上|里|中|外|内)$/, "")
    .trim();
  if (!clean || GENERIC_SCENE_LABELS.has(clean) || ["青石台阶", "青砖", "石台"].includes(clean)) return "";
  return clean.slice(0, 24);
}

function visualPropNames(text) {
  const source = String(text || "");
  const names = [];
  for (const term of [...PROP_REFERENCE_TERMS].sort((a, b) => b.length - a.length)) {
    if (source.includes(term)) names.push(cleanPropLabel(term));
  }
  names.push(...genericVisualPropNames(source));
  const objectRe = /(?:半截|半枚|一卷|一张|一只|一柄|一根|那柄|那张|那只|那截)?([\u4e00-\u9fffA-Za-z0-9·]{0,8}(?:断戟|青铜虎符|虎符|竹简|军旗|残旗|军籍册|试卷|草稿纸|寻狗启事|启事|网球|红绳|牵引绳|狗绳|毛线团|项圈|断绳|手机|照片|信件|信封|金箍棒|钢爪|地图|钥匙))/gu;
  for (const match of source.matchAll(objectRe)) names.push(cleanPropLabel(match[1]));
  return dedupeNonOverlapping(names.filter(Boolean)).slice(0, 4);
}

function genericVisualPropNames(text) {
  const source = String(text || "");
  if (!source) return [];
  const nounPattern = [...GENERIC_PROP_NOUN_TERMS].sort((a, b) => b.length - a.length).map(escapeRegExp).join("|");
  const contextPrefix = "(?:手中|手里|嘴里|怀里|脚边|身旁|面前|指尖|掌心|画面中|镜头中|叼着|吐出|吐在|捧着|拿着|握着|攥着|撑着|拾起|翻转|露出|放在|顶了顶|压住|反射|写着|批注)";
  const genericRe = new RegExp(`${contextPrefix}[^。！？!?；;]{0,18}?((?:[\\u4e00-\\u9fff]{0,8})?(?:${nounPattern}))`, "gu");
  const names = [];
  for (const match of source.matchAll(genericRe)) {
    const name = cleanPropLabel(match[1]);
    if (name && !isAnimalAliasName(name, source)) names.push(name);
  }
  const measureRe = new RegExp(`(?:一|半|那|这|其)?(?:个|只|张|卷|枚|截|根|柄|把|块|团|盒|箱)?((?:[\\u4e00-\\u9fff]{0,8})?(?:${nounPattern}))`, "gu");
  for (const match of source.matchAll(measureRe)) {
    const start = Math.max(0, match.index - 16);
    const end = Math.min(source.length, match.index + match[0].length + 16);
    const window = source.slice(start, end);
    if (KEY_PROP_ACTION_TERMS.some((term) => window.includes(term)) || /手中|手里|嘴里|怀里|脚边|面前|画面|镜头/.test(window)) {
      const name = cleanPropLabel(match[1]);
      if (name && !isAnimalAliasName(name, source)) names.push(name);
    }
  }
  return dedupeNonOverlapping(names.filter(Boolean));
}

function isAnimalAliasName(label, text) {
  const clean = escapeRegExp(String(label || "").trim());
  if (!clean) return false;
  const animalPattern = [...ANIMAL_REFERENCE_TERMS].sort((a, b) => b.length - a.length).map(escapeRegExp).join("|");
  return new RegExp(`(?:${animalPattern})[“"']${clean}[”"']`, "iu").test(String(text || ""));
}

function cleanPropLabel(value) {
  const clean = String(value || "")
    .replace(/^(?:他|她|它|这|那|其)?(?:叼着|吐出|吐在|捧着|拿着|握着|攥着|撑着|拾起|翻转|露出|放在|顶了顶|压住|反射|写着|批注|捏着|掏出|勾着|磨损严重的|没吃完的|湿漉漉的|湿透的|湿透|褪色的|褪色|发光的|发光|半截|半枚|一卷|一张|一只|一柄|一根|一块|一团|一盒|一箱|那柄|那张|那只|那截|那根|这根|这张|这只)+/, "")
    .trim();
  const term = [...PROP_REFERENCE_TERMS].sort((a, b) => b.length - a.length).find((item) => item && clean.includes(item));
  if (term) return term.slice(0, 24);
  const genericTerm = [...GENERIC_PROP_NOUN_TERMS].sort((a, b) => b.length - a.length).find((item) => item && clean.endsWith(item));
  if (genericTerm) return clean.slice(Math.max(0, clean.length - genericTerm.length - 8), clean.length).slice(0, 24);
  return clean.slice(0, 24);
}

function dedupeNonOverlapping(values) {
  const unique = [...new Set(values)];
  const result = [];
  for (const value of [...unique].sort((a, b) => b.length - a.length)) {
    if (!result.some((other) => value !== other && other.includes(value))) result.push(value);
  }
  return result.sort((a, b) => values.indexOf(a) - values.indexOf(b));
}

function dropSubsumedAssetRefs(refs) {
  return refs.filter((ref) => {
    const label = String(ref?.label || ref?.display_name || "").trim();
    const type = String(ref?.asset_type || "");
    return !refs.some((other) => {
      const otherLabel = String(other?.label || other?.display_name || "").trim();
      return type === String(other?.asset_type || "") && label && otherLabel && label !== otherLabel && otherLabel.includes(label);
    });
  });
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

function looksLikePropReference(label, evidence, context) {
  const text = `${label} ${evidence} ${context}`.toLowerCase();
  const labelText = String(label || "").toLowerCase();
  if (PROP_REFERENCE_TERMS.some((term) => labelText.includes(term.toLowerCase()))) return true;
  return ["球", "ball"].includes(labelText) && /网球|球面|球体|吐在|叼着|tennis\s+ball/i.test(text);
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

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
