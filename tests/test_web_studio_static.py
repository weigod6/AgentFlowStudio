from __future__ import annotations

import json
import subprocess
from pathlib import Path

from studio_static_helpers import STUDIO_ROOT, _source, _styles


def test_project_access_recovery_records_warning_without_console_error() -> None:
    script = r'''
import {
  reportProjectAccessRecovery,
  reportProjectCreateClientError,
  reportProjectDeleteClientError,
} from "./apps/studio/src/studio-project-runtime-ops.js";

const events = [];
let consoleErrors = 0;
const originalConsoleError = console.error;
console.error = () => { consoleErrors += 1; };
try {
  const returned = reportProjectAccessRecovery(
    { recordClientEvent: (event) => { events.push(event); return Promise.resolve(); } },
    new Error("project access denied"),
    "stale-project-001",
    "next-project-002",
    () => "Recovered after Bearer synthetic-secret at D:\\private\\runtime",
  );
  const errorsAfterRecovery = consoleErrors;
  reportProjectCreateClientError(
    { recordClientEvent: (event) => { events.push(event); return Promise.resolve(); } },
    new Error("create failed"),
    (error) => error.message,
  );
  reportProjectDeleteClientError(
    { recordClientEvent: (event) => { events.push(event); return Promise.resolve(); } },
    new Error("delete failed"),
    "delete-project-003",
    (error) => error.message,
  );
  process.stdout.write(JSON.stringify({ returned, events, errorsAfterRecovery, consoleErrors }));
} finally {
  console.error = originalConsoleError;
}
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    recovery = payload["events"][0]
    assert payload["errorsAfterRecovery"] == 0
    assert payload["returned"] == recovery
    assert recovery["event_type"] == "project_access_recovered"
    assert recovery["severity"] == "warning"
    assert recovery["action"] == "recover_project_access"
    assert recovery["project_id"] == "stale-project-001"
    assert recovery["details"] == {
        "stale_project_id": "stale-project-001",
        "next_project_id": "next-project-002",
    }
    assert "synthetic-secret" not in recovery["message"]
    assert "D:\\" not in recovery["message"]
    assert payload["consoleErrors"] == 2
    assert [event["event_type"] for event in payload["events"][1:]] == [
        "project_create_failed",
        "project_delete_failed",
    ]
    assert all(event["severity"] == "error" for event in payload["events"][1:])


def test_studio_static_entrypoint_is_the_only_user_frontend() -> None:
    assert STUDIO_ROOT.exists()
    assert not Path("apps/workbench").exists()
    assert not Path("apps/web").exists()

    index = (STUDIO_ROOT / "index.html").read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in index
    assert '<meta charset="utf-8" />' in index
    assert "<title>AFS Studio 创作图谱</title>" in index
    assert '<link rel="icon" href="./favicon.svg" type="image/svg+xml" />' in index
    assert (STUDIO_ROOT / "favicon.svg").is_file()
    assert './src/main.js' in index
    assert './styles/director.css' in index
    assert "/workbench" not in index


def test_studio_production_control_entry_is_linked_without_replacing_canvas() -> None:
    production_control = STUDIO_ROOT / "production-control"
    assert (production_control / "index.html").is_file()
    assert (production_control / "app.mjs").is_file()
    assert (production_control / "styles.css").is_file()

    index = (production_control / "index.html").read_text(encoding="utf-8")
    app = (production_control / "app.mjs").read_text(encoding="utf-8")
    topbar = (STUDIO_ROOT / "src" / "studio-topbar.js").read_text(encoding="utf-8")
    runtime_client = (STUDIO_ROOT / "src" / "runtime-client.js").read_text(encoding="utf-8")

    assert '<html lang="zh-CN">' in index
    assert "/studio/production-control/" in topbar
    assert 'href="/studio/"' in app
    assert "画布" in app
    assert "故事板 / 审片" in app
    assert "长篇生产" in app
    assert "结构化 Storyboard" in app
    assert "Canvas 探索视图" in app
    assert "资产身份链" in app
    assert "改写第 6 镜" in app
    assert "未选事实保持不变" in app
    assert "三镜头图像试验" in app
    assert "冻结图像试验" in app
    assert "不包含大模型脚本、视频、音频、导出、媒体质检、创作者验收或商业验证" in app
    assert "Provider gate" not in app
    assert "Provider smoke" not in app
    assert "调度下一镜头" in app
    assert "外部生成未启用" in app
    assert "strong-password-123" not in app
    assert "getProductionControl()" in runtime_client
    assert "getCommercialProduction()" in runtime_client
    assert "createCommercialProductionSample" in runtime_client
    assert "lockCommercialProductionStageGate" in runtime_client
    assert "requestCommercialProductionLocalRewrite" in runtime_client
    assert "create_commercial_production_sample" in runtime_client
    assert "lock_commercial_production_scope" in runtime_client
    assert "request_commercial_production_local_rewrite" in runtime_client
    assert "getCreatorGoldenTrial()" in runtime_client
    assert "dispatchCreatorGoldenTrialNext" in runtime_client
    assert "recordProductionControlMission" in runtime_client
    assert "approveProductionControlPlan" in runtime_client


def test_package_exposes_frontend_js_syntax_check() -> None:
    package = json.loads(Path("package.json").read_text(encoding="utf-8"))
    script = Path("tools/check-web-js.mjs")

    assert package["type"] == "module"
    assert package["scripts"]["check:studio-js"] == "node tools/check-web-js.mjs"
    assert script.is_file()
    source = script.read_text(encoding="utf-8")
    assert "apps/studio" in source
    assert "apps/site" in source
    assert "--check" in source


def test_studio_user_surface_has_no_common_mojibake_markers() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for suffix in ("*.html", "*.css", "*.js", "*.md")
        for path in STUDIO_ROOT.rglob(suffix)
    )

    for marker in (
        "\u9352",  # common mojibake marker seen when 创 is corrupted
        "\u9422",  # common mojibake marker seen when 画 is corrupted
        "\u93bb",  # common mojibake marker seen when 提 is corrupted
        "\u93c8",  # common mojibake marker seen when 本 is corrupted
        "\ufffd",
    ):
        assert marker not in combined


def test_studio_disallows_native_blocking_dialogs_and_global_canvas_fallback() -> None:
    source = _source()
    store_source = (STUDIO_ROOT / "src" / "store.js").read_text(encoding="utf-8")
    persistence_source = (STUDIO_ROOT / "src" / "store-persistence.js").read_text(encoding="utf-8")
    state_source = (STUDIO_ROOT / "src" / "store-state.js").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    for forbidden in ("window.prompt(", "window.confirm(", "window.alert("):
        assert forbidden not in source
    assert '|| localStorage.getItem(STORAGE_KEY)' not in persistence_source
    assert '|| localStorage.getItem(LEGACY_STORAGE_KEY)' not in persistence_source
    assert "migrateLegacyCanvasStorage" in store_source
    assert "localStorage.removeItem(STORAGE_KEY)" in persistence_source
    assert "localStorage.removeItem(LEGACY_STORAGE_KEY)" in persistence_source
    assert 'return { source: "stale", projectId: targetProjectId }' in store_source
    assert 'return { source: "local_newer" }' in store_source
    assert "shouldKeepLocalOverRemote" in store_source
    assert "hasStudioMeta(remoteState)" in store_source
    assert 'next.type === "video" && next.params.lastVideoPreviewUrl' in state_source
    assert '!String(next.previewUrl).includes("/video-generations/")' in state_source
    main_source = (STUDIO_ROOT / "src" / "main.js").read_text(encoding="utf-8")
    project_controller = (STUDIO_ROOT / "src" / "studio-project-controller.js").read_text(encoding="utf-8")
    assert "syncCurrentProjectMetaFromSummaries" in project_controller
    assert "const currentId = runtime.projectId || store.get().meta.projectId;" in project_controller
    assert "recoverProjectAccessDenied" in project_controller
    assert "isProjectAccessDeniedError" in project_controller
    assert "!projectSummaries.length && currentId" in project_controller
    assert 'input.type = "text";' in project_controller
    assert "createProjectController" in main_source
    drawer_source = (STUDIO_ROOT / "src" / "panels" / "drawer.js").read_text(encoding="utf-8")
    assert "state.meta.projectId, state.meta.projectName, state.meta.canvasName" in drawer_source
    canvas_body_source = (STUDIO_ROOT / "src" / "canvas-node-body.js").read_text(encoding="utf-8")
    assert '!["image", "video"].includes(node.type)' in canvas_body_source
    prompt_bar_source = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")
    assert "openExpandEditor(store, runtime, node)" in prompt_bar_source
    assert 'node.type === "video" || node.type === "script") {' not in prompt_bar_source
    assert "AFS_ALLOW_REMOTE_IMAGE" in env_example


def test_studio_has_homepage_navigation_and_account_session_surface() -> None:
    runtime_client = (STUDIO_ROOT / "src" / "runtime-client.js").read_text(encoding="utf-8")
    auth_gate = (STUDIO_ROOT / "src" / "auth-gate.js").read_text(encoding="utf-8")
    overlay = (STUDIO_ROOT / "src" / "overlay.js").read_text(encoding="utf-8")
    topbar = (STUDIO_ROOT / "src" / "studio-topbar.js").read_text(encoding="utf-8")
    main = (STUDIO_ROOT / "src" / "main.js").read_text(encoding="utf-8")

    assert "AUTH_TOKEN_STORAGE_KEY" in runtime_client
    assert "Authorization" in runtime_client
    assert "dispatchProjectAccessDenied" in runtime_client
    assert "project_access_denied" in runtime_client
    assert "authStatus()" in runtime_client
    assert "login(payload)" in runtime_client
    assert "register(payload)" in runtime_client
    assert "logout()" in runtime_client
    assert "saveStudioState(state, expectedVersion" in runtime_client
    assert "expected_version" in runtime_client
    assert "ensureAuthSession" in main
    assert "ensureAccessibleStartupProject" in main
    assert "bindProjectAccessRecovery" in main
    assert "afs:project-access-denied" in main
    assert "closeOnOutside: false" in auth_gate
    assert "options.closeOnOutside === false" in overlay
    assert "site-home-btn" in topbar
    assert 'href = "/site/"' in topbar
    assert "onBeforeSiteHome" in topbar
    assert "store.flushRuntimeSave()" in main
    assert "首页" in topbar


def test_studio_topbar_surfaces_creator_safe_service_status() -> None:
    runtime_client = (STUDIO_ROOT / "src" / "runtime-client.js").read_text(encoding="utf-8")
    runtime_status = (STUDIO_ROOT / "src" / "runtime-surface-status.js").read_text(encoding="utf-8")
    main = (STUDIO_ROOT / "src" / "main.js").read_text(encoding="utf-8")
    topbar = (STUDIO_ROOT / "src" / "studio-topbar.js").read_text(encoding="utf-8")
    styles = _styles()

    assert 'health()' in runtime_client
    assert 'requestJson("/health")' in runtime_client
    assert 'authStatus()' in runtime_client
    assert "runtimeSurfaceStatus" in main
    assert "refreshRuntimeSurfaceStatus" in main
    assert "loadRuntimeSurfaceStatus(runtimeClient" in main
    assert "runtime.health()" in runtime_status
    assert "runtime.authStatus()" in runtime_status
    assert "providerGateSummary" in runtime_status
    assert "Service health only; provider smoke, generated-media QA, human acceptance, and public readiness are not claimed." in runtime_status
    assert "创作服务已连接" in runtime_status
    assert "boundaryLabel" in runtime_status
    assert "runtimeSurfaceStatus" in topbar
    assert "runtimeStatusBadge" in topbar
    assert "safeRuntimeStatusState" in topbar
    assert "runtime-status-badge" in topbar
    assert "runtime-status-gates" not in topbar
    assert "runtime-status-boundary" not in topbar
    assert "Auth unknown" not in topbar
    assert "Provider gates unknown" not in topbar
    assert "Health only" not in topbar
    assert "data-state" in topbar or "dataset.state" in topbar
    assert ".runtime-status-badge.ready" in styles
    assert ".runtime-status-badge.unavailable" in styles


def test_studio_state_save_tracks_runtime_version_conflicts() -> None:
    store_source = (STUDIO_ROOT / "src" / "store.js").read_text(encoding="utf-8")
    runtime_save_source = (STUDIO_ROOT / "src" / "store-runtime-save.js").read_text(encoding="utf-8")

    assert "runtimeStateVersion" in store_source
    assert "payload?.state_version" in store_source
    assert "const snapshot = snapshotStudioState(state)" in store_source
    assert "saveStudioState(snapshot, runtimeStateVersion)" in store_source
    assert "error?.status === 409" in runtime_save_source
    assert "项目已在其他窗口更新" in runtime_save_source
    state_source = (STUDIO_ROOT / "src" / "store-state.js").read_text(encoding="utf-8")
    assert "sanitizeSnapshotForPersistence" in state_source
    assert "SAFE_PREVIEW_ROUTE_RE" in state_source
    assert "图像生成等待超时，已尝试从素材库恢复结果。" in state_source


def test_studio_user_surface_does_not_reintroduce_old_workbench_terms() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for suffix in ("*.html", "*.css", "*.js")
        for path in STUDIO_ROOT.rglob(suffix)
    )
    for term in ("/workbench", "LibTV", "memory-workbench", "provider raw"):
        assert term not in combined


def test_studio_keeps_flow_native_canvas_controls() -> None:
    source = _source()

    for marker in (
        "openAddNodeMenu",
        "openOptimizer",
        "director",
        "prompt-optimizations",
        "keyframe-generations",
        "image-assets",
        "uploadNodeImage",
        "collectConnectedImageAssetRefs",
        "connected_reference_nodes",
        "candidate_previews",
        "reusable_image_assets",
        "mergeImageAssets",
        "node-preview-img",
        "node-preview-video",
        "resizeNodeForImagePreview",
        "previewAspectRatio",
        "has-image-preview",
        "startNodeGeneration",
        "studio-state",
        "loadStudioState",
        "saveStudioState",
        "createNode",
        "undo()",
        "redo()",
    ):
        assert marker in source


def test_prompt_optimizer_provider_errors_use_user_facing_message() -> None:
    runtime_errors = (STUDIO_ROOT / "src" / "runtime-error-utils.js").read_text(encoding="utf-8")
    optimizer = (STUDIO_ROOT / "src" / "optimizer.js").read_text(encoding="utf-8")

    assert "提示词优化失败，请检查生成服务配置或稍后重试。" in runtime_errors
    assert "provider returned infrastructure error" in runtime_errors
    assert "unable to read `request.json`" in runtime_errors
    assert "bwrap:" in runtime_errors
    assert "提示词优化失败，请稍后重试。" in optimizer
    assert 'formatRuntimeError(error, "???????")' not in optimizer


def test_script_nodes_keep_prompt_bar_visible_with_content() -> None:
    prompt_bar = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")

    assert 'node.type === "script" || !node.content' in prompt_bar


def test_studio_asset_context_workflow_is_single_canvas() -> None:
    source = _source()
    styles = _styles()

    for marker in (
        "buildContextSubgraph",
        "context_subgraph",
        "runtime_work_mode",
        "temporary_lock_overrides",
        "visual_asset_ids",
        "promoteVisualAsset",
        "visualAssets",
        "fix-visual-asset",
        "context_bundle",
        "lastContextBundle",
        "connectNamedAssetToTarget",
        "connect-named-asset",
        "temporary-unlock",
        "temporaryLockOverrides",
        "uniqueLockWarnings",
        "visual-asset-panel",
    ):
        assert marker in source
    for marker in ("opt-context-assets", "opt-inline-btn", "context-bundle-summary", "visual-asset-panel"):
        assert marker in styles
    assert "mode-tab asset_capture" not in source
    assert "mode-tab context_generate" not in source


def test_image_node_prompt_bar_keeps_only_model_optimize_and_generate_controls() -> None:
    source = _source()
    prompt_bar = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")
    node_menu = (STUDIO_ROOT / "src" / "panels" / "node-menu.js").read_text(encoding="utf-8")
    node_actions = (STUDIO_ROOT / "src" / "node-actions.js").read_text(encoding="utf-8")

    assert "openModelPopover" in prompt_bar
    assert "openOptimizer" in prompt_bar
    assert "startNodeGeneration" in prompt_bar
    for removed in (
        "openImageSpecPopover",
        "openCameraPopover",
        "IMAGE_COUNTS",
        "IMAGE_QUALITY",
        "IMAGE_RESOLUTION",
        "IMAGE_RATIOS",
    ):
        assert removed not in prompt_bar
    assert "isRemoteVideoModel" in prompt_bar
    assert "pollNodeVideoGeneration" in prompt_bar
    assert "runPromptBarGeneration" in prompt_bar
    assert "声音" not in prompt_bar
    assert 'send.title = "继续轮询"' in prompt_bar
    assert "isPromptTextEditing" in prompt_bar
    assert '["TEXTAREA", "INPUT"].includes' in prompt_bar
    canvas_view = (STUDIO_ROOT / "src" / "canvas-view.js").read_text(encoding="utf-8")
    canvas_body = (STUDIO_ROOT / "src" / "canvas-node-body.js").read_text(encoding="utf-8")
    assert '!["image", "video"].includes(node.type)' in canvas_body
    assert "bar-cost" not in prompt_bar
    assert "当前视频模型不支持直接生成" in prompt_bar
    assert "当前版本仅图片节点支持真实生成" not in prompt_bar
    assert "uploadNodeImage" in node_menu
    assert "setNodeVideoFrame" in node_menu
    assert "pollNodeVideoGeneration" in node_menu
    assert "openPositionNear" in (STUDIO_ROOT / "src" / "panels" / "add-node-menu.js").read_text(encoding="utf-8")
    assert "syncRunAction" in canvas_view
    assert 'dataset.action = "video-poll"' in canvas_view
    canvas_action_handler = (STUDIO_ROOT / "src" / "canvas-node-action-handler.js").read_text(encoding="utf-8")
    assert "pollNodeVideoGeneration" in canvas_action_handler
    keyframe_actions = (STUDIO_ROOT / "src" / "node-keyframe-actions.js").read_text(encoding="utf-8")
    video_actions = (STUDIO_ROOT / "src" / "node-video-actions.js").read_text(encoding="utf-8")
    generation_actions = node_actions + keyframe_actions + video_actions
    assert generation_actions.count("restoreCancelledGeneration(store, node.id, previousNodeState);") == 3
    assert generation_actions.count("await store.flushRuntimeSave?.();\n      return;") >= 2
    drawer_source = "".join(
        path.read_text(encoding="utf-8")
        for path in (
            STUDIO_ROOT / "src" / "panels" / "drawer.js",
            STUDIO_ROOT / "src" / "panels" / "drawer-assets.js",
            STUDIO_ROOT / "src" / "panels" / "drawer-asset-actions.js",
        )
    )
    assert 'asset.kind === "visual_asset" && asset.asset_type === "character"' in drawer_source
    assert 'asset.kind === "character_asset"' in drawer_source
    assert 'character_asset: "角色资产"' in drawer_source
    assert "asset.preview_url" in drawer_source
    assert "node.params.visualAssets" in drawer_source
    assert "visualAssetRef" in drawer_source
    assert "setVideoFrameFromAsset" in drawer_source
    assert "firstFrameImageAssetId" in drawer_source
    assert "设为首帧" in drawer_source
    assert "retireVisualAsset" in drawer_source
    assert "draftAssetCard" in source
    assert "asset-card-drafts" in source
    assert "applyRetiredAsset" in drawer_source
    assert "确认停用" in drawer_source
    assert "asset.runtime_status" in drawer_source
    assert "上传/替换参考图" in node_menu
    assert "VIDEO_MODES" not in prompt_bar
    assert "VIDEO_COUNTS" not in prompt_bar
    assert "asset-reference-mode-tabs" in prompt_bar
    assert "buildAssetReferenceModeTabs" in prompt_bar
    assert "syncAssetReferenceModeTabs(wrap, mode)" in prompt_bar
    assert "syncAssetReferenceModeTabs(bar, assetReferenceMode(node))" in prompt_bar
    assert 'tab.setAttribute("aria-pressed"' in prompt_bar
