import { createEpisodeWorkspaceClient } from "./api-client.mjs";
import {
  buildCandidateSelectCommand,
  buildContinuityApplyCommand,
  buildDeliveryUnlockCommand,
  buildSelectionLockCommand,
  buildSelectionReviewCommand,
  buildSelectionUnlockCommand,
  buildShotReassignCommand,
  buildShotReviewCommand,
  commandIdFor,
} from "./commands.mjs";
import {
  activeShot,
  availableAction,
  buildWorkspaceModel,
  createInitialUiState,
  exactRefKey,
  focusIfAvailable,
  groupShotsByScene,
  inspectShot,
  mergeEpisodeWorkspaceState,
  nextShot,
  retainPendingCommandAfterFailure,
  selectMode,
  selectSceneFilter,
  selectStatusFilter,
  shouldShowCurrentVersusNext,
  updateUiRecovery,
  visibleShots,
} from "./state.mjs";

const app = document.querySelector("#app");
const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");
const episodeId = params.get("episode");
const episodeVersionId = params.get("version");
const client = projectId && episodeId && episodeVersionId
  ? createEpisodeWorkspaceClient(projectId, episodeId, episodeVersionId)
  : null;

let model = null;
let ui = null;
let studioState = {};
let studioStateVersion = "";
let persistTimer = null;
let commandRunning = false;
let statusMessage = "";

const icons = {
  chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>',
  lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
  shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4.5 6v5.5c0 4.6 3.1 7.8 7.5 9.5 4.4-1.7 7.5-4.9 7.5-9.5V6L12 3Z"/><path d="m9 12 2 2 4-4"/></svg>',
  issue: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 3 20h18L12 4Z"/><path d="M12 9v5M12 17h.01"/></svg>',
  spark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3Z"/></svg>',
};

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function lifecycleLabel(shot) {
  if (shot.delivery_invalid) return ["交付失效", "danger"];
  if (shot.production_state === "rework") return ["返工中", "danger"];
  if (shot.lifecycle_state === "locked") return ["已锁定", "success"];
  if (shot.selection_lifecycle_state === "locked") return ["选版已锁定", "success"];
  if (shot.selection_state === "selected") return ["已选候选", "success"];
  if (shot.review_state === "needs_review") return ["人类待审", "warning"];
  return [shot.status_label || "待完善", "neutral"];
}

function renderState(kind, title, message, action = "") {
  app.innerHTML = `<main class="empty-state ${escapeHtml(kind)}"><div class="brand-mark" aria-hidden="true">雨</div><h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p>${action}</main>`;
}

function renderError(error) {
  const action = error?.kind === "auth"
    ? '<a class="primary-button" href="/studio/">返回 Studio 登录</a>'
    : '<button class="primary-button" type="button" data-action="retry">重试</button>';
  const message = ({
    auth: "登录状态已失效或无权访问此项目。",
    not_found: "找不到这个单集，或你已无权访问。",
    stale: "项目事实已更新，请刷新后继续。",
    invalid: "请求与当前精确版本不一致。",
  })[error?.kind] || "工作区暂时无法完成请求，请稍后重试。";
  renderState("error", error?.kind === "auth" ? "需要重新登录" : "未能打开制作工作区", message, action);
  app.querySelector('[data-action="retry"]')?.addEventListener("click", hydrate);
}

function statusDot(tone) { return `<span class="status-dot ${tone}" aria-hidden="true"></span>`; }

function renderTopbar(current) {
  const privateProject = model.project.policy.visibility === "private";
  const noTraining = model.project.policy.training_use === "denied_by_default";
  const next = model.nextAction;
  return `<header class="topbar">
    <div class="identity"><div class="brand-mark compact" aria-hidden="true">雨</div><div class="project-copy"><strong>${escapeHtml(model.project.title)}</strong><span>${escapeHtml(model.episode.title)}</span></div></div>
    <nav class="mode-tabs" aria-label="单集工作模式">${[["storyboard", "故事板"], ["review", "审核"], ["delivery", "交付"]].map(([mode, label]) => `<button type="button" data-mode="${mode}" data-focus="mode-${mode}" aria-current="${ui.mode === mode ? "page" : "false"}">${label}</button>`).join("")}</nav>
    <div class="top-status">
      <span class="save-state">${icons.shield}<span>${ui.pendingCommand ? "命令待核对" : `事实 v${model.aggregateVersion} · 已同步`}</span></span>
      <span class="privacy-state">${icons.lock}<span>${privateProject ? "私有" : "项目可见"} · ${noTraining ? "不用于训练" : "按项目策略使用"}</span></span>
      <button class="next-action" type="button" data-action="go-next" data-focus="next-action" ${next ? "" : "disabled"}><span>建议下一步</span><strong>${escapeHtml(next?.label || "当前没有服务建议")}</strong>${icons.chevron}</button>
    </div>
    <div class="mobile-context"><span>当前查看：${current ? `镜头 ${current.sequence}` : "暂无镜头"}</span><button type="button" data-action="open-mobile-nav">镜头与问题</button></div>
  </header>`;
}

function renderLeftRail() {
  const filters = [["all", "全部镜头"], ["blocking", "阻断项"], ["needs_review", "待审核"], ["rework", "返工中"]];
  return `<aside class="left-rail" aria-label="场景与问题导航"><div class="rail-heading"><h2>场景</h2><span>${model.scenes.length}</span></div>
    <button class="scene-row" type="button" data-scene="all" aria-pressed="${ui.sceneFilterKey === "all"}"><span>全部场景</span><strong>${model.shots.length}</strong></button>
    ${model.scenes.map((scene) => `<button class="scene-row" type="button" data-scene="${escapeHtml(exactRefKey(scene.ref))}" aria-pressed="${ui.sceneFilterKey === exactRefKey(scene.ref)}"><span><small>场景 ${scene.sequence}</small>${escapeHtml(scene.title)}</span></button>`).join("")}
    <div class="rail-heading issue-heading"><h2>定位</h2></div><div class="filter-list">${filters.map(([filter, label]) => `<button type="button" data-filter="${filter}" aria-pressed="${ui.statusFilter === filter}"><span>${filter === "blocking" ? icons.issue : ""}${label}</span></button>`).join("")}</div>
    <button class="reset-link" type="button" data-action="reset-recovery" ${ui.pendingCommand ? "disabled" : ""}>清除本次恢复位置</button></aside>`;
}

function renderShotCard(shot) {
  const [label, tone] = lifecycleLabel(shot);
  const selected = exactRefKey(shot.ref) === ui.activeShotKey;
  const isNext = exactRefKey(shot.ref) === ui.nextShotKey;
  return `<button class="shot-card" type="button" data-shot="${escapeHtml(exactRefKey(shot.ref))}" data-focus="shot-${escapeHtml(shot.ref.entity_id)}" aria-pressed="${selected}" ${isNext ? 'data-next="true"' : ""}>
    <span class="frame"><span class="no-media">${shot.candidates.some((item) => item.artifact_present) ? "素材存在 · 未提供安全缩略图" : "暂无候选素材"}</span><strong class="shot-number">${String(shot.sequence).padStart(2, "0")}</strong><span class="duration">${Number(shot.duration_seconds || 0).toFixed(0)}s</span>${shot.blocking ? `<span class="frame-issue">${icons.issue}</span>` : ""}</span>
    <span class="card-meta"><span>${statusDot(tone)}${escapeHtml(label)}</span>${isNext ? "<strong>下一步</strong>" : ""}</span><span class="card-script">服务未提供镜头脚本文本</span></button>`;
}

function renderStoryboard() {
  const groups = groupShotsByScene(model, visibleShots(model, ui));
  if (!groups.length) return '<section class="workspace-empty"><h2>没有符合条件的镜头</h2><p>试试更换场景或问题筛选。</p></section>';
  return `<section class="storyboard" aria-label="故事板镜头工作面">${groups.map(({ scene, shots }) => `<section class="scene-group"><header><div><span>场景 ${scene.sequence}</span><h2>${escapeHtml(scene.title)}</h2></div><p>${shots.length} 个镜头</p></header><div class="shot-grid">${shots.map(renderShotCard).join("")}</div></section>`).join("")}</section>`;
}

function renderReviewMode(shot) {
  if (!shot) return '<section class="workspace-empty"><h2>暂无可审核镜头</h2></section>';
  const review = availableAction(shot, "review_shot");
  return `<section class="focused-mode"><header><span>精确镜头事实</span><h2>镜头 ${shot.sequence} · 审核</h2><p>审核会追加新版本和精确决策，不覆盖已有事实。</p></header><div class="review-sheet"><div><h3>审核状态</h3><p>${escapeHtml(lifecycleLabel(shot)[0])}</p></div><div><h3>审核意见</h3><p>${escapeHtml(shot.review_note || "暂无审核意见。")}</p></div></div><div class="mode-actions"><button class="primary-button" data-command="approve-shot" ${review.enabled ? "" : "disabled"}>批准精确版本</button><button class="secondary-button" data-command="reject-shot" ${review.enabled ? "" : "disabled"}>请求修改</button></div><p class="action-reason">${escapeHtml(review.reason)}</p></section>`;
}

function blockerLabel(value) {
  return ({ missing_assets: "仍有已登记候选缺少素材", delivery_not_frozen: "交付版本尚未冻结", preview_availability_unverified: "预览存在但可用性未经验证", preview_missing: "没有可播放预览" })[value] || "交付条件尚未满足";
}

function renderDeliveryMode() {
  const blockers = model.delivery.blockers || [];
  const canUnlock = Boolean(model.delivery.current_ref);
  return `<section class="focused-mode delivery-mode"><header><span>交付状态</span><h2>${blockers.length ? "交付仍被真实条件阻断" : "交付事实已齐备"}</h2><p>${blockers.length ? blockers.map(blockerLabel).join("；") : "服务还没有确认预览可播放，工作区不会伪造可交付状态或开放冻结。"}</p></header><div class="delivery-sheet"><div><strong>${model.delivery.missing_asset_count ?? 0}</strong><span>已证实缺失</span></div><div><strong>${model.truth.generation_dispatch_count ?? 0}</strong><span>真实任务标识</span></div><div><strong>${model.delivery.playable_preview_available ? "可用" : "未证实"}</strong><span>可播放预览</span></div></div>${canUnlock ? '<button class="primary-button" data-command="unlock-delivery">重新打开交付版本</button>' : '<button class="primary-button" disabled>冻结交付版本</button>'}<p class="action-reason">冻结只在服务确认选版已锁定、缺失为 0、预览可播放后开放。</p></section>`;
}

function renderCenter() {
  const current = activeShot(model, ui);
  const suggested = nextShot(model, ui);
  return `<main class="center-stage"><div class="stage-heading"><div><span>${ui.mode === "storyboard" ? "单集制作" : ui.mode === "review" ? "镜头审核" : "交付准备"}</span><h1>${ui.mode === "storyboard" ? "故事板" : ui.mode === "review" ? `镜头 ${current?.sequence ?? "—"} 审核` : "单集交付"}</h1></div><div class="focus-context"><span>${current ? `当前查看：镜头 ${current.sequence}` : "暂无镜头"}</span>${suggested && shouldShowCurrentVersusNext(ui) ? `<button type="button" data-action="go-next">建议下一步：镜头 ${suggested.sequence}</button>` : suggested ? "<strong>与建议下一步一致</strong>" : "<strong>暂无服务建议</strong>"}</div></div>${ui.mode === "storyboard" ? renderStoryboard() : ui.mode === "review" ? renderReviewMode(current) : renderDeliveryMode()}</main>`;
}

function renderCandidates(shot) {
  if (!shot.candidates.length) return '<p class="muted">还没有候选版本。</p>';
  const adopt = availableAction(shot, "adopt_candidate");
  return `<div class="candidate-list">${shot.candidates.map((candidate) => `<article><header><strong>${escapeHtml(candidate.label)}</strong><span>${escapeHtml(candidate.status_label)}</span></header><p>${candidate.artifact_present ? "已登记安全素材引用" : "没有素材引用"}</p><button class="inspector-action" data-command="select-candidate" data-candidate="${escapeHtml(exactRefKey(candidate.ref))}" ${adopt.enabled && candidate.selectable ? "" : "disabled"}>采用此候选</button></article>`).join("")}</div><p class="action-reason">${escapeHtml(adopt.reason)}</p>`;
}

function renderSelections(shot) {
  if (!shot.selections.length) return "";
  const latest = shot.selections.at(-1);
  const review = availableAction(shot, "review_selection");
  const lock = availableAction(shot, "lock_selection");
  return `<section data-section="selection"><div class="section-title"><h3>当前选版</h3><span>${escapeHtml(latest.lifecycle_state)}</span></div><div class="selection-actions"><button data-command="approve-selection" ${review.enabled ? "" : "disabled"}>批准选版</button><button data-command="lock-selection" ${lock.enabled ? "" : "disabled"}>锁定选版</button><button data-command="unlock-selection" ${latest.lifecycle_state === "locked" ? "" : "disabled"}>重新打开</button></div></section>`;
}

function renderContinuity(shot) {
  if (!shot.continuity.length) return '<p class="muted">当前镜头没有连续性事实。</p>';
  const action = availableAction(shot, "apply_continuity");
  const continuity = shot.continuity[0];
  const impact = model.shots.filter((item) => item.continuity.some((fact) => exactRefKey(fact.ref) === exactRefKey(continuity.ref))).length;
  return `<dl class="fact-list">${shot.facts.map((fact) => `<div><dt>${escapeHtml(fact.label)}</dt><dd>${escapeHtml(fact.value)}</dd></div>`).join("")}</dl><form class="continuity-form" data-continuity="${escapeHtml(exactRefKey(continuity.ref))}"><label>局部修正说明<input name="temporary_state" maxlength="200" placeholder="输入需要追加的当前状态" /></label><p>提交前可检查影响范围：${impact} 个精确镜头版本。</p><button class="inspector-action" type="submit" ${action.enabled ? "" : "disabled"}>应用连续性修正</button></form><p class="action-reason">${escapeHtml(action.reason)}</p>`;
}

function renderInspector() {
  const shot = activeShot(model, ui);
  if (!shot) return '<aside class="inspector"><section><p class="muted">此单集还没有镜头事实。</p></section></aside>';
  const [label, tone] = lifecycleLabel(shot);
  const review = availableAction(shot, "review_shot");
  return `<aside class="inspector" aria-label="当前镜头上下文"><header class="inspector-heading"><div><button class="mobile-back" type="button" data-action="back-to-shots">返回镜头列表</button><span>当前查看</span><h2>镜头 ${shot.sequence}</h2></div><span class="shot-status">${statusDot(tone)}${escapeHtml(label)}</span></header>
    <section data-section="overview"><h3>镜头版本</h3><p class="script-copy">${escapeHtml(shot.ref.entity_id)} · ${escapeHtml(shot.ref.version_id)}</p><div class="selection-actions"><button data-command="approve-shot" ${review.enabled ? "" : "disabled"}>批准</button><button data-command="reject-shot" ${review.enabled ? "" : "disabled"}>返工</button></div></section>
    <section data-section="scene"><div class="section-title"><h3>场景归属</h3><span>追加新版本</span></div><select class="scene-select" aria-label="目标场景">${model.scenes.map((scene) => `<option value="${escapeHtml(exactRefKey(scene.ref))}" ${exactRefKey(scene.ref) === exactRefKey(shot.scene_ref) ? "selected" : ""}>场景 ${scene.sequence} · ${escapeHtml(scene.title)}</option>`).join("")}</select><button class="inspector-action" data-command="reassign-shot" ${availableAction(shot, "reassign_scene").enabled ? "" : "disabled"}>保存局部场景修正</button></section>
    <section data-section="continuity"><div class="section-title"><h3>角色与场景事实</h3><span>精确版本</span></div>${renderContinuity(shot)}</section>
    <section data-section="candidates"><div class="section-title"><h3>候选与版本</h3><span>仅当前镜头</span></div>${renderCandidates(shot)}</section>${renderSelections(shot)}
    ${shot.agent_proposal ? `<section class="proposal" data-section="impact"><header>${icons.spark}<div><strong>${escapeHtml(shot.agent_proposal.title)}</strong><span>真实影响证据</span></div></header><div class="proposal-scope"><span>预计影响 ${shot.agent_proposal.declared_impact_count}</span><span>实际应用 ${shot.agent_proposal.applied_count}</span></div><details><summary>检查精确影响项</summary><ul>${shot.agent_proposal.declared_impact_refs.map((ref) => `<li>${escapeHtml(ref.entity_id)} · ${escapeHtml(ref.version_id)}</li>`).join("")}</ul></details></section>` : ""}</aside>`;
}

function render() {
  const priorScroll = app.querySelector(".center-stage")?.scrollTop ?? ui.scrollTop ?? 0;
  const current = activeShot(model, ui);
  app.innerHTML = `<div class="workspace-shell">${renderTopbar(current)}<div class="workspace-layout">${renderLeftRail()}${renderCenter()}${renderInspector()}</div><footer><span id="workspace-status" tabindex="-1">${escapeHtml(statusMessage || "项目事实与恢复位置已从服务读取")}</span><span>${model.delivery.missing_asset_count ?? model.truth.missing_asset_count ?? 0} 项已证实缺失 · ${model.truth.playable_preview_available ? "可检查预览" : "暂无可播放预览证据"}</span></footer></div>`;
  bindEvents();
  const center = app.querySelector(".center-stage");
  if (center && !window.matchMedia("(max-width: 760px)").matches) center.scrollTop = priorScroll;
}

function captureUiPosition() {
  const center = app.querySelector(".center-stage");
  const scrollTop = window.matchMedia("(max-width: 760px)").matches ? window.scrollY : center?.scrollTop || 0;
  ui = updateUiRecovery(ui, { scrollTop });
}

async function persistUi(required = false) {
  captureUiPosition();
  try {
    let merged = mergeEpisodeWorkspaceState(studioState, model, ui);
    let saved;
    try {
      saved = await client.saveStudioState(merged, studioStateVersion);
    } catch (error) {
      if (error?.kind !== "stale") throw error;
      const current = await client.loadStudioState();
      studioState = current.state || {};
      studioStateVersion = current.state_version || "";
      merged = mergeEpisodeWorkspaceState(studioState, model, ui);
      saved = await client.saveStudioState(merged, studioStateVersion);
    }
    studioState = saved.state || merged;
    studioStateVersion = saved.state_version || studioStateVersion;
    return true;
  } catch (error) {
    statusMessage = error?.kind === "stale" ? "恢复位置已在别处更新，请重试当前操作。" : "恢复位置暂时未保存。";
    render();
    if (required) throw error;
    return false;
  }
}

function queuePersist() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => { void persistUi(false); }, 180);
}

async function refreshAuthority(message = "已刷新最新项目事实") {
  const oldRecovery = mergeEpisodeWorkspaceState({}, model, ui).episode_workspace;
  const payload = await client.loadWorkspace();
  model = buildWorkspaceModel(payload);
  ui = createInitialUiState(model, oldRecovery);
  statusMessage = message;
  render();
}

async function clearPendingAfterAuthority(message) {
  ui = updateUiRecovery(ui, { pendingIdempotencyKey: "", pendingCommand: null });
  statusMessage = message;
  render();
  await persistUi(false);
}

async function runCommand(command) {
  if (commandRunning) return;
  if (ui.pendingCommand) {
    await reconcilePendingCommand();
    return;
  }
  clearTimeout(persistTimer);
  commandRunning = true;
  const idempotencyKey = commandIdFor(command.action);
  const pendingCommand = { idempotency_key: idempotencyKey, payload: command };
  ui = updateUiRecovery(ui, { pendingIdempotencyKey: idempotencyKey, pendingCommand });
  statusMessage = "正在保存可恢复命令…";
  render();
  let commandDispatched = false;
  try {
    await persistUi(true);
    commandDispatched = true;
    await client.executeCommand(command, idempotencyKey);
    await refreshAuthority("操作已追加到项目事实");
    await clearPendingAfterAuthority("操作已追加到项目事实");
  } catch (error) {
    try {
      if (retainPendingCommandAfterFailure(error?.kind, commandDispatched)) {
        await refreshAuthority(commandDispatched
          ? "响应未确认；原命令已保留，等待同标识核对"
          : "命令尚未发送；可恢复命令已保留，等待状态保存完成");
      } else {
        await refreshAuthority("命令被服务明确拒绝，已刷新最新事实");
        await clearPendingAfterAuthority("命令未执行；可以基于最新事实继续");
      }
    } catch {
      renderError(error);
    }
  } finally {
    commandRunning = false;
  }
}

async function reconcilePendingCommand() {
  const pending = ui.pendingCommand;
  if (!pending || commandRunning) return !pending;
  commandRunning = true;
  statusMessage = "正在用原命令标识核对未确认操作…";
  render();
  let commandDispatched = false;
  try {
    await persistUi(true);
    commandDispatched = true;
    await client.executeCommand(pending.payload, pending.idempotency_key);
    await refreshAuthority("未确认操作已由服务确认");
    await clearPendingAfterAuthority("未确认操作已由服务确认");
    return true;
  } catch (error) {
    try {
      if (retainPendingCommandAfterFailure(error?.kind, commandDispatched)) {
        await refreshAuthority(commandDispatched
          ? "未确认操作仍待核对；不会启动新命令"
          : "原命令尚未重放；等待可恢复状态保存完成");
      } else {
        await refreshAuthority("原命令被服务明确拒绝，已刷新最新事实");
        await clearPendingAfterAuthority("原命令未执行；工作区已解除等待状态");
      }
    } catch {
      renderError(error);
    }
    return false;
  } finally {
    commandRunning = false;
  }
}

function bindEvents() {
  app.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => { ui = selectMode(ui, button.dataset.mode); render(); queuePersist(); }));
  app.querySelectorAll("[data-shot]").forEach((button) => button.addEventListener("click", () => { const shot = model.shots.find((item) => exactRefKey(item.ref) === button.dataset.shot); if (shot) { ui = inspectShot(ui, shot.ref); render(); queuePersist(); if (window.matchMedia("(max-width: 760px)").matches) requestAnimationFrame(() => app.querySelector(".inspector")?.scrollIntoView({ block: "start" })); } }));
  app.querySelectorAll("[data-scene]").forEach((button) => button.addEventListener("click", () => { const scene = model.scenes.find((item) => exactRefKey(item.ref) === button.dataset.scene); ui = selectSceneFilter(ui, scene?.ref || "all"); render(); }));
  app.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { ui = selectStatusFilter(ui, button.dataset.filter); render(); }));
  app.querySelectorAll('[data-action="go-next"]').forEach((button) => button.addEventListener("click", () => { const shot = nextShot(model, ui); if (!shot) return; ui = inspectShot(ui, shot.ref); render(); queuePersist(); requestAnimationFrame(() => app.querySelector('[data-shot][data-next="true"]')?.focus()); }));
  app.querySelector('[data-action="open-mobile-nav"]')?.addEventListener("click", () => app.querySelector(".center-stage")?.scrollIntoView({ block: "start" }));
  app.querySelector('[data-action="back-to-shots"]')?.addEventListener("click", () => app.querySelector(".center-stage")?.scrollIntoView({ block: "start" }));
  app.querySelector('[data-action="reset-recovery"]')?.addEventListener("click", () => { if (!ui.pendingCommand) { ui = createInitialUiState(model); render(); queuePersist(); } });
  app.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => handleCommand(button.dataset.command, button)));
  app.querySelector(".continuity-form")?.addEventListener("submit", (event) => { event.preventDefault(); const shot = activeShot(model, ui); const continuity = shot.continuity.find((item) => exactRefKey(item.ref) === event.currentTarget.dataset.continuity); const value = new FormData(event.currentTarget).get("temporary_state")?.toString().trim(); if (value) void runCommand(buildContinuityApplyCommand(model, shot, continuity, { temporary_state: [...continuity.temporary_state, value] })); });
  app.onfocusin = (event) => {
    const focus = event.target.closest("[data-focus]")?.dataset.focus
      || (event.target.closest("[data-command]")?.dataset.command ? `command:${event.target.closest("[data-command]").dataset.command}` : "")
      || (event.target.name ? `field:${event.target.name}` : "");
    const section = event.target.closest("[data-section]")?.dataset.section || ui.inspectorSection;
    if (focus !== ui.focusedControl || section !== ui.inspectorSection) {
      ui = updateUiRecovery(ui, { focusedControl: focus, inspectorSection: section });
      queuePersist();
    }
  };
  app.querySelector(".center-stage")?.addEventListener("scroll", queuePersist, { passive: true });
}

function handleCommand(action, target) {
  const shot = activeShot(model, ui);
  const latestSelection = shot?.selections.at(-1);
  const builders = {
    "approve-shot": () => buildShotReviewCommand(model, shot, "approve"),
    "reject-shot": () => buildShotReviewCommand(model, shot, "reject"),
    "reassign-shot": () => buildShotReassignCommand(model, shot, model.scenes.find((scene) => exactRefKey(scene.ref) === app.querySelector(".scene-select")?.value)),
    "select-candidate": () => buildCandidateSelectCommand(model, shot, shot.candidates.find((candidate) => exactRefKey(candidate.ref) === target.dataset.candidate)),
    "approve-selection": () => buildSelectionReviewCommand(model, latestSelection, "approve"),
    "lock-selection": () => buildSelectionLockCommand(model, latestSelection),
    "unlock-selection": () => buildSelectionUnlockCommand(model, latestSelection),
    "unlock-delivery": () => buildDeliveryUnlockCommand(model),
  };
  try { void runCommand(builders[action]()); } catch { statusMessage = "当前精确版本无法构造此操作。"; render(); }
}

function restorePosition() {
  const center = app.querySelector(".center-stage");
  if (window.matchMedia("(max-width: 760px)").matches) window.scrollTo({ top: ui.scrollTop, behavior: "instant" });
  else if (center) center.scrollTop = ui.scrollTop;
  let focus = ui.focusedControl && app.querySelector(`[data-focus="${CSS.escape(ui.focusedControl)}"]`);
  if (!focus && ui.focusedControl.startsWith("command:")) focus = app.querySelector(`[data-command="${CSS.escape(ui.focusedControl.slice(8))}"]`);
  if (!focus && ui.focusedControl.startsWith("field:")) focus = app.querySelector(`[name="${CSS.escape(ui.focusedControl.slice(6))}"]`);
  focusIfAvailable(focus);
  if (window.matchMedia("(max-width: 760px)").matches && ui.inspectorSection !== "overview") {
    app.querySelector(`[data-section="${CSS.escape(ui.inspectorSection)}"]`)?.scrollIntoView({ block: "start" });
  }
}

async function hydrate() {
  if (!client) { renderState("empty", "缺少单集身份", "请从项目单集入口进入，并提供 project、episode 与 version。", '<a class="primary-button" href="/studio/">返回 Studio</a>'); return; }
  renderState("loading", "正在恢复制作现场", "正在读取经过身份验证的项目事实与 UI 检查点…");
  try {
    const [payload, saved] = await Promise.all([client.loadWorkspace(), client.loadStudioState()]);
    model = buildWorkspaceModel(payload);
    studioState = saved.state || {};
    studioStateVersion = saved.state_version || "";
    ui = createInitialUiState(model, studioState.episode_workspace);
    render();
    if (ui.pendingCommand) await reconcilePendingCommand();
    requestAnimationFrame(restorePosition);
  } catch (error) { renderError(error); }
}

if (projectId && !episodeId && !episodeVersionId) {
  import("./authoring-app.mjs?creator=v05")
    .then(({ startCreatorAuthoring }) => startCreatorAuthoring(app, projectId))
    .catch(() => renderState("error", "无法打开创作工作台", "请稍后重试。"));
} else {
  hydrate();
}
