import { el, showModal } from "../overlay.js";

const ROLE_LABELS = {
  screenwriter: "编剧",
  storyboard: "分镜",
  art: "美术",
  director: "导演",
  continuity: "连续性",
  qa: "质量检查",
  audio: "音频",
  edit: "剪辑",
  export: "交付",
};

const ACTION_LABELS = {
  "script.write": "撰写剧本",
  "storyboard.compose": "编排分镜",
  "art.create": "制作美术",
  "direction.review": "导演审看",
  "continuity.review": "连续性复核",
  "quality.review": "质量复核",
  "audio.produce": "制作音频",
  "edit.assemble": "剪辑合成",
  "export.deliver": "导出交付",
};

let closeActivePanel = null;

export function openDomainCrewPanel(controller) {
  closeActivePanel?.();
  const modal = el("section", "modal domain-crew-modal");
  modal.dataset.surface = "domain-crew";
  const closeButton = el("button", "modal-close domain-crew-close", "关闭");
  closeButton.type = "button";
  const header = el("header", "domain-crew-head");
  const titleBlock = el("div", "domain-crew-title-block");
  titleBlock.append(
    el("span", "domain-crew-kicker", "AFS DOMAIN CREW"),
    el("h2", "", "制作团队"),
    el("p", "", "以项目 API 权威状态协调九类制作角色、交接、主创裁决与变更复确认。"),
  );
  header.append(titleBlock, closeButton);
  const body = el("div", "domain-crew-body");
  modal.append(header, body);

  let unsubscribe = () => {};
  const close = showModal(modal, {
    ariaLabel: "制作团队控制台",
    initialFocus: ".domain-crew-close",
    onClose: () => {
      unsubscribe();
      if (closeActivePanel === close) closeActivePanel = null;
    },
  });
  closeActivePanel = close;
  closeButton.addEventListener("click", close);
  unsubscribe = controller.subscribe((state) => renderPanel(body, controller, state));
  void controller.load().catch(() => {});
  return close;
}

function renderPanel(body, controller, state) {
  body.replaceChildren();
  body.dataset.status = state.status;
  body.appendChild(boundaryBanner(state));
  if (state.error) body.appendChild(errorBanner(state.error, () => controller.load().catch(() => {})));
  if (state.status === "loading" && !state.crew) {
    body.appendChild(emptyNotice("正在读取制作团队权威状态…"));
    return;
  }
  if (state.status === "missing") {
    body.appendChild(createCrewGate(controller, state));
    return;
  }
  if (!state.crew) {
    body.appendChild(emptyNotice("当前项目尚无可显示的制作团队状态。"));
    return;
  }
  const crew = state.crew;
  body.append(
    crewSummary(crew, state),
    section("制作角色", "九类角色的身份、能力与项目所有权来自认证 API。", agentGrid(crew.agents || [])),
    section("任务与责任", "任务认领使用当前状态版本；画布节点只用于定位，不参与任务或传播权威计算。", taskSurface(controller, crew, state)),
    section("结构化沟通", "消息与交接绑定精确任务、实体和版本引用。", communicationSurface(controller, crew, state)),
    section("冲突与主创裁决", "裁决提交的受影响集合由服务端持久化图完整校验；前端列表不能定义权威范围。", conflictSurface(controller, crew, state)),
    section("一致性传播", "仅展示 API 返回的受影响工作、传播依据与待复确认状态。", propagationSurface(controller, crew, state)),
  );
}

function boundaryBanner(state) {
  const banner = el("div", "domain-crew-boundary");
  banner.dataset.evidence = "domain_crew_ledger_pass";
  banner.dataset.authorityValid = String(Boolean(state.authorityValid));
  banner.append(
    el("strong", "", "证据边界"),
    el("span", "", "当前界面证明认证域剧组台账与人工控制路径；不证明节点已由真实智能体运行时自主推进。"),
  );
  if (!state.authorityValid) banner.appendChild(el("span", "domain-crew-authority-locked", "权威状态不可用，所有写操作已锁定；请重新读取。"));
  if (state.busyAction) banner.appendChild(el("span", "domain-crew-busy", `正在同步：${state.busyAction}`));
  return banner;
}

function crewSummary(crew, state) {
  const summary = el("div", "domain-crew-summary");
  const items = [
    ["项目", crew.project_id],
    ["剧组", crew.crew_id],
    ["状态版本", String(crew.state_version)],
    ["角色", String((crew.agents || []).length)],
    ["任务", String((crew.tasks || []).length)],
    ["待复确认", String((crew.propagation_reconfirmations || []).filter((item) => item.reconfirmation_status === "required_pending").length)],
  ];
  for (const [label, value] of items) {
    const card = el("div", "domain-crew-stat");
    card.append(el("span", "", label), el("strong", "selectable-text", value || "—"));
    summary.appendChild(card);
  }
  const refresh = el("button", "ghost-btn domain-crew-refresh", state.status === "loading" ? "刷新中…" : "刷新权威状态");
  refresh.type = "button";
  refresh.disabled = state.status === "loading" || Boolean(state.busyAction);
  refresh.addEventListener("click", () => controller.load({ allowMissing: false }).catch(() => {}));
  summary.appendChild(refresh);
  return summary;
}

function agentGrid(agents) {
  const grid = el("div", "domain-crew-agent-grid");
  for (const agent of agents) {
    const card = el("article", "domain-crew-agent");
    card.dataset.role = agent.role;
    card.append(
      el("span", "domain-crew-role", ROLE_LABELS[agent.role] || agent.role),
      el("strong", "selectable-text", agent.agent_id),
      el("small", "", (agent.capabilities || []).map((item) => ACTION_LABELS[item] || item).join(" · ")),
      pill(agent.status || "registered"),
    );
    grid.appendChild(card);
  }
  return grid;
}

function taskSurface(controller, crew, state) {
  const wrap = el("div", "domain-crew-split");
  const list = el("div", "domain-crew-list");
  for (const task of crew.tasks || []) list.appendChild(taskCard(controller, crew, task, state));
  if (!list.children.length) list.appendChild(emptyNotice("暂无任务，可创建第一个域制作任务。"));
  wrap.append(list, createTaskForm(controller, crew, state));
  return wrap;
}

function taskCard(controller, crew, task, state) {
  const card = el("article", "domain-crew-record");
  card.dataset.taskId = task.task_id;
  const title = el("div", "domain-crew-record-title");
  title.append(el("strong", "selectable-text", task.objective || task.task_id), pill(task.status));
  card.append(
    title,
    metaRows([
      ["任务", task.task_id],
      ["责任智能体", task.assigned_agent_id],
      ["动作", ACTION_LABELS[task.action] || task.action],
      ["实体版本", `${task.entity_type}:${task.entity_id}@${task.version_id}`],
      ["节点", task.node_id],
    ]),
  );
  const actions = el("div", "domain-crew-record-actions");
  const focus = smallButton("定位节点", () => controller.navigateToNode(task.node_id));
  actions.appendChild(focus);
  if (task.status === "ready" && !task.claimed_by_agent_id) {
    const claim = smallButton("由责任智能体认领", () => controller.claimTask(task.task_id, task.assigned_agent_id).catch(() => {}));
    claim.dataset.action = "claim-domain-task";
    claim.disabled = domainCrewMutationsDisabled(state) || !agentById(crew, task.assigned_agent_id);
    actions.appendChild(claim);
  }
  card.appendChild(actions);
  return card;
}

function createTaskForm(controller, crew, state) {
  const form = actionForm("新建任务", "create-domain-task");
  const agent = agentSelect(crew, "assigned_agent_id");
  form.append(
    field("任务 ID", textInput("task_id", suggestedId("task"))),
    field("画布节点 ID", textInput("node_id", "script-node-001")),
    field("责任智能体", agent),
    field("执行动作", actionSelect("action", selectedAgent(crew, agent)?.capabilities?.[0])),
    field("实体类型", entityTypeSelect()),
    field("实体 ID", textInput("entity_id", "episode-001")),
    field("版本 ID", textInput("version_id", "v1")),
    field("任务目标", textInput("objective", "完成当前制作节点并交回结构化结果")),
    submitButton("创建任务", state),
  );
  agent.addEventListener("change", () => setSelectValue(form.elements.action, selectedAgent(crew, agent)?.capabilities?.[0]));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    controller.createTask(formPayload(form)).catch(() => {});
  });
  return form;
}

function communicationSurface(controller, crew, state) {
  const wrap = el("div", "domain-crew-communication");
  const history = el("div", "domain-crew-history-grid");
  history.append(recordList("消息", crew.messages || [], messageRecord), recordList("交接", crew.handoffs || [], (item) => handoffRecord(controller, item, state)));
  const forms = el("div", "domain-crew-form-grid");
  forms.append(messageForm(controller, crew, state), handoffForm(controller, crew, state));
  wrap.append(history, forms);
  return wrap;
}

function messageForm(controller, crew, state) {
  const form = actionForm("发送结构化消息", "send-domain-message");
  form.append(
    field("消息 ID", textInput("message_id", suggestedId("message"))),
    field("任务", taskSelect(crew, "task_id")),
    field("发送方", agentSelect(crew, "from_agent_id")),
    field("接收方", agentSelect(crew, "to_agent_id", 1)),
    field("消息类型", optionSelect("message_type", ["request", "response", "status", "decision"])),
    field("内容", textInput("content", "请按精确实体版本继续下游工作")),
    submitButton("发送消息", state),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    controller.sendMessage(withTaskRef(crew, formPayload(form))).catch(() => {});
  });
  return form;
}

function handoffForm(controller, crew, state) {
  const form = actionForm("发起任务交接", "create-domain-handoff");
  const receiver = agentSelect(crew, "to_agent_id", 1);
  form.append(
    field("交接 ID", textInput("handoff_id", suggestedId("handoff"))),
    field("源任务", taskSelect(crew, "task_id")),
    field("发送方", agentSelect(crew, "from_agent_id")),
    field("接收方", receiver),
    field("下游任务 ID", textInput("target_task_id", suggestedId("task-next"))),
    field("下游节点 ID", textInput("target_node_id", "storyboard-node-001")),
    field("下游动作", actionSelect("next_action", selectedAgent(crew, receiver)?.capabilities?.[0])),
    field("交接目标", textInput("objective", "接续当前实体版本的下游制作")),
    submitButton("发起交接", state),
  );
  receiver.addEventListener("change", () => setSelectValue(form.elements.next_action, selectedAgent(crew, receiver)?.capabilities?.[0]));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    controller.createHandoff(withTaskRef(crew, formPayload(form))).catch(() => {});
  });
  return form;
}

function messageRecord(item) {
  return compactRecord(item.message_id, `${item.from_agent_id} → ${item.to_agent_id}`, item.content, item.message_type);
}

function handoffRecord(controller, item, state) {
  const record = compactRecord(item.handoff_id, `${item.from_agent_id} → ${item.to_agent_id}`, item.objective, item.status);
  if (item.status === "pending_receiver") {
    const actions = el("div", "domain-crew-record-actions");
    for (const decision of ["accept", "reject"]) {
      const button = smallButton(decision === "accept" ? "接收" : "退回", () => controller.decideHandoff(item.handoff_id, {
        receiver_agent_id: item.to_agent_id,
        decision,
        note: decision === "accept" ? "接收精确版本交接" : "退回并请求修订",
      }).catch(() => {}));
      button.dataset.action = `handoff-${decision}`;
      button.disabled = domainCrewMutationsDisabled(state);
      actions.appendChild(button);
    }
    record.appendChild(actions);
  }
  return record;
}

function conflictSurface(controller, crew, state) {
  const wrap = el("div", "domain-crew-split");
  const list = el("div", "domain-crew-list");
  for (const conflict of crew.conflicts || []) {
    const card = compactRecord(conflict.conflict_id, conflict.reason, `${conflict.entity_type}:${conflict.entity_id}@${conflict.version_id}`, conflict.status);
    if (conflict.status === "awaiting_creator") card.appendChild(arbitrationForm(controller, crew, conflict, state));
    list.appendChild(card);
  }
  if (!list.children.length) list.appendChild(emptyNotice("暂无升级到主创的冲突。"));
  wrap.append(list, conflictForm(controller, crew, state));
  return wrap;
}

function conflictForm(controller, crew, state) {
  const form = actionForm("升级冲突", "create-domain-conflict");
  form.append(
    field("冲突 ID", textInput("conflict_id", suggestedId("conflict"))),
    field("关联任务", taskSelect(crew, "task_id")),
    field("提出方", agentSelect(crew, "raised_by_agent_id")),
    field("冲突说明", textInput("reason", "角色或场景版本需要主创裁决")),
    submitButton("升级到主创", state),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    controller.createConflict(withTaskRef(crew, formPayload(form))).catch(() => {});
  });
  return form;
}

function arbitrationForm(controller, crew, conflict, state) {
  const form = actionForm("主创裁决", "arbitrate-domain-conflict");
  form.classList.add("domain-crew-arbitration-form");
  const resume = agentSelect(crew, "resume_agent_id");
  const refs = document.createElement("textarea");
  refs.name = "affected_work_refs_json";
  refs.rows = 5;
  refs.value = "[]";
  refs.placeholder = "由运行时持久化依赖图完整校验的 JSON 数组";
  form.append(
    field("批准版本", textInput("selected_version_id", `${conflict.version_id}-approved`)),
    field("恢复智能体", resume),
    field("恢复动作", actionSelect("next_action", selectedAgent(crew, resume)?.capabilities?.[0])),
    field("裁决理由", textInput("rationale", "主创批准此版本并要求下游按权威传播集合复确认")),
    field("受影响工作引用", refs),
    el("p", "domain-crew-authority-note", "此列表不是前端权威；API 将对持久化任务/交接/版本图做完整性校验，遗漏、额外或外项目引用会被拒绝。"),
    submitButton("提交主创裁决", state),
  );
  resume.addEventListener("change", () => setSelectValue(form.elements.next_action, selectedAgent(crew, resume)?.capabilities?.[0]));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const payload = formPayload(form);
      const affected = JSON.parse(payload.affected_work_refs_json || "[]");
      if (!Array.isArray(affected)) throw new Error("受影响工作引用必须是 JSON 数组");
      delete payload.affected_work_refs_json;
      controller.arbitrateConflict(conflict.conflict_id, {
        ...payload,
        entity_type: conflict.entity_type,
        entity_id: conflict.entity_id,
        from_version_id: conflict.version_id,
        affected_work_refs: affected,
      }).catch(() => {});
    } catch (error) {
      refs.setCustomValidity(error.message);
      refs.reportValidity();
      setTimeout(() => refs.setCustomValidity(""), 0);
    }
  });
  return form;
}

function propagationSurface(controller, crew, state) {
  const wrap = el("div", "domain-crew-propagation");
  const refs = crew.propagation_reconfirmations || [];
  for (const item of refs) {
    const card = el("article", "domain-crew-record domain-crew-propagation-card");
    card.dataset.affectedRefId = item.affected_ref_id;
    const basis = item.propagation_basis || {};
    card.append(
      compactTitle(`${ROLE_LABELS[item.responsible_agent_role] || item.responsible_agent_role} · ${item.downstream_task_id}`, item.reconfirmation_status),
      metaRows([
        ["责任智能体", item.responsible_agent_id],
        ["节点", item.downstream_node_id],
        ["版本传播", `${item.from_version_id} → ${item.approved_version_id}`],
        ["裁决状态版本", String(basis.arbitration_state_version ?? "—")],
        ["裁决事件序号", String(basis.arbitration_event_sequence ?? "—")],
      ]),
    );
    const actions = el("div", "domain-crew-record-actions");
    actions.appendChild(smallButton("定位节点", () => controller.navigateToNode(item.downstream_node_id)));
    if (item.reconfirmation_status === "required_pending") {
      const reconfirm = smallButton("由责任智能体复确认", () => controller.reconfirmPropagation(item.affected_ref_id, {
        responsible_agent_id: item.responsible_agent_id,
        action: item.required_action,
        observed_version_id: item.approved_version_id,
      }).catch(() => {}));
      reconfirm.dataset.action = "reconfirm-propagation";
      reconfirm.disabled = domainCrewMutationsDisabled(state);
      actions.appendChild(reconfirm);
    }
    card.appendChild(actions);
    wrap.appendChild(card);
  }
  if (!refs.length) wrap.appendChild(emptyNotice("当前没有 API 权威传播记录；画布连线不会在此生成受影响工作。"));
  return wrap;
}

function section(title, description, content) {
  const element = el("section", "domain-crew-section");
  const head = el("div", "domain-crew-section-head");
  head.append(el("h3", "", title), el("p", "", description));
  element.append(head, content);
  return element;
}

function createCrewGate(controller, state) {
  const gate = el("div", "domain-crew-empty-gate");
  gate.append(el("h3", "", "为当前项目建立制作团队"), el("p", "", "初始化后将创建编剧、分镜、美术、导演、连续性、质检、音频、剪辑与交付九类认证角色。"));
  const input = textInput("crew_id", `crew-${sanitizeId(state.projectId || "project")}`);
  const button = el("button", "primary-btn", "初始化制作团队");
  button.type = "button";
  button.dataset.action = "create-domain-crew";
  button.disabled = domainCrewMutationsDisabled(state);
  button.addEventListener("click", () => controller.createCrew(input.value).catch(() => {}));
  gate.append(field("剧组 ID", input), button);
  return gate;
}

function actionForm(title, action) {
  const form = el("form", "domain-crew-form");
  form.dataset.action = action;
  form.appendChild(el("h4", "", title));
  return form;
}

function recordList(title, items, render) {
  const wrap = el("div", "domain-crew-history");
  wrap.appendChild(el("h4", "", `${title} · ${items.length}`));
  for (const item of items.slice(-8).reverse()) wrap.appendChild(render(item));
  if (!items.length) wrap.appendChild(emptyNotice(`暂无${title}`));
  return wrap;
}

function compactRecord(id, headline, detail, status) {
  const card = el("article", "domain-crew-record domain-crew-record-compact");
  card.append(compactTitle(headline || id, status), metaRows([["ID", id], ["详情", detail || "—"]]));
  return card;
}

function compactTitle(title, status) {
  const head = el("div", "domain-crew-record-title");
  head.append(el("strong", "selectable-text", title || "—"), pill(status || "recorded"));
  return head;
}

function metaRows(rows) {
  const meta = el("dl", "domain-crew-meta");
  for (const [label, value] of rows) meta.append(el("dt", "", label), el("dd", "selectable-text", value || "—"));
  return meta;
}

function pill(status) {
  const element = el("span", `domain-crew-pill status-${sanitizeId(status)}`, status || "—");
  return element;
}

function field(label, input) {
  const wrapper = el("label", "domain-crew-field");
  wrapper.append(el("span", "", label), input);
  return wrapper;
}

function textInput(name, value = "") {
  const input = document.createElement("input");
  input.type = "text";
  input.name = name;
  input.value = value;
  input.required = true;
  input.maxLength = name.includes("objective") ? 500 : 160;
  return input;
}

function optionSelect(name, values, selected = "") {
  const select = document.createElement("select");
  select.name = name;
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = ACTION_LABELS[value] || ROLE_LABELS[value] || value;
    option.selected = value === selected;
    select.appendChild(option);
  }
  return select;
}

function agentSelect(crew, name, index = 0) {
  return optionSelect(name, (crew.agents || []).map((agent) => agent.agent_id), (crew.agents || [])[index]?.agent_id);
}

function taskSelect(crew, name) {
  return optionSelect(name, (crew.tasks || []).map((task) => task.task_id));
}

function actionSelect(name, selected = "") {
  return optionSelect(name, Object.keys(ACTION_LABELS), selected);
}

function entityTypeSelect() {
  return optionSelect("entity_type", ["project", "character", "scene", "shot"], "project");
}

function submitButton(label, state) {
  const button = el("button", "primary-btn", label);
  button.type = "submit";
  button.disabled = domainCrewMutationsDisabled(state);
  return button;
}

export function domainCrewMutationsDisabled(state) {
  return !state?.authorityValid || Boolean(state?.busyAction) || state?.status === "loading";
}

function smallButton(label, handler) {
  const button = el("button", "ghost-btn domain-crew-small-action", label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function errorBanner(message, retry) {
  const banner = el("div", "domain-crew-error");
  banner.setAttribute("role", "alert");
  banner.append(el("span", "selectable-text", message), smallButton("重新读取", retry));
  return banner;
}

function emptyNotice(message) {
  return el("div", "domain-crew-empty", message);
}

function formPayload(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function withTaskRef(crew, payload) {
  const task = (crew.tasks || []).find((item) => item.task_id === payload.task_id);
  if (!task) return payload;
  return { ...payload, entity_type: task.entity_type, entity_id: task.entity_id, version_id: task.version_id };
}

function selectedAgent(crew, select) {
  return (crew.agents || []).find((agent) => agent.agent_id === select.value);
}

function agentById(crew, agentId) {
  return (crew.agents || []).find((agent) => agent.agent_id === agentId);
}

function setSelectValue(select, value) {
  if (select && value) select.value = value;
}

function suggestedId(prefix) {
  return `${prefix}-${Date.now().toString(36)}`;
}

function sanitizeId(value) {
  const normalized = String(value || "").replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "");
  return normalized.slice(0, 140) || "unknown";
}
