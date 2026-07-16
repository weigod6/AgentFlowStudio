const STORAGE_KEY = "afs_ui_language";

const MESSAGES = {
  "zh-CN": {
    workspace: "工作空间",
    projects: "项目",
    episodes: "单集",
    crew: "制作团队",
    review: "审核",
    delivery: "交付",
    overview: "概览",
    todo: "待办",
    productionOverview: "制作总览",
    decisions: "待主创决策",
    crewActivity: "剧组动态",
    deliveryReadiness: "交付准备度",
    enterCanvas: "进入制作画布",
    backOverview: "返回制作总览",
    approve: "批准",
    revise: "退回修改",
    impact: "查看影响",
    retry: "重新加载",
    signOut: "退出登录",
    language: "语言",
    loading: "正在读取制作进度…",
    empty: "还没有项目",
    emptyCopy: "新建第一个项目后，制作团队的任务、决策和交付状态会集中显示在这里。",
    error: "暂时无法读取制作状态",
    recovery: "账户状态已确认，可重新加载工作空间。",
  },
  en: {
    workspace: "Workspace",
    projects: "Projects",
    episodes: "Episodes",
    crew: "Crew",
    review: "Review",
    delivery: "Delivery",
    overview: "Overview",
    todo: "Tasks",
    productionOverview: "Production overview",
    decisions: "Creator decisions",
    crewActivity: "Crew activity",
    deliveryReadiness: "Delivery readiness",
    enterCanvas: "Open production canvas",
    backOverview: "Back to overview",
    approve: "Approve",
    revise: "Request changes",
    impact: "View impact",
    retry: "Reload",
    signOut: "Sign out",
    language: "Language",
    loading: "Loading production progress…",
    empty: "No projects yet",
    emptyCopy: "Create a project to see crew work, decisions, and delivery status here.",
    error: "Production status is temporarily unavailable",
    recovery: "Your account is confirmed. Reload the workspace to continue.",
  },
};

export function currentLocale() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "zh-CN";
  } catch {
    return "zh-CN";
  }
}

export function setLocale(locale) {
  const next = locale === "en" ? "en" : "zh-CN";
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Language choice is best-effort and contains no project data.
  }
  document.documentElement.lang = next;
  return next;
}

export function message(key, locale = currentLocale()) {
  return MESSAGES[locale]?.[key] || MESSAGES["zh-CN"][key] || key;
}
