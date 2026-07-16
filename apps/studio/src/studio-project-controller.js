import { createRuntimeClient } from "./runtime-client.js";
import { syncRuntimeAssets } from "./runtime-asset-sync.js";
import {
  persistActiveProject,
  recentProjectIds,
  rememberProject,
  safeProjectId,
  syncProjectUrl,
} from "./studio-project-session.js";
import { el, showModal } from "./overlay.js";
import { icon } from "./icons.js";
import { formatRuntimeError } from "./runtime-error-utils.js";
import {
  createProjectWithRetry,
  isTestProject,
  reportProjectAccessRecovery,
  reportProjectCreateClientError,
  reportProjectDeleteClientError,
} from "./studio-project-runtime-ops.js";

const EMPTY_PROJECT_ID = "studio-empty";

export function createProjectController({ store, getRuntime, setRuntime, render, onProjectReady }) {
  let projectSummaries = [];
  let showAllProjects = false;
  let currentAuthUser = null;
  let projectAccessRecovery = null;

  async function applyProject(projectId, runtimeClient, { projectName, syncAssets = true, recoverOnDenied = true } = {}) {
    const safe = safeProjectId(projectId) || "studio-local-001";
    persistActiveProject(safe);
    rememberProject(safe);
    syncProjectUrl(safe);
    setRuntime(runtimeClient);
    const switchResult = await store.switchProject(safe, runtimeClient);
    if (isProjectAccessDeniedError(switchResult?.error)) {
      if (recoverOnDenied) {
        await recoverProjectAccessDenied(switchResult.error);
      } else {
        await showEmptyProjectState();
      }
      return;
    }
    if (projectName) {
      store.set((s) => {
        s.meta.projectName = projectName;
        if (!s.meta.canvasName) s.meta.canvasName = "画布 1";
      }, { history: false });
    }
    await store.flushRuntimeSave();
    if (syncAssets) {
      await syncRuntimeAssets(store, runtimeClient);
    }
    await onProjectReady?.(runtimeClient);
    await refreshProjectSummaries();
  }

  async function refreshProjectSummaries() {
    try {
      const payload = await getRuntime().listProjects();
      projectSummaries = Array.isArray(payload?.projects) ? payload.projects : [];
      syncCurrentProjectMetaFromSummaries();
    } catch {
      projectSummaries = [];
    }
    render();
  }

  async function ensureAccessibleStartupProject() {
    await refreshProjectSummaries();
    const runtime = getRuntime();
    if (projectSummaries.some((item) => item.project_id === runtime.projectId)) {
      return;
    }
    if (projectSummaries.length) {
      await switchProject(projectSummaries[0].project_id);
      return;
    }
    await showEmptyProjectState();
  }

  async function recoverProjectAccessDenied(error = null) {
    if (projectAccessRecovery) return projectAccessRecovery;
    projectAccessRecovery = (async () => {
      await refreshProjectSummaries();
      const runtime = getRuntime();
      const currentId = runtime.projectId || store.get().meta.projectId;
      if (projectSummaries.some((item) => item.project_id === currentId)) {
        return false;
      }
      const next = projectSummaries.find((item) => item.project_id);
      if (next?.project_id) {
        await applyProject(next.project_id, createRuntimeClient(next.project_id), {
          projectName: projectDisplayName(next),
          recoverOnDenied: false,
        });
      } else {
        await showEmptyProjectState();
      }
      reportProjectAccessRecovery(runtime, error, currentId, next?.project_id || EMPTY_PROJECT_ID, safeError);
      return true;
    })().finally(() => {
      projectAccessRecovery = null;
    });
    return projectAccessRecovery;
  }

  async function switchProject(projectId) {
    const safe = safeProjectId(projectId) || "studio-local-001";
    if (!safe || safe === getRuntime().projectId) {
      return;
    }
    const nextRuntime = createRuntimeClient(safe);
    await applyProject(safe, nextRuntime);
  }

  async function createNewProject() {
    try {
      const name = await requestProjectName(projectSummaries);
      if (name === null) return false;
      const suffix = Math.random().toString(36).slice(2, 8);
      const projectId = safeProjectId(`studio-${Date.now()}-${suffix}`);
      const projectName = name.trim() || "AFS Studio project";
      const nextRuntime = createRuntimeClient(projectId);
      const created = await createProjectWithRetry(nextRuntime, {
        project_id: projectId,
        project_type: "studio_creator_authoring",
        goal: projectName,
      });
      await applyProject(projectId, nextRuntime, { projectName, syncAssets: false });
      const creatorEntry = created?.episode_bootstrap?.workspace_entry?.href;
      if (creatorEntry) {
        window.location.assign(creatorEntry);
      }
      return true;
    } catch (error) {
      reportProjectCreateClientError(getRuntime(), error, safeError);
      showProjectCreateError(error);
      return false;
    }
  }

  async function deleteProject(project) {
    const projectId = safeProjectId(project?.project_id || project);
    if (!projectId) return;
    const label = projectDisplayName(resolveProjectSummary(projectId, project)) || projectId;
    const confirmed = await requestProjectDeleteConfirmation(label);
    if (!confirmed) return;
    try {
      await getRuntime().deleteProject(projectId);
      const currentId = getRuntime().projectId || store.get().meta.projectId;
      await refreshProjectSummaries();
      if (projectId === currentId) {
        const next = projectSummaries.find((item) => item.project_id && item.project_id !== projectId);
        if (next?.project_id) {
          await switchProject(next.project_id);
        } else {
          await showEmptyProjectState();
        }
      }
      showProjectDeleteSuccess(label, projectSummaries.length);
    } catch (error) {
      reportProjectDeleteClientError(getRuntime(), error, projectId, safeError);
      showProjectDeleteError(error);
    }
  }

  function projectOptions(state) {
    const runtime = getRuntime();
    const currentId = runtime.projectId || state.meta.projectId;
    const current = {
      project_id: currentId,
      studio_state_meta: {
        projectName: state.meta.projectName,
        canvasName: state.meta.canvasName,
      },
    };
    const known = projectSummaries.length ? [...projectSummaries] : [];
    if (!projectSummaries.length && currentId && currentId !== EMPTY_PROJECT_ID && !known.some((item) => item.project_id === currentId)) {
      known.unshift(current);
    }
    const recent = recentProjectIds();
    const normal = known.filter((item) => !isTestProject(item)).slice(0, 5);
    const normalIds = new Set(normal.map((item) => item.project_id));
    return showAllProjects ? known : known.filter((item) =>
      item.project_id === currentId || recent.includes(item.project_id) || normalIds.has(item.project_id));
  }

  function hiddenProjectCount(state) {
    const runtime = getRuntime();
    const currentId = state.meta.projectId || runtime.projectId;
    if (showAllProjects) return 0;
    const visibleIds = new Set(projectOptions(state).map((item) => item.project_id));
    return projectSummaries.filter((item) => item.project_id !== currentId && !visibleIds.has(item.project_id)).length;
  }

  return {
    get summaries() {
      return projectSummaries;
    },
    get showAllProjects() {
      return showAllProjects;
    },
    get authUser() {
      return currentAuthUser;
    },
    rememberStartupProject(projectId) {
      rememberProject(projectId || getRuntime().projectId);
    },
    setAuthUser(user) {
      currentAuthUser = user || null;
    },
    toggleProjectFilter() {
      showAllProjects = !showAllProjects;
      render();
    },
    refreshProjectSummaries,
    ensureAccessibleStartupProject,
    recoverProjectAccessDenied,
    switchProject,
    createNewProject,
    deleteProject,
    projectOptions,
    hiddenProjectCount,
  };

  function syncCurrentProjectMetaFromSummaries() {
    const runtime = getRuntime();
    const currentId = runtime.projectId || store.get().meta.projectId;
    const found = projectSummaries.find((item) => item.project_id === currentId);
    const meta = found?.studio_state_meta || {};
    const projectName = String(meta.projectName || "").trim();
    const canvasName = String(meta.canvasName || "").trim();
    if (!projectName && !canvasName) return;
    store.set((s) => {
      if (projectName) s.meta.projectName = projectName;
      if (canvasName) s.meta.canvasName = canvasName;
    }, { history: false, persist: false });
  }

  function resolveProjectSummary(projectId, project) {
    if (project && typeof project === "object") return project;
    return projectSummaries.find((item) => item.project_id === projectId) || { project_id: projectId };
  }

  async function showEmptyProjectState() {
    const nextRuntime = emptyProjectRuntimeClient();
    persistActiveProject(EMPTY_PROJECT_ID);
    syncProjectUrl(EMPTY_PROJECT_ID);
    setRuntime(nextRuntime);
    await store.switchProject(EMPTY_PROJECT_ID, nextRuntime);
    store.set((s) => {
      s.meta.projectName = "暂无项目";
      s.meta.canvasName = "请新建项目";
      s.nodes = {};
      s.edges = {};
      s.order = [];
      s.assets = [];
      s.selection = { nodeIds: [], edgeId: null };
      s.ui.saveState = "本地暂存";
      s.ui.saveMessage = "当前没有项目，请新建项目后开始创作。";
    }, { history: false, persist: false });
    render();
  }
}

function emptyProjectRuntimeClient() {
  const runtime = createRuntimeClient(EMPTY_PROJECT_ID);
  return {
    ...runtime,
    loadStudioState: null,
    saveStudioState: null,
  };
}

function requestProjectName(existingProjects = []) {
  return new Promise((resolve) => {
    const modal = el("div", "modal compact project-create-modal");
    const head = el("div", "modal-head");
    head.appendChild(el("strong", "", "新建视频项目"));
    const closeBtn = el("button", "modal-close");
    closeBtn.innerHTML = icon("x", 15);
    head.appendChild(el("span", "head-spacer"));
    head.appendChild(closeBtn);

    const body = el("div", "modal-body project-create-body");
    const field = el("label", "modal-field");
    field.appendChild(el("span", "", "项目名称"));
    const input = document.createElement("input");
    input.type = "text";
    input.value = uniqueProjectName("AFS 内测项目", existingProjects);
    input.maxLength = 80;
    field.appendChild(input);
    const error = el("div", "modal-error");
    error.hidden = true;
    body.append(field, error);

    const actions = el("div", "modal-actions");
    const cancel = el("button", "ghost-btn", "取消");
    const confirm = el("button", "primary-btn", "创建并切换");
    actions.append(cancel, confirm);

    modal.append(head, body, actions);
    let settled = false;
    const close = showModal(modal, { onClose: () => { if (!settled) resolve(null); } });
    const finish = () => {
      if (settled) return;
      const name = input.value.trim();
      if (!name) {
        error.textContent = "请先填写项目名称。";
        error.hidden = false;
        input.focus();
        return;
      }
      if (isDuplicateProjectName(name, existingProjects)) {
        error.textContent = `项目名称“${name}”已存在，请换一个名称。`;
        error.hidden = false;
        input.focus();
        input.select();
        return;
      }
      settled = true;
      close();
      resolve(name);
    };

    confirm.addEventListener("click", finish);
    cancel.addEventListener("click", () => {
      if (settled) return;
      settled = true;
      close();
      resolve(null);
    });
    closeBtn.addEventListener("click", () => {
      if (settled) return;
      settled = true;
      close();
      resolve(null);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        finish();
      }
      if (event.key === "Escape") {
        if (settled) return;
        settled = true;
        close();
        resolve(null);
      }
    });
    requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  });
}

function isDuplicateProjectName(name, projects) {
  const normalized = normalizeProjectName(name);
  if (!normalized) return false;
  return (Array.isArray(projects) ? projects : []).some((project) => normalizeProjectName(projectDisplayName(project)) === normalized);
}

function uniqueProjectName(baseName, projects) {
  const base = String(baseName || "AFS 内测项目").trim() || "AFS 内测项目";
  if (!isDuplicateProjectName(base, projects)) return base;
  for (let index = 2; index < 1000; index += 1) {
    const candidate = `${base} ${index}`;
    if (!isDuplicateProjectName(candidate, projects)) return candidate;
  }
  return `${base} ${Date.now()}`;
}

function normalizeProjectName(name) {
  return String(name || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function projectDisplayName(project) {
  return project?.studio_state_meta?.projectName || project?.goal || project?.project_id || "";
}

function isProjectAccessDeniedError(error) {
  if (!error) return false;
  const code = String(error.errorCode || error.payload?.error || error.payload?.detail?.error || "").trim();
  if (code === "project_access_denied") return true;
  const status = Number(error.status || 0);
  const message = error instanceof Error ? error.message : String(error || "");
  return status === 403 && /project[_ ]access[_ ]denied|没有访问该项目的权限/i.test(message);
}

function showProjectCreateError(error) {
  const message = safeError(error);
  const modal = el("div", "modal compact project-create-modal");
  const head = el("div", "modal-head");
  head.appendChild(el("strong", "", "项目创建失败"));
  const closeBtn = el("button", "modal-close");
  closeBtn.innerHTML = icon("x", 15);
  head.appendChild(el("span", "head-spacer"));
  head.appendChild(closeBtn);
  const body = el("div", "modal-body project-create-body");
  body.appendChild(el("div", "modal-error", `Runtime 没有完成项目创建：${message}`));
  const actions = el("div", "modal-actions");
  const ok = el("button", "primary-btn", "知道了");
  actions.appendChild(ok);
  modal.append(head, body, actions);
  const close = showModal(modal);
  closeBtn.addEventListener("click", close);
  ok.addEventListener("click", close);
}

function requestProjectDeleteConfirmation(projectName) {
  return new Promise((resolve) => {
    const modal = el("div", "modal compact project-delete-modal");
    const head = el("div", "modal-head");
    head.appendChild(el("strong", "", "删除项目"));
    const closeBtn = el("button", "modal-close");
    closeBtn.innerHTML = icon("x", 15);
    head.appendChild(el("span", "head-spacer"));
    head.appendChild(closeBtn);

    const body = el("div", "modal-body project-delete-body");
    body.appendChild(el("p", "", `确定要删除项目“${projectName}”吗？`));
    body.appendChild(el("p", "", "删除后该项目将从项目列表中移除。"));
    body.appendChild(el("p", "modal-error", "此操作可能会删除该项目的画布状态、素材引用、任务记录和生成历史。"));

    const actions = el("div", "modal-actions");
    const cancel = el("button", "ghost-btn", "取消");
    const confirm = el("button", "primary-btn danger", "确认删除");
    actions.append(cancel, confirm);
    modal.append(head, body, actions);

    let settled = false;
    const close = showModal(modal, { onClose: () => { if (!settled) resolve(false); } });
    const finish = (value) => {
      if (settled) return;
      settled = true;
      close();
      resolve(value);
    };
    cancel.addEventListener("click", () => finish(false));
    closeBtn.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
  });
}

function showProjectDeleteError(error) {
  const message = safeError(error);
  const modal = el("div", "modal compact project-delete-modal");
  const head = el("div", "modal-head");
  head.appendChild(el("strong", "", "项目删除失败"));
  const closeBtn = el("button", "modal-close");
  closeBtn.innerHTML = icon("x", 15);
  head.appendChild(el("span", "head-spacer"));
  head.appendChild(closeBtn);
  const body = el("div", "modal-body project-delete-body");
  body.appendChild(el("div", "modal-error", `Runtime 没有完成项目删除：${message}`));
  const actions = el("div", "modal-actions");
  const ok = el("button", "primary-btn", "知道了");
  actions.appendChild(ok);
  modal.append(head, body, actions);
  const close = showModal(modal);
  closeBtn.addEventListener("click", close);
  ok.addEventListener("click", close);
}

function showProjectDeleteSuccess(projectName, remainingCount = 0) {
  const modal = el("div", "modal compact project-delete-modal");
  const head = el("div", "modal-head");
  head.appendChild(el("strong", "", "项目删除成功"));
  const closeBtn = el("button", "modal-close");
  closeBtn.innerHTML = icon("x", 15);
  head.appendChild(el("span", "head-spacer"));
  head.appendChild(closeBtn);
  const body = el("div", "modal-body project-delete-body");
  body.appendChild(el("p", "", `项目“${projectName}”已从项目列表中移除。`));
  body.appendChild(el("p", "", remainingCount > 0
    ? "如果删除的是当前项目，系统已自动切换到其他可用项目。"
    : "当前已经没有项目，请点击“新建项目”后继续创作。"));
  const actions = el("div", "modal-actions");
  const ok = el("button", "primary-btn", "知道了");
  actions.appendChild(ok);
  modal.append(head, body, actions);
  const close = showModal(modal);
  closeBtn.addEventListener("click", close);
  ok.addEventListener("click", close);
}

function safeError(error) {
  const formatted = formatRuntimeError(error, "未知错误");
  if (/network connection interrupted|Failed to fetch|Gateway timeout/i.test(formatted)) {
    return "Runtime 连接短暂中断，请刷新项目列表后再重试。";
  }
  return formatted;
}
