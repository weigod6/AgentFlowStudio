import {
  assetCardFieldsForType,
  assetCardTypeLabel,
  normalizeAssetCardDraft,
} from "./asset-card-drafts.js";

export function assetImagePrompt(draft) {
  const card = normalizeAssetCardDraft(draft);
  return [
    assetImageLead(card),
    assetImageModeInstruction(card.asset_type, card.label),
    "Use professional concept art rendering with clear forms, readable materials, stable proportions, and production reference quality.",
    `Asset signature: ${card.signature}`,
    "Asset facts:",
    assetFieldLines(card),
    "Keep the result as a visual reference image only. Do not turn it into a storyboard keyframe or a complete narrative scene.",
    "Forbidden: software dashboard, app interface, data chart, infographic, UI panel, typography, captions, labels, watermarks, logos, borders, decorative card layout.",
  ].filter(Boolean).join("\n");
}

export function assetImageRatio(assetType) {
  return safeAssetType(assetType) === "prop" ? "1:1" : "16:9";
}

export function assetPromptSupplementFromNode(node) {
  const manual = assetCardUserAdjustmentText(node);
  if (manual) return `User asset-card adjustment:\n${manual}`;
  return "";
}

export function assetCardUserAdjustmentText(node) {
  const candidates = [
    String(node?.params?.assetCardDraft?.user_edited_text || "").trim(),
    String(node?.prompt || "").trim(),
  ];
  for (const manual of candidates) {
    if (!manual) continue;
    if (looksLikeGeneratedAssetCardText(manual) || looksLikeGeneratedAssetImagePrompt(manual)) continue;
    return manual;
  }
  return "";
}

export function assetCardPromptPlaceholder(assetType) {
  const type = safeAssetType(assetType);
  if (type === "scene") return "上传场景参考图，再写调整要求，如：只把天气改为雪夜，保持空间结构和视角组不变";
  if (type === "prop") return "上传道具参考图，再写调整要求，如：只把材质改为旧铜，保持形状、比例和四视图不变";
  return "上传角色参考图，再写调整要求，如：只给左脸增加一道浅疤，保持身份、服装和四视图布局不变";
}

function assetImageLead(card) {
  return `Visual target: reusable ${assetCardTypeLabel(card.asset_type, card.character_subtype)} reference image for asset named ${cleanAssetName(card.label)}.`;
}

function assetImageModeInstruction(assetType, label) {
  const assetName = cleanAssetName(label);
  if (assetType === "scene") {
    return [
      `环境参考图：只生成名为 ${assetName} 的场景资产，不生成角色资产或角色设定表。`,
      `Environment reference for asset named ${assetName}: show the same environment/location from multiple clear camera angles in one image.`,
      "Required layout: clean 2x2 grid of four independent 16:9 environment views: center-axis wide establishing view, reverse angle, overhead/spatial layout view, and lighting/material detail view.",
      "Keep the same architecture, skyline, horizon, props, lighting direction, time of day, and spatial relationship across all views.",
      "上游剧情中的角色名只作为环境痕迹参考；不得渲染任何角色主体、头像、半身像、全身转面、手持武器或人物剪影。",
      "No people, character shadows, silhouettes, hands, vehicles as subjects, text, labels, border captions, UI, or unrelated story objects unless explicitly listed as environment elements.",
    ].join(" ");
  }
  if (assetType === "prop") {
    return [
      `Object reference for asset named ${assetName}: show one prop/object with orthographic front, side, top, and close-up material/detail views.`,
      "Keep the object centered, isolated, readable, and consistent across all views.",
      "Do not let a character or environment become the main subject.",
    ].join(" ");
  }
  return [
    `角色参考图：只生成名为 ${assetName} 的单一角色资产；上游剧情里的其他角色名、道具名和场景名不进入画面。`,
    `Character reference sheet for asset named ${assetName}: use this exact layout in one image: front half-body close-up, centered full-body front view, left-side full-body profile view, and back full-body view.`,
    "Keep one consistent identity, head shape, body proportions, limb structure, silhouette, palette, material, and expression across every view.",
    "Use a plain neutral studio background with minimal ground shadow; no second character, handheld weapons, separate props, background objects, typography, labels, or scene environment. Body-integrated traits explicitly listed in Asset facts may remain visible.",
  ].join(" ");
}

function cleanAssetName(value) {
  return String(value || "asset").replace(/^@+/, "").replace(/[<>]/g, "").trim() || "asset";
}

function assetFieldLines(card) {
  return assetCardFieldsForType(card.asset_type)
    .map(([key, label]) => `${providerFieldLabel(card.asset_type, key, label)}: ${card.feature_card[key] || "to be confirmed"}`)
    .join("\n");
}

function providerFieldLabel(assetType, key, fallback) {
  const labels = {
    character: {
      identity: "Identity",
      appearance: "Overall recognizable structure",
      hair: "Hair / fur / head covering",
      face: "Face / head details",
      build: "Body build and proportions",
      wardrobe: "Outer shell / clothing",
      palette: "Color palette",
      demeanor: "Mood / expression",
      reference_views: "Required reference views",
    },
    scene: {
      location: "Location",
      layout: "Spatial layout",
      props: "Environment elements",
      lighting_mood: "Lighting mood",
      palette: "Scene color palette",
      time_weather: "Time and weather",
      view_set: "Required camera angles",
    },
    prop: {
      category: "Object category",
      appearance: "Recognizable details",
      material: "Materials and craft",
      scale: "Scale relationship",
      usage: "Usage state",
      interaction: "Holding / interaction relationship",
      continuity: "Continuity constraint",
      reference_views: "Required reference views",
    },
  };
  return labels[safeAssetType(assetType)]?.[key] || fallback;
}

function looksLikeGeneratedAssetCardText(value) {
  return /^资产类型：/u.test(value)
    || /状态：候选草稿/u.test(value)
    || /特征卡：/u.test(value)
    || /不可变锁定项：/u.test(value);
}

function looksLikeGeneratedAssetImagePrompt(value) {
  return /^Visual target: reusable /u.test(value)
    || /Asset facts:/u.test(value)
    || /Forbidden: software dashboard/u.test(value)
    || /Character reference sheet for asset named/u.test(value)
    || /Environment reference for asset named/u.test(value)
    || /Object reference for asset named/u.test(value);
}

function safeAssetType(value) {
  return ["character", "scene", "prop"].includes(value) ? value : "character";
}
