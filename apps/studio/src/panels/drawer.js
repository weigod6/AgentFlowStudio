import { el } from "../overlay.js";
import { renderAssetDrawer } from "./drawer-assets.js";
import { renderJobCenter } from "./job-center.js";
import { renderProjectNavigator } from "./project-navigator.js";

const DRAWER_TABS = [
  ["canvas", "画布"],
  ["assets", "素材"],
  ["jobs", "进度"],
  ["history", "作品"],
];
const DRAWER_WIDTH_STORAGE_KEY = "afs_studio_drawer_width";
const DRAWER_MIN_WIDTH = 168;
const DRAWER_MAX_WIDTH = 420;

export function renderDrawer(state, store, runtime) {
  const drawer = document.getElementById("drawer");
  applyDrawerWidth(readDrawerWidth(state));
  drawer.classList.toggle("collapsed", !state.ui.drawerOpen);
  const signature = drawerSignature(state);
  if (drawer.dataset.signature === signature) return;
  drawer.dataset.signature = signature;
  drawer.replaceChildren();

  drawer.appendChild(drawerHeader());
  drawer.appendChild(projectLabel(state));
  drawer.appendChild(domainCrewEntry());
  drawer.appendChild(tabBar(state, store));

  const body = el("div", "drawer-body");
  if (state.ui.drawerTab === "canvas") renderProjectNavigator(state, store, body);
  else if (state.ui.drawerTab === "assets") renderAssetDrawer(state, store, runtime, drawer, body);
  else if (state.ui.drawerTab === "jobs") renderJobCenter(state, store, body, "jobs");
  else renderJobCenter(state, store, body, "history");
  drawer.appendChild(body);
  drawer.appendChild(drawerFooter(state, store));
  drawer.appendChild(drawerResizeHandle(state, store));
}

function domainCrewEntry() {
  const button = el("button", "domain-crew-entry");
  button.type = "button";
  button.dataset.action = "open-domain-crew";
  button.append(el("span", "", "制作团队"), el("small", "", "9 个专业岗位"));
  button.addEventListener("click", () => window.dispatchEvent(new CustomEvent("afs:studio-open-domain-crew")));
  return button;
}

function drawerHeader() {
  const head = el("div", "drawer-head");
  head.appendChild(el("div", "topbar-logo", "▣"));
  return head;
}

function projectLabel(state) {
  const proj = el("div", "drawer-project");
  proj.appendChild(el("span", "proj-name", state.meta.projectName));
  proj.appendChild(el("span", "", "|"));
  proj.appendChild(el("span", "", `${state.meta.canvasName} ▾`));
  return proj;
}

function tabBar(state, store) {
  const tabs = el("div", "drawer-tabs");
  for (const [id, label] of DRAWER_TABS) {
    const tab = el("button", `drawer-tab${state.ui.drawerTab === id ? " active" : ""}`, label);
    tab.addEventListener("click", () => store.set((s) => { s.ui.drawerTab = id; }));
    tabs.appendChild(tab);
  }
  return tabs;
}

function drawerFooter(state, store) {
  const foot = el("div", "drawer-foot");
  const collapse = el("button", "icon-btn", "↤");
  collapse.title = "收起节点侧栏";
  collapse.addEventListener("click", () => store.set((s) => { s.ui.drawerOpen = false; }));
  foot.appendChild(collapse);
  foot.appendChild(el("span", "", `共 ${state.order.length} 节点`));
  return foot;
}

function drawerSignature(state) {
  return [
    state.meta.projectId, state.meta.projectName, state.meta.canvasName,
    state.ui.drawerOpen, state.ui.drawerWidth, state.ui.drawerTab,
    state.ui.drawerSearch, state.ui.assetLifecycleFilter, state.ui.navigatorSearch,
    state.order.join(","),
    state.selection.nodeIds.join(","),
    Object.keys(state.groups).join(","),
    state.assets.length,
    ...Object.values(state.nodes).map((n) => n.title),
    ...state.assets.map((asset) => `${asset.title}:${asset.safe_summary}:${asset.source_node_id}:${asset.asset_id || asset.visual_asset_id || asset.id}:${asset.status || asset.asset_status || ""}:${asset.runtime_status || ""}`),
    ...Object.values(state.nodes).flatMap((node) => (node.params?.visualAssets || []).map((asset) => `${node.id}:${asset.asset_id}:${asset.status || ""}:${asset.runtime_status || ""}:${asset.disabled_reason || ""}`)),
  ].join("|");
}

function drawerResizeHandle(state, store) {
  const handle = el("div", "drawer-resize-handle");
  handle.title = "拖动调整侧栏宽度";
  handle.addEventListener("pointerdown", (event) => bindDrawerResize(event, state, store));
  return handle;
}

function bindDrawerResize(event, state, store) {
  if (!state.ui.drawerOpen) return;
  event.preventDefault();
  const handle = event.currentTarget;
  handle.setPointerCapture?.(event.pointerId);
  document.body.classList.add("is-resizing-drawer");
  let width = readDrawerWidth(state);
  const onMove = (moveEvent) => {
    width = clampDrawerWidth(moveEvent.clientX);
    applyDrawerWidth(width);
  };
  const onEnd = () => {
    document.body.classList.remove("is-resizing-drawer");
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onEnd);
    window.removeEventListener("pointercancel", onEnd);
    storeDrawerWidth(width);
    store.set((s) => {
      s.ui.drawerWidth = width;
    }, { history: false, persist: false });
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onEnd, { once: true });
  window.addEventListener("pointercancel", onEnd, { once: true });
}

function readDrawerWidth(state) {
  let stored = 0;
  try {
    stored = Number(window.localStorage?.getItem(DRAWER_WIDTH_STORAGE_KEY) || 0);
  } catch {
    stored = 0;
  }
  const value = Number(stored || state.ui.drawerWidth || 196);
  return clampDrawerWidth(value);
}

function storeDrawerWidth(width) {
  try {
    window.localStorage?.setItem(DRAWER_WIDTH_STORAGE_KEY, String(clampDrawerWidth(width)));
  } catch {
    // Browser storage can be blocked; the live CSS width still works for this session.
  }
}

function applyDrawerWidth(width) {
  document.documentElement.style.setProperty("--drawer-w", `${clampDrawerWidth(width)}px`);
}

function clampDrawerWidth(value) {
  return Math.max(DRAWER_MIN_WIDTH, Math.min(DRAWER_MAX_WIDTH, Math.round(Number(value) || 196)));
}
