export const ASSET_CARD_FIELDS = {
  character: [
    ["identity", "身份定位"],
    ["appearance", "外形总览"],
    ["hair", "发型/毛发/颜色"],
    ["face", "面部/头部特征"],
    ["build", "体态身形"],
    ["wardrobe", "服装/外观"],
    ["palette", "主色调"],
    ["demeanor", "气质状态"],
    ["reference_views", "设定板视图组"],
  ],
  scene: [
    ["location", "地点定位"],
    ["layout", "空间结构"],
    ["props", "关键道具"],
    ["lighting_mood", "光影氛围"],
    ["palette", "场景配色"],
    ["time_weather", "时间天气"],
    ["view_set", "多视角视图组"],
  ],
  prop: [
    ["category", "道具类别"],
    ["appearance", "外观细节"],
    ["material", "材质工艺"],
    ["scale", "尺寸比例"],
    ["usage", "使用方式"],
    ["interaction", "持握/互动关系"],
    ["continuity", "连续性约束"],
    ["reference_views", "道具视图组"],
  ],
};

export function assetCardDraftFromRef(asset, structuredShot, options = {}) {
  const assetType = safeAssetType(asset?.asset_type);
  const label = safeLabel(asset?.label, assetType);
  const shotText = shotDescription(structuredShot);
  const profile = assetProfileFromRef(asset);
  const featureCard = featureCardFromProfile(assetType, label, shotText, profile)
    || defaultFeatureCard(assetType, label, shotText);
  const profileLocks = continuityLocksFromProfile(profile);
  return normalizeAssetCardDraft({
    card_id: `asset_card:${structuredShot?.shot_id || "shot"}:${assetType}:${slug(label)}`,
    asset_type: assetType,
    character_subtype: cleanText(profile?.character_subtype || asset?.character_subtype || ""),
    label,
    status: "draft",
    source: "shot_asset_recognition",
    source_script_node_id: options.sourceScriptNodeId || "",
    source_shot_id: structuredShot?.shot_id || "",
    source_asset_ref: asset || {},
    role_in_shot: roleInShot(assetType, label),
    signature: signatureFromProfile(assetType, label, profile) || signatureFor(assetType, label, shotText),
    feature_card: featureCard,
    negative_locks: profileLocks.length ? profileLocks : defaultLocks(assetType, label),
    provider_negative_locks: stringList(profile?.negative_locks || asset?.negative_locks),
    facts: plainObject(profile?.facts || asset?.facts),
    fact_evidence: stringList(profile?.fact_evidence || asset?.fact_evidence),
    missing_fact_fields: stringList(profile?.missing_fact_fields || asset?.missing_fact_fields),
    asset_fact_profile: profile || asset?.asset_fact_profile || null,
    evidence_text: shotText.slice(0, 500),
    memory_policy: {
      writes_fixed_asset: false,
      included_in_context_before_confirmation: false,
      requires_human_confirmation: true,
    },
    created_at: new Date().toISOString(),
  });
}

export function normalizeAssetCardDraft(draft) {
  const assetType = safeAssetType(draft?.asset_type);
  const label = safeLabel(draft?.label, assetType);
  const evidenceText = String(draft?.evidence_text || "");
  return {
    ...draft,
    asset_type: assetType,
    label,
    status: draft?.status || "draft",
    signature: normalizedSignature(assetType, label, evidenceText, draft?.signature),
    feature_card: normalizedFeatureCard(assetType, label, evidenceText, draft?.feature_card),
    negative_locks: lines(draft?.negative_locks),
    memory_policy: {
      writes_fixed_asset: false,
      included_in_context_before_confirmation: false,
      requires_human_confirmation: true,
      ...(draft?.memory_policy || {}),
    },
  };
}

export function assetCardFieldsForType(assetType) { return ASSET_CARD_FIELDS[safeAssetType(assetType)] || ASSET_CARD_FIELDS.character; }

export function assetCardTypeLabel(assetType, characterSubtype = "") {
  if (safeAssetType(assetType) === "character" && cleanText(characterSubtype) === "animal") return "动物角色资产";
  if (safeAssetType(assetType) === "character" && cleanText(characterSubtype) === "robot") return "机器人角色资产";
  return { character: "角色资产", scene: "场景资产", prop: "道具资产" }[safeAssetType(assetType)];
}

export function assetCardText(draft) {
  const card = normalizeAssetCardDraft(draft);
  const fieldLines = assetCardFieldsForType(card.asset_type)
    .map(([key, label]) => `- ${label}：${card.feature_card[key] || "待补充"}`);
  const lockLines = card.negative_locks.length
    ? card.negative_locks.map((item) => `- ${item}`)
    : ["- 确认固定前不进入生成约束"];
  return [
    `资产类型：${assetCardTypeLabel(card.asset_type, card.character_subtype)}`,
    `资产名称：@${card.label}`,
    "状态：候选草稿，确认固定前不会进入关键帧约束",
    `一句话签名：${card.signature}`,
    "特征卡：",
    ...fieldLines,
    "不可变锁定项：",
    ...lockLines,
    `来源分镜：${card.source_shot_id || "未标记"}`,
  ].join("\n");
}

function defaultFeatureCard(assetType, label, shotText) {
  if (assetType === "scene") {
    const sceneText = `${label} ${shotText}`.trim();
    return {
      location: sceneLocation(label, sceneText),
      layout: sceneLayout(sceneText),
      props: sceneProps(sceneText),
      lighting_mood: sceneLightingMood(sceneText),
      palette: scenePalette(sceneText),
      time_weather: sceneTimeWeather(sceneText),
      view_set: "同一场景的俯瞰全景、正向广角、入口/边缘视角、光影或材质细节视角，空间关系保持一致",
    };
  }
  if (assetType === "prop") {
    return {
      category: label,
      appearance: propAppearance(label, shotText),
      material: propMaterial(label, shotText),
      scale: "与角色/场景比例一致",
      usage: propUsage(label, shotText),
      interaction: propInteraction(label, shotText),
      continuity: "后续镜头保持同一造型、材质和使用状态",
      reference_views: "正面、侧面、俯视、局部结构/材质特写，比例与材质保持一致",
    };
  }
  return {
    identity: characterIdentity(label, shotText),
    appearance: characterAppearance(label, shotText),
    hair: characterHair(label, shotText),
    face: characterFace(label, shotText),
    build: characterBuild(label, shotText),
    wardrobe: characterWardrobe(label, shotText),
    palette: characterPalette(label, shotText),
    demeanor: characterDemeanor(label, shotText),
    reference_views: "正面半身特写 + 全身正面居中 + 左侧面全身 + 背面全身；无任何道具或背景物体，比例与外观保持一致",
  };
}

function signatureFor(assetType, label, shotText) {
  if (assetType === "scene") {
    return `${label}：${sceneSignatureHint(label, shotText)}`.slice(0, 120);
  }
  if (assetType === "character") {
    return `${label}：${characterSignatureHint(label, shotText)}`.slice(0, 120);
  }
  if (assetType === "prop") {
    return `${label}：${propSignatureHint(label, shotText)}`.slice(0, 120);
  }
  const suffix = {
    character: "可复用角色，身份与外观待确认",
    scene: "可复用场景，空间与光影待确认",
    prop: "可复用道具，外观与使用方式待确认",
  }[assetType] || "可复用资产";
  const hint = phraseFromShot(shotText, suffix);
  return `${label}：${hint}`.slice(0, 120);
}

function defaultLocks(assetType, label) {
  if (assetType === "scene") return [`保持${label}空间结构`, "保持多视角空间关系一致", "保持时间/光影氛围", "保持关键环境元素"];
  if (assetType === "prop") return [`保持${label}外观`, "保持多视图结构一致", "保持材质和尺寸比例", "保持使用状态连续"];
  return [`保持${label}身份`, "保持正侧背视图一致", "保持外观辨识点", "保持体态比例", "保持主色调"];
}

function roleInShot(assetType, label) {
  if (assetType === "scene") return `${label}作为当前分镜空间承载`;
  if (assetType === "prop") return `${label}作为剧情动作道具`;
  return `${label}作为当前分镜角色主体`;
}

function phraseFromShot(text, fallback) {
  const clean = stripAssetTags(String(text || "")).replace(/\s+/g, " ").trim();
  if (!clean) return fallback;
  return clean.split(/[。；.!?！？]/u)[0].slice(0, 80) || fallback;
}

function normalizedSignature(assetType, label, evidenceText, value) {
  let clean = cleanDraftField(value);
  if (assetType === "scene") clean = cleanSceneDraftField(clean);
  if (assetType === "character") clean = cleanCharacterDraftField(label, clean);
  if (!signatureHasMeaning(clean, label)) return signatureFor(assetType, label, evidenceText);
  if (clean.includes("：") || clean.includes(":")) return clean.slice(0, 120);
  return `${label}：${clean}`.slice(0, 120);
}

function normalizedFeatureCard(assetType, label, evidenceText, card) {
  const fallback = defaultFeatureCard(assetType, label, evidenceText);
  const source = card && typeof card === "object" ? card : {};
  const result = {};
  for (const [key] of assetCardFieldsForType(assetType)) {
    let clean = cleanDraftField(source[key]);
    if (assetType === "scene") clean = cleanSceneDraftField(clean);
    if (assetType === "character") clean = cleanCharacterDraftField(label, clean);
    const text = clean && signatureHasMeaning(clean, label) ? clean : fallback[key];
    if (text) result[key] = String(text).slice(0, 260);
  }
  return result;
}

function signatureHasMeaning(value, label) {
  const clean = stripAssetTags(String(value || ""))
    .replace(new RegExp(`^${escapeRegExp(label)}\\s*[：:]?\\s*`, "u"), "")
    .replace(/[：:\s]+$/u, "")
    .trim();
  return clean.length > 0;
}

function cleanDraftField(value) {
  return stripAssetTags(String(value || ""))
    .replace(/\s+/g, " ")
    .replace(/^[\s，。、；：:]+/u, "")
    .trim();
}

function cleanSceneDraftField(value) {
  const clean = stripAssetTags(value);
  if (!clean) return "";
  if (/孙悟空|金刚狼|金箍棒|钢爪|双爪|角色手持|大战|角色主体|人物主体|画面绝对主体/u.test(clean)) return "";
  return clean;
}

function cleanCharacterDraftField(label, value) {
  const clean = stripAssetTags(value);
  if (!clean) return "";
  if (characterFieldLooksLikeStoryContext(label, clean)) return "";
  return clean;
}

function characterFieldLooksLikeStoryContext(label, value) {
  const text = String(value || "");
  const otherCharacters = ["孙悟空", "金刚狼"].filter((name) => !sameAssetName(name, label));
  if (otherCharacters.some((name) => text.includes(name))) return true;
  if (/镜号|画面描述|景别|光影氛围|运镜|对白|旁白|音效|资产：|来源分镜/u.test(text)) return true;
  if (/金箍棒|山巅|石台|战场|云海|大战|对决|碰撞|冲击波|火花/u.test(text)) return true;
  return false;
}

function sameAssetName(left, right) {
  const a = cleanAssetNameForCompare(left);
  const b = cleanAssetNameForCompare(right);
  return Boolean(a && b && (a === b || a.includes(b) || b.includes(a)));
}

function cleanAssetNameForCompare(value) {
  return String(value || "").replace(/^@+/, "").replace(/\s+/g, "").trim();
}

function shotDescription(structuredShot) { return String(structuredShot?.description || structuredShot?.source_text || "").trim(); }

function stripAssetTags(text) {
  return String(text || "").replace(/@[^\s，。、；：:]+(?:（[^）]*）)?/gu, "").replace(/^[\s，。、；：:]+/u, "").trim();
}

function characterIdentity(label, text) {
  if (/孙悟空/.test(label)) return "孙悟空，东方神话战士角色，身手敏捷，战斗姿态强";
  if (/金刚狼/.test(label)) return "金刚狼，成熟粗犷男性近身格斗角色，强韧、压迫感强，指间金属利爪是身体特征";
  if (/机器人|机械|金属机身/.test(text)) return label === "主角" ? "来自未来的机器人主角" : `${label}，未来科幻机器人角色`;
  return label;
}

function characterAppearance(label, text) {
  if (/孙悟空/.test(label)) return "猴相人形战士轮廓，面部毛发与锐利眼神清晰，保留神话战斗辨识度";
  if (/金刚狼/.test(label)) return "强壮紧凑的成熟男性近战战士轮廓，肩背紧实，面部线条硬朗，指间金属利爪可作为身体能力显露；不是猴相、不是神话战士、不是金色竖毛、不是银发科幻角色";
  if (/机器人|机械|金属机身/.test(text)) {
    return "金属机身，精密发光纹路，清晰头部轮廓、躯干比例和四肢结构";
  }
  if (/脸|眼神|表情|体态|轮廓/.test(text)) return "根据分镜保留脸部、体态和轮廓辨识点";
  return "根据分镜描述确定可复用外观辨识点";
}

function characterHair(label, text) {
  if (/孙悟空/.test(label)) return "棕金色竖立毛发，猴相鬓毛清晰，发冠或头饰不改变主体头部比例";
  if (/金刚狼/.test(label)) return "深色短发或粗硬发型，夸张鬓角与胡须轮廓保持硬派辨识度；不是银发，不使用金色猴毛、发冠或神话头饰";
  if (/黑发|黑色头发|黑色短发|黑色长发/.test(text)) return "黑色头发，长度和发型按分镜语境确定";
  if (/短发/.test(text)) return "短发，发型保持可复用辨识点";
  if (/长发/.test(text)) return "长发，发型保持可复用辨识点";
  if (/机器人|机械|金属机身/.test(text)) return "无自然毛发，头部外壳或发光结构作为识别点";
  return "按分镜语境确定发型、毛发或头部外观，后续可人工补充";
}

function characterFace(label, text) {
  if (/孙悟空/.test(label)) return "猴相面部，眉眼锐利，脸部毛发自然，表情坚毅；面部新增标记需保持自然皮肤或毛发质感";
  if (/金刚狼/.test(label)) return "成熟男性硬朗面部线条，眉眼紧张，胡茬、鬓角或络腮胡清楚，表情克制凶猛；不是少年感、中性脸或精致科幻偶像脸";
  if (/疤|伤疤|刀疤/.test(text)) return "面部标记按分镜描述保留，疤痕需细小自然，不符号化、不变成妆容";
  if (/脸|眼神|表情|五官/.test(text)) return "根据分镜保留脸部、五官、眼神与显著标记";
  return "保持面部/头部可识别特征，后续可人工补充";
}

function characterSignatureHint(label, text) {
  if (/孙悟空/.test(label)) return "东方神话战士角色，猴相面部、棕金毛发和敏捷体态清晰";
  if (/金刚狼/.test(label)) return "硬派近身格斗角色，深色短发鬓角、成熟男性体态、胡茬和指间金属利爪清晰";
  return phraseFromShot(cleanCharacterDraftField(label, text), "可复用角色，身份与外观待确认");
}

function characterBuild(label, text) {
  if (/孙悟空/.test(label)) return "敏捷精瘦的战士体态，四肢有爆发力，比例不变形";
  if (/金刚狼/.test(label)) return "结实强韧、偏矮壮紧凑的近战体态，肩背和粗壮手臂力量感明显";
  if (/机器人|机械|金属机身/.test(text)) return "保持成年类人比例、头身比、躯干和四肢机械结构关系";
  if (/瘦|偏瘦|纤细/.test(text)) return "偏瘦体型，身形比例稳定";
  return "根据分镜保持体态、身高比例和动作能力设定";
}

function characterWardrobe(label, text) {
  if (/孙悟空/.test(label)) return "东方神话战斗服饰或护甲层，保持敏捷战士轮廓，不携带手持道具";
  if (/金刚狼/.test(label)) return "硬派旧皮革或深色贴身战斗服，贴合近身格斗体态；无科幻装甲、无青色发光线、无东方神话盔甲、披帛、发冠或手持道具";
  if (/机器人|机械|金属机身/.test(text)) return "无传统服装，机械外壳与发光部件作为外观层";
  return "服装或外观按分镜语境确定，后续可人工补充";
}

function characterPalette(label, text) {
  if (/孙悟空/.test(label)) return "棕金毛发、赤褐战斗服和暗金护甲点缀";
  if (/金刚狼/.test(label)) return "深棕、黑色、旧皮革、低饱和黄色点缀和冷金属利爪，避免银发科幻灰、青色发光线或金色神话主色";
  if (/冷蓝|星光|星空|月光|青蓝|蓝/.test(text)) return "冷灰金属与青蓝发光纹路，低饱和城市反射";
  if (/霓虹/.test(text)) return "低饱和霓虹反射与主体主色调保持一致";
  return "主色调按分镜语境确定，后续可人工补充";
}

function characterDemeanor(label, text) {
  if (/孙悟空/.test(label)) return "凌厉昂扬、敏捷自信，战意清晰";
  if (/金刚狼/.test(label)) return "克制凶猛、目光压迫，低身蓄力的近战攻击感";
  if (/安静|专注|孤独|沉静|忧伤/.test(text)) return "安静专注，孤独沉静，略带诗意的科幻疏离感";
  return phraseFromShot(cleanCharacterDraftField(label, text), "神态服务当前剧情");
}

function sceneLocation(label, text) {
  if (/码头|港口|海港|岸边|河岸|海岸/.test(text)) return `${label}环境`;
  if (/屋顶|楼顶|天台/.test(text)) return "夜晚城市屋顶/楼顶平台";
  if (/街道|街区|路面|人行道|独自行走|街道氛围/.test(text)) return /雨夜/.test(text) ? "雨夜城市街道/街区外景" : "城市街道/街区外景";
  if (/山巅|山脊|石台|云海|战场/.test(text)) return "山巅石台战场";
  if (/城市|天际线/.test(text)) return "城市外景与天际线环境";
  return label;
}

function sceneSignatureHint(label, text) {
  const sceneText = `${label} ${text}`.trim();
  if (/山巅|山脊|石台|云海|战场/.test(sceneText)) {
    return "山巅石台、悬崖边缘、云海远山和破碎山石构成的可复用环境";
  }
  return phraseFromShot(cleanSceneDraftField(sceneText), "可复用场景，空间与光影待确认");
}

function sceneLayout(text) {
  if (/码头|港口|海港|岸边|河岸|海岸/.test(text)) {
    return "码头或岸线作为空间中心，水面、栈桥/堤岸、远处灯光和可通行动线形成前中后景层次";
  }
  if (/屋顶|楼顶|天台/.test(text)) {
    return "屋顶边缘与平台前景，远处城市天际线，广阔星空占据主要空间";
  }
  if (/街道|街区|路面|人行道|独自行走|街道氛围|雨夜/.test(text)) {
    return "可通行的城市街道/人行道前景，路面雨水反光，街边建筑与远处城市灯光形成纵深";
  }
  if (/城市|天际线/.test(text)) {
    return "城市街区或外景空间，前景可行走区域、中景建筑界面和远处天际线层次清晰";
  }
  if (/山巅|山脊|石台|云海|战场/.test(text)) {
    return "破碎山巅石台作为空间中心，悬崖边缘、周围云海、远处山脊和碎石裂纹形成前中后景层次";
  }
  return "根据分镜画面确定空间结构、主体位置和远近层次";
}

function sceneProps(text) {
  if (/码头|港口|海港|岸边|河岸|海岸/.test(text)) return "水面、栈桥/堤岸、系泊设施、湿润地面和远处灯光作为环境元素";
  if (/山巅|山脊|石台|云海|战场/.test(text)) return "山石平台、破碎石块、云雾层次和远处山脊作为环境元素";
  if (/金箍棒|钢爪|武器/.test(text)) return "角色手持道具应拆为道具资产，场景只保留环境元素";
  if (/灯火|霓虹|高楼|天际线/.test(text)) return "城市灯火、远处高楼、低饱和霓虹反射作为环境元素";
  return "保留分镜中出现的关键环境元素，不额外新增无关道具";
}

function sceneLightingMood(text) {
  if (/雨夜|雨|潮湿|湿地|积水/.test(text) && /夜|低照度|深夜/.test(text)) return "低照度雨夜光线，冷色阴影压低环境，湿润路面保留城市反射";
  if (/山巅|山脊|石台|云海|战场/.test(text)) return "高海拔自然天光与云雾逆光，岩石暗部压低，边缘轮廓清晰";
  if (/冷蓝|月光|星光|星空|霓虹|低饱和/.test(text)) return "冷蓝月光与星光主导，城市霓虹提供低饱和反射";
  if (/低照度|夜晚|深夜/.test(text)) return "低照度夜景光线，暗部压低，主体轮廓清晰";
  return phraseFromShot(cleanSceneDraftField(text), "自然光影，氛围服务剧情");
}

function scenePalette(text) {
  if (/码头|港口|海港|岸边|河岸|海岸/.test(text)) return "冷灰水面、湿润暗部、远处暖色灯光和低饱和反射";
  if (/雨夜|街道|霓虹/.test(text)) return "冷蓝、黑灰和低饱和霓虹反射";
  if (/山巅|山脊|石台|云海|战场/.test(text)) return "冷灰山石、暗蓝云雾、雷光高亮和低饱和金属火花";
  if (/冷蓝|月光|星光|青蓝/.test(text)) return "冷蓝、青蓝和低饱和暗部";
  return "场景配色按分镜语境确定，后续可人工补充";
}

function sceneTimeWeather(text) {
  if (/雨夜/.test(text)) return "雨夜，空气潮湿，路面有反光和轻微雨雾";
  if (/山巅|山脊|石台|云海|战场/.test(text)) return "高山云海与强风薄雾，具体日夜按分镜语境确定";
  if (/雨|雾|风/.test(text)) return "按分镜天气与空气状态确定，保留雨/雾/风等已出现天气线索";
  if (/夜|星空|月光/.test(text)) return "晴朗夜晚，冷蓝月光与星光主导";
  return "按分镜语境确定";
}

function propAppearance(label, text) {
  if (/金箍棒/.test(label)) return "长棍类神话武器，金属质感，两端箍纹与棒身比例清楚，轮廓修长";
  if (/钢爪/.test(label)) return "三刃金属爪，锋利、对称、可从手部伸出，结构清楚";
  if (/伞|雨伞/.test(label)) return "伞面、伞骨和手柄结构清楚，外轮廓完整，开合状态按分镜语境确定";
  if (/灯|路灯|灯具|灯柱/.test(label)) return "独立灯具/光源结构，外轮廓清楚，发光区域和支撑结构可辨认";
  return "根据分镜描述确定外观轮廓和辨识细节";
}

function propMaterial(label, text) {
  if (/金箍棒/.test(label)) return "金属或鎏金材质，磨损边缘与雕刻纹路可见";
  if (/钢爪/.test(label)) return "冷色金属材质，边缘高光锐利";
  if (/伞|雨伞/.test(label)) return "防水布面、金属或木质伞骨与手柄，湿润反光按天气确定";
  if (/灯|路灯|灯具|灯柱/.test(label) && /科幻|未来|金属/.test(text)) return "金属与半透明发光材料，冷色反射";
  return "材质待人工确认";
}

function propUsage(label, text) {
  if (/金箍棒/.test(label)) return "由孙悟空持握、横扫、格挡或立在身侧使用";
  if (/钢爪/.test(label)) return "由金刚狼近身格斗、迎击和格挡使用";
  if (/伞|雨伞/.test(label)) return /雨|雨夜/.test(text) ? "作为遮雨、遮挡或情绪动作道具使用" : "作为手持、遮挡或场景互动道具使用";
  if (/灯|路灯|灯具|灯柱/.test(label)) return "作为环境光源或局部照明使用";
  return "按分镜动作使用";
}

function propInteraction(label, text) {
  if (/金箍棒/.test(label)) return "与孙悟空手部动作绑定，不能变成独立场景装饰或其他武器";
  if (/钢爪/.test(label)) return "与金刚狼手部和近战动作绑定，数量、朝向和长度保持一致";
  if (/伞|雨伞/.test(label)) return "与持握手部、遮挡角度和天气状态保持连续，不变成背景装饰";
  if (/手持|持握|拿着|挥|横扫|格挡|刺|砍/.test(text)) return "与角色手部动作和镜头连续性保持一致";
  return "与角色、场景的互动关系按分镜语境确定";
}

function propSignatureHint(label, text) {
  return `${propAppearance(label, text)}，${propMaterial(label, text)}`.slice(0, 90);
}

function assetProfileFromRef(asset) {
  const candidates = [asset?.profile_plan, asset?.asset_fact_profile, asset?.fact_profile];
  for (const candidate of candidates) {
    if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) return candidate;
  }
  const facts = plainObject(asset?.facts);
  if (Object.keys(facts).length) {
    return {
      facts,
      character_subtype: cleanText(asset?.character_subtype || ""),
      continuity_locks: stringList(asset?.continuity_locks),
      negative_locks: stringList(asset?.negative_locks),
      fact_evidence: stringList(asset?.fact_evidence),
      missing_fact_fields: stringList(asset?.missing_fact_fields),
    };
  }
  return null;
}

function featureCardFromProfile(assetType, label, shotText, profile) {
  if (!profile || typeof profile !== "object") return null;
  const facts = plainObject(profile.facts);
  if (!Object.keys(facts).length) return null;
  const subtype = cleanText(profile.character_subtype || "");
  if (assetType === "character" && subtype === "animal") {
    return animalFeatureCardFromFacts(label, facts, profile);
  }
  if (assetType === "character" && subtype === "robot") {
    return robotFeatureCardFromFacts(label, facts, profile, shotText);
  }
  if (assetType === "character") {
    return humanFeatureCardFromFacts(label, facts, profile, shotText);
  }
  if (assetType === "scene") {
    return sceneFeatureCardFromFacts(label, facts, profile, shotText);
  }
  if (assetType === "prop") {
    return propFeatureCardFromFacts(label, facts, profile, shotText);
  }
  return null;
}

function animalFeatureCardFromFacts(label, facts, profile) {
  const species = factText(facts, "species");
  const color = factText(facts, "color_pattern");
  const state = factText(facts, "surface_state");
  const age = factText(facts, "size_or_age");
  const marks = factList(facts, "distinctive_marks");
  const actions = factList(facts, "current_action");
  const relationships = factList(facts, "relationship");
  const evidence = evidenceSummary(profile);
  return {
    identity: compactJoin([label, species && `物种：${species}`], "；") || label,
    appearance: compactJoin([
      species && `${species}动物主体`,
      color && `${color}毛色/花纹`,
      state && `${state}体表状态`,
      age && `${age}体型/年龄感`,
      ...marks,
    ], "；") || evidence || `${label}动物外观按分镜事实保持`,
    hair: compactJoin([
      color && `毛色/花纹：${color}`,
      state && `毛发/体表状态：${state}`,
      ...marks,
    ], "；") || `按证据保持${label}毛发、耳朵、尾巴和体表特征`,
    face: marks.length ? marks.join("；") : `保持${label}头部、耳朵、眼睛、口鼻轮廓一致`,
    build: compactJoin([age && `体型：${age}`, actions.length && `动作能力：${actions.join("、")}`], "；")
      || `保持${label}体型比例和动物动作能力`,
    wardrobe: "无服装；以毛色、体表状态、耳尾结构和自然动物外形作为外观层",
    palette: color ? `主色调/毛色：${color}` : `按分镜光影保持${label}自然毛色与体表色调`,
    demeanor: compactJoin([
      actions.length && `当前动作/状态：${actions.join("、")}`,
      relationships.length && `关系：${relationships.join("、")}`,
    ], "；") || "神态和动作服务当前分镜剧情",
    reference_views: "动物设定板：正面头部特写 + 全身正面居中 + 左侧面全身 + 背面全身；不添加衣物、项圈或无关道具，毛色/体型/耳尾/标记保持一致",
  };
}

function humanFeatureCardFromFacts(label, facts, profile, shotText) {
  const hair = factText(facts, "hair");
  const wardrobe = factText(facts, "wardrobe");
  const appearance = factText(facts, "appearance_context") || evidenceSummary(profile) || shotText;
  return {
    identity: factText(facts, "identity") || label,
    appearance: appearance || characterAppearance(label, shotText),
    hair: hair || characterHair(label, shotText),
    face: characterFace(label, appearance || shotText),
    build: characterBuild(label, appearance || shotText),
    wardrobe: wardrobe || characterWardrobe(label, shotText),
    palette: characterPalette(label, appearance || shotText),
    demeanor: characterDemeanor(label, appearance || shotText),
    reference_views: "正面半身特写 + 全身正面居中 + 左侧面全身 + 背面全身；无任何道具或背景物体，比例与外观保持一致",
  };
}

function robotFeatureCardFromFacts(label, facts, profile, shotText) {
  const shell = factText(facts, "body_material_or_shell") || evidenceSummary(profile);
  return {
    identity: factText(facts, "identity") || label,
    appearance: shell || characterAppearance(label, shotText),
    hair: "无自然毛发；头部外壳、发光结构或机械轮廓作为识别点",
    face: shell || "保持头部外壳、面部结构和发光部件一致",
    build: "保持机械体型比例、躯干和四肢结构关系",
    wardrobe: "无传统服装；机械外壳与发光部件作为外观层",
    palette: characterPalette(label, shell || shotText),
    demeanor: characterDemeanor(label, shell || shotText),
    reference_views: "机器人设定板：头部特写 + 全身正面 + 左侧面 + 背面；机械结构、材质和发光部件保持一致",
  };
}

function sceneFeatureCardFromFacts(label, facts, profile, shotText) {
  const elements = factList(facts, "key_environment_elements");
  const structure = factText(facts, "spatial_structure");
  const lighting = factText(facts, "lighting_atmosphere");
  return {
    location: factText(facts, "location_type") || label,
    layout: structure || sceneLayout(shotText),
    props: elements.length ? elements.join("；") : sceneProps(shotText),
    lighting_mood: lighting || sceneLightingMood(shotText),
    palette: scenePalette(shotText),
    time_weather: sceneTimeWeather(shotText),
    view_set: "同一场景的俯瞰全景、正向广角、入口/边缘视角、光影或材质细节视角，空间关系保持一致",
  };
}

function propFeatureCardFromFacts(label, facts, profile, shotText) {
  const appearance = factText(facts, "appearance") || evidenceSummary(profile);
  return {
    category: factText(facts, "identity") || label,
    appearance: appearance || propAppearance(label, shotText),
    material: propMaterial(label, appearance || shotText),
    scale: "与角色/场景比例一致",
    usage: propUsage(label, appearance || shotText),
    interaction: propInteraction(label, appearance || shotText),
    continuity: "后续镜头保持同一造型、材质和使用状态",
    reference_views: "正面、侧面、俯视、局部结构/材质特写，比例与材质保持一致",
  };
}

function signatureFromProfile(assetType, label, profile) {
  if (!profile || typeof profile !== "object") return "";
  const facts = plainObject(profile.facts);
  if (!Object.keys(facts).length) return "";
  const subtype = cleanText(profile.character_subtype || "");
  const summary = profileFactSummary(assetType, subtype, facts);
  return summary ? `${label}：${summary}`.slice(0, 120) : "";
}

function profileFactSummary(assetType, subtype, facts) {
  if (assetType === "character" && subtype === "animal") {
    return compactJoin([
      factText(facts, "species"),
      factText(facts, "color_pattern") && `${factText(facts, "color_pattern")}毛色/花纹`,
      factText(facts, "size_or_age"),
      factText(facts, "surface_state"),
      ...factList(facts, "distinctive_marks").slice(0, 2),
      ...factList(facts, "current_action").slice(0, 3),
    ], "；");
  }
  if (assetType === "scene") {
    return compactJoin([
      factText(facts, "location_type"),
      factText(facts, "spatial_structure"),
      factText(facts, "lighting_atmosphere"),
    ], "；");
  }
  return compactJoin(Object.values(facts).flatMap((value) => Array.isArray(value) ? value : [value]).slice(0, 5), "；");
}

function continuityLocksFromProfile(profile) {
  return stringList(profile?.continuity_locks || profile?.identity_locks).slice(0, 8);
}

function factText(facts, key) {
  const value = facts?.[key];
  if (Array.isArray(value)) return value.map(cleanText).filter(Boolean).join("、");
  return cleanText(value);
}

function factList(facts, key) {
  const value = facts?.[key];
  const items = Array.isArray(value) ? value : [value];
  return items.map(cleanText).filter(Boolean).slice(0, 8);
}

function evidenceSummary(profile) {
  return stringList(profile?.fact_evidence || profile?.evidence_text).join("；").slice(0, 180);
}

function plainObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function stringList(value) {
  const source = Array.isArray(value) ? value : String(value || "").split(/\r?\n/);
  return source.map(cleanText).filter(Boolean).slice(0, 16);
}

function compactJoin(values, separator) {
  return values.map(cleanText).filter(Boolean).join(separator);
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function safeAssetType(value) { return ["character", "scene", "prop"].includes(String(value || "")) ? String(value) : "character"; }

function safeLabel(value, assetType) {
  const fallback = assetType === "scene" ? "主要场景" : assetType === "prop" ? "关键道具" : "主角";
  return String(value || fallback).replace(/^@+/, "").trim().slice(0, 40) || fallback;
}

function lines(value) {
  const source = Array.isArray(value) ? value : String(value || "").split(/\r?\n/);
  return source.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 16);
}

function slug(value) { return encodeURIComponent(value).replace(/%/g, "").slice(0, 40).toLowerCase() || "asset"; }

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
