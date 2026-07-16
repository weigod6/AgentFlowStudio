import {
  STORAGE_KEY,
  executeCommand,
  loadState,
  resetState,
  saveState,
  stateSummary,
} from "./model.mjs";

const app = document.querySelector("#app");
const scenario = await fetch("./scenario.json").then((response) => {
  if (!response.ok) throw new Error("无法载入演示场景");
  return response.json();
});

if (new URLSearchParams(window.location.search).get("reset") === "1") {
  window.localStorage.removeItem(STORAGE_KEY);
  window.history.replaceState({}, "", window.location.pathname);
}

let state = loadState(window.localStorage, scenario);
let commandCounter = 0;

const escapeHtml = (value = "") => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const idempotencyKey = (type) => `prototype-${type}-${Date.now()}-${commandCounter++}`;

function dispatch(command, { persist = true, renderView = true, idempotent = true } = {}) {
  state = executeCommand(state, {
    ...command,
    idempotency_key: idempotent
      ? command.idempotency_key || idempotencyKey(command.type)
      : undefined,
  });
  if (persist) state = saveState(window.localStorage, state);
  if (renderView) render();
}

function statusLabel(run) {
  if (run.control_state === "paused") return "已暂停";
  return {
    queued: "排队中",
    running: `运行中 ${run.progress}%`,
    "waiting-human": "等待人工决策",
    retrying: "正在重试",
    blocked: "已阻断",
    completed: "已完成",
    cancelled: "已取消",
  }[run.execution_state] || run.execution_state;
}

function statusTone(run) {
  if (run.control_state === "paused") return "paused";
  return run.execution_state;
}

function icon(name) {
  const paths = {
    mission: '<path d="M12 3v18M3 12h18"/><circle cx="12" cy="12" r="5"/>',
    play: '<path d="m8 5 11 7-11 7z"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    retry: '<path d="M20 6v6h-6M4 18v-6h6"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9M5.5 15A7 7 0 0 0 18 17.5l2-2.5"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    lock: '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    alert: '<path d="M12 3 2.8 20h18.4L12 3Z"/><path d="M12 9v4M12 17h.01"/>',
    save: '<path d="M5 4h12l2 2v14H5z"/><path d="M8 4v6h8V4M8 16h8"/>',
    reset: '<path d="M4 12a8 8 0 1 0 2.3-5.7L4 8"/><path d="M4 3v5h5"/>',
    chevron: '<path d="m9 18 6-6-6-6"/>',
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.chevron}</svg>`;
}

function headerTemplate() {
  const summary = stateSummary(state);
  return `
    <header class="topbar">
      <div class="brand-group">
        <div class="brand-mark" aria-hidden="true">灯</div>
        <button class="project-switch" type="button" data-action="toggle-plan" aria-expanded="${state.ui.plan_expanded}">
          <strong>雨灯 · 第一集</strong>${icon("chevron")}
        </button>
        <span class="save-state">${icon("save")} ${state.saved_at ? "已自动保存" : "尚未保存"}</span>
      </div>
      <div class="topbar-actions">
        <span class="simulation-lock">模拟执行 · 未调用 Provider</span>
        <span class="privacy-state">${icon("lock")} 私有 · 默认不用于训练</span>
        <button class="icon-button" type="button" data-action="reset" title="重置演示" aria-label="重置演示">${icon("reset")}</button>
      </div>
      <output class="sr-only" data-testid="state-summary">${escapeHtml(JSON.stringify(summary))}</output>
    </header>`;
}

function missionTemplate() {
  const textLength = state.mission.story_text.length;
  const inProduction = state.stage === "production";
  return `
    <section class="mission-band ${state.ui.plan_expanded ? "" : "is-collapsed"}" data-region="mission">
      <div class="mission-copy">
        <div class="section-heading">
          <span class="section-icon">${icon("mission")}</span>
          <div>
            <h1>把故事交给数字剧组，从可编辑计划开始</h1>
            <p>${inProduction ? "计划已批准，三条任务正在同一事实链上推进。" : "输入故事、参考与约束；系统先给出计划，不直接开始生成。"}</p>
          </div>
        </div>
        <label class="story-field ${inProduction ? "is-compact" : ""}">
          <span>故事 / 剧本</span>
          <textarea id="story-input" ${inProduction ? "readonly" : ""} rows="${inProduction ? 3 : 8}">${escapeHtml(state.mission.story_text)}</textarea>
          <small>${textLength.toLocaleString("zh-CN")} 字 · 建议 2,000–5,000 字</small>
        </label>
        <div class="constraint-list" aria-label="参考约束">
          ${state.mission.reference_constraints.map((constraint) => `<span>${escapeHtml(constraint)}</span>`).join("")}
        </div>
        ${state.stage === "mission" ? `
          <button class="primary-button" type="button" data-action="propose-plan">
            ${icon("play")}生成可编辑计划（模拟）
          </button>` : ""}
      </div>
      <div class="mission-meta">
        <div><span>目标</span><strong>完成可恢复的 135 秒单集生产闭环</strong></div>
        <div><span>预算上限</span><strong>¥ ${state.mission.budget_cap.toLocaleString("zh-CN")} <em>模拟</em></strong></div>
        <div><span>Provider</span><strong>禁止调用 · dispatch 0</strong></div>
        <div><span>事实源</span><strong>Mission / Run / Artifact 同一项目</strong></div>
      </div>
    </section>`;
}

function planTemplate() {
  if (state.stage === "mission") return "";
  return `
    <section class="plan-band" data-region="plan">
      <div class="plan-title-row">
        <div>
          <h2>${state.plan.approved ? "已批准的执行计划" : "检查并修改执行计划"}</h2>
          <p>版本 ${state.plan.version} · 一次批准将原子启动 ${state.plan.tasks.length} 条有边界的并行任务</p>
        </div>
        ${state.plan.approved ? '<span class="approved-mark">已批准 1 次</span>' : ""}
      </div>
      <div class="plan-flow">
        ${state.plan.tasks.map((task, index) => `
          <article class="plan-step">
            <span class="step-number">${index + 1}</span>
            <div>
              <strong>${escapeHtml(task.title)}</strong>
              <label>
                <span class="sr-only">${escapeHtml(task.title)}范围</span>
                <textarea data-plan-task="${task.task_id}" ${state.plan.approved ? "readonly" : ""}>${escapeHtml(state.ui.plan_task_drafts[task.task_id] ?? task.scope)}</textarea>
              </label>
              <small>${escapeHtml(task.agent)} · 估算 ¥${task.estimated_cost}（模拟）</small>
            </div>
          </article>`).join('<span class="flow-arrow">→</span>')}
      </div>
      ${!state.plan.approved ? `
        <div class="approval-row">
          <div class="approval-note">批准前可改任务边界；批准后通过变更命令产生新计划版本。</div>
          <button class="primary-button" type="button" data-action="approve-plan">批准计划并启动 3 条任务</button>
        </div>` : ""}
    </section>`;
}

function runControls(run) {
  if (run.execution_state === "completed") {
    return `<span class="run-complete">${icon("check")} 已写回阶段成果</span>`;
  }
  if (run.execution_state === "waiting-human") {
    return `<button class="decision-button" type="button" data-action="open-decision">去处理</button>`;
  }
  if (run.control_state === "paused") {
    return `<button class="small-button" type="button" data-action="resume-run" data-run-id="${run.run_id}">${icon("play")}恢复</button>`;
  }
  return `
    <button class="icon-button" type="button" data-action="pause-run" data-run-id="${run.run_id}" title="暂停任务" aria-label="暂停任务">${icon("pause")}</button>
    <button class="icon-button" type="button" data-action="retry-run" data-run-id="${run.run_id}" title="从检查点重试" aria-label="从检查点重试">${icon("retry")}</button>`;
}

function cockpitTemplate() {
  if (!state.plan.approved) return "";
  const used = state.runs.reduce((sum, run) => sum + run.incurred_cost, 0);
  return `
    <section class="cockpit" data-region="cockpit">
      <div class="cockpit-heading">
        <div>
          <h2>生产驾驶舱 <span>${state.runs.length} 条并行任务</span></h2>
          <p>任务、负责人、成本、阻断和恢复点保持在同一项目事实链。</p>
        </div>
        <div class="budget-meter">
          <span>已用 ¥${used} / ¥${state.mission.budget_cap}（模拟）</span>
          <progress max="${state.mission.budget_cap}" value="${used}"></progress>
        </div>
      </div>
      <div class="run-list">
        ${state.runs.map((run, index) => {
          const task = state.plan.tasks.find((item) => item.task_id === run.task_id);
          return `
            <article class="run-row tone-${statusTone(run)}" data-run-id="${run.run_id}">
              <div class="run-index">${index + 1}</div>
              <div class="run-identity">
                <div class="run-title-line">
                  <strong>${escapeHtml(task.title)}</strong>
                  <span class="status-tag">${statusLabel(run)}</span>
                </div>
                <span>${escapeHtml(run.assigned_agent)}</span>
              </div>
              <div class="run-progress">
                <div class="progress-track"><span style="width:${run.progress}%"></span></div>
                <div class="progress-labels"><span>接收任务</span><span>分析</span><span>写回</span><span>检查</span></div>
              </div>
              <div class="run-metrics">
                <span>模拟成本</span><strong>¥ ${run.incurred_cost}</strong><small>估算 ¥ ${run.estimated_cost}</small>
              </div>
              <div class="run-blocker">
                ${run.execution_state === "waiting-human" ? `<span>阻断原因</span><strong>需要确认角色与场景基线</strong>` : `<span>恢复点</span><strong>${escapeHtml(run.checkpoint_ref)}</strong>`}
              </div>
              <div class="run-actions">${runControls(run)}</div>
            </article>`;
        }).join("")}
      </div>
      <p class="simulation-footnote">所有进度、成本与 Agent 时序均为模拟。未调用 Provider，也未生成真实媒体。</p>
    </section>`;
}

function shotStatus(shot) {
  if (shot.revision_state === "applied") return "已写回新版本";
  if (shot.revision_state === "proposed") return "拟修改 · 局部调整";
  if (shot.revision_state === "protected") return "保持不变";
  if (shot.status === "ready") return "结构已确认";
  if (shot.status === "needs-review") return "需要审核";
  return "模拟草案";
}

function artifactWorkspaceTemplate() {
  if (!state.plan.approved) return "";
  return `
    <section class="workspace" data-region="artifacts">
      <aside class="scene-rail" aria-label="场景与状态">
        <h2>产物</h2>
        <button class="rail-item is-active" type="button" data-action="mobile-view" data-view="artifacts"><span>全部镜头</span><strong>15</strong></button>
        <div class="rail-group">
          <h3>场景</h3>
          <span>01 雨巷 <b>5</b></span>
          <span>02 档案塔 <b>5</b></span>
          <span>03 塔顶 <b>5</b></span>
        </div>
        <div class="rail-group">
          <h3>状态</h3>
          <span class="state-proposed">拟修改 <b>1</b></span>
          <span class="state-protected">保持不变 <b>1</b></span>
          <span class="state-ready">结构已确认 <b>6</b></span>
        </div>
      </aside>
      <div class="storyboard">
        <div class="workspace-heading">
          <div><h2>分镜</h2><p>15 个镜头 · Agent 产物写回同一对象</p></div>
          <div class="view-toggle" role="group" aria-label="分镜视图">
            <button class="is-active" type="button">故事板</button>
            <button type="button" disabled title="本轮不实现可选视觉板">视觉板</button>
          </div>
        </div>
        <div class="shot-grid">
          ${state.artifacts.shots.map((shot) => {
            const isActive = shot.entity_id === state.artifacts.active_artifact_id;
            return `
              <button class="shot-card ${isActive ? "is-active" : ""} revision-${shot.revision_state}" type="button" data-action="select-shot" data-entity-id="${shot.entity_id}">
                <div class="shot-preview preview-${(shot.number % 5) + 1}">
                  <span class="shot-number">${shot.number}</span>
                  <span class="simulated-media">模拟分镜</span>
                  <span class="shot-duration">${shot.duration}</span>
                </div>
                <div class="shot-caption">
                  <strong>${escapeHtml(shot.beat)}</strong>
                  <span>${shotStatus(shot)}</span>
                </div>
              </button>`;
          }).join("")}
        </div>
      </div>
      ${inspectorTemplate()}
    </section>`;
}

function inspectorTemplate() {
  const active = state.artifacts.shots.find((shot) => shot.entity_id === state.artifacts.active_artifact_id);
  const isShot7 = active.entity_id === "shot-007";
  const decision = state.decisions.find((item) => item.decision_id === "decision-world-baseline");
  const showDecision = state.ui.inspector === "decision" && decision.status === "pending";
  if (showDecision) {
    return `
      <aside class="inspector decision-inspector" data-region="inspector">
        <div class="inspector-heading"><div><span>待决事项</span><h2>${escapeHtml(decision.title)}</h2></div><button class="icon-button" type="button" data-action="close-decision" aria-label="关闭">×</button></div>
        <p>${escapeHtml(decision.prompt)}</p>
        <div class="decision-facts">
          <span>3 个角色</span><span>3 个场景</span><span>15 个镜头将引用</span>
        </div>
        <div class="decision-callout">这个选择只解除“角色与场景设定”任务的等待，不会重启已完成的剧本拆解。</div>
        <button class="primary-button full" type="button" data-action="resolve-decision">确认并继续</button>
        <button class="secondary-button full" type="button" data-action="close-decision">返回调整计划</button>
      </aside>`;
  }
  return `
    <aside class="inspector" data-region="inspector">
      <div class="inspector-heading">
        <div><span>当前查看</span><h2>镜头 ${active.number} · ${escapeHtml(active.beat)}</h2></div>
        <span class="artifact-version">${escapeHtml(active.version_id)}</span>
      </div>
      ${isShot7 ? revisionInspectorTemplate() : `
        <div class="inspector-section"><h3>产物状态</h3><p>${shotStatus(active)}。该镜头仍保留稳定对象身份，后续候选、审核和交付都引用它的精确版本。</p></div>
        <div class="inspector-section"><h3>写回目标</h3><dl><dt>场景</dt><dd>${escapeHtml(active.scene)}</dd><dt>当前版本</dt><dd>${escapeHtml(active.version_id)}</dd><dt>Provider</dt><dd>未调用</dd></dl></div>`}
      <div class="delivery-truth">
        <div><span>审核</span><strong>${state.review.state === "needs-review" ? "待审核" : "部分阻塞"}</strong><small>${state.review.blockers.length} 项待处理</small></div>
        <div><span>交付</span><strong>阻塞中</strong><small>缺少 25 项真实素材</small></div>
      </div>
      <button class="delivery-button" type="button" disabled>继续交付（阻塞中）</button>
    </aside>`;
}

function revisionInspectorTemplate() {
  const applied = state.continuity.status === "executed";
  return `
    <div class="inspector-section revision-summary">
      <div class="section-title-line"><h3>${applied ? "局部修订已写回" : "Agent 局部修订提案"}</h3><span>${applied ? "新版本" : "待你决定"}</span></div>
      <p>修复角色连续性，只更新镜头 7。镜头 8 默认冻结，不触发无关重做。</p>
      <ul>${state.continuity.changes.map((change) => `<li>${escapeHtml(change)}</li>`).join("")}</ul>
    </div>
    <div class="inspector-section impact-preview">
      <h3>影响预览</h3>
      <div class="impact-row changed"><span>镜头 7</span><strong>${applied ? "shot-007-v2" : "将生成新版本"}</strong></div>
      <div class="impact-row protected"><span>镜头 8</span><strong>shot-008-v1 · 保持不变</strong></div>
      <small>角色/场景/镜头 identity 不变，只追加精确版本。</small>
    </div>
    ${!applied ? `
      <div class="revision-actions">
        <button class="primary-button full" type="button" data-action="apply-revision">采纳本提案（仅镜头 7）</button>
        <button class="secondary-button full" type="button" data-action="toast" data-message="已保留当前版本；未产生任何写回">拒绝并保持当前</button>
      </div>` : `
      <div class="writeback-proof">${icon("check")} 已写回 shot-007-v2；protected ref 仍为 shot-008-v1</div>`}
  `;
}

function mobileTabsTemplate() {
  if (!state.plan.approved) return "";
  const pending = state.decisions.filter((item) => item.status === "pending").length;
  return `
    <nav class="mobile-tabs" aria-label="移动端视图">
      <button class="${state.ui.mobile_view === "tasks" ? "is-active" : ""}" type="button" data-action="mobile-view" data-view="tasks">任务 <span>${state.runs.length}</span></button>
      <button class="${state.ui.mobile_view === "artifacts" ? "is-active" : ""}" type="button" data-action="mobile-view" data-view="artifacts">产物 <span>15</span></button>
      <button class="${state.ui.mobile_view === "decisions" ? "is-active" : ""}" type="button" data-action="mobile-view" data-view="decisions">待决 <span>${pending}</span></button>
    </nav>`;
}

function render() {
  document.documentElement.dataset.mobileView = state.ui.mobile_view;
  app.innerHTML = `
    <div class="app-shell">
      ${headerTemplate()}
      <main>
        ${missionTemplate()}
        ${planTemplate()}
        ${mobileTabsTemplate()}
        ${cockpitTemplate()}
        ${artifactWorkspaceTemplate()}
      </main>
      ${state.ui.toast ? `<div class="toast" role="status">${escapeHtml(state.ui.toast)}<button type="button" data-action="clear-toast" aria-label="关闭">×</button></div>` : ""}
    </div>`;
  bindEvents();
}

function bindEvents() {
  app.querySelector("#story-input")?.addEventListener("change", (event) => {
    if (state.stage === "production") return;
    dispatch({ type: "mission.update", story_text: event.target.value });
  });
  app.querySelectorAll("[data-plan-task]").forEach((field) => {
    field.addEventListener("input", (event) => {
      dispatch({
        type: "ui.plan-draft.update",
        task_id: field.dataset.planTask,
        scope: event.target.value,
      }, { renderView: false, idempotent: false });
    });
  });
  app.querySelectorAll("[data-action]").forEach((control) => {
    control.addEventListener("click", () => handleAction(control));
  });
}

function handleAction(control) {
  const action = control.dataset.action;
  if (action === "propose-plan") dispatch({ type: "plan.propose" });
  if (action === "approve-plan") dispatch({
    type: "plan.approve",
    idempotency_key: `approve-${state.plan.plan_id}`,
    task_updates: Object.entries(state.ui.plan_task_drafts).map(([task_id, scope]) => ({ task_id, scope })),
  });
  if (action === "pause-run") dispatch({ type: "run.pause", run_id: control.dataset.runId });
  if (action === "resume-run") dispatch({ type: "run.resume", run_id: control.dataset.runId });
  if (action === "retry-run") dispatch({ type: "run.retry", run_id: control.dataset.runId });
  if (action === "open-decision") dispatch({ type: "ui.inspector", inspector: "decision" });
  if (action === "close-decision") dispatch({ type: "ui.inspector", inspector: "revision" });
  if (action === "resolve-decision") dispatch({ type: "decision.resolve", decision_id: "decision-world-baseline" });
  if (action === "select-shot") dispatch({ type: "artifact.select", entity_id: control.dataset.entityId });
  if (action === "apply-revision") dispatch({ type: "revision.apply", proposal_id: state.continuity.proposal_id, idempotency_key: `apply-${state.continuity.proposal_id}` });
  if (action === "mobile-view") dispatch({ type: "ui.mobile-view", view: control.dataset.view });
  if (action === "toast") dispatch({ type: "ui.toast", message: control.dataset.message });
  if (action === "clear-toast") dispatch({ type: "ui.toast", message: "" });
  if (action === "toggle-plan") {
    state.ui.plan_expanded = !state.ui.plan_expanded;
    state = saveState(window.localStorage, state);
    render();
  }
  if (action === "reset" && window.confirm("重置演示会清除本地恢复状态。继续吗？")) {
    state = resetState(window.localStorage, scenario);
    render();
  }
}

window.__AFS_PROTOTYPE__ = {
  getState: () => JSON.parse(JSON.stringify(state)),
  dispatch,
  reset: () => {
    state = resetState(window.localStorage, scenario);
    render();
  },
};

render();
