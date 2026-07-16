export const DEFAULT_KEYFRAME_VIDEO_MOTION = "轻微缓慢推进，保持温和连续性和呼吸感镜头。";

export function buildKeyframeVideoPrompt(source, videoAssetPlan, options = {}) {
  const duration = String(options.duration || source?.params?.spec?.duration || "5s").trim();
  const profile = videoPromptProfile(source, videoAssetPlan);
  const motion = videoSafeText(options.motion || DEFAULT_KEYFRAME_VIDEO_MOTION, profile);
  const assetLines = videoAssetLines(videoAssetPlan, profile);
  const sourceSummary = videoSafeSourceSummary(source, profile);
  return [
    `${duration} 图生视频时间轴：以上游关键帧作为 0.0s 首帧视觉锚点。`,
    "首帧锁定：0.0s 必须贴合上游关键帧的主体身份、外观细节、道具几何、场景布局、镜头构图、光影和色彩关系。",
    "资产连续性锁定：",
    ...assetLines,
    "时间轴动作设计：",
    "0.0s：完全承接首帧姿态和构图，不跳切，不改主体外观，不改变场景。",
    "0.0-1.0s：保持首帧空间关系，只加入轻微呼吸、环境光、微风、毛发或衣物的细微运动。",
    "1.0-2.5s：主体在原位做温和小幅动作、视线变化或姿态微调，道具保持形状、尺度和摆放关系。",
    "2.5-4.0s：动作自然推进但幅度克制，主体关系和构图保持稳定。",
    "4.0-5.0s：动作自然收束到可作为下一镜头衔接的姿态，保留首帧身份和空间关系。",
    `镜头运动：${motion} 保持中低幅度镜头变化，避免大幅推拉、旋转、换景或突然剪辑。`,
    negativeConstraintLine(profile),
    sourceSummary ? `上游关键帧摘要（仅作为首帧和连续性依据）：${sourceSummary}` : "",
  ].filter(Boolean).join("\n");
}

function videoAssetLines(videoAssetPlan, profile) {
  const assets = safeArray(videoAssetPlan?.assets);
  if (!assets.length) {
    return ["- 未识别到独立资产卡：以首帧中已经出现的角色、道具、场景为唯一连续性依据。"];
  }
  return assets.map((asset) => {
    const status = assetStatusLabel(asset.status);
    const policy = asset.reference_policy === "reference_images_available"
      ? "有参考图时按参考图稳定身份与外观"
      : "无参考图时仅按首帧和文字摘要约束";
    const lock = continuityLockForAsset(asset);
    const signature = videoSafeText(asset.signature || "", profile);
    const suffix = signature ? `；视觉摘要：${compactText(signature, 120)}` : "";
    return `- @${asset.label}（${videoAssetTypeLabel(asset)} / ${status}）：${lock}；${policy}${suffix}`;
  });
}

function continuityLockForAsset(asset) {
  const type = String(asset?.asset_type || "").trim();
  if (type === "character" && isAnimalAsset(asset)) {
    return "锁定物种、毛色/花纹、体型比例、自然动物外观和首帧位置";
  }
  if (type === "character") return "锁定身份、外观轮廓、体态比例和首帧站位";
  if (type === "scene") return "锁定地形结构、空间层次、光影方向、天气和镜头落点";
  if (type === "prop") return "锁定道具形状、尺度、材质、握持或摆放关系";
  return "锁定首帧中的外观、功能和与其他资产的空间关系";
}

function videoAssetTypeLabel(asset) {
  const type = String(asset?.asset_type || "").trim();
  if (type === "character" && isAnimalAsset(asset)) return "动物角色";
  if (type === "character") return "角色";
  if (type === "scene") return "场景";
  if (type === "prop") return "道具";
  return "资产";
}

function assetStatusLabel(status) {
  const value = String(status || "").trim().toLowerCase();
  if (["fixed", "ready", "accepted"].includes(value)) return "已固定";
  if (["rejected", "retired"].includes(value)) return "不使用";
  return "候选";
}

function videoSafeSourceSummary(source, profile) {
  const text = String(source?.prompt || source?.result || "").trim();
  if (!text) return "";
  const lines = text
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.replace(/^根据分镜生成关键帧[:：]\s*/u, "分镜意图："))
    .filter((line) => !/(画面要求|单张关键帧|候选资产卡|未固定不阻断|不作为参考图注入|已固定资产|必须保持)/u.test(line));
  return compactText(videoSafeText(lines.join(" "), profile), 420);
}

function negativeConstraintLine(profile) {
  const base = "负向约束：不新增角色、不新增额外道具、不出现文字、水印、UI、边框；不改变主体身份、外观轮廓、道具结构或场景位置关系。";
  if (!profile.careSensitive) return base;
  return `${base} 涉及动物或儿童时，保持安全、平静、被照顾的状态，全程动作克制、关系稳定。`;
}

function videoPromptProfile(source, videoAssetPlan) {
  const assets = safeArray(videoAssetPlan?.assets);
  const text = [
    source?.prompt || "",
    source?.result || "",
    ...assets.flatMap((asset) => [asset?.label, asset?.signature, asset?.character_subtype, asset?.asset_type]),
  ].join(" ");
  const hasAnimal = assets.some(isAnimalAsset) || /猫|狗|犬|幼犬|奶狗|小狗|小猫|橘猫|puppy|kitten|cat|dog/i.test(text);
  const hasChild = /儿童|孩子|小孩|小男孩|小女孩|男孩|女孩|学生|高中生|child|kid/i.test(text);
  const hasCareCue = /照看|照顾|救助|陪伴|治愈|温和|日常|安静|平静/.test(text);
  return {
    hasAnimal,
    hasChild,
    careSensitive: hasAnimal || hasChild || hasCareCue,
  };
}

function isAnimalAsset(asset) {
  const text = `${asset?.label || ""} ${asset?.signature || ""} ${asset?.character_subtype || ""} ${asset?.asset_type || ""}`;
  return /animal|猫|狗|犬|幼犬|奶狗|小狗|小猫|橘猫|puppy|kitten|cat|dog/i.test(text);
}

function videoSafeText(value, profile) {
  let text = String(value || "").replace(/\s+/gu, " ").trim();
  if (!text) return "";
  const common = [
    [/保留对峙关系/gu, "保持首帧空间关系"],
    [/对峙张力/gu, "温和连续性"],
    [/对峙/gu, "同框互动"],
    [/冲突张力增强/gu, "情绪自然推进"],
    [/冲突张力/gu, "情绪节奏"],
    [/冲突/gu, "互动"],
    [/蓄势/gu, "姿态微调"],
  ];
  for (const [pattern, replacement] of common) text = text.replace(pattern, replacement);
  if (profile.careSensitive) {
    const care = [
      [/刚叼回一只/gu, "正在照看一只"],
      [/叼回/gu, "带回"],
      [/叼着/gu, "靠近"],
      [/蹬踹/gu, "轻微小幅动作"],
      [/爪子悬在半空/gu, "爪子保持自然小幅动作"],
      [/挣扎/gu, "轻微动作"],
      [/湿漉漉/gu, "毛发湿润"],
      [/滴着水/gu, "带有水珠"],
      [/炸毛/gu, "毛发状态"],
      [/死命一塞/gu, "轻轻靠近"],
      [/塞进/gu, "靠近"],
      [/塞/gu, "靠近"],
      [/缺耳|缺了一小块/gu, "耳部特征"],
      [/伤疤|旧伤疤|伤口|流血|锁链|金属撞击/gu, "可见细节"],
      [/攻击|威胁|追逐|打斗/gu, "高强度动作"],
    ];
    for (const [pattern, replacement] of care) text = text.replace(pattern, replacement);
  }
  return compactText(text, 520);
}

function compactText(value, limit) {
  const text = String(value || "").replace(/\s+/gu, " ").trim();
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 3))}...` : text;
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}
