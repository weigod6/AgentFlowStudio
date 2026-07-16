import { createRuntimeClient, saveAuthToken } from "../src/runtime-client.js";
import { icon } from "../src/icons.js";

const app = document.querySelector("#app");
const params = new URLSearchParams(window.location.search);

let projectId = safeProjectId(params.get("project")) || "";
let runtime = projectId ? createRuntimeClient(projectId) : createRuntimeClient();
let control = null;
let trial = null;
let commercial = null;
let user = null;
let busy = false;
let notice = "";
let activeTab = params.get("view") || "mission";
let commercialView = params.get("commercialView") || "storyboard";

const tabs = [
  ["mission", "目标"],
  ["longform", "长篇生产"],
  ["plan", "计划"],
  ["cockpit", "制作"],
  ["trial", "图像试验"],
  ["artifacts", "素材"],
  ["review", "审片"],
];

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function safeProjectId(value) {
  return String(value || "").trim().replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
}

function commandKey(prefix) {
  const raw = `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
  return raw.replace(/[^A-Za-z0-9_.:-]+/g, "-");
}

function setProject(nextProjectId) {
  projectId = safeProjectId(nextProjectId);
  runtime = createRuntimeClient(projectId || "production-control");
  const url = new URL(window.location.href);
  if (projectId) url.searchParams.set("project", projectId);
  else url.searchParams.delete("project");
  window.history.replaceState({}, "", url);
}

function syncRouteState() {
  const url = new URL(window.location.href);
  if (projectId) url.searchParams.set("project", projectId);
  if (activeTab) url.searchParams.set("view", activeTab);
  if (commercialView) url.searchParams.set("commercialView", commercialView);
  window.history.replaceState({}, "", url);
}

function statusLabel(value) {
  return {
    empty: "未开始",
    missing: "未开始",
    recorded: "已记录",
    proposed: "待批准",
    planned: "待批准",
    approved: "已批准",
    running: "进行中",
    queued: "排队",
    "waiting-human": "等待人工",
    retrying: "重试中",
    blocked: "阻塞",
    completed: "完成",
    succeeded: "完成",
    partially_complete: "部分完成",
    awaiting_review: "等待审核",
    cancelled: "取消",
    active: "可执行",
    paused: "暂停",
    "pause-requested": "暂停中",
    "resume-requested": "恢复中",
    "cancel-requested": "取消中",
  }[value] || value || "未知";
}

function toneForRun(run) {
  if (run.execution_state === "completed") return "ok";
  if (run.execution_state === "waiting-human" || run.blocked) return "warn";
  if (run.control_state === "paused" || run.execution_state === "retrying") return "hold";
  if (run.execution_state === "cancelled") return "danger";
  return "live";
}

function renderShell(content) {
  app.innerHTML = `<div class="pc-shell">
    <header class="pc-topbar">
      <a class="brand" href="/studio/">${icon("grid", 18)}<span>智能制片中枢</span></a>
      <nav class="surface-links" aria-label="Studio routes">
        <a href="/studio/">画布</a>
        <a href="${escapeHtml(control?.workspace_entry?.href || "#")}" ${control?.version ? "" : "aria-disabled=\"true\""}>故事板 / 审片</a>
      </nav>
      <div class="session">${user ? `<span>${escapeHtml(user.display_name || user.email || "账号")}</span><button type="button" data-action="logout">${icon("user", 15)}退出</button>` : ""}</div>
    </header>
    ${content}
  </div>`;
  bindGlobal();
}

function renderLoading(message = "正在读取项目记录…") {
  renderShell(`<main class="state-screen"><div class="mark">${icon("bolt", 30)}</div><h1>${escapeHtml(message)}</h1></main>`);
}

function renderAuth(status = {}) {
  const invite = status.invite_registration_available
    ? '<label>邀请码<input name="invite_code" autocomplete="one-time-code" /></label>'
    : "";
  renderShell(`<main class="auth-grid">
    <section class="auth-panel">
      <h1>登录制片工作台</h1>
      <form data-form="login">
        <label>邮箱<input name="email" type="email" autocomplete="email" required /></label>
        <label>密码<input name="password" type="password" autocomplete="current-password" required /></label>
        <button type="submit" class="primary">${icon("play", 16)}登录</button>
      </form>
    </section>
    <section class="auth-panel muted-panel">
      <h2>创建内部账号</h2>
      <form data-form="register">
        <label>邮箱<input name="email" type="email" autocomplete="email" required /></label>
        <label>显示名<input name="display_name" autocomplete="name" /></label>
        <label>密码<input name="password" type="password" autocomplete="new-password" required /></label>
        ${invite}
        <button type="submit">${icon("plus", 16)}注册并进入</button>
      </form>
    </section>
  </main>`);
  app.querySelector('[data-form="login"]')?.addEventListener("submit", onLogin);
  app.querySelector('[data-form="register"]')?.addEventListener("submit", onRegister);
}

function renderProjectSetup() {
  renderShell(`<main class="state-screen project-setup">
    <div class="mark">${icon("folder", 28)}</div>
    <h1>新建一集制作项目</h1>
    <form data-form="project">
      <label>项目名称<input name="goal" value="第一集制作计划" maxlength="120" required /></label>
      <button type="submit" class="primary">${icon("plus", 16)}创建项目</button>
    </form>
  </main>`);
  app.querySelector('[data-form="project"]')?.addEventListener("submit", onCreateProject);
}

function renderApp() {
  if (!control) {
    renderProjectSetup();
    return;
  }
  syncRouteState();
  const content = `<div class="pc-app">
    <aside class="pc-sidebar">
      <div class="pc-title">
        <span class="mark">${icon("bolt", 22)}</span>
        <div><strong>制片工作台</strong><small>${statusLabel(control.plan.status)}</small></div>
      </div>
      <nav class="tabs">${tabs.map(([key, label]) => `<button type="button" data-tab="${key}" aria-current="${activeTab === key ? "page" : "false"}">${label}</button>`).join("")}</nav>
      <div class="ledger-box">
        <span>项目记录</span>
        <strong>v${control.version}</strong>
        <small>${control.event_count} 次更新 · ${control.outbox_count} 个待同步项</small>
        <button type="button" data-action="rebuild">${icon("retry", 14)}检查记录</button>
      </div>
    </aside>
    <main class="pc-main">
      ${renderHeader()}
      ${renderActiveTab()}
    </main>
    <aside class="agent-rail">
      ${renderAgentRail()}
    </aside>
  </div>`;
  renderShell(content);
  bindApp();
}

function renderHeader() {
  const progress = control.runs.length
    ? Math.round((control.runs.filter((run) => run.execution_state === "completed").length / control.runs.length) * 100)
    : 0;
  return `<section class="overview-band">
    <div>
      <span class="eyeless">外部生成未启用 · 已记录 ${control.provider_dispatch_count} 次外部任务</span>
      <h1>${control.mission.objective ? escapeHtml(control.mission.objective) : "等待填写制作目标"}</h1>
      <p>${notice ? escapeHtml(notice) : "所有制作操作都会保存到同一份项目记录，刷新后仍可恢复。"}</p>
    </div>
    <div class="metrics" aria-label="生产摘要">
      <div><strong>${control.plan.task_specs.length || control.tasks.length}</strong><span>任务</span></div>
      <div><strong>${control.runs.length}</strong><span>制作项</span></div>
      <div><strong>${progress}%</strong><span>进度</span></div>
      <div><strong>${control.artifacts.length}</strong><span>素材</span></div>
    </div>
  </section>`;
}

function renderActiveTab() {
  if (activeTab === "plan") return renderPlan();
  if (activeTab === "longform") return renderCommercialProduction();
  if (activeTab === "cockpit") return renderCockpit();
  if (activeTab === "trial") return renderTrial();
  if (activeTab === "artifacts") return renderArtifacts();
  if (activeTab === "review") return renderReview();
  return renderMission();
}

function renderMission() {
  const disabled = control.mission.status === "recorded";
  return `<section class="work-surface">
    <header><h2>制作目标</h2><p>${disabled ? "目标已保存到项目记录。" : "先写清楚这一集要完成什么，以及哪些内容不能被改动。"}</p></header>
    <form data-form="mission" class="mission-form">
      <label>目标<textarea name="objective" rows="5" ${disabled ? "disabled" : ""}>${escapeHtml(control.mission.objective || "制作一集可审片、可返工、可锁版的故事板：先确认目标，再批准计划，最后把候选素材写回到对应镜头。")}</textarea></label>
      <div class="constraint-grid">
        <label>边界 1<input name="constraint" ${disabled ? "disabled" : ""} value="本轮不调用外部生成服务。" /></label>
        <label>边界 2<input name="constraint" ${disabled ? "disabled" : ""} value="局部返工时保留未受影响镜头。" /></label>
      </div>
      <button type="submit" class="primary" ${disabled || busy ? "disabled" : ""}>${icon("check", 16)}保存目标</button>
    </form>
  </section>`;
}

function renderPlan() {
  const approved = control.plan.status === "approved";
  const specs = control.plan.task_specs.length ? control.plan.task_specs : defaultTasks();
  return `<section class="work-surface">
    <header><h2>计划</h2><p>${approved ? "计划已批准，制作项已创建。" : "批准前可以编辑每一步的范围。"}</p></header>
    <form data-form="plan" class="plan-form">
      ${specs.map((task, index) => `<fieldset>
        <legend>${escapeHtml(task.title || `任务 ${index + 1}`)}</legend>
        <input name="title" value="${escapeHtml(task.title || `任务 ${index + 1}`)}" ${approved ? "disabled" : ""} />
        <textarea name="boundary" rows="3" ${approved ? "disabled" : ""}>${escapeHtml(task.boundary)}</textarea>
      </fieldset>`).join("")}
      <div class="form-actions">
        <button type="submit" ${approved || busy ? "disabled" : ""}>${icon("pencil", 16)}保存计划</button>
        <button type="button" class="primary" data-action="approve-plan" ${approved || !control.plan.task_specs.length || busy ? "disabled" : ""}>${icon("check", 16)}批准并启动</button>
      </div>
    </form>
  </section>`;
}

function renderCockpit() {
  if (!control.runs.length) return emptyPanel("制作", "批准计划后会出现制作项。");
  return `<section class="run-board">
    ${control.runs.map((run, index) => `<article class="run-row ${toneForRun(run)}">
      <div class="run-main">
        <span class="run-index">${String(index + 1).padStart(2, "0")}</span>
        <div><h3>${escapeHtml(run.task_title)}</h3><p>${escapeHtml(run.boundary)}</p></div>
      </div>
      <div class="run-state">
        <strong>${statusLabel(run.execution_state)}</strong>
        <span>${statusLabel(run.control_state)} · 第 ${run.attempt_count} 次尝试</span>
        <small>${escapeHtml(costLabel(run.simulated_cost_label))}</small>
      </div>
      <div class="run-actions" data-run="${escapeHtml(run.run_id)}">
        ${run.control_state === "paused"
          ? button("resume", "恢复", "play")
          : button("pause", "暂停", "clock")}
        ${button("retry", "重试", "retry")}
        ${run.waiting_human ? button("decide_human", "确认", "check") : button("waiting_human", "人工", "user")}
        ${run.blocked ? button("clear_blocker", "放行", "check") : button("block", "阻塞", "lock")}
        ${button("provider_gate", "预算", "lock")}
        ${button("writeback", "写回", "bookmark")}
        ${button("complete", "完成", "check")}
      </div>
    </article>`).join("")}
  </section>`;
}

function renderTrial() {
  const currentTrial = trial || { status: "empty", event_count: 0, dispatches: {}, target_shot_ids: ["shot-001", "shot-002", "shot-003"], admission_receipts: [], non_claims: [] };
  const targetShots = currentTrial.target_shot_ids?.length ? currentTrial.target_shot_ids : ["shot-001", "shot-002", "shot-003"];
  const dispatches = currentTrial.dispatches || {};
  const canRecord = currentTrial.status === "empty";
  const canApprove = currentTrial.status === "planned";
  const canDispatch = currentTrial.status === "approved" || currentTrial.status === "running";
  return `<section class="work-surface trial-surface">
    <header>
      <h2>三镜头图像试验</h2>
      <p>仅调度 3 个图像/关键帧外部生成候选；不包含大模型脚本、视频、音频、导出、媒体质检、创作者验收或商业验证。金额只作合成准入上限，不代表真实账单。</p>
    </header>
    <div class="trial-grid">
      <article>
        <span>状态</span>
        <strong>${statusLabel(currentTrial.status)}</strong>
        <small>${currentTrial.event_count || 0} 次记录 · ${currentTrial.provider_dispatch_count || 0} 次外部调度</small>
      </article>
      <article>
        <span>合成准入上限</span>
        <strong>${moneyLabel(currentTrial.project_ceiling)}</strong>
        <small>单镜头合成估算 ${moneyLabel(currentTrial.estimated_unit_cost)} · 真实账单未核验</small>
      </article>
      <article>
        <span>人类门</span>
        <strong>${currentTrial.waiting_human ? "等待确认" : "已记录当前决策"}</strong>
        <small>媒体质量、人类接受、商业验证分开标记</small>
      </article>
    </div>
    <div class="shot-trial-list">
      ${targetShots.map((shotId) => {
        const dispatch = dispatches[shotId] || {};
        const writeback = dispatch.episode_writeback || {};
        return `<article>
          <strong>${escapeHtml(shotLabel(shotId))}</strong>
          <span>${statusLabel(writeback.status || dispatch.status || "missing")}</span>
          <small>${gateLabel(dispatch.provider_gate)} · ${writeback.human_review_state === "needs_review" ? "待人工审片" : "未写回候选"}</small>
        </article>`;
      }).join("")}
    </div>
    <div class="form-actions">
      <button type="button" data-action="trial-mission" ${!canRecord || busy ? "disabled" : ""}>${icon("bookmark", 16)}冻结图像试验</button>
      <button type="button" data-action="trial-approve" ${!canApprove || busy ? "disabled" : ""}>${icon("check", 16)}批准试验</button>
      <button type="button" class="primary" data-action="trial-dispatch" ${!canDispatch || busy ? "disabled" : ""}>${icon("play", 16)}调度下一镜头</button>
    </div>
    <footer class="trial-nonclaims">
      ${(currentTrial.non_claims || []).map((item) => `<span>${escapeHtml(nonClaimLabel(item))}</span>`).join("")}
    </footer>
  </section>`;
}

function renderCommercialProduction() {
  const state = commercial || { status: "empty", version: 0 };
  if (state.status === "empty") {
    return `<section class="work-surface longform-empty">
      <header>
        <h2>长篇生产纵切</h2>
        <p>创建一个可运行样本：多集大纲、第一集 4 场 16 镜、人物/动物/场景/道具资产、创作方案继承和同源 Storyboard/Canvas。</p>
      </header>
      <div class="commercial-proof-grid">
        <article><strong>事实层</strong><span>Project/IP → Bible → Arc → Episode → Scene → Shot</span></article>
        <article><strong>资产链</strong><span>Entity → Identity → Variant → ReferenceSet → Candidate → Approved</span></article>
        <article><strong>返工</strong><span>Scope lock → 局部改写 → 未选事实不漂移</span></article>
      </div>
      <button type="button" class="primary" data-action="commercial-sample" ${busy ? "disabled" : ""}>${icon("plus", 16)}创建可操作样本</button>
    </section>`;
  }
  const locked = state.stage_gates?.storyboard_scope_lock?.status === "locked";
  return `<section class="commercial-shell">
    <header class="commercial-header">
      <div>
        <span class="eyeless">同一事实源 · v${state.version} · 外部生成 ${state.provider_dispatch_count || state.production_control?.provider_dispatch_count || 0} 次</span>
        <h2>${escapeHtml(state.hierarchy?.project_title || "长篇项目")}</h2>
        <p>${escapeHtml(state.hierarchy?.arc?.title || "")} · ${escapeHtml(state.hierarchy?.volume?.title || "")}</p>
      </div>
      <div class="segmented" role="tablist" aria-label="长篇生产模式">
        <button type="button" data-commercial-view="storyboard" aria-current="${commercialView === "storyboard" ? "page" : "false"}">${icon("frames", 15)}故事板</button>
        <button type="button" data-commercial-view="canvas" aria-current="${commercialView === "canvas" ? "page" : "false"}">${icon("grid", 15)}画布</button>
      </div>
    </header>
    <div class="commercial-layout">
      <div class="commercial-main">
        ${renderCommercialFacts(state)}
        ${commercialView === "canvas" ? renderCommercialCanvas(state) : renderCommercialStoryboard(state)}
      </div>
      <aside class="commercial-side">
        ${renderCommercialGate(state, locked)}
        ${renderCommercialRecipe(state)}
        ${renderCommercialAssets(state)}
      </aside>
    </div>
  </section>`;
}

function renderCommercialFacts(state) {
  const bibleFacts = [
    ...(state.hierarchy?.story_bible?.facts || []),
    ...(state.hierarchy?.world_bible?.facts || []),
  ];
  return `<div class="fact-chain">
    <article><span>IP</span><strong>${escapeHtml(state.hierarchy?.ip_title || "")}</strong></article>
    <article><span>故事 / 世界</span><strong>${bibleFacts.length} 条事实</strong></article>
    <article><span>多集大纲</span><strong>${state.episodes?.length || 0} 集</strong></article>
    <article><span>当前集</span><strong>${state.storyboard?.scene_count || 0} 场 · ${state.storyboard?.shot_count || 0} 镜</strong></article>
  </div>`;
}

function renderCommercialStoryboard(state) {
  const scenes = state.scenes || [];
  const shots = state.shots || [];
  return `<div class="storyboard-surface">
    <header><h3>结构化 Storyboard</h3><p>默认生产面。每个镜头引用同一批资产和创作方案，局部改写只更新被选镜头。</p></header>
    <div class="episode-strip">${(state.episodes || []).map((episode) => `<article class="${episode.episode_id === state.selected_episode_id ? "selected" : ""}">
      <span>第 ${episode.sequence} 集</span><strong>${escapeHtml(episode.title)}</strong><small>${escapeHtml(episode.logline)}</small>
    </article>`).join("")}</div>
    <div class="scene-stack">
      ${scenes.map((scene) => {
        const sceneShots = shots.filter((shot) => shot.scene_id === scene.scene_id);
        return `<article class="scene-block">
          <header><span>${String(scene.sequence).padStart(2, "0")}</span><div><h4>${escapeHtml(scene.title)}</h4><p>${escapeHtml(scene.purpose)}</p></div></header>
          <div class="shot-grid">${sceneShots.map((shot) => `<button type="button" class="shot-tile ${shot.review_state === "needs_review" ? "needs-review" : ""}" data-shot-id="${escapeHtml(shot.shot_id)}">
            <strong>${escapeHtml(shot.shot_id.replace("shot-", "镜头 "))}</strong>
            <span>${escapeHtml(shot.beat)}</span>
            <small>${escapeHtml(shot.version_id)} · ${shot.asset_refs.length} 个资产</small>
          </button>`).join("")}</div>
        </article>`;
      }).join("")}
    </div>
  </div>`;
}

function renderCommercialCanvas(state) {
  const scenes = state.scenes || [];
  const assets = state.assets || [];
  return `<div class="canvas-surface">
    <header><h3>Canvas 探索视图</h3><p>用于关系和空间组织，不作为第二套事实源。</p></header>
    <div class="canvas-map">
      ${scenes.map((scene, index) => `<article style="--x:${(index % 2) * 42 + 8}%;--y:${Math.floor(index / 2) * 34 + 8}%">
        <span>场景</span><strong>${escapeHtml(scene.title)}</strong><small>${escapeHtml(scene.scene_id)}</small>
      </article>`).join("")}
      ${assets.slice(0, 6).map((asset, index) => `<article class="asset-node" style="--x:${(index % 3) * 28 + 14}%;--y:${58 + Math.floor(index / 3) * 20}%">
        <span>${escapeHtml(assetTypeLabel(asset.type))}</span><strong>${escapeHtml(asset.name)}</strong><small>${escapeHtml(asset.reference_set_id)}</small>
      </article>`).join("")}
    </div>
  </div>`;
}

function renderCommercialGate(state, locked) {
  const gate = state.stage_gates?.storyboard_scope_lock || {};
  const last = state.revision_requests?.at?.(-1);
  return `<section class="commercial-card">
    <h3>Stage Gate</h3>
    <p>${locked ? "故事板范围已锁定，可恢复返工。" : "锁定后才能提交局部改写。"}</p>
    <div class="fact-pills">
      <span>${statusLabel(gate.status || "missing")}</span>
      <span>${gate.locked_refs?.length || 0} 个锁定引用</span>
    </div>
    <button type="button" data-action="commercial-lock" ${locked || busy ? "disabled" : ""}>${icon("lock", 15)}锁定范围</button>
    <button type="button" class="primary" data-action="commercial-rewrite" ${!locked || busy ? "disabled" : ""}>${icon("pencil", 15)}改写第 6 镜</button>
    ${last ? `<small>最近返工：${escapeHtml(last.target_ref?.entity_id || "")} · 未选事实 ${last.protected_digest_equal ? "未漂移" : "需复查"}</small>` : ""}
  </section>`;
}

function renderCommercialRecipe(state) {
  const cards = state.production_recipe?.cards || {};
  return `<section class="commercial-card">
    <h3>创作方案</h3>
    <div class="recipe-list">
      ${Object.entries(cards).map(([key, value]) => `<div><span>${escapeHtml(recipeLabel(key))}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
    </div>
  </section>`;
}

function renderCommercialAssets(state) {
  return `<section class="commercial-card">
    <h3>资产身份链</h3>
    <div class="asset-chain-list">${(state.assets || []).map((asset) => `<article>
      <strong>${escapeHtml(asset.name)}</strong>
      <span>${escapeHtml(assetTypeLabel(asset.type))} · 置信度 ${Math.round((asset.recognition?.confidence || 0) * 100)}%</span>
      <small>${escapeHtml(asset.base_identity?.version_id)} → ${escapeHtml(asset.episode_variant?.version_id)} → ${escapeHtml(asset.reference_set_id)} → ${escapeHtml(asset.approved_version?.version_id)}</small>
    </article>`).join("")}</div>
  </section>`;
}

function renderArtifacts() {
  if (!control.artifacts.length) return emptyPanel("素材", "写回后会显示受影响镜头与被保护镜头。");
  return `<section class="artifact-surface">
    <header><h2>素材</h2><p>写回会保留来源制作项，并说明哪些镜头被改动、哪些镜头被保护。</p></header>
    <div class="artifact-list">
      ${control.artifacts.map((artifact, index) => `<article>
        <strong>素材 ${index + 1}</strong>
        <p>${escapeHtml(operationLabel(artifact.operation))} · 来源 ${escapeHtml(taskName(artifact.task_id))}</p>
        <dl>
          <div><dt>受影响</dt><dd>${escapeHtml(refLabel(artifact.affected_ref))}</dd></div>
          <div><dt>保护</dt><dd>${artifact.protected_refs.map(refLabel).map(escapeHtml).join("、")}</dd></div>
          <div><dt>来源</dt><dd>已记录制作来源</dd></div>
        </dl>
      </article>`).join("")}
    </div>
  </section>`;
}

function renderReview() {
  const continuity = control.continuity;
  return `<section class="review-grid">
    <article>
      <h2>连续性</h2>
      <p>${continuity.shot_local_rework_protected ? "局部返工保护已记录。" : "等待写回保护证据。"}</p>
      <div class="fact-pills">
        <span>${continuity.affected_refs.length} 个受影响</span>
        <span>${continuity.protected_refs.length} 个受保护</span>
        <span>${continuity.impact_assessment_count} 个影响评估</span>
      </div>
    </article>
    <article>
      <h2>审片 / 交付</h2>
      <p>${control.review.delivery_readback === "internal_delivery_packet_ready" ? "可以进入故事板检查候选素材。" : "交付仍在等待素材与审片结果。"}</p>
      <a class="primary link-button" href="${escapeHtml(control.workspace_entry.href)}">${icon("frames", 16)}打开故事板 / 审片</a>
    </article>
    <article>
      <h2>尚未完成</h2>
      <ul>${control.review.non_claims.map((item) => `<li>${escapeHtml(nonClaimLabel(item))}</li>`).join("")}</ul>
    </article>
  </section>`;
}

function renderAgentRail() {
  const suggestions = [];
  if (control.mission.status !== "recorded") suggestions.push("先记录目标。");
  else if (!control.plan.task_specs.length) suggestions.push("生成三段式计划。");
  else if (control.plan.status !== "approved") suggestions.push("批准后会创建任务与制作项。");
  else if (!control.artifacts.length) suggestions.push("选择一个制作项并写回候选素材。");
  else suggestions.push("检查审核 / 交付读回。");
  return `<section>
    <h2>制作建议</h2>
    <p>本轮只使用项目内的确定性记录</p>
    <ol>${suggestions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>
  </section>
  <section>
    <h2>恢复</h2>
    <p>重新加载后会从项目记录恢复。</p>
    <strong>${control.recovery.ledger_rebuildable ? "可恢复" : "待检查"}</strong>
  </section>`;
}

function defaultTasks() {
  return [
    { title: "镜头拆解", boundary: "拆解制作目标为局部镜头与连续性检查。" },
    { title: "候选写回", boundary: "生成本轮确定性候选，并标清预算与影响范围。" },
    { title: "审核交付", boundary: "汇总写回、连续性和交付读回证据。" },
  ];
}

function button(action, label, iconName) {
  return `<button type="button" data-run-action="${action}" ${busy ? "disabled" : ""}>${icon(iconName, 14)}${label}</button>`;
}

function emptyPanel(title, text) {
  return `<section class="work-surface empty-work"><header><h2>${escapeHtml(title)}</h2><p>${escapeHtml(text)}</p></header></section>`;
}

function taskName(taskId) {
  const spec = control.plan.task_specs.find((task) => task.task_id === taskId);
  return spec?.title || "任务";
}

function costLabel(value) {
  return String(value || "")
    .replace(/provider closed/gi, "外部生成未启用")
    .replace(/provider/gi, "外部生成")
    .replace(/simulated/gi, "预估");
}

function operationLabel(value) {
  return {
    shot_local_rework: "镜头局部返工",
    artifact_writeback: "产物写回",
    "asset_candidate.create_version": "候选素材写回",
  }[value] || "产物写回";
}

function assetTypeLabel(value) {
  return {
    human: "人物",
    animal: "动物",
    scene_location: "场景 / 地点",
    prop: "道具",
    creature: "生物",
    vehicle: "载具",
    effect: "特效",
  }[value] || "资产";
}

function recipeLabel(value) {
  return {
    genre: "类型",
    narrative_grammar: "叙事语法",
    shot_language: "镜头语言",
    visual_style: "视觉风格",
    motion_audio: "运动 / 声音",
    negative_constraints: "负面约束",
    provider_adapter: "生成适配",
  }[value] || value;
}

function nonClaimLabel(value) {
  return {
    not_provider_smoke: "未连接外部生成服务",
    not_generated_media_qa: "未进行生成媒体质检",
    not_human_acceptance: "未完成创作者验收",
    not_business_validation: "未进行商业验证",
    "provider smoke is separate from media quality": "外部服务冒烟与媒体质量分开",
    "human acceptance is not claimed": "未声明人类接受",
    "business validation is not claimed": "未声明商业验证",
    "actual provider billing is not proven by this route": "真实账单需另行核对",
  }[value] || "仍需后续确认";
}

function moneyLabel(value) {
  if (!value) return "未设置";
  const amount = Number(value.amount || 0);
  const currency = value.currency || "USD";
  return amount > 0 ? `${currency} ${amount.toFixed(2)}` : "未设置";
}

function gateLabel(value) {
  if (!value || !value.status) return "未调度";
  if (value.status === "ready") return "外部服务门禁已开启";
  if (value.status === "blocked") return "外部服务门禁关闭";
  return statusLabel(value.status);
}

function shotLabel(value) {
  return `镜头 ${String(value || "").replace("shot-", "")}`;
}

function refLabel(ref) {
  if (!ref) return "无";
  const objectType = {
    episode_shot: "镜头",
    shot: "镜头",
    artifact: "产物",
    asset_candidate: "候选素材",
  }[ref.object_type] || "对象";
  return `${objectType} ${String(ref.object_id || "").replace("shot-", "")}`;
}

function bindGlobal() {
  app.querySelector('[data-action="logout"]')?.addEventListener("click", async () => {
    try { await runtime.logout(); } catch { saveAuthToken(""); }
    user = null;
    control = null;
    commercial = null;
    await init();
  });
}

function bindApp() {
  app.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => {
    activeTab = button.dataset.tab;
    renderApp();
  }));
  app.querySelectorAll("[data-commercial-view]").forEach((button) => button.addEventListener("click", () => {
    commercialView = button.dataset.commercialView || "storyboard";
    renderApp();
  }));
  app.querySelector('[data-form="mission"]')?.addEventListener("submit", onMission);
  app.querySelector('[data-form="plan"]')?.addEventListener("submit", onPlan);
  app.querySelector('[data-action="approve-plan"]')?.addEventListener("click", onApprovePlan);
  app.querySelector('[data-action="commercial-sample"]')?.addEventListener("click", onCommercialSample);
  app.querySelector('[data-action="commercial-lock"]')?.addEventListener("click", onCommercialLock);
  app.querySelector('[data-action="commercial-rewrite"]')?.addEventListener("click", onCommercialRewrite);
  app.querySelector('[data-action="trial-mission"]')?.addEventListener("click", onTrialMission);
  app.querySelector('[data-action="trial-approve"]')?.addEventListener("click", onTrialApprove);
  app.querySelector('[data-action="trial-dispatch"]')?.addEventListener("click", onTrialDispatch);
  app.querySelector('[data-action="rebuild"]')?.addEventListener("click", onRebuild);
  app.querySelectorAll("[data-run-action]").forEach((button) => button.addEventListener("click", () => {
    const runId = button.closest("[data-run]")?.dataset.run;
    void runAction(runId, button.dataset.runAction);
  }));
}

async function onLogin(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await guarded(async () => {
    const session = await runtime.login({ email: form.get("email"), password: form.get("password") });
    user = session.user;
    await ensureProjectAndRefresh();
  }, "登录失败");
}

async function onRegister(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await guarded(async () => {
    const session = await runtime.register({
      email: form.get("email"),
      password: form.get("password"),
      display_name: form.get("display_name") || "",
      invite_code: form.get("invite_code") || "",
    });
    user = session.user;
    await ensureProjectAndRefresh();
  }, "注册失败");
}

async function onCreateProject(event) {
  event.preventDefault();
  const goal = new FormData(event.currentTarget).get("goal")?.toString().trim() || "第一集制作计划";
  const id = projectId || safeProjectId(`production-control-${Date.now()}`);
  await guarded(async () => {
    setProject(id);
    await runtime.createProject({ project_id: id, goal, project_type: "studio_episode_production", status: "in_progress" });
    await refresh();
  }, "创建项目失败");
}

async function onMission(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const constraints = form.getAll("constraint").map((item) => String(item || "").trim()).filter(Boolean);
  await mutate(
    runtime.recordProductionControlMission({
      expected_version: control.version,
      objective: String(form.get("objective") || "").trim(),
      constraints,
      created_at: new Date().toISOString(),
    }, commandKey("mission")),
    "目标已保存",
    "mission",
  );
}

async function onPlan(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fields = [...form.querySelectorAll("fieldset")].map((fieldset) => ({
    title: fieldset.querySelector('[name="title"]')?.value || "任务",
    boundary: fieldset.querySelector('[name="boundary"]')?.value || "确定性任务边界",
    capability: "deterministic.worker",
    dependency_task_ids: [],
  }));
  await mutate(
    runtime.saveProductionControlPlan({
      expected_version: control.version,
      tasks: fields,
      estimated_cost_max: 0,
      created_at: new Date().toISOString(),
    }, commandKey("plan")),
    "计划已保存",
    "plan",
  );
}

async function onApprovePlan() {
  await mutate(
    runtime.approveProductionControlPlan({ expected_version: control.version, created_at: new Date().toISOString() }, commandKey("approve")),
    "计划已批准，制作项已启动",
    "cockpit",
  );
}

async function onCommercialSample() {
  await mutateCommercial(
    runtime.createCommercialProductionSample({
      expected_version: commercial?.version || 0,
      title: "雾港异闻录",
      created_at: new Date().toISOString(),
    }, commandKey("commercial-sample")),
    "长篇生产样本已创建",
  );
}

async function onCommercialLock() {
  await mutateCommercial(
    runtime.lockCommercialProductionStageGate({
      expected_version: commercial?.version || 0,
      note: "锁定第一集 4 场 16 镜及资产身份链。",
      created_at: new Date().toISOString(),
    }, commandKey("commercial-lock")),
    "故事板范围已锁定",
  );
}

async function onCommercialRewrite() {
  await mutateCommercial(
    runtime.requestCommercialProductionLocalRewrite({
      expected_version: commercial?.version || 0,
      target_shot_id: "shot-006",
      replacement_beat: "近景：分镜本空白页只显出半个白鹤轮廓，林澈先停手确认范围锁，再允许第 6 镜返工。",
      reason: "只调整第 6 镜的悬念节奏，不改变人物、场景、道具或未选镜头。",
      created_at: new Date().toISOString(),
    }, commandKey("commercial-rewrite")),
    "第 6 镜已局部改写，未选事实保持不变",
  );
}

async function onTrialMission() {
  await mutateTrial(
    runtime.recordCreatorGoldenTrialMission({
      objective: control?.mission?.objective || "制作一个三镜头的创作者主导 AI 原生制片系统样片。",
      constraints: ["保持三镜头连续性。", "生成后等待人类审核。"],
      project_ceiling_amount: 25,
      estimated_unit_cost_amount: 3,
      currency: "USD",
      created_at: new Date().toISOString(),
    }, commandKey("trial-mission")),
    "3 镜头图像试验已冻结",
  );
}

async function onTrialApprove() {
  await mutateTrial(
    runtime.approveCreatorGoldenTrial({
      expected_event_count: trial?.event_count || 0,
      created_at: new Date().toISOString(),
    }, commandKey("trial-approve")),
    "试验计划已批准，下一步可以调度外部生成服务",
  );
}

async function onTrialDispatch() {
  await mutateTrial(
    runtime.dispatchCreatorGoldenTrialNext({
      expected_event_count: trial?.event_count || 0,
      provider_service_id: "image_relay",
      estimated_cost_amount: 0.1,
      generated_at: new Date().toISOString(),
    }, commandKey("trial-dispatch")),
    "下一镜头调度结果已记录",
  );
}

async function runAction(runId, action) {
  if (!runId || !action) return;
  await mutate(
    runtime.runProductionControlAction(runId, {
      expected_version: control.version,
      action,
      decision_option: "确认继续",
      note: action === "block" ? "等待局部证据确认。" : "",
      created_at: new Date().toISOString(),
    }, commandKey(action)),
    "操作已保存到项目记录",
    action === "writeback" ? "artifacts" : "cockpit",
  );
}

async function onRebuild() {
  await guarded(async () => {
    const result = await runtime.rebuildProductionControl();
    notice = result.ok ? "项目记录检查通过" : "项目记录检查未通过";
    await refresh(false);
  }, "项目记录检查失败");
}

async function mutate(promise, message, nextTab) {
  await guarded(async () => {
    const result = await promise;
    control = result.control;
    notice = message;
    activeTab = nextTab || activeTab;
    renderApp();
  }, "命令失败");
}

async function mutateTrial(promise, message) {
  await guarded(async () => {
    const result = await promise;
    trial = result.trial;
    notice = message;
    activeTab = "trial";
    renderApp();
  }, "图像试验命令失败");
}

async function mutateCommercial(promise, message) {
  await guarded(async () => {
    const result = await promise;
    commercial = result.production;
    notice = message;
    activeTab = "longform";
    renderApp();
  }, "长篇生产命令失败");
}

async function guarded(fn, fallback) {
  if (busy) return;
  busy = true;
  try {
    await fn();
  } catch (error) {
    notice = error?.message || fallback;
    if (control) renderApp();
    else renderProjectSetup();
  } finally {
    busy = false;
    if (control) renderApp();
  }
}

async function ensureProjectAndRefresh() {
  if (projectId) {
    await refresh();
    return;
  }
  renderProjectSetup();
}

async function refresh(render = true) {
  if (!projectId) {
    control = null;
    trial = null;
    commercial = null;
    if (render) renderProjectSetup();
    return;
  }
  runtime = createRuntimeClient(projectId);
  try {
    const [payload, trialPayload, commercialPayload] = await Promise.all([
      runtime.getProductionControl(),
      runtime.getCreatorGoldenTrial().catch(() => null),
      runtime.getCommercialProduction().catch(() => null),
    ]);
    control = payload.control;
    trial = trialPayload?.trial || null;
    commercial = commercialPayload?.production || null;
    if (render) renderApp();
  } catch (error) {
    control = null;
    trial = null;
    commercial = null;
    notice = error?.message || "";
    renderProjectSetup();
  }
}

async function init() {
  renderLoading();
  try {
    const status = await runtime.authStatus();
    if (status.auth_required && !status.authenticated) {
      renderAuth(status);
      return;
    }
    user = status.user || null;
    await ensureProjectAndRefresh();
  } catch {
    renderAuth({});
  }
}

init();
