export const STORAGE_KEY = "afs.product-discovery.rainlight.v0.1";

export const EXECUTION_STATES = Object.freeze([
  "queued",
  "running",
  "waiting-human",
  "retrying",
  "blocked",
  "completed",
  "cancelled",
]);

export const CONTROL_STATES = Object.freeze([
  "active",
  "pause-requested",
  "paused",
  "resume-requested",
  "cancel-requested",
]);

const clone = (value) => JSON.parse(JSON.stringify(value));

function nowIso() {
  return new Date().toISOString();
}

function event(type, summary, details = {}) {
  return {
    event_id: `${type}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    type,
    summary,
    details,
    occurred_at: nowIso(),
    simulated: true,
  };
}

export function createInitialState(scenario) {
  const storyText = scenario.story_blocks.join("\n\n");
  return {
    schema_version: "afs.product_discovery.production_state.v0.1",
    scenario_id: scenario.scenario_id,
    stage: "mission",
    simulation: true,
    provider_dispatch_count: 0,
    saved_at: null,
    restored_count: 0,
    mission: {
      title: scenario.title,
      story_text: storyText,
      reference_constraints: [...scenario.reference_constraints],
      budget_cap: 3000,
      privacy: "private",
      training_use: "denied_by_default",
    },
    plan: {
      plan_id: "plan-rainlight-v1",
      version: 1,
      approved: false,
      approved_at: null,
      tasks: clone(scenario.plan_tasks),
    },
    runs: [],
    decisions: [],
    artifacts: {
      active_artifact_id: null,
      shots: [],
      writebacks: [],
    },
    continuity: null,
    review: {
      state: "not-started",
      blockers: [],
    },
    delivery: {
      state: "not-started",
      playable_preview: false,
      missing_asset_count: 0,
    },
    simulation_fixture: {
      shots: clone(scenario.shots),
      continuity: {
        proposal_id: "continuity-proposal-shot-007-v1",
        status: "pending",
        target_entity_id: "shot-007",
        target_version_id: "shot-007-v1",
        protected_refs: ["shot-008@shot-008-v1"],
        predicted_impact_refs: ["shot-007@shot-007-v1"],
        applied_refs: [],
        changes: [
          "铜制灯扣恢复到右肩",
          "左眉疤保持不镜像",
          "雨线密度稍增，人物目光更坚定",
          "机位、时长与镜头 8 均保持不变",
        ],
      },
    },
    ui: {
      mobile_view: "tasks",
      inspector: "revision",
      plan_expanded: true,
      plan_task_drafts: Object.fromEntries(
        scenario.plan_tasks.map((task) => [task.task_id, task.scope]),
      ),
      toast: "",
    },
    processed_commands: [],
    events: [event("mission.recorded", "已载入《雨灯》单集目标与参考约束")],
  };
}

function appendEvent(state, nextEvent) {
  state.events.unshift(nextEvent);
  state.events = state.events.slice(0, 40);
}

function markProcessed(state, command) {
  if (command.idempotency_key) {
    state.processed_commands.push(command.idempotency_key);
  }
}

function ensureKnownRun(state, runId) {
  const run = state.runs.find((candidate) => candidate.run_id === runId);
  if (!run) throw new Error(`Unknown run: ${runId}`);
  return run;
}

function approvePlan(state) {
  if (state.plan.approved) return;
  state.plan.approved = true;
  state.plan.approved_at = nowIso();
  state.stage = "production";
  state.ui.plan_expanded = false;
  state.runs = state.plan.tasks.map((task, index) => ({
    run_id: `run-${task.task_id}`,
    task_id: task.task_id,
    attempt_id: `attempt-${task.task_id}-1`,
    assigned_agent: task.agent,
    execution_state: task.seed_execution_state,
    control_state: "active",
    progress: task.seed_progress,
    estimated_cost: task.estimated_cost,
    incurred_cost: task.seed_execution_state === "completed" ? task.estimated_cost : Math.round(task.estimated_cost * task.seed_progress / 100),
    currency: "CNY",
    simulated: true,
    attempt_number: 1,
    blocker_ref: task.seed_execution_state === "waiting-human" ? "decision-world-baseline" : null,
    checkpoint_ref: `checkpoint-${task.task_id}-${index + 1}`,
    output_target_refs: task.task_id === "task-storyboard" ? ["episode-rainlight", "shot-001..shot-015"] : ["episode-rainlight"],
    written_artifact_refs: task.seed_execution_state === "completed" ? ["episode-rainlight@breakdown-v1"] : [],
  }));
  state.decisions = [{
    decision_id: "decision-world-baseline",
    task_id: "task-world-building",
    status: "pending",
    title: "确认角色与场景基线",
    prompt: "林遥、小祁、余叔与三个场景已从剧本提取。是否按当前约束固化，继续写入分镜？",
    options: ["确认并继续", "返回调整"],
  }];
  state.artifacts.active_artifact_id = "shot-007";
  state.artifacts.shots = state.simulation_fixture.shots.map((shot) => ({
    ...clone(shot),
    status: shot.number <= 6 ? "ready" : "draft",
    revision_state: shot.number === 7 ? "proposed" : shot.number === 8 ? "protected" : "unchanged",
    source_run_id: "run-task-storyboard",
    simulated: true,
  }));
  state.artifacts.writebacks = [{
    writeback_id: "writeback-storyboard-draft-v1",
    target_collection_ref: "episode-rainlight@storyboard-v1",
    written_artifact_refs: state.artifacts.shots.map((shot) => `${shot.entity_id}@${shot.version_id}`),
    source_run_id: "run-task-storyboard",
    status: "partial",
    simulated: true,
  }];
  state.runs.find((run) => run.task_id === "task-storyboard").written_artifact_refs =
    state.artifacts.writebacks[0].written_artifact_refs;
  state.continuity = clone(state.simulation_fixture.continuity);
  state.review = {
    state: "blocked",
    blockers: ["角色与场景基线待确认", "镜头 7 局部修订待决策"],
  };
  state.delivery = {
    state: "blocked",
    playable_preview: false,
    missing_asset_count: 25,
  };
  appendEvent(state, event("plan.approved", "一次批准已原子启动 3 条并行任务", {
    plan_id: state.plan.plan_id,
    run_ids: state.runs.map((run) => run.run_id),
  }));
  appendEvent(state, event("artifact.writeback.partial", "分镜 Agent 已把 15 个模拟草案写回 Storyboard", {
    source_run_id: "run-task-storyboard",
    writeback_id: "writeback-storyboard-draft-v1",
  }));
  appendEvent(state, event("decision.requested", "角色与场景任务等待创作者确认基线", {
    decision_id: "decision-world-baseline",
    run_id: "run-task-world-building",
  }));
}

function resolveWorldDecision(state) {
  const decision = state.decisions.find((item) => item.decision_id === "decision-world-baseline");
  if (!decision || decision.status === "resolved") return;
  decision.status = "resolved";
  decision.resolution = "confirmed";
  decision.resolved_at = nowIso();
  const run = ensureKnownRun(state, "run-task-world-building");
  run.execution_state = "running";
  run.progress = 78;
  run.blocker_ref = null;
  state.review.blockers = state.review.blockers.filter((item) => item !== "角色与场景基线待确认");
  appendEvent(state, event("decision.responded", "角色与场景基线已确认，设定任务继续执行", {
    decision_id: decision.decision_id,
    run_id: run.run_id,
  }));
}

function applyShotRevision(state) {
  if (state.continuity.status === "executed") return;
  const shot7 = state.artifacts.shots.find((shot) => shot.entity_id === "shot-007");
  const shot8 = state.artifacts.shots.find((shot) => shot.entity_id === "shot-008");
  const protectedVersion = shot8.version_id;
  shot7.parent_version_id = shot7.version_id;
  shot7.version_id = "shot-007-v2";
  shot7.status = "needs-review";
  shot7.revision_state = "applied";
  shot8.revision_state = "protected";
  state.continuity.status = "executed";
  state.continuity.applied_refs = ["shot-007@shot-007-v2"];
  state.artifacts.writebacks.push({
    writeback_id: "writeback-shot-007-v2",
    target_entity_id: "shot-007",
    from_version_id: "shot-007-v1",
    to_version_id: "shot-007-v2",
    protected_refs: [`shot-008@${protectedVersion}`],
    source_run_id: "run-task-storyboard",
    simulated: true,
  });
  state.review.blockers = state.review.blockers.filter((item) => item !== "镜头 7 局部修订待决策");
  state.review.state = state.review.blockers.length ? "blocked" : "needs-review";
  appendEvent(state, event("artifact.writeback.completed", "镜头 7 已生成新版本；镜头 8 精确版本保持不变", {
    changed_ref: "shot-007@shot-007-v2",
    protected_ref: `shot-008@${protectedVersion}`,
  }));
}

export function executeCommand(currentState, command) {
  const state = clone(currentState);
  if (command.idempotency_key && state.processed_commands.includes(command.idempotency_key)) {
    return state;
  }

  switch (command.type) {
    case "mission.update":
      state.mission.story_text = command.story_text;
      appendEvent(state, event("mission.revised", "创作者更新了故事输入"));
      break;
    case "plan.propose":
      state.stage = "plan-review";
      appendEvent(state, event("plan.proposed", "已生成可编辑计划（模拟）"));
      break;
    case "plan.task.update": {
      const task = state.plan.tasks.find((item) => item.task_id === command.task_id);
      if (!task) throw new Error(`Unknown task: ${command.task_id}`);
      if (task.scope === command.scope) break;
      task.scope = command.scope;
      state.plan.version += 1;
      appendEvent(state, event("plan.revised", `${task.title}范围已更新`, { task_id: task.task_id }));
      break;
    }
    case "plan.approve": {
      const updates = Array.isArray(command.task_updates) ? command.task_updates : [];
      const changedTasks = [];
      for (const update of updates) {
        const task = state.plan.tasks.find((item) => item.task_id === update.task_id);
        if (!task) throw new Error(`Unknown task: ${update.task_id}`);
        if (task.scope === update.scope) continue;
        task.scope = update.scope;
        changedTasks.push(task.task_id);
      }
      if (changedTasks.length) {
        state.plan.version += 1;
        appendEvent(state, event("plan.revised", "创作者提交了计划范围调整", {
          task_ids: changedTasks,
        }));
      }
      approvePlan(state);
      break;
    }
    case "run.pause": {
      const run = ensureKnownRun(state, command.run_id);
      if (["completed", "cancelled"].includes(run.execution_state)) break;
      run.control_state = "paused";
      appendEvent(state, event("run.paused", `${run.assigned_agent}已在当前检查点暂停`, { run_id: run.run_id }));
      break;
    }
    case "run.resume": {
      const run = ensureKnownRun(state, command.run_id);
      if (run.control_state !== "paused") break;
      run.control_state = "active";
      if (run.execution_state === "retrying") run.execution_state = "running";
      appendEvent(state, event("run.resumed", `${run.assigned_agent}已从检查点恢复`, { run_id: run.run_id }));
      break;
    }
    case "run.retry": {
      const run = ensureKnownRun(state, command.run_id);
      if (["completed", "cancelled"].includes(run.execution_state)) break;
      run.execution_state = "retrying";
      run.control_state = "active";
      run.attempt_number += 1;
      run.attempt_id = `attempt-${run.task_id}-${run.attempt_number}`;
      appendEvent(state, event("run.retrying", `${run.assigned_agent}正在从最近检查点重试`, { run_id: run.run_id }));
      break;
    }
    case "decision.resolve":
      resolveWorldDecision(state);
      break;
    case "artifact.select":
      state.artifacts.active_artifact_id = command.entity_id;
      state.ui.inspector = command.entity_id === "shot-007" ? "revision" : "artifact";
      break;
    case "revision.apply":
      applyShotRevision(state);
      break;
    case "ui.mobile-view":
      state.ui.mobile_view = command.view;
      break;
    case "ui.plan-draft.update": {
      const task = state.plan.tasks.find((item) => item.task_id === command.task_id);
      if (!task) throw new Error(`Unknown task: ${command.task_id}`);
      state.ui.plan_task_drafts[task.task_id] = command.scope;
      break;
    }
    case "ui.inspector":
      state.ui.inspector = command.inspector;
      break;
    case "ui.toast":
      state.ui.toast = command.message;
      break;
    default:
      throw new Error(`Unknown command type: ${command.type}`);
  }

  markProcessed(state, command);
  state.saved_at = nowIso();
  return state;
}

export function saveState(storage, state) {
  const next = { ...state, saved_at: nowIso() };
  storage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function loadState(storage, scenario) {
  const raw = storage.getItem(STORAGE_KEY);
  if (!raw) return createInitialState(scenario);
  try {
    const state = JSON.parse(raw);
    if (state.schema_version !== "afs.product_discovery.production_state.v0.1") {
      return createInitialState(scenario);
    }
    state.ui.plan_task_drafts ||= Object.fromEntries(
      state.plan.tasks.map((task) => [task.task_id, task.scope]),
    );
    state.restored_count = (state.restored_count || 0) + 1;
    state.ui.toast = "已恢复计划、任务、产物与待决事项";
    return state;
  } catch {
    return createInitialState(scenario);
  }
}

export function resetState(storage, scenario) {
  storage.removeItem(STORAGE_KEY);
  return createInitialState(scenario);
}

export function stateSummary(state) {
  return {
    stage: state.stage,
    plan_approved: state.plan.approved,
    run_states: state.runs.map((run) => run.execution_state),
    active_artifact_id: state.artifacts.active_artifact_id,
    pending_decisions: state.decisions.filter((item) => item.status === "pending").length,
    shot_7_version: state.artifacts.shots.find((shot) => shot.entity_id === "shot-007")?.version_id,
    shot_8_version: state.artifacts.shots.find((shot) => shot.entity_id === "shot-008")?.version_id,
    provider_dispatch_count: state.provider_dispatch_count,
  };
}
