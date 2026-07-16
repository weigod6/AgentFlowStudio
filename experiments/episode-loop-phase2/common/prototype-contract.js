export const HARNESS_SCHEMA_VERSION = "afs_episode_loop_phase2_harness.v0.1";
export const FIXTURE_SHA256 = "0af7d008c39074765e60057f5d80f92f59ce45999ef3e216fd8562a963b9a2a2";
export const VARIANTS = Object.freeze(["guided", "storyboard", "hybrid"]);

export function assertVariant(variant) {
  if (!VARIANTS.includes(variant)) {
    throw new Error("未知原型类型，无法载入同任务测试。");
  }
  return variant;
}

export function createInitialState({ variant, scenario }) {
  assertVariant(variant);
  if (scenario?.schema_version !== "afs_episode_loop_phase2_scenario.v0.1") {
    throw new Error("测试场景版本不一致，请刷新原型基线。");
  }
  return {
    schema_version: HARNESS_SCHEMA_VERSION,
    variant,
    fixture_sha256: FIXTURE_SHA256,
    active_task: "orientation",
    active_view: "project_start",
    completed_tasks: [],
    decisions: {
      repaired_shot_006_scene: false,
      confirmed_shot_007_conflict: false,
      changed_shot_008: false,
      selected_shot_011_version: null,
    },
    checkpoint: {
      required_reconfirmations: scenario.revision_task.affected_downstream_count,
      completed_reconfirmations: 0,
      reload_required_at: scenario.revision_task.reload_after_reconfirmed_count,
      reload_observed: false,
    },
    delivery: {
      missing_asset_count: scenario.truth_constraints.missing_asset_count,
      provider_dispatch_count: scenario.truth_constraints.provider_dispatch_count,
      playable_preview_available: scenario.truth_constraints.playable_preview_available,
      status: "blocked_missing_assets",
    },
  };
}

export function assertTruthfulState(state) {
  if (state?.schema_version !== HARNESS_SCHEMA_VERSION) throw new Error("原型状态版本不一致。");
  assertVariant(state.variant);
  if (state.fixture_sha256 !== FIXTURE_SHA256) throw new Error("测试素材已漂移。");
  if (state.delivery.missing_asset_count !== 25) throw new Error("缺失素材事实已漂移。");
  if (state.delivery.provider_dispatch_count !== 0) throw new Error("原型禁止调用 Provider。");
  if (state.delivery.playable_preview_available) throw new Error("素材缺失时不得伪造可播放预览。");
  if (state.delivery.status !== "blocked_missing_assets") throw new Error("交付阻断状态不一致。");
  if (state.decisions.changed_shot_008) throw new Error("对照镜头 8 不应被修改。");
  const checkpoint = state.checkpoint;
  if (
    !checkpoint
    || checkpoint.required_reconfirmations !== 8
    || checkpoint.reload_required_at !== 3
    || !Number.isInteger(checkpoint.completed_reconfirmations)
    || checkpoint.completed_reconfirmations < 0
    || checkpoint.completed_reconfirmations > checkpoint.required_reconfirmations
  ) {
    throw new Error("恢复检查点不完整或已漂移。");
  }
  if (checkpoint.reload_observed && checkpoint.completed_reconfirmations < 3) {
    throw new Error("刷新恢复不能早于 3/8 检查点。");
  }
  if (
    !checkpoint.reload_observed
    && checkpoint.completed_reconfirmations > checkpoint.reload_required_at
  ) {
    throw new Error("超过 3/8 检查点前必须完成刷新恢复。");
  }
  return state;
}
