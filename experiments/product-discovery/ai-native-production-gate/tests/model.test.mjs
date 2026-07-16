import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  EXECUTION_STATES,
  STORAGE_KEY,
  createInitialState,
  executeCommand,
  loadState,
  saveState,
} from "../src/model.mjs";

const scenario = JSON.parse(await readFile(new URL("../scenario.json", import.meta.url), "utf8"));

function command(state, type, payload = {}, key = `${type}-test`) {
  return executeCommand(state, { type, ...payload, idempotency_key: key });
}

function approvedState() {
  let state = createInitialState(scenario);
  state = command(state, "plan.propose");
  return command(state, "plan.approve", {}, "approve-plan-rainlight-v1");
}

test("frozen story input is between 2k and 5k Chinese characters", () => {
  const story = scenario.story_blocks.join("\n\n");
  assert.ok(story.length >= 2000, `story was ${story.length} characters`);
  assert.ok(story.length <= 5000, `story was ${story.length} characters`);
});

test("one idempotent approval starts exactly three bounded parallel runs", () => {
  let state = createInitialState(scenario);
  assert.equal(state.artifacts.shots.length, 0, "Mission stage must not preload domain artifacts");
  assert.equal(state.artifacts.writebacks.length, 0);
  assert.equal(state.decisions.length, 0, "Mission stage must not preload a run decision");
  state = command(state, "plan.propose");
  state = command(state, "plan.approve", {}, "approve-plan-rainlight-v1");
  assert.equal(state.plan.approved, true);
  assert.equal(state.ui.plan_expanded, false, "approved production restores into the compact Mission view");
  assert.equal(state.runs.length, 3);
  assert.deepEqual(
    state.runs.map((run) => run.execution_state),
    ["completed", "waiting-human", "running"],
  );
  assert.ok(state.runs.every((run) => EXECUTION_STATES.includes(run.execution_state)));
  assert.ok(state.runs.every((run) => run.assigned_agent && run.output_target_refs.length));
  assert.equal(state.provider_dispatch_count, 0);
  assert.equal(state.artifacts.shots.length, 15);
  assert.equal(state.artifacts.writebacks.length, 1);
  assert.equal(state.artifacts.writebacks[0].source_run_id, "run-task-storyboard");
  assert.equal(state.decisions.length, 1);
  assert.equal(state.events.filter((item) => item.type === "artifact.writeback.partial").length, 1);

  state = command(state, "plan.approve", {}, "approve-plan-rainlight-v1");
  assert.equal(state.runs.length, 3, "replayed approval must not duplicate runs");
  assert.equal(state.artifacts.writebacks.length, 1, "replayed approval must not duplicate writeback");
});

test("persisted UI draft becomes one domain plan revision at approval", () => {
  let state = createInitialState(scenario);
  state = command(state, "plan.propose");
  const taskId = "task-storyboard";
  const revisedScope = "写回 15 个可审核镜头，并优先提交镜头 7/8 连续性对照";
  state = executeCommand(state, {
    type: "ui.plan-draft.update",
    task_id: taskId,
    scope: revisedScope,
  });
  assert.notEqual(state.plan.tasks[2].scope, revisedScope, "draft must not mutate domain plan");
  assert.equal(state.ui.plan_task_drafts[taskId], revisedScope);

  state = command(state, "plan.approve", {
    task_updates: [{ task_id: taskId, scope: revisedScope }],
  }, "approve-revised-plan");
  assert.equal(state.plan.version, 2);
  assert.equal(state.plan.tasks[2].scope, revisedScope);
  assert.equal(state.events.filter((item) => item.type === "plan.revised").length, 1);
  assert.equal(state.events.filter((item) => item.type === "plan.approved").length, 1);
});

test("waiting-human decision unblocks only its owning run", () => {
  let state = approvedState();
  const before = state.runs.map((run) => run.execution_state);
  state = command(state, "decision.resolve", { decision_id: "decision-world-baseline" });
  assert.deepEqual(before, ["completed", "waiting-human", "running"]);
  assert.deepEqual(state.runs.map((run) => run.execution_state), ["completed", "running", "running"]);
  assert.equal(state.decisions[0].status, "resolved");
});

test("pause, resume, and retry preserve run identity and add an attempt", () => {
  let state = approvedState();
  const runId = "run-task-storyboard";
  state = command(state, "run.pause", { run_id: runId }, "pause-storyboard");
  assert.equal(state.runs[2].control_state, "paused");
  state = command(state, "run.resume", { run_id: runId }, "resume-storyboard");
  assert.equal(state.runs[2].control_state, "active");
  state = command(state, "run.retry", { run_id: runId }, "retry-storyboard");
  assert.equal(state.runs[2].execution_state, "retrying");
  assert.equal(state.runs[2].attempt_number, 2);
  assert.equal(state.runs[2].run_id, runId);
});

test("Shot 7 writeback appends a version and preserves Shot 8 exact ref", () => {
  let state = approvedState();
  const shot8Before = state.artifacts.shots.find((shot) => shot.entity_id === "shot-008").version_id;
  state = command(state, "revision.apply", { proposal_id: state.continuity.proposal_id }, "apply-shot7-v1");
  const shot7 = state.artifacts.shots.find((shot) => shot.entity_id === "shot-007");
  const shot8 = state.artifacts.shots.find((shot) => shot.entity_id === "shot-008");
  assert.equal(shot7.parent_version_id, "shot-007-v1");
  assert.equal(shot7.version_id, "shot-007-v2");
  assert.equal(shot8.version_id, shot8Before);
  assert.deepEqual(state.continuity.applied_refs, ["shot-007@shot-007-v2"]);
  assert.deepEqual(state.continuity.protected_refs, ["shot-008@shot-008-v1"]);
  assert.equal(state.artifacts.writebacks.length, 2);

  state = command(state, "revision.apply", { proposal_id: state.continuity.proposal_id }, "apply-shot7-v1");
  assert.equal(state.artifacts.writebacks.length, 2, "replay must not duplicate writeback");
});

test("reload restores plan, runs, artifact focus, and pending decisions", () => {
  const memory = new Map();
  const storage = {
    getItem: (key) => memory.get(key) ?? null,
    setItem: (key, value) => memory.set(key, value),
    removeItem: (key) => memory.delete(key),
  };
  let state = approvedState();
  state = command(state, "artifact.select", { entity_id: "shot-008" }, "select-shot8");
  saveState(storage, state);
  const restored = loadState(storage, scenario);
  assert.equal(memory.has(STORAGE_KEY), true);
  assert.equal(restored.plan.approved, true);
  assert.equal(restored.runs.length, 3);
  assert.equal(restored.artifacts.active_artifact_id, "shot-008");
  assert.equal(restored.decisions.filter((item) => item.status === "pending").length, 1);
  assert.equal(restored.restored_count, 1);
});
