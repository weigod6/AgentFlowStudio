import { icon } from "./icons.js";
import { el } from "./overlay.js";

export function renderTopbar(options) {
  const topbar = document.getElementById("topbar");
  if (!topbar) return;
  const {
    state,
    store,
    runtime,
    projectSummaries,
    projectOptions,
    hiddenProjectCount,
    showAllProjects,
    onToggleProjectFilter,
    onSwitchProject,
    onCreateProject,
    onOpenHome,
    onBeforeSiteHome,
    authUser,
    runtimeSurfaceStatus,
    onSignOut,
    onRetrySave,
  } = options;
  const signature = [
    state.meta.projectId,
    state.ui.drawerOpen,
    state.meta.projectName,
    state.meta.canvasName,
    state.ui.saveState,
    state.ui.saveMessage,
    runtimeSurfaceStatus?.state || "runtime-unknown",
    runtimeSurfaceStatus?.label || "",
    runtimeSurfaceStatus?.authLabel || "",
    runtimeSurfaceStatus?.providerGateLabel || "",
    showAllProjects ? "all-projects" : "studio-projects",
    authUser?.user_id || "anonymous",
    projectSummaries.map((item) => item.project_id).join(","),
  ].join("|");
  if (topbar.dataset.signature === signature) return;
  topbar.dataset.signature = signature;
  topbar.classList.toggle("drawer-open", state.ui.drawerOpen);
  topbar.classList.toggle("save-attention", isSaveAttentionState(state.ui.saveState));
  topbar.replaceChildren();

  if (!state.ui.drawerOpen) {
    renderCompactTopbar(topbar, { state, store, runtime, projectOptions, hiddenProjectCount, showAllProjects, onToggleProjectFilter, onSwitchProject, onCreateProject, onOpenHome, onBeforeSiteHome });
  } else {
    topbar.appendChild(siteHomeLink(onBeforeSiteHome));
    topbar.appendChild(studioHomeButton(onOpenHome));
    topbar.appendChild(productionControlLink(runtime?.projectId));
    appendProjectControls(topbar, { runtime, projectOptions, hiddenProjectCount, showAllProjects, onToggleProjectFilter, onSwitchProject, onCreateProject });
  }

  topbar.appendChild(el("div", "topbar-spacer"));
  const right = el("div", "topbar-right");
  if (runtimeSurfaceStatus) right.appendChild(runtimeStatusBadge(runtimeSurfaceStatus));
  if (authUser) right.appendChild(accountButton(authUser, onSignOut));
  right.appendChild(savePill(state, onRetrySave));
  topbar.appendChild(right);
}

function renderCompactTopbar(topbar, options) {
  const { state, store, runtime, projectOptions, onOpenHome, onBeforeSiteHome } = options;
  const openDrawer = el("button", "icon-btn drawer-restore");
  openDrawer.innerHTML = icon("panel", 15);
  openDrawer.title = "展开侧栏";
  openDrawer.addEventListener("click", () => store.set((s) => { s.ui.drawerOpen = true; }, { history: false, persist: false }));
  topbar.appendChild(openDrawer);

  topbar.appendChild(el("div", "topbar-logo", "AFS"));
  topbar.appendChild(siteHomeLink(onBeforeSiteHome));
  topbar.appendChild(studioHomeButton(onOpenHome));
  topbar.appendChild(productionControlLink(runtime?.projectId));

  const title = el("div", "topbar-title compact-project");
  title.appendChild(el("span", "proj-name", state.meta.projectName));
  title.appendChild(el("span", "divider"));
  title.appendChild(el("span", "canvas-name", `${state.meta.canvasName} ▾`));
  topbar.appendChild(title);

  appendProjectControls(topbar, { ...options, projectOptions });
}

function siteHomeLink(onBeforeSiteHome) {
  const home = el("a", "icon-btn site-home-btn");
  home.href = "/site/";
  home.innerHTML = `${icon("globe", 14)}<span>首页</span>`;
  home.title = "返回网站首页";
  home.addEventListener("click", async (event) => {
    if (!onBeforeSiteHome) return;
    event.preventDefault();
    await onBeforeSiteHome();
    window.location.href = home.href;
  });
  return home;
}

function studioHomeButton(onOpenHome) {
  const home = el("button", "icon-btn studio-home-btn");
  home.innerHTML = `${icon("grid", 14)}<span>制作总览</span>`;
  home.title = "返回制作总览";
  home.setAttribute("aria-label", "返回制作总览");
  home.addEventListener("click", onOpenHome);
  return home;
}

function productionControlLink(projectId) {
  const href = projectId
    ? `/studio/production-control/?project=${encodeURIComponent(projectId)}`
    : "/studio/production-control/";
  const link = el("a", "icon-btn studio-home-btn production-control-btn");
  link.href = href;
  link.innerHTML = `${icon("bolt", 14)}<span>制片工作台</span>`;
  link.title = "打开制片工作台";
  link.setAttribute("aria-label", "打开制片工作台");
  return link;
}

function accountButton(user, onSignOut) {
  const label = user.display_name || user.email || "账号";
  const button = el("button", "icon-btn account-btn");
  button.innerHTML = `${icon("user", 14)}<span>${label}</span>`;
  button.title = "退出登录";
  button.addEventListener("click", () => onSignOut?.());
  return button;
}

function runtimeStatusBadge(status) {
  const state = safeRuntimeStatusState(status?.state);
  const label = String(status?.label || "正在连接创作服务");
  const badge = el("span", `runtime-status-badge ${state}`);
  badge.dataset.state = state;
  badge.appendChild(el("span", "runtime-status-dot"));
  badge.appendChild(el("span", "runtime-status-label", label));
  badge.title = state === "ready"
    ? "创作服务连接正常"
    : state === "checking"
      ? "正在确认创作服务状态"
      : "创作服务连接异常，请稍后重试";
  return badge;
}

function safeRuntimeStatusState(value) {
  const state = String(value || "").trim().toLowerCase();
  return ["checking", "ready", "attention", "unavailable"].includes(state) ? state : "attention";
}

function appendProjectControls(topbar, options) {
  const { runtime, projectOptions, hiddenProjectCount, showAllProjects, onToggleProjectFilter, onSwitchProject, onCreateProject } = options;
  const projectSelect = el("select", "project-select");
  projectSelect.title = "切换项目";
  for (const item of projectOptions) {
    const option = document.createElement("option");
    option.value = item.project_id;
    option.textContent = projectLabel(item);
    option.selected = item.project_id === runtime.projectId;
    projectSelect.appendChild(option);
  }
  projectSelect.addEventListener("change", () => onSwitchProject(projectSelect.value));
  topbar.appendChild(projectSelect);

  const newProject = el("button", "icon-btn");
  newProject.innerHTML = icon("plus", 14);
  newProject.title = "新建项目";
  newProject.addEventListener("click", onCreateProject);
  topbar.appendChild(newProject);
  appendProjectFilterToggle(topbar, { hiddenProjectCount, showAllProjects, onToggleProjectFilter });
}

function appendProjectFilterToggle(topbar, { hiddenProjectCount, showAllProjects, onToggleProjectFilter }) {
  if (!hiddenProjectCount && !showAllProjects) return;
  const toggle = el("button", "icon-btn project-noise-toggle");
  toggle.innerHTML = icon("more", 14);
  toggle.title = showAllProjects ? "收起测试项目" : `显示全部项目（隐藏 ${hiddenProjectCount} 个测试项目）`;
  toggle.addEventListener("click", onToggleProjectFilter);
  topbar.appendChild(toggle);
}

function projectLabel(item) {
  return item.studio_state_meta?.projectName || item.goal || item.project_id;
}

function savePill(state, onRetrySave) {
  const saveState = state.ui.saveState || "本地暂存";
  const retryable = isRetryableSaveState(saveState) && typeof onRetrySave === "function";
  const save = retryable
    ? el("button", `save-pill save-pill-button ${saveClass(saveState)}`, `${saveState} · 重试`)
    : el("span", `save-pill ${saveClass(saveState)}`, saveState);
  save.title = [
    state.ui.saveMessage || "",
    retryable ? "点击重试保存" : "",
  ].filter(Boolean).join(" ");
  if (retryable) {
    save.type = "button";
    save.setAttribute("aria-label", `重试保存：${state.ui.saveMessage || saveState}`);
    save.addEventListener("click", () => onRetrySave?.());
  }
  return save;
}

function isRetryableSaveState(state) {
  return state === "保存失败" || state === "需要登录";
}

function isSaveAttentionState(state) {
  return isRetryableSaveState(state) || state === "保存冲突";
}

function saveClass(state) {
  if (state === "已保存") return "saved";
  if (state === "保存中" || state === "同步中") return "saving";
  if (state === "保存失败" || state === "需要登录") return "failed";
  if (state === "保存冲突") return "failed conflict";
  return "local";
}
