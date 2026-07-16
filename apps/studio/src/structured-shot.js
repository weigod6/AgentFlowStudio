import { normalizeAssetExtractionRefs } from "./asset-extraction-contract.js";

const ASSET_RE = /@([A-Za-z0-9_\-\u4e00-\u9fff·]+)/g;
const SCENE_HINTS = ["主要场景", "场景", "办公室", "房间", "街道", "巷口", "窄巷", "巷子", "青石台阶", "青砖", "屋顶", "楼顶", "天台", "城市", "天际线", "森林", "海边", "山谷", "山巅", "山脊", "石台", "战场", "云海", "云栈洞口", "洞口", "洞内", "山洞", "餐厅", "车内", "走廊", "宫殿", "庭院", "广场", "屏幕"];
const KNOWN_CHARACTER_NAMES = ["唐僧", "白骨精", "孙悟空", "猪八戒", "沙僧", "金刚狼", "林晚"];
const CHARACTER_HINTS = ["主角", "角色", "人物", "女孩", "男孩", "女人", "男人", "老人", "孩子", "机器人", "队长", "老师", "学生", ...KNOWN_CHARACTER_NAMES];
const PROP_HINTS = ["荧光绿网球", "网球", "红绳", "牵引绳", "狗绳", "毛线团", "项圈", "断绳", "断戟", "青铜虎符", "虎符", "竹简", "军旗", "残旗", "旧军籍册", "军籍册", "试卷", "草稿纸", "寻狗启事", "启事", "金箍棒", "钢爪", "手机", "电脑", "键盘", "刀", "剑", "棍", "棒", "车辆", "汽车", "信件", "信封", "信纸", "照片", "路灯", "台灯", "灯具", "灯柱", "书", "门"];
const KEY_PROP_LABELS = new Set(["断戟", "青铜虎符", "虎符", "竹简", "军旗", "残旗", "旧军籍册", "军籍册", "金箍棒", "钢爪", "荧光绿网球", "网球", "红绳", "牵引绳", "狗绳", "项圈", "断绳", "手机", "寻狗启事", "启事", "地图"]);
const KEY_PROP_ACTION_RE = /手持|死攥|攥|握|拿|捧|叼|吐|顶|勾|勾着|拾起|翻转|展开|散开|露出|震颤|嗡鸣|照亮|反射|检查|查看|写着|批注|锁定|递|掏出/;
const GENERIC_CHARACTER_LABELS = new Set(["主角", "角色", "人物"]);
const GENERIC_SCENE_LABELS = new Set(["主要场景", "场景"]);

export function structuredShotFromSegment(segment, index) {
  const parsed = structuredShotFromFormattedText(segment, index);
  if (parsed) return parsed;
  const source = cleanText(segment);
  const extraction = extractShotAssetExtraction(source);
  const assetRefs = extraction.asset_refs;
  return {
    shot_id: `shot_${String(index).padStart(2, "0")}`,
    index,
    duration: inferDuration(source),
    description: descriptionWithAssets(source, assetRefs),
    shot_size: inferShotSize(source),
    light_atmosphere: inferLighting(source),
    camera_motion: inferCameraMotion(source),
    dialogue: inferDialogue(source),
    sound: inferSound(source),
    asset_refs: assetRefs,
    dropped_asset_ref_diagnostics: extraction.dropped_asset_ref_diagnostics,
    source_text: source,
  };
}

export function structuredShotFromFormattedText(text, index) {
  const fields = {};
  for (const rawLine of String(text || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    const match = line.match(/^([^：:]+)[：:]\s*(.+)$/);
    if (!match) continue;
    fields[match[1].trim()] = match[2].trim();
  }
  if (!fields["画面描述"] && !fields["景别"] && !fields["运镜"]) return null;
  const description = fields["画面描述"] || cleanText(text);
  const assetText = fields["资产"] || description;
  const extraction = extractShotAssetExtraction(`${assetText}\n${description}`);
  const assetRefs = extraction.asset_refs;
  return {
    shot_id: `shot_${String(index).padStart(2, "0")}`,
    index,
    duration: fields["时长"] || inferDuration(description),
    description: descriptionWithAssets(description, assetRefs),
    shot_size: fields["景别"] || inferShotSize(description),
    light_atmosphere: fields["光影氛围"] || inferLighting(description),
    camera_motion: fields["运镜"] || inferCameraMotion(description),
    dialogue: fields["对白/旁白"] || fields["对白"] || fields["旁白"] || inferDialogue(description),
    sound: fields["音效"] || inferSound(description),
    asset_refs: assetRefs,
    dropped_asset_ref_diagnostics: extraction.dropped_asset_ref_diagnostics,
    source_text: cleanText(text),
  };
}

export function structuredShotText(shot) {
  const assetLine = shot.asset_refs.length
    ? shot.asset_refs.map(assetRefDisplay).join("、")
    : "无明确可固定资产";
  return [
    `镜号：${String(shot.index).padStart(2, "0")}`,
    `时长：${shot.duration}`,
    `画面描述：${shot.description}`,
    `景别：${shot.shot_size}`,
    `光影氛围：${shot.light_atmosphere}`,
    `运镜：${shot.camera_motion}`,
    `对白/旁白：${shot.dialogue}`,
    `音效：${shot.sound}`,
    `资产：${assetLine}`,
  ].join("\n");
}

export function extractShotAssetRefs(text) {
  return extractShotAssetExtraction(text).asset_refs;
}

function extractShotAssetExtraction(text) {
  const refs = [];
  for (const match of String(text || "").matchAll(ASSET_RE)) {
    pushAssetRef(refs, match[1], classifyAsset(match[1], text), "explicit", text);
  }
  addImplicitRefs(refs, text);
  return principalAssetExtraction(normalizeAssetExtractionRefs(refs, { context: text, includeInferred: true }));
}

export function normalizeShotAssetRefs(assetRefs, context = "") {
  return normalizeShotAssetRefsWithDiagnostics(assetRefs, context).asset_refs;
}

export function normalizeShotAssetRefsWithDiagnostics(assetRefs, context = "") {
  return principalAssetExtraction(normalizeAssetExtractionRefs(Array.isArray(assetRefs) ? assetRefs : [], { context }));
}

export function refineStructuredShotAssets(shot, context = "", options = {}) {
  if (!shot || typeof shot !== "object") return shot;
  const inferMissingAssets = options.inferMissingAssets !== false;
  const source = [shot.description, shot.source_text, inferMissingAssets ? context : ""].filter(Boolean).join("\n");
  const extraction = Array.isArray(shot.asset_refs) && shot.asset_refs.length
    ? principalAssetExtraction(normalizeAssetExtractionRefs(shot.asset_refs, { context: source, includeInferred: true }))
    : (inferMissingAssets ? extractShotAssetExtraction(source) : { asset_refs: [], dropped_asset_ref_diagnostics: [] });
  const refs = extraction.asset_refs;
  return {
    ...shot,
    description: descriptionWithAssets(String(shot.description || source || ""), refs),
    asset_refs: refs,
    dropped_asset_ref_diagnostics: [
      ...(Array.isArray(shot.dropped_asset_ref_diagnostics) ? shot.dropped_asset_ref_diagnostics : []),
      ...(extraction.dropped_asset_ref_diagnostics || []),
    ],
  };
}

function principalAssetExtraction(extraction) {
  const refs = Array.isArray(extraction?.asset_refs) ? extraction.asset_refs : [];
  const accepted = [];
  const dropped = [...(Array.isArray(extraction?.dropped_asset_ref_diagnostics) ? extraction.dropped_asset_ref_diagnostics : [])];
  let autoCharacterCount = 0;
  let autoSceneCount = 0;
  let autoPropCount = 0;
  for (const ref of refs) {
    const principal = isPrincipalOrManualAssetRef(ref);
    const manualOrFixed = isManualOrFixedAssetRef(ref);
    const explicitNamed = isExplicitNamedAssetRef(ref);
    const type = String(ref.asset_type || "");
    if (manualOrFixed) {
      accepted.push(ref);
    } else if (principal && type === "prop" && autoPropCount < 2) {
      accepted.push(ref);
      autoPropCount += 1;
    } else if (principal && type === "character" && (explicitNamed || autoCharacterCount < 2)) {
      accepted.push(ref);
      if (!explicitNamed) autoCharacterCount += 1;
    } else if (principal && type === "scene" && (explicitNamed || autoSceneCount < 1)) {
      accepted.push(ref);
      if (!explicitNamed) autoSceneCount += 1;
    } else {
      dropped.push({
        display_name: ref.display_name || ref.label || "",
        label: ref.label || ref.display_name || "",
        asset_type: ref.asset_type || "prop",
        reason: principalDropReason(type),
        evidence_text: ref.evidence_text || ref.visual_evidence_span || "",
      });
    }
  }
  return { asset_refs: accepted, dropped_asset_ref_diagnostics: dropped };
}

function principalDropReason(assetType) {
  if (assetType === "character") return "secondary_character_requires_manual_asset_entry";
  if (assetType === "scene") return "secondary_scene_requires_manual_asset_entry";
  if (assetType === "prop") return "prop_requires_manual_asset_entry";
  return "unsupported_asset_type_requires_manual_entry";
}

function isPrincipalOrManualAssetRef(ref) {
  if (ref?.asset_type !== "prop") return true;
  return isManualOrFixedAssetRef(ref) || isKeyPropRef(ref);
}

function isManualOrFixedAssetRef(ref) {
  const source = String(ref?.source || "").toLowerCase();
  const status = String(ref?.status || "").toLowerCase();
  return source === "manual" || status === "fixed" || Boolean(ref?.graph_asset_id);
}

function isExplicitNamedAssetRef(ref) {
  const source = String(ref?.source || "").toLowerCase();
  const type = String(ref?.asset_type || "");
  const label = String(ref?.label || ref?.display_name || "");
  if (type === "prop") return false;
  if (!source.includes("explicit") && String(ref?.status || "").toLowerCase() !== "mentioned") return false;
  return !GENERIC_CHARACTER_LABELS.has(label) && !GENERIC_SCENE_LABELS.has(label);
}

function isKeyPropRef(ref) {
  const label = String(ref?.label || ref?.display_name || "").trim();
  const evidence = [ref?.evidence_text, ref?.visual_evidence_span, ref?.descriptive_signature, ref?.source_text].filter(Boolean).join(" ");
  const status = String(ref?.status || "").toLowerCase();
  const source = String(ref?.source || "").toLowerCase();
  if (!label) return false;
  if (KEY_PROP_LABELS.has(label) && (evidence.includes(label) || source.includes("explicit") || ["mentioned", "prop_relevant", "key_prop"].includes(status))) return true;
  if (["mentioned", "prop_relevant", "key_prop"].includes(status) && evidence.includes(label)) return true;
  return KEY_PROP_ACTION_RE.test(`${label} ${evidence}`) && PROP_HINTS.some((term) => label.includes(term));
}

export function assetRefToken(asset) {
  return `@${asset.label}`;
}

export function assetRefDisplay(asset) {
  return `${assetRefToken(asset)}（${assetTypeShortLabel(asset.asset_type)}）`;
}

export function assetTypeLabel(asset) {
  return {
    character: "角色资产",
    scene: "场景资产",
    prop: "道具资产",
  }[asset.asset_type] || "资产";
}

function addImplicitRefs(refs, text) {
  const hasCharacter = refs.some((asset) => asset.asset_type === "character");
  const hasScene = refs.some((asset) => asset.asset_type === "scene");
  for (const label of inferCharacterLabels(text)) {
    pushAssetRef(refs, label, "character", "candidate", text);
  }
  if (!hasCharacter && CHARACTER_HINTS.some((hint) => text.includes(hint))) {
    const label = inferCharacterLabel(text);
    if (label && !GENERIC_CHARACTER_LABELS.has(label)) pushAssetRef(refs, label, "character", "candidate", text);
  }
  if (!hasScene && SCENE_HINTS.some((hint) => text.includes(hint))) {
    const label = inferSceneLabel(text);
    if (label && !GENERIC_SCENE_LABELS.has(label)) pushAssetRef(refs, label, "scene", "candidate", text);
  }
}

function pushAssetRef(refs, label, assetType, source, context = "", options = {}) {
  const clean = cleanAssetLabel(semanticAssetLabel(label, assetType, context));
  if (!clean || refs.some((asset) => asset.label === clean)) return;
  const ref = {
    label: clean,
    asset_id: options.asset_id || `candidate:${assetType}:${slug(clean)}`,
    graph_asset_id: options.graph_asset_id || "",
    asset_type: assetType,
    status: options.status || (source === "explicit" ? "mentioned" : "candidate"),
    source,
  };
  if (!ref.graph_asset_id) delete ref.graph_asset_id;
  refs.push(ref);
}

function descriptionWithAssets(source, assetRefs) {
  return replaceGenericAssetTokens(String(source || ""), assetRefs);
}

function replaceGenericAssetTokens(source, assetRefs) {
  let text = source;
  const character = assetRefs.find((asset) => asset.asset_type === "character" && !GENERIC_CHARACTER_LABELS.has(asset.label));
  const scene = assetRefs.find((asset) => asset.asset_type === "scene" && !GENERIC_SCENE_LABELS.has(asset.label));
  if (character) {
    for (const label of GENERIC_CHARACTER_LABELS) text = text.replaceAll(`@${label}`, assetRefToken(character));
  }
  if (scene) {
    for (const label of GENERIC_SCENE_LABELS) text = text.replaceAll(`@${label}`, assetRefToken(scene));
  }
  return text;
}

function semanticAssetLabel(label, assetType, context) {
  const clean = cleanAssetLabel(label);
  if (assetType === "character" && GENERIC_CHARACTER_LABELS.has(clean)) return inferCharacterLabel(context) || clean;
  if (assetType === "scene" && GENERIC_SCENE_LABELS.has(clean)) return inferSceneLabel(context) || clean;
  return clean;
}

function inferCharacterLabel(text) {
  const labels = inferCharacterLabels(text);
  if (labels.length) return labels[0];
  const source = String(text || "");
  if (/未来.*机器人|机器人.*未来/.test(source)) return "未来机器人";
  if (/机器人|机械人|仿生人/.test(source)) return "机器人";
  if (/女孩|少女/.test(source)) return "女孩";
  if (/男孩|少年/.test(source)) return "男孩";
  if (/老人/.test(source)) return "老人";
  if (/孩子/.test(source)) return "孩子";
  return "";
}

function inferCharacterLabels(text) {
  const source = String(text || "");
  const labels = [];
  const relationRe = /([\u4e00-\u9fffA-Za-z0-9·]{2,12})(?:大战|对决|迎娶|娶了|娶|嫁给|爱上|遇见|面对|追击|追杀|营救|守护)([\u4e00-\u9fffA-Za-z0-9·]{2,12})/gu;
  for (const match of source.matchAll(relationRe)) {
    appendLabel(labels, trimCharacterName(match[1]));
    appendLabel(labels, trimCharacterName(match[2]));
  }
  const knownMatches = [];
  for (const name of KNOWN_CHARACTER_NAMES) {
    const index = source.indexOf(name);
    if (index >= 0) knownMatches.push({ name, index });
  }
  knownMatches
    .sort((a, b) => a.index - b.index)
    .forEach((item) => appendLabel(labels, item.name));
  if (/女生|女孩|少女/.test(source)) {
    appendLabel(labels, source.includes("女生") ? "女生" : "女孩");
  }
  return labels.slice(0, 6);
}

function inferSceneLabel(text) {
  const source = String(text || "");
  const isNight = /夜|星空|月光|霓虹|灯火/.test(source);
  const isCity = /城市|高楼|天际线|霓虹|楼宇/.test(source);
  const isRooftop = /屋顶|楼顶|天台/.test(source);
  if (isNight && isCity && isRooftop) return "夜晚城市屋顶";
  if (isCity && isRooftop) return "城市屋顶";
  if (isNight && isCity) return "夜晚城市";
  if (isRooftop) return "屋顶平台";
  if (isCity) return "城市场景";
  if (/云栈洞口|洞口|洞内|山洞/.test(source)) return source.includes("云栈") ? "云栈洞口" : "山洞场景";
  if (/老城区巷口|巷口|窄巷|巷子|青石台阶|青砖/.test(source)) return /老城区|巷口/.test(source) ? "老城区巷口" : "巷道空间";
  if (source.includes("古战场")) return "古战场";
  if (source.includes("战场")) return "战场";
  if (looksLikeMountainBattleScene(source)) return "山巅石台战场";
  return "";
}

function looksLikeMountainBattleScene(source) {
  if (/山巅|山脊|云海/.test(source)) return true;
  return source.includes("石台") && /山|峰|云|战|大战|对决|破碎/.test(source);
}

function inferDuration(text) {
  const match = text.match(/(\d{1,2})\s*(?:s|秒)/i);
  if (match) return `${match[1]}s`;
  if (text.length > 130) return "8s";
  if (text.length > 70) return "6s";
  return "5s";
}

function inferShotSize(text) {
  if (/大远景|远景|全貌|城市|山谷|天空/.test(text)) return "远景";
  if (/全景|全身|环境/.test(text)) return "全景";
  if (/近景|脸|眼神|表情/.test(text)) return "近景";
  if (/特写|手指|瞳孔|细节|屏幕/.test(text)) return "特写";
  if (/半身|肩/.test(text)) return "半身景";
  return "中景";
}

function inferLighting(text) {
  if (/夜|霓虹|暗|阴影/.test(text)) return "低照度，冷色阴影压低环境";
  if (/晨|清晨|阳光|明亮/.test(text)) return "自然主光，明亮通透";
  if (/雨|雾|烟|尘/.test(text)) return "柔散光，空气颗粒增强层次";
  if (/紧张|压迫|冲突/.test(text)) return "高反差侧光，氛围紧张";
  return "自然光影，氛围服务情绪推进";
}

function inferCameraMotion(text) {
  if (/推近|逼近|靠近/.test(text)) return "缓慢推近";
  if (/拉远|退后/.test(text)) return "缓慢拉远";
  if (/跟随|追|奔跑/.test(text)) return "跟拍移动";
  if (/摇|环绕/.test(text)) return "轻微摇移";
  if (/切|闪回|突然/.test(text)) return "快速切入";
  return "固定机位，轻微呼吸感";
}

function inferDialogue(text) {
  const quote = text.match(/[“"](.*?)[”"]/);
  if (quote?.[1]) return quote[1].slice(0, 80);
  const line = text.match(/(?:对白|旁白|台词)\s*[:：]\s*(.+)$/);
  return line?.[1]?.slice(0, 80) || "无明确对白";
}

function inferSound(text) {
  if (/键盘|电脑|屏幕/.test(text)) return "键盘声与设备低频环境音";
  if (/雨|海|风/.test(text)) return "环境自然声持续铺底";
  if (/冲突|奔跑|撞|爆/.test(text)) return "急促动作音与低频冲击";
  return "环境底噪，动作音随画面同步";
}

function classifyAsset(label, context) {
  if (SCENE_HINTS.some((hint) => label.includes(hint) || context.includes(`${label}里`) || context.includes(`${label}中`))) return "scene";
  if (label === "灯") return /@灯|路灯|台灯|灯具|灯柱|灯盏/.test(context) ? "prop" : "scene";
  if (label === "信") return /@信|信件|信封|信纸|一封信|书信/.test(context) ? "prop" : "character";
  if (PROP_HINTS.some((hint) => label.includes(hint))) return "prop";
  return "character";
}

function appendLabel(labels, value) {
  const clean = String(value || "").replace(/^[以把将和与及、，。；：:\s]+|[的与和及、，。；：:\s]+$/g, "").trim();
  if (clean && !labels.includes(clean)) labels.push(clean.slice(0, 24));
}

function trimCharacterName(value) {
  return String(value || "")
    .replace(/^(以|把|将|当|用|和|与|及|、)+/, "")
    .replace(/^.*(?:是|讲述|关于|围绕)/, "")
    .replace(/(为核心|为主题|为主|展开|对决|战斗|格斗|碰撞).*$/, "")
    .replace(/(但是|但|却|旁观|观战|从旁).*$/, "")
    .trim();
}

function assetTypeShortLabel(assetType) {
  return {
    character: "角色",
    scene: "场景",
    prop: "道具",
  }[assetType] || "资产";
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function cleanAssetLabel(value) {
  return String(value || "").replace(/[，。；:：,.!?！？]+$/g, "").trim().slice(0, 24);
}

function slug(value) {
  return encodeURIComponent(value).replace(/%/g, "").slice(0, 32).toLowerCase() || "asset";
}
