import { createCreatorAuthoringClient } from "./authoring-api-client.mjs?creator=v05";
import {
  commandIdentity,
  createEntityCommand,
  reorderCommand,
  restoreShotCommand,
  reviseEntityCommand,
  reviseShotCommand,
  stableIdentity,
} from "./authoring-commands.mjs";
import {
  approvedReferenceSets,
  createAuthoringUi,
  currentEpisode,
  currentShot,
  refKey,
  sameStableRef,
  scenesForEpisode,
  shotsForScene,
  stableKey,
  uiPreference,
  validPendingEnvelope,
} from "./authoring-model.mjs";

let root;
let client;
let model;
let ui;
let studioState = {};
let studioStateVersion = "";
let statusMessage = "";
let busy = false;
let createDialog = "";
let createParentRef = "";
let impactDialog = null;
let versionDiff = null;
let mobileNavOpen = false;
let stateWrite = Promise.resolve();

const entityCollections = [
  "story_bibles",
  "series",
  "arcs",
  "episodes",
  "scenes",
  "reference_assets",
  "reference_sets",
];

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function entityLabel(type) {
  return ({
    project: "项目资料",
    story_bible: "世界设定",
    series: "长篇故事",
    arc: "篇章",
    episode: "单集",
    scene: "场景",
    shot: "镜头",
    reference_asset: "参考资产",
    reference_set: "参考基准",
  })[type] || "内容";
}

function exactEntity(ref) {
  if (!ref) return null;
  if (sameStableRef(model.project?.ref, ref)) return model.project;
  for (const collection of [...entityCollections, "shots"]) {
    const found = (model[collection] || []).find((item) => sameStableRef(item.ref, ref));
    if (found) return found;
  }
  return null;
}

function selectedEntity() {
  if (ui.selectedSection.startsWith("shot:")) return currentShot(model, ui);
  const found = [...entityCollections, "shots"]
    .flatMap((collection) => model[collection] || [])
    .find((item) => stableKey(item.ref) === ui.selectedSection);
  return found || currentShot(model, ui) || model.project;
}

function renderState(title, message, action = "") {
  root.innerHTML = `<main class="creator-boot"><div class="creator-mark">AFS</div><h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p>${action}</main>`;
}

function render() {
  const episode = currentEpisode(model, ui);
  const shots = currentShot(model, ui);
  const saveLabel = ui.pendingCommand ? "等待确认" : "已保存";
  root.innerHTML = `
    <main class="creator-shell" data-mode="${ui.mode}">
      <header class="creator-topbar">
        <a class="creator-brand" href="/studio/" aria-label="返回 AFS Studio"><span>AFS</span> 创作室</a>
        <nav class="creator-modes" aria-label="创作视图">
          <button type="button" data-mode="storyboard" aria-current="${ui.mode === "storyboard" ? "page" : "false"}">故事板</button>
          <button type="button" data-mode="canvas" aria-current="${ui.mode === "canvas" ? "page" : "false"}">画布</button>
        </nav>
        <div class="creator-save"><span class="save-dot" aria-hidden="true"></span>${saveLabel}</div>
        <div class="creator-private">仅项目成员可见</div>
        <button class="creator-new" type="button" data-open-create>新建</button>
      </header>
      <section class="creator-mobile-context">
        <button type="button" data-mobile-nav>篇章</button>
        <strong>${escapeHtml(episode?.title || model.project.title)}</strong>
        <button type="button" data-open-create>新建</button>
      </section>
      <div class="creator-layout">
        ${renderNavigation()}
        ${ui.mode === "canvas" ? renderCanvas() : renderStoryboard()}
        ${renderInspector(shots)}
      </div>
      <footer class="creator-footer" role="status" aria-live="polite">
        <span>${escapeHtml(statusMessage || "更改会先预览影响，再由你确认保存。")}</span>
        <button type="button" data-technical>${ui.technicalOpen ? "收起技术追溯" : "技术追溯"}</button>
        ${ui.technicalOpen ? `<span class="creator-trace">内容版本 ${model.aggregate_version} · 本页不调用生成服务</span>` : ""}
      </footer>
      ${createDialog ? renderCreateDialog(createDialog) : ""}
      ${impactDialog ? renderImpactDialog(impactDialog) : ""}
    </main>`;
  bindEvents();
}

function renderNavigation() {
  const active = ui.selectedSection;
  const item = (record, label, meta = "") => `
    <button class="creator-nav-item ${active === stableKey(record.ref) ? "active" : ""}" type="button" data-entity="${escapeHtml(stableKey(record.ref))}">
      <span>${escapeHtml(label)}</span>${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
    </button>`;
  return `<aside class="creator-nav ${mobileNavOpen ? "mobile-open" : ""}" aria-label="故事结构">
    <button class="creator-project-title ${active === stableKey(model.project.ref) ? "active" : ""}" type="button" data-entity="${escapeHtml(stableKey(model.project.ref))}">
      <span>项目</span><strong>${escapeHtml(model.project.title)}</strong>
    </button>
    <section><h2>故事与世界</h2>
      ${(model.story_bibles || []).map((record) => item(record, record.title)).join("") || '<p class="creator-nav-empty">还没有世界设定</p>'}
      ${(model.series || []).map((record) => item(record, record.title)).join("") || '<p class="creator-nav-empty">还没有长篇故事</p>'}
    </section>
    <section><h2>篇章</h2>
      ${(model.arcs || []).map((record) => item(record, record.title, `篇章 ${record.sequence}`)).join("")}
      ${(model.episodes || []).map((record) => item(record, `第 ${record.sequence} 集  ${record.title}`)).join("") || '<p class="creator-nav-empty">新建第一集开始创作</p>'}
    </section>
    <section><h2>参考基准</h2>
      ${(model.reference_sets || []).map((record) => item(record, record.title, record.approval_state === "approved" ? "已确认" : "待确认")).join("") || '<p class="creator-nav-empty">还没有参考基准</p>'}
      ${(model.reference_assets || []).map((record) => item(record, record.label, record.approval_state === "approved" ? "已确认" : "待确认")).join("")}
    </section>
  </aside>`;
}

function renderStoryboard() {
  const episode = currentEpisode(model, ui);
  if (!episode) return renderEmptyStage();
  const scenes = scenesForEpisode(model, episode.ref);
  return `<section class="creator-stage" aria-label="故事板">
    <header class="creator-stage-heading">
      <div><span>第 ${episode.sequence} 集</span><h1>${escapeHtml(episode.title)}</h1><p>${escapeHtml(episode.summary || "为这一集写下关键变化。")}</p></div>
      <div class="creator-stage-actions"><button type="button" data-move="episode:up" data-ref="${escapeHtml(stableKey(episode.ref))}">本集上移</button><button type="button" data-move="episode:down" data-ref="${escapeHtml(stableKey(episode.ref))}">本集下移</button><button type="button" data-create="scene">添加场景</button></div>
    </header>
    <div class="creator-scenes">
      ${scenes.map((scene) => renderScene(scene)).join("") || '<div class="creator-empty-inline"><h2>从第一个场景开始</h2><p>场景负责组织空间、动作和镜头顺序。</p><button type="button" data-create="scene">添加场景</button></div>'}
    </div>
  </section>`;
}

function renderScene(scene) {
  const shots = shotsForScene(model, scene.ref);
  return `<article class="creator-scene">
    <header><div class="chapter-number">${scene.sequence}</div><button type="button" class="scene-title" data-entity="${escapeHtml(stableKey(scene.ref))}"><span>场景 ${scene.sequence}</span><strong>${escapeHtml(scene.title)}</strong></button><div><button type="button" data-move="scene:up" data-ref="${escapeHtml(stableKey(scene.ref))}" aria-label="场景上移">↑</button><button type="button" data-move="scene:down" data-ref="${escapeHtml(stableKey(scene.ref))}" aria-label="场景下移">↓</button><button type="button" data-create="shot" data-parent="${escapeHtml(stableKey(scene.ref))}">添加镜头</button></div></header>
    <div class="creator-shot-list">
      ${shots.map((shot) => renderShot(shot)).join("") || '<p class="creator-nav-empty">这个场景还没有镜头。</p>'}
    </div>
  </article>`;
}

function renderShot(shot) {
  const active = sameStableRef(shot.ref, ui.selectedShot);
  return `<div class="creator-shot ${active ? "selected" : ""}" data-shot="${escapeHtml(stableKey(shot.ref))}" role="button" tabindex="0" aria-pressed="${active}">
    <span class="drag-handle" aria-hidden="true">⠿</span>
    <span class="shot-index">${shot.sequence}</span>
    <span class="shot-copy"><strong>${escapeHtml(shot.title)}</strong><small>${escapeHtml(shot.creative_intent || shot.summary || "补充镜头创作意图")}</small></span>
    <span class="shot-duration">${Number(shot.duration_seconds).toFixed(1).replace(".0", "")} 秒</span>
    <span class="shot-reorder"><button type="button" data-move="shot:up" data-ref="${escapeHtml(stableKey(shot.ref))}" aria-label="镜头上移">↑</button><button type="button" data-move="shot:down" data-ref="${escapeHtml(stableKey(shot.ref))}" aria-label="镜头下移">↓</button></span>
  </div>`;
}

function renderCanvas() {
  const episode = currentEpisode(model, ui);
  const scenes = episode ? scenesForEpisode(model, episode.ref) : [];
  return `<section class="creator-stage creator-canvas" aria-label="画布投影">
    <header class="creator-stage-heading"><div><span>同源画布</span><h1>${escapeHtml(episode?.title || model.project.title)}</h1><p>这里展示故事板中的同一批场景和镜头，不创建第二套内容。</p></div></header>
    <div class="canvas-flow">
      ${scenes.map((scene) => `<section class="canvas-lane"><h2>${escapeHtml(scene.title)}</h2><div>${shotsForScene(model, scene.ref).map((shot) => `<button type="button" class="canvas-node ${sameStableRef(shot.ref, ui.selectedShot) ? "selected" : ""}" data-shot="${escapeHtml(stableKey(shot.ref))}"><span>${shot.sequence}</span><strong>${escapeHtml(shot.title)}</strong><small>${escapeHtml(shot.creative_intent || "待补充创作意图")}</small></button>`).join("")}</div></section>`).join("") || '<div class="creator-empty-inline"><h2>画布会从故事板自动生成</h2><p>先创建单集、场景和镜头。</p></div>'}
    </div>
  </section>`;
}

function renderEmptyStage() {
  return `<section class="creator-stage"><div class="creator-empty-stage"><span class="empty-rule"></span><h1>从故事与第一集开始</h1><p>建立世界设定、篇章和单集，然后把场景与镜头逐步写出来。所有内容都会保存为可恢复版本。</p><div><button type="button" data-create="series">新建长篇故事</button><button type="button" data-create="episode">新建第一集</button></div></div></section>`;
}

function renderInspector() {
  const entity = selectedEntity();
  if (!entity) return `<aside class="creator-inspector"><h2>创作资料</h2><p>选择一项内容开始编辑。</p></aside>`;
  const isShot = entity.ref.entity_type === "shot";
  const isAsset = entity.ref.entity_type === "reference_asset";
  const isSet = entity.ref.entity_type === "reference_set";
  const reorder = ["arc", "episode", "scene", "shot"].includes(entity.ref.entity_type) ? `<div class="inspector-reorder"><button type="button" data-move="${entity.ref.entity_type}:up" data-ref="${escapeHtml(stableKey(entity.ref))}">上移</button><button type="button" data-move="${entity.ref.entity_type}:down" data-ref="${escapeHtml(stableKey(entity.ref))}">下移</button></div>` : "";
  return `<aside class="creator-inspector ${ui.mobileInspectorOpen ? "mobile-open" : ""}" aria-label="${escapeHtml(entityLabel(entity.ref.entity_type))}编辑">
    <header><div><span>${escapeHtml(entityLabel(entity.ref.entity_type))}</span><h2>${isShot ? "镜头创作意图" : escapeHtml(entity.title || entity.label || model.project.title)}</h2></div>${reorder}<button type="button" data-close-inspector aria-label="关闭编辑面板">×</button></header>
    <form class="creator-edit-form" data-edit-type="${escapeHtml(entity.ref.entity_type)}">
      ${isAsset ? renderAssetFields(entity) : isSet ? renderReferenceSetFields(entity) : renderCreativeFields(entity)}
      ${isShot ? `<button class="impact-button" type="button" data-submit-edit>预览影响</button><button class="version-link" type="button" data-toggle-versions>版本与恢复</button>${renderVersionHistory(entity)}` : '<button class="impact-button" type="button" data-submit-edit>保存更改</button>'}
      <p class="impact-note">${isShot ? "更改会先预览影响，再由你确认保存。" : "保存后会产生一个可追溯的新版本。"}</p>
    </form>
  </aside>`;
}

function renderCreativeFields(entity) {
  const sets = approvedReferenceSets(model);
  const referenceField = ["episode", "scene", "shot"].includes(entity.ref.entity_type) ? `<label>参考基准<select name="reference_set_ref"><option value="">不绑定</option>${sets.map((item) => `<option value="${escapeHtml(refKey(item.ref))}" ${sameStableRef(entity.reference_set_ref, item.ref) && entity.reference_set_ref?.version_id === item.ref.version_id ? "selected" : ""}>${escapeHtml(item.title)}</option>`).join("")}</select></label>` : "";
  return `
    <label>标题<input name="title" required maxlength="200" value="${escapeHtml(entity.title || model.project.title)}"></label>
    <label>摘要<textarea name="summary" rows="3" maxlength="4000">${escapeHtml(entity.summary || "")}</textarea></label>
    ${"creative_intent" in entity || entity.ref.entity_type === "project" ? `<label>创作意图<textarea name="creative_intent" rows="4" maxlength="4000">${escapeHtml(entity.creative_intent || "")}</textarea></label>` : ""}
    ${entity.ref.entity_type === "project" ? `<label>IP 资料<textarea name="ip_profile" rows="3" maxlength="4000">${escapeHtml(entity.ip_profile || "")}</textarea></label>` : ""}
    ${entity.ref.entity_type === "story_bible" ? `<label>世界规则<textarea name="world_rules" rows="4">${escapeHtml((entity.world_rules || []).join("\n"))}</textarea></label>` : ""}
    ${entity.ref.entity_type === "shot" ? `<label>时长（秒）<input name="duration_seconds" type="number" min="0.1" max="3600" step="0.1" value="${escapeHtml(entity.duration_seconds)}"></label>` : ""}
    ${referenceField}`;
}

function renderAssetFields(entity) {
  return `<label>名称<input name="label" required maxlength="200" value="${escapeHtml(entity.label)}"></label><label>身份特征<textarea name="identity" rows="4" maxlength="1000">${escapeHtml(entity.identity)}</textarea></label><label>置信度<input name="confidence" type="number" min="0" max="1" step="0.01" value="${entity.confidence}"></label><label class="confirm-line"><input name="human_confirmed" type="checkbox" ${entity.human_confirmed ? "checked" : ""}> 我已核对这个版本</label><label>确认状态<select name="approval_state"><option value="pending_human" ${entity.approval_state === "pending_human" ? "selected" : ""}>等待人工确认</option><option value="approved" ${entity.approval_state === "approved" ? "selected" : ""}>已确认</option><option value="rejected" ${entity.approval_state === "rejected" ? "selected" : ""}>不采用</option></select></label>`;
}

function renderReferenceSetFields(entity) {
  return `<label>标题<input name="title" required maxlength="200" value="${escapeHtml(entity.title)}"></label><label>摘要<textarea name="summary" rows="4">${escapeHtml(entity.summary || "")}</textarea></label><fieldset><legend>包含的参考资产</legend>${(model.reference_assets || []).map((asset) => `<label class="confirm-line"><input type="checkbox" name="asset_refs" value="${escapeHtml(refKey(asset.ref))}" ${entity.asset_refs.some((ref) => refKey(ref) === refKey(asset.ref)) ? "checked" : ""}> ${escapeHtml(asset.label)} · ${asset.approval_state === "approved" ? "已确认" : "待确认"}</label>`).join("") || "<p>请先创建参考资产。</p>"}</fieldset><label class="confirm-line"><input name="human_confirmed" type="checkbox" ${entity.human_confirmed ? "checked" : ""}> 我已核对这个基准</label><label>确认状态<select name="approval_state"><option value="pending_human" ${entity.approval_state === "pending_human" ? "selected" : ""}>等待人工确认</option><option value="approved" ${entity.approval_state === "approved" ? "selected" : ""}>已确认</option><option value="rejected" ${entity.approval_state === "rejected" ? "selected" : ""}>不采用</option></select></label>`;
}

function renderVersionHistory(shot) {
  if (!ui.technicalOpen) return "";
  const diff = renderVersionDiff(shot);
  return `<section class="version-history"><h3>历史版本</h3>${(shot.versions || []).slice().reverse().map((version) => `<div><span>版本 ${version.revision}</span><strong>${escapeHtml(version.title)}</strong>${version.ref.version_id !== shot.ref.version_id ? `<span><button type="button" data-diff="${escapeHtml(refKey(version.ref))}">比较</button><button type="button" data-restore="${escapeHtml(refKey(version.ref))}">恢复为新版本</button></span>` : "<small>当前</small>"}</div>`).join("")}${diff}</section>`;
}

function renderVersionDiff(shot) {
  if (!versionDiff || !sameStableRef(versionDiff.right_ref, shot.ref)) return "";
  const changes = versionDiff.changes || {};
  return `<div class="creator-diff"><h4>版本差异</h4><div class="diff-exact-refs"><span>${escapeHtml(versionLabel(shot, versionDiff.left_ref))}</span><span>${escapeHtml(versionLabel(shot, versionDiff.right_ref))}</span></div>${Object.entries(changes).map(([field, values]) => `<p><strong>${escapeHtml(diffFieldLabel(field))}</strong><del>${escapeHtml(diffValue(values.before))}</del><ins>${escapeHtml(diffValue(values.after))}</ins></p>`).join("") || "<p>两个版本的创作字段一致。</p>"}</div>`;
}

function versionLabel(shot, ref) {
  const version = (shot.versions || []).find((item) => refKey(item.ref) === refKey(ref));
  return version ? `版本 ${version.revision} · ${version.ref.version_id}` : refKey(ref);
}

function diffFieldLabel(field) { return ({ title: "标题", summary: "摘要", creative_intent: "创作意图", duration_seconds: "时长", reference_set_ref: "参考基准" })[field] || field; }
function diffValue(value) { if (value == null || value === "") return "未填写"; if (typeof value === "object") return value.entity_type === "reference_set" ? "指定参考基准版本" : "已设置"; return String(value); }

function renderCreateDialog(type) {
  const blocker = createBlocker(type);
  const options = [
    ["story_bible", "世界设定"], ["series", "长篇故事"], ["arc", "篇章"], ["episode", "单集"],
    ["scene", "场景"], ["shot", "镜头"], ["reference_asset", "参考资产"], ["reference_set", "参考基准"],
  ];
  return `<div class="creator-modal-backdrop" role="presentation"><section class="creator-modal" role="dialog" aria-modal="true" aria-labelledby="create-title"><header><div><span>新建内容</span><h2 id="create-title">${escapeHtml(entityLabel(type))}</h2></div><button type="button" data-close-modal aria-label="关闭">×</button></header><form class="creator-create-form"><label>内容类型<select name="entity_type">${options.map(([value, label]) => `<option value="${value}" ${type === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>${blocker ? `<p class="form-help">${escapeHtml(blocker)}</p>` : ""}${renderCreateFields(type)}<div class="modal-actions"><button type="button" data-close-modal>取消</button><button class="impact-button" type="button" data-submit-create ${blocker ? "disabled" : ""}>创建</button></div></form></section></div>`;
}

function renderCreateFields(type) {
  const episode = currentEpisode(model, ui);
  const current = currentShot(model, ui);
  const directedParent = exactEntity(findRefByStable(createParentRef));
  const currentScene = directedParent?.ref.entity_type === "scene" ? directedParent : current ? model.scenes.find((scene) => sameStableRef(scene.ref, current.scene_ref)) : scenesForEpisode(model, episode?.ref)[0];
  const base = `<label>标题或名称<input name="title" required maxlength="200"></label><label>摘要<textarea name="summary" rows="3" maxlength="4000"></textarea></label>`;
  if (type === "reference_asset") return `<label>资产类型<select name="asset_kind"><option value="human">人物</option><option value="animal">动物</option><option value="scene">场景</option><option value="location">地点</option><option value="prop">道具</option></select></label><label>名称<input name="title" required></label><label>身份特征<textarea name="identity" rows="4" required></textarea></label><label>置信度<input name="confidence" type="number" min="0" max="1" step="0.01" value="0.5"></label><p class="form-help">创建后先进入人工确认，不会静默批准。</p>`;
  if (type === "reference_set") return `${base}<fieldset><legend>包含的参考资产</legend>${(model.reference_assets || []).map((asset) => `<label class="confirm-line"><input type="checkbox" name="asset_refs" value="${escapeHtml(refKey(asset.ref))}"> ${escapeHtml(asset.label)} · ${asset.approval_state === "approved" ? "已确认" : "待确认"}</label>`).join("") || "<p>请先创建参考资产。</p>"}</fieldset><p class="form-help">含未确认资产的基准会保持“等待人工确认”。</p>`;
  if (type === "story_bible") return `${base}<label>世界规则（每行一条）<textarea name="world_rules" rows="4"></textarea></label>`;
  if (type === "arc") return `${base}<label>所属长篇<select name="series_ref">${optionsFor(model.series, "title")}</select></label><label>世界设定<select name="story_bible_ref"><option value="">不绑定</option>${optionsFor(model.story_bibles, "title")}</select></label><label>顺序<input name="sequence" type="number" min="1" value="${(model.arcs?.length || 0) + 1}"></label><label>创作意图<textarea name="creative_intent" rows="3"></textarea></label>`;
  if (type === "episode") return `${base}<label>所属长篇<select name="series_ref">${optionsFor(model.series, "title")}</select></label><label>篇章<select name="arc_ref"><option value="">不绑定</option>${optionsFor(model.arcs, "title")}</select></label><label>集序<input name="sequence" type="number" min="1" value="${(model.episodes?.length || 0) + 1}"></label><label>创作意图<textarea name="creative_intent" rows="3"></textarea></label>`;
  if (type === "scene") return `${base}<label>所属单集<select name="episode_ref">${optionsFor(model.episodes, "title", episode?.ref)}</select></label><label>场序<input name="sequence" type="number" min="1" value="${scenesForEpisode(model, episode?.ref).length + 1}"></label><label>创作意图<textarea name="creative_intent" rows="3"></textarea></label>`;
  if (type === "shot") return `${base}<label>所属场景<select name="scene_ref">${optionsFor(model.scenes, "title", currentScene?.ref)}</select></label><label>镜序<input name="sequence" type="number" min="1" value="${shotsForScene(model, currentScene?.ref).length + 1}"></label><label>创作意图<textarea name="creative_intent" rows="3"></textarea></label><label>时长（秒）<input name="duration_seconds" type="number" min="0.1" max="3600" step="0.1" value="4"></label>`;
  return `${base}${type === "series" ? '<label>创作意图<textarea name="creative_intent" rows="3"></textarea></label>' : ""}`;
}

function optionsFor(items = [], field, selectedRef = null) {
  return items.map((item) => `<option value="${escapeHtml(refKey(item.ref))}" ${sameStableRef(item.ref, selectedRef) ? "selected" : ""}>${escapeHtml(item[field])}</option>`).join("");
}

function renderImpactDialog(preview) {
  return `<div class="creator-modal-backdrop"><section class="creator-modal impact-modal" role="dialog" aria-modal="true" aria-labelledby="impact-title"><header><div><span>保存前确认</span><h2 id="impact-title">${preview.kind === "restore" ? "确认恢复范围" : "确认这次镜头更改"}</h2></div><button type="button" data-close-impact aria-label="关闭">×</button></header><div class="impact-summary"><div><strong>${preview.direct_affected_refs.length}</strong><span>直接影响</span></div><div><strong>${preview.transitive_affected_refs.length}</strong><span>关联影响</span></div><div><strong>${preview.protected_refs.length}</strong><span>保持不变</span></div></div>${impactRefList("直接改变", preview.direct_affected_refs)}${impactRefList("需要重新检查", preview.transitive_affected_refs)}${impactRefList("明确保持不变", preview.protected_refs)}<p>${preview.stale_candidate_refs.length || preview.stale_review_refs.length ? "旧候选或评审仍保留在历史版本中，不会自动继承为已确认。" : "这次更改只会新增一个镜头版本，未选择内容保持不变。"}</p><p>预计后续需要检查 ${preview.estimated_follow_up} 项。</p><div class="modal-actions"><button type="button" data-close-impact>返回修改</button><button class="impact-button" type="button" data-confirm-impact>确认并保存</button></div></section></div>`;
}

function impactRefList(title, refs) { return `<details class="impact-refs" ${title === "直接改变" ? "open" : ""}><summary>${title}</summary><ul>${refs.map((ref) => `<li>${escapeHtml(refDisplay(ref))}</li>`).join("") || "<li>无</li>"}</ul></details>`; }
function refDisplay(ref) { const entity = exactEntity(ref); return entity ? `${entityLabel(ref.entity_type)} · ${entity.title || entity.label || "未命名"}` : entityLabel(ref.entity_type); }

function findRef(serialized) {
  return [...entityCollections, "shots"]
    .flatMap((collection) => model[collection] || [])
    .map((item) => item.ref)
    .find((ref) => refKey(ref) === serialized) || null;
}

function bindEvents() {
  root.querySelectorAll(".creator-modes button[data-mode]").forEach((button) => button.addEventListener("click", () => {
    ui.mode = button.dataset.mode;
    render();
    void persistUi();
  }));
  root.querySelectorAll("[data-open-create]").forEach((button) => button.addEventListener("click", () => { createParentRef = ""; createDialog = suggestedCreateType(); render(); }));
  root.querySelectorAll("[data-create]").forEach((button) => button.addEventListener("click", () => { createParentRef = button.dataset.parent || ""; createDialog = button.dataset.create; render(); }));
  root.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", () => { createDialog = ""; render(); }));
  root.querySelector(".creator-create-form select[name='entity_type']")?.addEventListener("change", (event) => { createDialog = event.target.value; render(); });
  root.querySelector(".creator-create-form")?.addEventListener("submit", (event) => { event.preventDefault(); void submitCreate(event.currentTarget).catch((error) => { statusMessage = `无法创建：${error?.message || "表单内容无效。"}`; render(); }); });
  root.querySelector("[data-submit-create]")?.addEventListener("click", () => {
    const form = root.querySelector(".creator-create-form");
    if (form) void submitCreate(form).catch((error) => { statusMessage = `无法创建：${error?.message || "表单内容无效。"}`; render(); });
  });
  root.querySelectorAll("[data-entity]").forEach((button) => button.addEventListener("click", () => {
    ui.selectedSection = button.dataset.entity;
    const entity = exactEntity(findRefByStable(button.dataset.entity));
    if (entity?.ref.entity_type === "episode") ui.selectedEpisode = entity.ref;
    ui.mobileInspectorOpen = true;
    mobileNavOpen = false;
    render();
    void persistUi();
  }));
  root.querySelectorAll("[data-shot]").forEach((button) => button.addEventListener("click", () => {
    const shot = model.shots.find((item) => stableKey(item.ref) === button.dataset.shot);
    if (!shot) return;
    if (!sameStableRef(shot.ref, ui.selectedShot)) versionDiff = null;
    ui.selectedShot = shot.ref;
    ui.selectedSection = `shot:${shot.ref.entity_id}`;
    ui.mobileInspectorOpen = true;
    render();
    void persistUi();
  }));
  root.querySelectorAll("[data-shot]").forEach((item) => item.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); item.click(); } }));
  root.querySelector("[data-mobile-nav]")?.addEventListener("click", () => { mobileNavOpen = !mobileNavOpen; render(); });
  root.querySelector("[data-close-inspector]")?.addEventListener("click", () => { ui.mobileInspectorOpen = false; render(); void persistUi(); });
  root.querySelector(".creator-edit-form")?.addEventListener("submit", (event) => { event.preventDefault(); void submitEdit(event.currentTarget).catch((error) => { statusMessage = `无法保存：${error?.message || "表单内容无效。"}`; render(); }); });
  root.querySelector("[data-submit-edit]")?.addEventListener("click", () => {
    const form = root.querySelector(".creator-edit-form");
    if (form) void submitEdit(form).catch((error) => { statusMessage = `无法保存：${error?.message || "表单内容无效。"}`; render(); });
  });
  root.querySelector("[data-toggle-versions]")?.addEventListener("click", () => { ui.technicalOpen = !ui.technicalOpen; render(); void persistUi(); });
  root.querySelector("[data-technical]")?.addEventListener("click", () => { ui.technicalOpen = !ui.technicalOpen; render(); void persistUi(); });
  root.querySelectorAll("[data-restore]").forEach((button) => button.addEventListener("click", () => void previewRestore(button.dataset.restore)));
  root.querySelectorAll("[data-diff]").forEach((button) => button.addEventListener("click", () => void showVersionDiff(button.dataset.diff)));
  root.querySelectorAll("[data-move]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); void moveEntity(button.dataset.ref, button.dataset.move.split(":")[1]); }));
  root.querySelector("[data-close-impact]")?.addEventListener("click", () => { impactDialog = null; render(); });
  root.querySelector("[data-confirm-impact]")?.addEventListener("click", () => void confirmImpact());
  root.onkeydown = (event) => { if (event.key === "Escape" && (createDialog || impactDialog || mobileNavOpen)) { createDialog = ""; impactDialog = null; mobileNavOpen = false; render(); } };
}

function findRefByStable(key) {
  if (stableKey(model.project.ref) === key) return model.project.ref;
  return [...entityCollections, "shots"]
    .flatMap((collection) => model[collection] || [])
    .map((item) => item.ref)
    .find((ref) => stableKey(ref) === key) || null;
}

function suggestedCreateType() {
  if (!model.series.length) return "series";
  if (!model.episodes.length) return "episode";
  if (!scenesForEpisode(model, currentEpisode(model, ui)?.ref).length) return "scene";
  return "shot";
}

function createBlocker(type) {
  if (type === "arc" && !model.series.length) return "请先创建长篇故事，再创建篇章。";
  if (type === "episode" && !model.series.length) return "请先创建长篇故事，再创建单集。";
  if (type === "scene" && !model.episodes.length) return "请先创建单集，再创建场景。";
  if (type === "shot" && !scenesForEpisode(model, currentEpisode(model, ui)?.ref).length) return "请先为当前单集创建场景，再创建镜头。";
  return "";
}

async function submitCreate(form) {
  const data = new FormData(form);
  const type = String(data.get("entity_type") || createDialog);
  const blocker = createBlocker(type);
  if (blocker) { statusMessage = blocker; render(); return; }
  const title = String(data.get("title") || "").trim();
  const entityId = stableIdentity(type);
  const projectRef = model.project.ref;
  let entity;
  if (type === "series") entity = { project_ref: projectRef, title, summary: text(data, "summary"), creative_intent: text(data, "creative_intent") };
  else if (type === "story_bible") entity = { project_ref: projectRef, title, summary: text(data, "summary"), world_rules: lines(data, "world_rules") };
  else if (type === "arc") entity = { series_ref: findRef(text(data, "series_ref")), story_bible_ref: findRef(text(data, "story_bible_ref")), sequence: number(data, "sequence"), title, summary: text(data, "summary"), creative_intent: text(data, "creative_intent") };
  else if (type === "episode") entity = { series_ref: findRef(text(data, "series_ref")), arc_ref: findRef(text(data, "arc_ref")), sequence: number(data, "sequence"), title, summary: text(data, "summary"), creative_intent: text(data, "creative_intent") };
  else if (type === "scene") entity = { episode_ref: findRef(text(data, "episode_ref")), sequence: number(data, "sequence"), title, summary: text(data, "summary"), creative_intent: text(data, "creative_intent") };
  else if (type === "shot") entity = { scene_ref: findRef(text(data, "scene_ref")), sequence: number(data, "sequence"), title, summary: text(data, "summary"), creative_intent: text(data, "creative_intent"), duration_seconds: number(data, "duration_seconds") };
  else if (type === "reference_asset") entity = { project_ref: projectRef, asset_kind: text(data, "asset_kind"), label: title, identity: text(data, "identity"), confidence: number(data, "confidence"), approval_state: "pending_human", human_confirmed: false };
  else if (type === "reference_set") entity = { project_ref: projectRef, title, summary: text(data, "summary"), scope_kind: "project", scope_refs: [projectRef], asset_refs: data.getAll("asset_refs").map((value) => findRef(String(value))).filter(Boolean), approval_state: "pending_human", human_confirmed: false };
  if (!entity) return;
  try {
    if (await runCommand(createEntityCommand(model, type, entityId, entity))) {
      createDialog = "";
      createParentRef = "";
      render();
    }
  } catch (error) { statusMessage = `无法创建：${error?.message || "请检查内容后再试。"}`; render(); }
}

async function submitEdit(form) {
  const entity = selectedEntity();
  if (!entity) return;
  const data = new FormData(form);
  const type = entity.ref.entity_type;
  let changes;
  if (type === "reference_asset") changes = { label: text(data, "label"), identity: text(data, "identity"), confidence: number(data, "confidence"), approval_state: text(data, "approval_state"), human_confirmed: data.has("human_confirmed") };
  else if (type === "reference_set") changes = { title: text(data, "title"), summary: text(data, "summary"), asset_refs: data.getAll("asset_refs").map((value) => findRef(String(value))).filter(Boolean), approval_state: text(data, "approval_state"), human_confirmed: data.has("human_confirmed") };
  else {
    changes = { title: text(data, "title"), summary: text(data, "summary") };
    if (form.elements.creative_intent) changes.creative_intent = text(data, "creative_intent");
    if (form.elements.ip_profile) changes.ip_profile = text(data, "ip_profile");
    if (form.elements.world_rules) changes.world_rules = lines(data, "world_rules");
    if (form.elements.duration_seconds) changes.duration_seconds = number(data, "duration_seconds");
    if (form.elements.reference_set_ref) changes.reference_set_ref = findRef(text(data, "reference_set_ref"));
  }
  if (type === "shot") {
    try {
      const preview = await client.previewShotImpact({ expected_aggregate_version: model.aggregate_version, shot_ref: entity.ref, changes });
      impactDialog = { ...preview, kind: "revise", shot: entity, changes };
      render();
    } catch (error) { statusMessage = `无法预览影响：${error?.message || "请重新读取后再试。"}`; render(); }
    return;
  }
  await runCommand(reviseEntityCommand(model, entity.ref, changes));
}

async function previewRestore(serializedRef) {
  const shot = currentShot(model, ui);
  const historicalRef = findShotVersionRef(serializedRef);
  if (!shot || !historicalRef) return;
  try {
    const preview = await client.previewShotRestore({ expected_aggregate_version: model.aggregate_version, historical_ref: historicalRef, current_ref: shot.ref });
    impactDialog = { ...preview, kind: "restore", shot, historicalRef };
    render();
  } catch (error) { statusMessage = `无法预览恢复：${error?.message || "请重新读取后再试。"}`; render(); }
}

async function showVersionDiff(serializedRef) {
  const shot = currentShot(model, ui);
  const historicalRef = findShotVersionRef(serializedRef);
  if (!shot || !historicalRef) return;
  try { versionDiff = { left_ref: historicalRef, right_ref: shot.ref, changes: (await client.diffShotVersions({ left_ref: historicalRef, right_ref: shot.ref })).changes }; render(); }
  catch (error) { statusMessage = `无法比较版本：${error?.message || "请稍后再试。"}`; render(); }
}

async function confirmImpact() {
  if (!impactDialog) return;
  const command = impactDialog.kind === "restore"
    ? restoreShotCommand(model, impactDialog.shot, impactDialog.historicalRef, impactDialog)
    : reviseShotCommand(model, impactDialog.shot, impactDialog.changes, impactDialog);
  impactDialog = null;
  await runCommand(command);
}

function findShotVersionRef(serialized) {
  return (currentShot(model, ui)?.versions || []).map((item) => item.ref).find((ref) => refKey(ref) === serialized) || null;
}

async function moveEntity(stable, direction) {
  const ref = findRefByStable(stable);
  const entity = exactEntity(ref);
  if (!entity || !["arc", "episode", "scene", "shot"].includes(entity.ref.entity_type)) return;
  const siblings = entity.ref.entity_type === "arc"
    ? model.arcs.filter((item) => sameStableRef(item.series_ref, entity.series_ref)).sort((a, b) => a.sequence - b.sequence)
    : entity.ref.entity_type === "episode"
      ? model.episodes.filter((item) => sameStableRef(item.series_ref, entity.series_ref)).sort((a, b) => a.sequence - b.sequence)
      : entity.ref.entity_type === "scene"
        ? scenesForEpisode(model, entity.episode_ref)
        : shotsForScene(model, entity.scene_ref);
  const index = siblings.findIndex((item) => sameStableRef(item.ref, entity.ref));
  const target = direction === "up" ? index - 1 : index + 1;
  if (index < 0 || target < 0 || target >= siblings.length) return;
  const ordered = siblings.slice();
  [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
  await runCommand(reorderCommand(model, ordered.map((item) => item.ref)));
}

function text(data, key) { return String(data.get(key) || "").trim(); }
function number(data, key) { return Number(data.get(key) || 0); }
function lines(data, key) { return text(data, key).split(/\r?\n/).map((item) => item.trim()).filter(Boolean); }

async function runCommand(command, replayKey = "") {
  if (busy) { statusMessage = "上一项更改仍在保存，请稍候。"; render(); return false; }
  busy = true;
  const idempotencyKey = replayKey || commandIdentity(command.action);
  const envelope = { schema_version: "afs_creator_pending_command.v0.1", idempotency_key: idempotencyKey, command, status: "pending" };
  ui.pendingCommand = envelope;
  ui.pendingFailure = "";
  statusMessage = "正在保存更改…";
  render();
  try {
    await persistUi({ immediate: true });
    const receipt = await client.executeCommand(command, idempotencyKey);
    if (!receipt || (!receipt.replayed && receipt.aggregate_version !== command.expected_aggregate_version + 1)) {
      throw new Error("服务没有返回可核对的保存凭据");
    }
  } catch (error) {
    ui.pendingCommand = { ...envelope, status: "failed" };
    ui.pendingFailure = error?.message || "命令未完成";
    statusMessage = `保存未完成：${ui.pendingFailure}`;
    await persistUi({ immediate: true }).catch(() => {});
    busy = false;
    render();
    return false;
  }
  try {
    model = await client.loadWorkspace();
    ui.pendingCommand = null;
    ui.pendingFailure = "";
    ui = createAuthoringUi(model, uiPreference(ui));
    versionDiff = null;
    statusMessage = "更改已保存，并可在版本记录中恢复。";
    await persistUi({ immediate: true });
  } catch (error) {
    ui.pendingCommand = envelope;
    ui.pendingFailure = "";
    statusMessage = "更改已经保存；刷新后会核对保存记录。";
  }
  busy = false;
  render();
  return true;
}

async function persistUi({ immediate = false } = {}) {
  const state = { ...studioState, creator_authoring: uiPreference(ui) };
  const write = async () => {
    const saved = await client.saveStudioState(state, studioStateVersion);
    studioState = saved.state || state;
    studioStateVersion = saved.state_version || studioStateVersion;
  };
  stateWrite = stateWrite.then(write, write);
  return immediate ? stateWrite : stateWrite.catch(() => {});
}

async function reconcilePending() {
  const pending = ui.pendingCommand;
  if (!pending) return;
  if (!validPendingEnvelope(pending)) {
    ui.pendingFailure = "检测到不完整的待保存命令。为保护项目，工作台没有覆盖它。";
    statusMessage = ui.pendingFailure;
    render();
    return;
  }
  if (pending.status === "failed") {
    statusMessage = `上次保存明确失败：${ui.pendingFailure || "请重新读取后再编辑。"}`;
    render();
    return;
  }
  await runCommand(pending.command, pending.idempotency_key);
}

export async function startCreatorAuthoring(target, projectId) {
  root = target;
  client = createCreatorAuthoringClient(projectId);
  renderState("正在恢复创作现场", "正在读取已保存的故事结构与待确认更改…");
  try {
    const [workspace, saved] = await Promise.all([client.loadWorkspace(), client.loadStudioState()]);
    model = workspace;
    studioState = saved.state || {};
    studioStateVersion = saved.state_version || "";
    ui = createAuthoringUi(model, studioState.creator_authoring || {});
    render();
    await reconcilePending();
  } catch (error) {
    const action = error?.kind === "auth" ? '<a class="impact-button" href="/studio/">返回登录</a>' : '<button class="impact-button" type="button" onclick="location.reload()">重试</button>';
    renderState("无法打开创作工作台", error?.message || "请稍后重试。", action);
  }
}
