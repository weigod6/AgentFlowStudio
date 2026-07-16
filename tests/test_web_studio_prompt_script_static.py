import json
import subprocess

from studio_static_helpers import STUDIO_ROOT, _styles


def test_prompt_optimization_is_inline_and_selection_safe() -> None:
    optimizer = (STUDIO_ROOT / "src" / "optimizer.js").read_text(encoding="utf-8")
    prompt_bar = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")
    styles = _styles()

    assert "showPopover" not in optimizer
    assert "optimizer-pop" not in optimizer
    assert "promptOptimizationState" in optimizer
    assert 'status: "running"' in optimizer
    assert 'percent: 12' in optimizer
    assert 'store.get().nodes[nodeId]' in optimizer
    assert "connectNamedAssetToTarget" in optimizer
    assert "buildAssetReferenceActions" in optimizer
    assert "lastCreativeRuntimeContractSummary" in optimizer
    assert "creative_runtime_contract_summary" in optimizer
    assert "normalizeCreativeRuntimeContractSummary" in (STUDIO_ROOT / "src" / "optimizer-contract.js").read_text(encoding="utf-8")
    assert "creativeRuntimeContractSummary" in (STUDIO_ROOT / "src" / "canvas-node-body.js").read_text(encoding="utf-8")
    assert "creative-runtime-contract-summary" in styles
    assert "prompt-shimmer" in prompt_bar
    assert "syncPromptBarState" in prompt_bar
    assert "promptTextShimmer" in styles


def test_text_prompt_optimization_uses_and_updates_visible_content() -> None:
    script = r'''
import { openOptimizer } from "./apps/studio/src/optimizer.js";
import { buildOptimizationRequest } from "./apps/studio/src/optimizer-contract.js";
import { nodeBodySignature } from "./apps/studio/src/canvas-node-body.js";

const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: "",
      content: "原始上传剧本正文",
      params: {},
      status: "complete",
    },
  },
  edges: {},
  assets: [],
  groups: {},
  selection: { nodeIds: ["text_1"], edgeId: null },
  ui: {},
};
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
};
const beforeSignature = nodeBodySignature(state.nodes.text_1);
const request = buildOptimizationRequest(state, state.nodes.text_1);
const textarea = { value: "", classList: { add() {}, remove() {} } };
await openOptimizer(store, {
  async optimizePrompt(payload) {
    if (payload.prompt_text !== "原始上传剧本正文") {
      throw new Error(`unexpected prompt_text: ${payload.prompt_text}`);
    }
    return {
      user_prompt: "优化后的剧本正文",
      user_prompt_plain: "优化后的剧本正文",
      optimization_mode: "text",
      context_bundle: { warnings: [] },
      model_call_context_id: "mctx_prompt_text_001",
      model_call_context_summary: {
        context_id: "mctx_prompt_text_001",
        schema_version: "afs_model_call_context.v0.1",
        operation_intent: "prompt_optimize",
        generation_target: "prompt",
        artifact: {
          artifact_id: "runs-proj-job-model_call_context",
          artifact_type: "text_artifact",
          filename: "model_call_context.json",
          role: "model_call_context",
          media_type: "application/json",
        },
        context_sources: {
          context_bundle_present: true,
          included_asset_count: 1,
          excluded_asset_count: 0,
          feedback_context_overlay_count: 0,
          upstream_ref_count: 0,
        },
        asset_context: {
          context_eligible_asset_count: 1,
          draft_assets_enter_context: false,
        },
        reference_context: { reference_image_count: 0 },
        provider_constraints: {
          capability: "llm",
          provider_gate: "AFS_ALLOW_REMOTE_LLM",
        },
        trace_summary: { warning_ids: [], feedback_context_overlay_ids: [] },
        safety_boundary: {
          no_secrets: true,
          no_provider_raw: true,
          no_credentialed_url: true,
          no_local_path: true,
          no_media_bytes: true,
          feedback_is_not_memory: true,
          draft_assets_are_not_context_truth: true,
        },
        non_claims: ["not_provider_execution"],
      },
      creative_runtime_contract_id: "crtc_prompt_text_001",
      creative_runtime_contract_summary: {
        contract_id: "crtc_prompt_text_001",
        schema_version: "afs_creative_runtime_contract.v0.1",
        operation: "prompt_optimization",
        generation_target: "prompt",
        artifact: {
          artifact_id: "runs-proj-job-creative_runtime_contract",
          artifact_type: "text_artifact",
          filename: "creative_runtime_contract.json",
          role: "creative_runtime_contract",
          media_type: "application/json",
        },
        memory_context: {
          project_memory_count: 0,
          user_preference_count: 0,
          promotion_candidates_only: true,
        },
        knowledge_context: {
          rule_count: 3,
          director_scenario_count: 0,
          registry_hash: "kb_hash_001",
        },
        asset_context: {
          fixed_asset_count: 1,
          draft_asset_count: 0,
          unresolved_asset_count: 0,
        },
        model_call_context: {
          context_id: "mctx_prompt_text_001",
          schema_version: "afs_model_call_context.v0.1",
        },
        provider_context: {
          capability: "llm",
          required_gate: "AFS_ALLOW_REMOTE_LLM",
          gate_status: "blocked",
          provider_calls_started: false,
        },
        evidence_context: {
          model_call_context_id: "mctx_prompt_text_001",
          safe_manifest_ref: "prompt_optimization_safe_manifest.json",
        },
        non_claims: ["not_provider_execution", "not_generated_media_qa", "not_human_acceptance"],
      },
    };
  },
}, "text_1", null, textarea);
const afterSignature = nodeBodySignature(state.nodes.text_1);
process.stdout.write(JSON.stringify({
  requestPrompt: request.prompt_text,
  prompt: state.nodes.text_1.prompt,
  content: state.nodes.text_1.content,
  textarea: textarea.value,
  status: state.nodes.text_1.params.promptOptimizationState.status,
  modelContextId: state.nodes.text_1.params.lastModelCallContextId,
  stateModelContextId: state.nodes.text_1.params.promptOptimizationState.model_call_context_id,
  modelContextEligibleAssets: state.nodes.text_1.params.lastModelCallContextSummary.asset_context.context_eligible_asset_count,
  modelContextArtifact: state.nodes.text_1.params.lastModelCallContextSummary.artifact.filename,
  contractId: state.nodes.text_1.params.lastCreativeRuntimeContractId,
  stateContractId: state.nodes.text_1.params.promptOptimizationState.creative_runtime_contract_id,
  contractArtifact: state.nodes.text_1.params.lastCreativeRuntimeContractSummary.artifact.filename,
  contractProviderCallsStarted: state.nodes.text_1.params.lastCreativeRuntimeContractSummary.provider_context.provider_calls_started,
  contractNonClaims: state.nodes.text_1.params.lastCreativeRuntimeContractSummary.non_claims,
  lastContextBundlePresent: Boolean(state.nodes.text_1.params.lastContextBundle),
  signatureChanged: beforeSignature !== afterSignature,
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["requestPrompt"] == "原始上传剧本正文"
    assert payload["prompt"] == "优化后的剧本正文"
    assert payload["content"] == "优化后的剧本正文"
    assert payload["textarea"] == "优化后的剧本正文"
    assert payload["status"] == "complete"
    assert payload["modelContextId"] == "mctx_prompt_text_001"
    assert payload["stateModelContextId"] == "mctx_prompt_text_001"
    assert payload["modelContextEligibleAssets"] == 1
    assert payload["modelContextArtifact"] == "model_call_context.json"
    assert payload["contractId"] == "crtc_prompt_text_001"
    assert payload["stateContractId"] == "crtc_prompt_text_001"
    assert payload["contractArtifact"] == "creative_runtime_contract.json"
    assert payload["contractProviderCallsStarted"] is False
    assert "not_human_acceptance" in payload["contractNonClaims"]
    assert payload["lastContextBundlePresent"] is True
    assert payload["signatureChanged"] is True


def test_asset_card_prompt_optimization_syncs_mode_input_and_visible_revision() -> None:
    script = r'''
import { openOptimizer } from "./apps/studio/src/optimizer.js";
import { ASSET_REFERENCE_MODES } from "./apps/studio/src/asset-revision-references.js";

const draft = {
  asset_type: "character",
  label: "Test character",
  status: "draft",
  signature: "Reference-inspired character",
  feature_card: {
    identity: "Reference-inspired identity",
    appearance: "Long coat and dark legwear",
  },
};
const state = {
  nodes: {
    image_1: {
      id: "image_1",
      type: "image",
      prompt: "stale instruction",
      content: "",
      params: {
        nodeRole: "asset_card_draft",
        assetReferenceMode: ASSET_REFERENCE_MODES.ORIGINALIZE_IP_SAFE,
        assetCardDraft: { ...draft, user_edited_text: "stale instruction" },
        uploads: [{ asset_id: "img_ref_001", filename: "reference.png", role: "reference_image" }],
        spec: { ratio: "16:9", count: 1 },
      },
      status: "complete",
    },
  },
  edges: {},
  assets: [],
  groups: {},
  selection: { nodeIds: ["image_1"], edgeId: null },
  ui: {},
};
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
};
const textarea = {
  value: "remove the black stockings",
  classList: { add() {}, remove() {} },
};
const button = {
  disabled: false,
  classList: { toggle() {} },
  querySelector() { return { textContent: "" }; },
};
let captured = null;
await openOptimizer(store, {
  async optimizePrompt(payload) {
    captured = payload;
    return {
      user_prompt: "Intent: localized edit template\nSubject/Character: remove only the black stockings and keep identity stable.",
      user_prompt_plain: "remove only the black stockings and keep identity stable",
      optimization_mode: "i2i",
      context_bundle: { warnings: [] },
    };
  },
}, "image_1", button, textarea);
const node = state.nodes.image_1;
process.stdout.write(JSON.stringify({
  requestPrompt: captured.prompt_text,
  requestMode: captured.node_parameters.reference_transform_mode,
  requestRevision: captured.node_parameters.asset_card_revision.changed_fields[0].to,
  visibleDraftText: node.params.assetCardDraft.user_edited_text,
  visiblePrompt: node.prompt,
  visibleRevision: node.params.assetCardRevision.changed_fields[0].to,
  visibleMode: node.params.assetCardRevision.mode,
  textarea: textarea.value,
  status: node.params.promptOptimizationState.status,
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["requestPrompt"] == "remove the black stockings"
    assert payload["requestMode"] == "originalize_ip_safe"
    assert payload["requestRevision"] == "remove the black stockings"
    assert payload["visibleDraftText"].startswith("Intent: localized edit template")
    assert payload["visiblePrompt"] == payload["visibleDraftText"]
    assert " ".join(payload["visibleRevision"].split()) == " ".join(payload["visibleDraftText"].split())
    assert payload["visibleMode"] == "originalize_ip_safe"
    assert payload["textarea"] == payload["visibleDraftText"]
    assert payload["status"] == "complete"


def test_script_like_text_node_optimization_uses_script_surface_contract() -> None:
    script = r'''
import { buildOptimizationRequest } from "./apps/studio/src/optimizer-contract.js";

const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: "",
      content: "片名：《白骨灯》\n\n唐僧娶了白骨精，婚礼夜里孙悟空和猪八戒在殿外旁观。唐僧发现灯影里有第二副白骨。结尾，白骨精把红盖头递到他手里。",
      params: {
        scriptInputMode: "idea_expanded_script",
        sourceTextNodeId: "seed_text",
        storyboardBreakdown: { status: "shots_ready_for_review", shots: [{ shot_id: "shot_01" }] },
      },
      status: "complete",
    },
  },
  edges: {},
  assets: [],
  groups: {},
};
const request = buildOptimizationRequest(state, state.nodes.text_1);
process.stdout.write(JSON.stringify({
  generationTarget: request.generation_target,
  scriptInputMode: request.node_parameters.scriptInputMode,
  scriptIntent: request.node_parameters.script_surface_intent,
  sourceTextNodeId: request.node_parameters.sourceTextNodeId,
  shotCount: request.node_parameters.storyboardBreakdown.shot_count,
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["generationTarget"] == "script"
    assert payload["scriptInputMode"] == "idea_expanded_script"
    assert payload["scriptIntent"] == "preserve_script_shape"
    assert payload["sourceTextNodeId"] == "seed_text"
    assert payload["shotCount"] == 1


def test_studio_state_save_strips_provider_raw_but_keeps_safe_model_summary() -> None:
    script = r'''
import { normalizeSnapshot } from "./apps/studio/src/store-state.js";

const snapshot = normalizeSnapshot({
  meta: { projectId: "proj_safe_summary", projectName: "Safe", canvasName: "Canvas", seq: 1 },
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      title: "文本",
      prompt: "剧本正文",
      content: "剧本正文",
      status: "complete",
      params: {
        uploads: [{
          asset_id: "img_unsafe_note",
          role: "reference_image",
          user_intent: "Authorization=TOPSECRET token=MYTOKEN secret=MYSECRET api_key=APIKEY signed_url=https://private.example/out.png",
        }],
        lastContextBundle: {
          included_assets: [{ asset_id: "asset_1", provider_raw: { unsafe: true }, label: "孙悟空" }],
          text_channel: { raw_provider_response: { unsafe: true }, safe_summary: "safe" },
        },
        lastModelCallContextSummary: {
          artifact: { filename: "model_call_context.json" },
          safety_boundary: { no_provider_raw: true, no_local_path: true },
        },
        lastGenerationManifest: {
          status: "blocked",
          batch_status: "failed",
          stage: "provider_request_read",
          failure_class: "provider_timeout",
          output_count: 0,
          retry_count: 1,
          raw_provider_response_stored: false,
          provider_raw_persisted: false,
          provider_diagnostics: {
            provider_stage: "provider_request_read",
            failure_class: "provider_timeout",
            reason: "API relay request timed out while reading provider result",
            retry_count: 1,
            attempt_count: 2,
          },
          blocks: [{
            block_id: "remote_image_provider_not_ready",
            reason: "The read operation timed out",
            failure_class: "provider_timeout",
            provider_stage: "provider_request_read",
            provider_raw_persisted: false,
          }],
        },
        generationBlockedReason: "provider_raw_persisted false should not reach persistence",
        lastCreativeRuntimeContractSummary: {
          contract_id: "crtc_safe_summary",
          artifact: { filename: "creative_runtime_contract.json" },
          provider_context: {
            required_gate: "AFS_ALLOW_REMOTE_LLM",
            provider_calls_started: false,
            provider_raw_response: { unsafe: true },
          },
          non_claims: ["not_provider_execution", "not_human_acceptance"],
        },
      },
    },
  },
  edges: {},
  order: ["text_1"],
  assets: [
    {
      asset_id: "img_1",
      label: "候选图",
      preview_url: "/projects/proj_safe_summary/image-assets/img_1/preview",
      provider_raw_response: { unsafe: true },
    },
  ],
});

process.stdout.write(JSON.stringify(snapshot));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "provider_raw_response" not in serialized
    assert "raw_provider_response" not in serialized
    assert "provider_raw_persisted" not in serialized
    assert "raw_provider_response_stored" not in serialized
    assert '"provider_raw"' not in serialized
    assert "Authorization=TOPSECRET" not in serialized
    assert "MYTOKEN" not in serialized
    assert "MYSECRET" not in serialized
    assert "APIKEY" not in serialized
    assert "signed_url" not in serialized
    summary = payload["nodes"]["text_1"]["params"]["lastModelCallContextSummary"]
    assert summary["safety_boundary"]["no_provider_raw"] is True
    assert summary["safety_boundary"]["no_secrets"] is False
    assert summary["artifact"]["filename"] == "model_call_context.json"
    manifest = payload["nodes"]["text_1"]["params"]["lastGenerationManifest"]
    assert manifest["stage"] == "provider_request_read"
    assert manifest["failure_class"] == "provider_timeout"
    assert manifest["blocks"][0]["provider_stage"] == "provider_request_read"
    assert manifest["provider_diagnostics"]["attempt_count"] == 2
    assert "provider-response-redacted" in payload["nodes"]["text_1"]["params"]["generationBlockedReason"]
    contract = payload["nodes"]["text_1"]["params"]["lastCreativeRuntimeContractSummary"]
    assert contract["contract_id"] == "crtc_safe_summary"
    assert contract["artifact"]["filename"] == "creative_runtime_contract.json"
    assert contract["provider_context"]["provider_calls_started"] is False
    assert "provider_raw_response" not in json.dumps(contract, ensure_ascii=False)


def test_creative_runtime_contract_summary_renders_for_content_and_prompt_nodes() -> None:
    script = r'''
import { buildNodeBody } from "./apps/studio/src/canvas-node-body.js";

function makeElement(tagName) {
  const element = {
    tagName: String(tagName || "").toUpperCase(),
    children: [],
    dataset: {},
    style: {},
    className: "",
    title: "",
    value: "",
    placeholder: "",
    disabled: false,
    textContent: "",
    innerHTML: "",
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    addEventListener() {},
  };
  Object.defineProperty(element, "innerText", {
    get() {
      const own = [this.textContent, String(this.innerHTML || "").replace(/<[^>]+>/g, " ")]
        .filter(Boolean)
        .join(" ");
      return [own, ...this.children.map((child) => child.innerText || child.textContent || "")]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    },
  });
  return element;
}

globalThis.document = { createElement: makeElement };

const summary = {
  contract_id: "crtc_prompt_text_001",
  schema_version: "afs_creative_runtime_contract.v0.1",
  operation: "prompt_optimization",
  generation_target: "prompt",
  artifact: { filename: "creative_runtime_contract.json" },
  memory_context: { project_memory_count: 0, user_preference_count: 0, promotion_candidates_only: true },
  knowledge_context: { rule_count: 3, director_scenario_count: 0, registry_hash: "kb_hash_001" },
  asset_context: { fixed_asset_count: 1, draft_asset_count: 0, unresolved_asset_count: 0 },
  model_call_context: { context_id: "mctx_prompt_text_001", schema_version: "afs_model_call_context.v0.1" },
  provider_context: {
    capability: "llm",
    required_gate: "AFS_ALLOW_REMOTE_LLM",
    gate_status: "blocked",
    provider_calls_started: false,
    provider_raw_response: { unsafe: true },
  },
  evidence_context: {
    model_call_context_id: "mctx_prompt_text_001",
    safe_manifest_ref: "prompt_optimization_safe_manifest.json",
  },
  non_claims: ["not_provider_execution", "not_generated_media_qa", "not_human_acceptance"],
};

const textNode = {
  id: "text_1",
  type: "text",
  content: "Optimized text",
  prompt: "Optimized text",
  status: "complete",
  params: { lastCreativeRuntimeContractSummary: summary },
};
const imageNode = {
  id: "image_1",
  type: "image",
  prompt: "Optimized image prompt",
  status: "empty",
  params: { lastCreativeRuntimeContractSummary: summary },
};

const textRendered = buildNodeBody(textNode, { icon: "text", intents: [] }, null).map((part) => part.innerText).join(" ");
const imageRendered = buildNodeBody(imageNode, { icon: "image", intents: [] }, null).map((part) => part.innerText).join(" ");
process.stdout.write(JSON.stringify({ textRendered, imageRendered }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    for rendered in (payload["textRendered"], payload["imageRendered"]):
        assert "本次制作依据" in rendered
        assert "尚未开始生成 · 3 条规则" in rendered
        assert "生成状态：尚未开始" in rendered
        assert "制作规则：3 条" in rendered
        assert "参考素材：1 个固定 / 0 个草稿" in rendered
        assert "待确认素材：0 个" in rendered
        assert "产物记录：已记录" in rendered
        assert "需人工确认：3 项" in rendered
        assert "Creative contract" not in rendered
        assert "prompt_optimization" not in rendered
        assert "AFS_ALLOW_REMOTE_LLM" not in rendered
        assert "provider" not in rendered.lower()
        assert "crtc_prompt_text_001" not in rendered
        assert "mctx_prompt_text_001" not in rendered
        assert "provider_raw_response" not in rendered


def test_text_node_has_script_import_expand_and_breakdown_controls() -> None:
    prompt_bar = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")
    canvas_action_handler = (STUDIO_ROOT / "src" / "canvas-node-action-handler.js").read_text(encoding="utf-8")
    node_actions = (STUDIO_ROOT / "src" / "node-actions.js").read_text(encoding="utf-8")
    script_breakdown = (STUDIO_ROOT / "src" / "script-breakdown.js").read_text(encoding="utf-8")
    script_file_import = (STUDIO_ROOT / "src" / "script-file-import.js").read_text(encoding="utf-8")
    nodes = (STUDIO_ROOT / "src" / "nodes.js").read_text(encoding="utf-8")

    assert "importScriptFileIntoTextNode" in prompt_bar
    assert "expandTextIdeaToScript" in prompt_bar
    assert "splitTextNodeToStoryboardNodes" in prompt_bar
    assert "导入剧本" in prompt_bar
    assert "扩写剧本" in prompt_bar
    assert "拆分分镜" in prompt_bar
    assert "export function splitScriptIntoShots" in script_breakdown
    assert "formal_script_before_storyboard_breakdown" in script_breakdown
    assert "storyboard_placeholder_outline" in script_breakdown
    assert "looksLikeStoryboardPlaceholder" in script_breakdown
    assert "SCRIPT_UPLOAD_ACCEPT" in script_breakdown
    assert '["text", "script"].includes(node.type) && action === "upload"' in canvas_action_handler
    assert 'node.type === "text" && action === "upload"' not in canvas_action_handler
    assert canvas_action_handler.index('["text", "script"].includes(node.type) && action === "upload"') < canvas_action_handler.index('else if (action === "upload") uploadNodeImage')
    for marker in (".docx", ".pptx", ".doc", ".ppt", "readScriptFileText", "extractLegacyOfficeBinaryText"):
        assert marker in script_file_import
    assert 'createNode(store, "script"' in script_breakdown
    assert "connect(store, fresh.id, shotNode.id)" in script_breakdown
    assert "剧本拆分分镜" in nodes
    assert "splitTextNodeToStoryboardNodes(store, node, runtime)" in node_actions


def test_storyboard_breakdown_runtime_failure_is_visible_not_local_fallback() -> None:
    script = r'''
import { splitTextNodeToStoryboardNodes } from "./apps/studio/src/script-breakdown.js";

const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: "小明有一只猫，小猫捡到了一只狗。",
      content: "小明有一只猫，小猫捡到了一只狗。",
      params: {},
      status: "complete",
      x: 0,
      y: 0,
      w: 280,
      h: 280,
    },
  },
  edges: {},
  order: ["text_1"],
  assets: [],
  groups: {},
  selection: { nodeIds: ["text_1"], edgeId: null },
  ui: {},
};
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
  nextId: (prefix) => `${prefix}_${Object.keys(state.nodes).length + 1}`,
};
const runtime = {
  breakdownStoryboard: async () => {
    throw new Error("Runtime request failed (422): provider output rejected");
  },
};
const created = await splitTextNodeToStoryboardNodes(store, state.nodes.text_1, runtime);
process.stdout.write(JSON.stringify({ created, node: state.nodes.text_1, node_count: Object.keys(state.nodes).length }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["created"] == []
    assert payload["node_count"] == 1
    assert payload["node"]["status"] == "error"
    assert payload["node"]["params"]["storyboardBreakdownState"]["status"] == "failed"
    assert payload["node"]["params"]["generationPolicyStatus"] == "needs_attention"
    assert "小明有一只猫" in payload["node"]["prompt"]


def test_storyboard_breakdown_runtime_local_fallback_is_visible_to_tester() -> None:
    script = r'''
import { splitTextNodeToStoryboardNodes } from "./apps/studio/src/script-breakdown.js";

const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: "小明蹲在老城区巷口，橘猫“煤球”叼回一只湿漉漉的奶狗。",
      content: "小明蹲在老城区巷口，橘猫“煤球”叼回一只湿漉漉的奶狗。",
      params: {},
      status: "complete",
      x: 0,
      y: 0,
      w: 280,
      h: 280,
    },
  },
  edges: {},
  order: ["text_1"],
  assets: [],
  groups: {},
  selection: { nodeIds: ["text_1"], edgeId: null },
  ui: {},
};
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
  nextId: (prefix) => `${prefix}_${Object.keys(state.nodes).length + 1}`,
};
const runtime = {
  breakdownStoryboard: async () => ({
    provider_calls_started: false,
    fallback_visible_to_user: true,
    fallback_reason: "llm_gate_blocked",
    fallback_message: "LLM gate 未开启，已使用本地保守分镜；结果需要人工复核后再继续资产识别。",
    safe_manifest: {
      status: "local_fallback",
      fallback_visible_to_user: true,
      fallback_reason: "llm_gate_blocked",
      fallback_message: "LLM gate 未开启，已使用本地保守分镜；结果需要人工复核后再继续资产识别。",
    },
    shots: [{
      shot_id: "shot_01",
      index: 1,
      duration: "6s",
      description: "@小明 @煤球 @奶狗 @老城区巷口。小明蹲在老城区巷口，橘猫“煤球”叼回一只湿漉漉的奶狗。",
      shot_size: "中景",
      light_atmosphere: "自然光影",
      camera_motion: "固定机位",
      dialogue: "无明确对白",
      sound: "环境底噪",
      asset_refs: [
        { label: "小明", asset_type: "character", status: "candidate", source: "candidate" },
        { label: "煤球", asset_type: "character", character_subtype: "animal", status: "candidate", source: "candidate" },
        { label: "奶狗", asset_type: "character", character_subtype: "animal", status: "candidate", source: "candidate" },
        { label: "老城区巷口", asset_type: "scene", status: "candidate", source: "candidate" },
      ],
    }],
  }),
};
const created = await splitTextNodeToStoryboardNodes(store, state.nodes.text_1, runtime);
process.stdout.write(JSON.stringify({ created, node: state.nodes.text_1, shot: state.nodes[created[0]] }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert len(payload["created"]) == 1
    assert payload["node"]["status"] == "complete"
    assert payload["node"]["params"]["storyboardBreakdownState"]["status"] == "fallback"
    assert payload["node"]["params"]["storyboardBreakdown"]["fallback_reason"] == "llm_gate_blocked"
    assert payload["node"]["params"]["generationPolicyStatus"] == "needs_attention"
    assert "LLM gate" in payload["node"]["params"]["generationBlockedReason"]
    assert "煤球" in payload["shot"]["prompt"]


def test_storyboard_breakdown_preserves_empty_runtime_asset_refs_without_global_pollution() -> None:
    script = r'''
import { splitTextNodeToStoryboardNodes } from "./apps/studio/src/script-breakdown.js";

const rainScript = "《雨痕》高中生小红站在空荡校门口屋檐外。远处教学楼顶，闪电劈亮整片灰云。";
const cleanShotDescription = "特写试卷背面：小红缓缓翻转试卷，露出密密麻麻补写的演算字迹；最后一行手写：“第12题，其实可以换种思路。”墨迹未干，在湿纸上微微晕散。";
const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: rainScript,
      content: rainScript,
      params: {},
      status: "complete",
      x: 0,
      y: 0,
      w: 280,
      h: 280,
    },
  },
  edges: {},
  order: ["text_1"],
  assets: [],
  groups: {},
  selection: { nodeIds: ["text_1"], edgeId: null },
  ui: {},
};
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
  nextId: (prefix) => `${prefix}_${Object.keys(state.nodes).length + 1}`,
};
const runtime = {
  breakdownStoryboard: async () => ({
    provider_calls_started: true,
    fallback_visible_to_user: false,
    safe_manifest: { status: "provider_structured", fallback_visible_to_user: false },
    asset_auto_binding_graph: {
      artifact_type: "agentflow_asset_auto_binding_graph",
      algorithm_id: "test",
      binding_suggestions: [
        {
          binding_id: "bind_xiaohong",
          binding_state: "bound",
          graph_asset_id: "graph:character:小红",
          fixed_visual_asset_id: "asset_xiaohong",
          asset_type: "character",
          label: "小红",
          confidence: 1,
        },
        {
          binding_id: "bind_rooftop",
          binding_state: "bound",
          graph_asset_id: "graph:scene:屋顶平台",
          fixed_visual_asset_id: "asset_rooftop",
          asset_type: "scene",
          label: "屋顶平台",
          confidence: 1,
        },
      ],
    },
    shots: [{
      shot_id: "S05",
      index: 5,
      duration: "2.2",
      description: cleanShotDescription,
      shot_size: "特写",
      light_atmosphere: "阴天漫射光",
      camera_motion: "缓慢下移聚焦最后一行字",
      dialogue: "第12题，其实可以换种思路。",
      sound: "纸页翻转窸窣声",
      asset_refs: [],
      dropped_asset_ref_diagnostics: [
        { label: "数学试卷", asset_type: "prop", reason: "prop_requires_manual_asset_entry" },
      ],
    }],
  }),
};
const created = await splitTextNodeToStoryboardNodes(store, state.nodes.text_1, runtime);
const shot = state.nodes[created[0]];
process.stdout.write(JSON.stringify({ created, shot }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    shot = payload["shot"]
    structured = shot["params"]["structuredShot"]

    assert len(payload["created"]) == 1
    assert structured["asset_refs"] == []
    assert shot["params"]["shotAssetRefs"] == []
    assert "无明确可固定资产" in shot["prompt"]
    assert "@可见人物" not in shot["prompt"]
    assert "@屋顶平台" not in shot["prompt"]
    assert "@小红" not in structured["description"]
    assert "@屋顶平台" not in structured["description"]
    assert "nodeReferenceStack" not in shot["params"]


def test_asset_auto_binding_does_not_match_empty_shot_refs_to_every_asset() -> None:
    script = r'''
import { nodeReferenceStackForGraphBoundAssets } from "./apps/studio/src/asset-auto-binding-refs.js";

const graph = {
  artifact_type: "agentflow_asset_auto_binding_graph",
  algorithm_id: "test",
  binding_suggestions: [{
    binding_id: "bind_xiaohong",
    binding_state: "bound",
    graph_asset_id: "graph:character:小红",
    fixed_visual_asset_id: "asset_xiaohong",
    asset_type: "character",
    label: "小红",
    confidence: 1,
  }],
};
const empty = nodeReferenceStackForGraphBoundAssets(graph, { asset_refs: [] }, "shot_empty");
const matched = nodeReferenceStackForGraphBoundAssets(graph, {
  asset_refs: [{ graph_asset_id: "graph:character:小红", asset_type: "character", label: "小红" }],
}, "shot_match");
process.stdout.write(JSON.stringify({ empty, matched }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["empty"] is None
    assert payload["matched"]["summary"]["asset_auto_binding_reference_count"] == 1


def test_idea_expansion_fallback_outputs_formal_script_not_storyboard_template() -> None:
    script = r'''
import { expandTextIdeaToScript } from "./apps/studio/src/script-breakdown.js";

const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: "一个来自未来的机器人，在农村屋顶上看星星",
      content: "",
      params: {},
      status: "empty",
    },
  },
  edges: {},
  order: ["text_1"],
  assets: [],
  groups: {},
  selection: { nodeIds: ["text_1"], edgeId: null },
  ui: {},
};
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
};
await expandTextIdeaToScript(store, null, state.nodes.text_1);
process.stdout.write(JSON.stringify(state.nodes.text_1));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    node = json.loads(completed.stdout)

    assert node["params"]["scriptInputMode"] == "idea_expanded_script"
    assert node["params"]["scriptExpansionSourceIdea"] == "一个来自未来的机器人，在农村屋顶上看星星"
    assert "片名：《" in node["prompt"]
    assert "遥星R-17" in node["prompt"]
    assert "屋顶" in node["prompt"]
    assert "童声" in node["prompt"]
    assert "正式短视频剧本" not in node["prompt"]
    assert "分镜 01" not in node["prompt"]
    assert "推进主体" not in node["prompt"]
    assert "展示变化" not in node["prompt"]
    assert "收束结果" not in node["prompt"]


def test_idea_expansion_request_preserves_source_idea_and_rejects_optimizer_output() -> None:
    script = r'''
import { expandTextIdeaToScript } from "./apps/studio/src/script-breakdown.js";

const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: "一个人在睡觉",
      content: "",
      params: {},
      status: "empty",
    },
  },
  edges: {},
  order: ["text_1"],
  assets: [],
  groups: {},
  selection: { nodeIds: ["text_1"], edgeId: null },
  ui: {},
};
let captured = null;
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
};
const runtime = {
  optimizePrompt: async (request) => {
    captured = request;
    const text = [
      "意图：围绕一个人在睡觉形成清晰创作方向。",
      "角色/主体：Primary character。",
      "场景/美术：Primary scene。",
      "负面约束：不要水印。"
    ].join("\n");
    return {
      user_prompt: text,
      user_prompt_plain: text
    };
  }
};
await expandTextIdeaToScript(store, runtime, state.nodes.text_1);
process.stdout.write(JSON.stringify({ node: state.nodes.text_1, captured }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    node = payload["node"]
    captured = payload["captured"]

    assert captured["prompt_text"] != "一个人在睡觉"
    assert "原始想法：一个人在睡觉" in captured["prompt_text"]
    assert captured["node_parameters"]["source_idea"] == "一个人在睡觉"
    assert captured["node_parameters"]["script_generation_mode"] == "idea_to_script"
    assert captured["node_parameters"]["remote_optimizer_required"] is True
    assert captured["node_parameters"]["llm_provider"] == "prompt_optimizer"
    assert captured["node_parameters"]["llm_model"] == "prompt-optimizer"
    assert node["params"]["scriptInputMode"] == "idea_expanded_script"
    assert node["params"]["scriptExpansionSourceIdea"] == "一个人在睡觉"
    assert "片名：《" in node["prompt"]
    assert "意图：" not in node["prompt"]
    assert "角色/主体：" not in node["prompt"]
    assert "Primary character" not in node["prompt"]


def test_idea_expansion_reuses_original_source_and_does_not_nest_generated_script() -> None:
    script = r'''
import { expandTextIdeaToScript } from "./apps/studio/src/script-breakdown.js";

const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: "白雪公主穿越到现代",
      content: "",
      params: {},
      status: "empty",
    },
  },
  edges: {},
  order: ["text_1"],
  assets: [],
  groups: {},
  selection: { nodeIds: ["text_1"], edgeId: null },
  ui: {},
};
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
};
const prompts = [];
const runtime = {
  async optimizePrompt(payload) {
    prompts.push(payload.node_parameters.source_idea);
    return {
      user_prompt: `片名：《现代白雪》\n\n成稿来自：${payload.node_parameters.source_idea}`,
      user_prompt_plain: `片名：《现代白雪》\n\n成稿来自：${payload.node_parameters.source_idea}`,
      optimization_mode: "script",
      context_bundle: { warnings: [] },
    };
  },
};
await expandTextIdeaToScript(store, runtime, state.nodes.text_1);
const first = state.nodes.text_1.prompt;
await expandTextIdeaToScript(store, runtime, state.nodes.text_1);
process.stdout.write(JSON.stringify({
  prompts,
  first,
  second: state.nodes.text_1.prompt,
  source: state.nodes.text_1.params.scriptExpansionSourceIdea,
  mode: state.nodes.text_1.params.scriptInputMode,
  status: state.nodes.text_1.params.scriptExpansionState.status,
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["prompts"] == ["白雪公主穿越到现代", "白雪公主穿越到现代"]
    assert payload["first"] == payload["second"]
    assert payload["source"] == "白雪公主穿越到现代"
    assert payload["mode"] == "idea_expanded_script"
    assert payload["status"] == "complete"


def test_idea_expansion_runtime_failure_is_visible_not_local_fallback() -> None:
    script = r'''
import { expandTextIdeaToScript } from "./apps/studio/src/script-breakdown.js";

const state = {
  nodes: {
    text_1: {
      id: "text_1",
      type: "text",
      prompt: "一个人在睡觉",
      content: "",
      params: {},
      status: "empty",
    },
  },
  edges: {},
  order: ["text_1"],
  assets: [],
  groups: {},
  selection: { nodeIds: ["text_1"], edgeId: null },
  ui: {},
};
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
};
const runtime = {
  optimizePrompt: async () => {
    const error = new Error("Runtime request failed (422): remote_llm_gate_closed");
    error.payload = {
      detail: {
        error: "invalid_prompt_optimization",
        message: "remote LLM prompt optimization unavailable: remote_llm_gate_closed",
        details: { raw_detail: "remote LLM prompt optimization unavailable: remote_llm_gate_closed" },
      },
    };
    throw error;
  }
};
await expandTextIdeaToScript(store, runtime, state.nodes.text_1);
process.stdout.write(JSON.stringify(state.nodes.text_1));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    node = json.loads(completed.stdout)

    assert node["status"] == "error"
    assert node["params"]["scriptExpansionState"]["status"] == "failed"
    assert node["params"]["generationPolicyStatus"] == "needs_attention"
    assert node["params"]["generationBlockedReason"] == "提示词优化失败，请检查生成服务配置或稍后重试。"
    assert "remote_llm_gate_closed" not in node["params"]["generationBlockedReason"]
    assert node["params"].get("scriptInputMode") != "idea_expanded_script_fallback"
    assert "片名：《" not in node["prompt"]


def test_text_script_body_receives_generated_content_and_keeps_editable_surface() -> None:
    script_breakdown = (STUDIO_ROOT / "src" / "script-breakdown.js").read_text(encoding="utf-8")
    canvas_body = (STUDIO_ROOT / "src" / "canvas-node-body.js").read_text(encoding="utf-8")
    canvas_view = (STUDIO_ROOT / "src" / "canvas-view.js").read_text(encoding="utf-8")
    canvas_input = (STUDIO_ROOT / "src" / "canvas-input.js").read_text(encoding="utf-8")
    prompt_bar = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")
    action_handler = (STUDIO_ROOT / "src" / "canvas-node-action-handler.js").read_text(encoding="utf-8")
    styles = _styles()

    assert "node.content = prompt" in script_breakdown
    assert "visibleText" in script_breakdown
    assert "scriptExpansionState?.status === \"running\"" in canvas_view
    assert "promptTaskLabel(task)" in prompt_bar
    assert "storyboardBreakdownState" in prompt_bar
    assert "node-content-editor" in canvas_body
    assert "text-content-view" in canvas_body
    assert "openNodePromptEditor" in canvas_input
    assert "promptBarNodeId" in prompt_bar
    assert "scriptExpansionSourceIdea" in script_breakdown
    assert "delete n.params.scriptExpansionSourceIdea" in prompt_bar
    assert "content-shimmer" in canvas_body
    assert ".text-content-view.content-shimmer" in styles
    assert "node-context-toolbar" not in canvas_view
    assert "node-context-toolbar" not in styles
    assert '["text", "script"].includes(node.type) && action === "upload"' in action_handler


def test_storyboard_breakdown_creates_reviewable_structured_shots_without_asset_prep_nodes() -> None:
    script_breakdown = (STUDIO_ROOT / "src" / "script-breakdown.js").read_text(encoding="utf-8")
    structured_shot = (STUDIO_ROOT / "src" / "structured-shot.js").read_text(encoding="utf-8")
    asset_nodes = (STUDIO_ROOT / "src" / "shot-asset-nodes.js").read_text(encoding="utf-8")

    assert "structuredShotFromSegment" in script_breakdown
    assert "breakdownStoryboard" in script_breakdown
    assert "createShotAssetPrepNodes" not in script_breakdown
    assert "export function structuredShotFromSegment" in structured_shot
    for field in ["镜号：", "时长：", "画面描述：", "景别：", "光影氛围：", "运镜：", "资产："]:
        assert field in structured_shot
    assert "export function extractShotAssetRefs" in structured_shot
    assert "KNOWN_CHARACTER_NAMES" in structured_shot
    assert '"孙悟空"' in structured_shot
    assert '"金刚狼"' in structured_shot
    assert '"金箍棒"' in structured_shot
    assert '"山巅"' in structured_shot
    assert '"战场"' in structured_shot
    assert '"主要场景"' in structured_shot
    assert '"路灯"' in structured_shot
    assert '"信", "照片", "灯"' not in structured_shot
    assert "shotAssetRefs" in script_breakdown
    assert "assetPrepState" in script_breakdown
    assert "pending_user_review" in script_breakdown
    assert 'createNode(store, "image"' in asset_nodes
    assert "asset_prep" in asset_nodes
    assert "connect(store, scriptNodeId, assetNode.id)" in asset_nodes


def test_storyboard_asset_cards_are_editable_candidates_before_fixed_context() -> None:
    asset_drafts = (STUDIO_ROOT / "src" / "asset-card-drafts.js").read_text(encoding="utf-8")
    asset_nodes = (STUDIO_ROOT / "src" / "shot-asset-nodes.js").read_text(encoding="utf-8")
    asset_panel = (STUDIO_ROOT / "src" / "panels" / "asset-card-panel.js").read_text(encoding="utf-8")
    node_menu = (STUDIO_ROOT / "src" / "panels" / "node-menu.js").read_text(encoding="utf-8")

    assert "assetCardDraftFromRef" in asset_drafts
    assert "assetCardText" in asset_drafts
    assert "asset-card-image-prompts.js" in asset_nodes
    assert "included_in_context_before_confirmation: false" in asset_drafts
    assert "node.params.assetCardDraft" in asset_nodes
    assert 'node.params.nodeRole = "asset_card_draft"' in asset_nodes
    assert "node.params.visualAssets" not in asset_nodes
    assert "openAssetCardPanel" in asset_panel
    assert "保存并重新生成资产图" in asset_panel
    assert "局部图像编辑未开放" in asset_panel
    assert "startNodeGeneration" in asset_panel
    assert "await store.flushRuntimeSave?.();" in asset_panel
    assert "编辑资产卡" in node_menu
    assert "openAssetCardPanel(store, nodeId, runtime)" in node_menu


def test_asset_card_drafts_clean_legacy_tag_pollution_and_backfill_reference_views() -> None:
    script = r'''
import {
  assetCardDraftFromRef,
  normalizeAssetCardDraft,
} from "./apps/studio/src/asset-card-drafts.js";
import { assetImagePrompt } from "./apps/studio/src/asset-card-image-prompts.js";
import { structuredShotFromSegment } from "./apps/studio/src/structured-shot.js";

const shot = {
  shot_id: "shot_01",
  description: "@主角 @主要场景。描绘一个来自未来的机器人在城市屋顶静静仰望星空的孤独、沉静、诗意的科幻瞬间。夜晚的高层城市屋顶，远处高楼灯火与天际线微弱闪烁，头顶星空清澈深远，冷蓝月光与星光主导。",
};
const structured = structuredShotFromSegment(shot.description, 1);
const character = assetCardDraftFromRef({ label: "主角", asset_type: "character" }, shot);
const scene = assetCardDraftFromRef({ label: "主要场景", asset_type: "scene" }, shot);
const legacyCharacter = normalizeAssetCardDraft({
  asset_type: "character",
  label: "主角",
  signature: "主角：@主角 @主要场景",
  feature_card: {
    identity: "来自未来的机器人主角",
    appearance: "金属机身，精密发光纹路",
    demeanor: "@主角 @主要场景",
  },
  evidence_text: shot.description,
});
const legacyScene = normalizeAssetCardDraft({
  asset_type: "scene",
  label: "主要场景",
  signature: "主要场景：@主角 @主要场景",
  feature_card: {
    location: "夜晚城市屋顶/楼顶平台",
    lighting_mood: "@主角 @主要场景",
  },
  evidence_text: shot.description,
});

process.stdout.write(JSON.stringify({
  structured,
  character,
  scene,
  legacyCharacter,
  legacyScene,
  legacyCharacterPrompt: assetImagePrompt(legacyCharacter),
  legacyScenePrompt: assetImagePrompt(legacyScene),
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    labels = [item["label"] for item in payload["structured"]["asset_refs"]]

    generated_surface = json.dumps(
        {
            "legacyCharacterSignature": payload["legacyCharacter"]["signature"],
            "legacyCharacterFeatureCard": payload["legacyCharacter"]["feature_card"],
            "legacySceneSignature": payload["legacyScene"]["signature"],
            "legacySceneFeatureCard": payload["legacyScene"]["feature_card"],
            "legacyCharacterPrompt": payload["legacyCharacterPrompt"],
            "legacyScenePrompt": payload["legacyScenePrompt"],
        },
        ensure_ascii=False,
    )
    assert labels[:2] == ["未来机器人", "夜晚城市屋顶"]
    assert "@主角" not in payload["structured"]["description"]
    assert "@主要场景" not in payload["structured"]["description"]
    assert "@主角 @主要场景" not in generated_surface
    assert payload["character"]["feature_card"]["reference_views"]
    assert payload["scene"]["feature_card"]["view_set"]
    assert payload["legacyCharacter"]["feature_card"]["reference_views"]
    assert payload["legacyScene"]["feature_card"]["view_set"]
    assert "Character reference sheet" in payload["legacyCharacterPrompt"]
    assert "front half-body close-up" in payload["legacyCharacterPrompt"]
    assert "Environment reference" in payload["legacyScenePrompt"]
    for polluted in ("多视图角色设定表", "多视角场景设定图", "设定板", "软件界面"):
        assert polluted not in payload["legacyCharacterPrompt"] + payload["legacyScenePrompt"]
    assert "Forbidden: software dashboard, app interface, data chart" in payload["legacyCharacterPrompt"]


def test_asset_card_draft_uses_runtime_profile_plan_for_animal_facts() -> None:
    script = r'''
import { assetCardDraftFromRef, assetCardText } from "./apps/studio/src/asset-card-drafts.js";

const shot = {
  shot_id: "S01",
  description: "镜号：01 时长：3.2 画面描述：@老槐树 @橘猫 @小狗。老槐树粗壮盘曲的树根特写，泥土湿润微裂；纸盒内橘猫蜷卧，尾巴尖轻晃，嘴里叼着湿漉漉的小狗，小狗左耳缺一小块，正打喷嚏 景别：特写 光影氛围：午后柔光，树影斑驳",
};
const cat = assetCardDraftFromRef({
  label: "橘猫",
  asset_type: "character",
  profile_plan: {
    character_subtype: "animal",
    facts: {
      identity: "橘猫",
      species: "猫",
      color_pattern: "橘色",
      current_action: ["蜷卧", "尾巴尖轻晃", "叼着小狗"],
      relationship: ["保护小狗"],
    },
    continuity_locks: ["保持橘猫身份", "保持橘色毛色", "保持尾巴动作和体型比例"],
    negative_locks: ["不要新增项圈或衣物"],
    fact_evidence: ["纸盒内橘猫蜷卧，尾巴尖轻晃，嘴里叼着湿漉漉的小狗"],
  },
}, shot);
const dog = assetCardDraftFromRef({
  label: "小狗",
  asset_type: "character",
  profile_plan: {
    character_subtype: "animal",
    facts: {
      identity: "小狗",
      species: "狗",
      color_pattern: "灰白相间",
      surface_state: "湿漉漉",
      size_or_age: "幼小",
      distinctive_marks: ["左耳缺一小块"],
      current_action: ["打喷嚏"],
    },
    continuity_locks: ["保持灰白相间毛色", "保持左耳缺一小块", "保持幼小体型"],
    negative_locks: ["不要新增项圈或衣物"],
  },
}, shot);
process.stdout.write(JSON.stringify({
  cat,
  dog,
  catText: assetCardText(cat),
  dogText: assetCardText(dog),
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    surface = json.dumps(payload, ensure_ascii=False)

    for expected in ("橘色", "尾巴尖轻晃", "叼着小狗", "灰白相间", "湿漉漉", "左耳缺一小块"):
        assert expected in surface
    for placeholder in ("身份与外观待确认", "服装或外观按分镜语境确定", "根据分镜描述确定可复用外观辨识点"):
        assert placeholder not in surface
    assert payload["cat"]["character_subtype"] == "animal"
    assert payload["dog"]["facts"]["color_pattern"] == "灰白相间"
    assert "资产类型：动物角色资产" in payload["catText"]
    assert "资产类型：动物角色资产" in payload["dogText"]
    assert "资产类型：角色资产" not in payload["catText"] + payload["dogText"]
    assert "无服装" in payload["catText"] + payload["dogText"]


def test_structured_shot_refinement_preserves_runtime_asset_profiles() -> None:
    script = r'''
import { refineStructuredShotAssets } from "./apps/studio/src/structured-shot.js";
import { assetCardDraftFromRef, assetCardText } from "./apps/studio/src/asset-card-drafts.js";

const shot = {
  shot_id: "S01",
  index: 1,
  description: "@小明 @橘猫 @老城区巷口。小明给橘猫顺毛。",
  asset_refs: [
    {
      label: "橘猫",
      asset_type: "character",
      status: "mentioned",
      source: "runtime_asset_plan",
      profile_plan: {
        character_subtype: "animal",
        facts: {
          identity: "橘猫",
          species: "猫",
          color_pattern: "橘色",
          current_action: ["顺毛"],
        },
        continuity_locks: ["保持橘色毛色"],
        negative_locks: ["不要新增衣物"],
      },
    },
  ],
};
const refined = refineStructuredShotAssets(shot, shot.description);
const draft = assetCardDraftFromRef(refined.asset_refs[0], refined);
process.stdout.write(JSON.stringify({
  ref: refined.asset_refs[0],
  draft,
  text: assetCardText(draft),
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["ref"]["profile_plan"]["character_subtype"] == "animal"
    assert payload["draft"]["character_subtype"] == "animal"
    assert payload["draft"]["facts"]["species"] == "猫"
    assert "资产类型：动物角色资产" in payload["text"]
    assert "根据分镜描述确定可复用外观辨识点" not in payload["text"]


def test_frontend_structured_shot_does_not_treat_bluestone_steps_as_battlefield() -> None:
    script = r'''
import { structuredShotFromSegment, structuredShotText } from "./apps/studio/src/structured-shot.js";

const shot = structuredShotFromSegment(
  "片名：《猫捡到狗那天》小明蹲在老城区巷口的青石台阶上，指尖沾着猫粮碎屑，正给蜷在纸箱里的橘猫顺毛。夕阳斜切过窄巷高墙，在青砖地面投下细长影子。",
  1,
);
process.stdout.write(JSON.stringify({ shot, text: structuredShotText(shot) }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    refs = {(item["label"], item["asset_type"]) for item in payload["shot"]["asset_refs"]}

    assert "山巅石台战场" not in payload["text"]
    assert ("老城区巷口", "scene") in refs


def test_frontend_structured_shot_does_not_fabricate_generic_assets_for_ancient_battlefield() -> None:
    script = r'''
import { structuredShotFromSegment, structuredShotText } from "./apps/studio/src/structured-shot.js";

const shot = structuredShotFromSegment(
  "《断戟惊雷》暴雨如注，古战场泥泞翻涌。沈砚单膝陷在泥中，右臂青筋暴起，死攥半截断戟，指节泛白如骨。",
  1,
);
process.stdout.write(JSON.stringify({ shot, text: structuredShotText(shot) }));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    refs = {(item["label"], item["asset_type"]) for item in payload["shot"]["asset_refs"]}
    surface = json.dumps(payload, ensure_ascii=False)

    assert "可见人物" not in surface
    assert "山巅石台战场" not in surface
    assert "@可见人物" not in surface
    assert "@山巅石台战场" not in surface
    assert ("沈砚", "character") in refs
    assert ("古战场", "scene") in refs
    assert not payload["shot"]["description"].startswith("@")


def test_storyboard_asset_recognition_prioritizes_principal_characters_and_key_props() -> None:
    script = r'''
import { structuredShotFromSegment } from "./apps/studio/src/structured-shot.js";

const shot = structuredShotFromSegment(
  "唐僧娶了白骨精，孙悟空和猪八戒在远处旁观。白骨精手边放着@金箍棒，殿内红烛摇晃。",
  1,
);
process.stdout.write(JSON.stringify({
  refs: shot.asset_refs.map((item) => [item.label, item.asset_type]),
  dropped: shot.dropped_asset_ref_diagnostics.map((item) => [item.label, item.asset_type, item.reason]),
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert ["唐僧", "character"] in payload["refs"]
    assert ["白骨精", "character"] in payload["refs"]
    assert ["金箍棒", "prop"] in payload["refs"]
    assert ["孙悟空", "character", "secondary_character_requires_manual_asset_entry"] in payload["dropped"]
    assert ["猪八戒", "character", "secondary_character_requires_manual_asset_entry"] in payload["dropped"]
    assert ["金箍棒", "prop", "prop_requires_manual_asset_entry"] not in payload["dropped"]


def test_asset_and_storyboard_cards_use_compact_editor_layout() -> None:
    body = (STUDIO_ROOT / "src" / "canvas-node-body.js").read_text(encoding="utf-8")
    styles = _styles()
    shot_asset_nodes = (STUDIO_ROOT / "src" / "shot-asset-nodes.js").read_text(encoding="utf-8")
    script_breakdown = (STUDIO_ROOT / "src" / "script-breakdown.js").read_text(encoding="utf-8")

    assert "asset-card-content-editor" in body
    assert ".node .node-content-editor.asset-card-content-editor" in styles
    assert "min-height: 112px" in styles
    assert "Math.max(230, Math.min(340" in shot_asset_nodes
    assert "Math.max(220, Math.min(360" in script_breakdown


def test_prompt_bar_canvas_double_click_and_node_motion_are_stable() -> None:
    prompt_bar = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")
    canvas_input = (STUDIO_ROOT / "src" / "canvas-input.js").read_text(encoding="utf-8")
    event_targets = (STUDIO_ROOT / "src" / "dom-event-targets.js").read_text(encoding="utf-8")
    styles = _styles()

    assert "s.ui.promptBarNodeId = node.id;" in prompt_bar
    assert "isBlankCanvasDoubleClick" in canvas_input
    assert "closestFromEvent(e, \".node\")" in canvas_input
    assert "event?.composedPath?.()" in event_targets
    assert "document.elementFromPoint" in event_targets
    assert "#canvas-empty-hint" in canvas_input
    assert 'new Set(["canvas-root", "canvas-viewport", "world", "node-layer"])' in canvas_input
    assert "Math.abs(dx) + Math.abs(dy) <= 2 && !session.moved" in canvas_input
    assert "node-land" not in styles
    assert "scale: 1.012" not in styles
    assert ".node:hover { transform" not in styles


def test_script_nodes_identify_assets_and_create_keyframe_layer_without_candidate_pollution() -> None:
    nodes = (STUDIO_ROOT / "src" / "nodes.js").read_text(encoding="utf-8")
    node_actions = (STUDIO_ROOT / "src" / "node-actions.js").read_text(encoding="utf-8")
    node_menu = (STUDIO_ROOT / "src" / "panels" / "node-menu.js").read_text(encoding="utf-8")
    storyboard_actions = (STUDIO_ROOT / "src" / "storyboard-node-actions.js").read_text(encoding="utf-8")
    keyframes = (STUDIO_ROOT / "src" / "storyboard-keyframes.js").read_text(encoding="utf-8")
    optimizer_contract = (STUDIO_ROOT / "src" / "optimizer-contract.js").read_text(encoding="utf-8")
    visible_assets = (STUDIO_ROOT / "src" / "node-visible-assets.js").read_text(encoding="utf-8")
    lifecycle = (STUDIO_ROOT / "src" / "asset-lifecycle.js").read_text(encoding="utf-8")

    assert "识别资产" in nodes
    assert "生成关键帧层" in nodes
    assert "identifyScriptAssets" in node_actions
    assert "createStoryboardKeyframeLayer" in node_actions
    assert "识别资产" in node_menu
    assert "生成关键帧层" in node_menu
    assert "createKeyframeNodesForStoryboard" in keyframes
    assert "ensureShotAssetPrepNodesForScriptNode(store, fresh)" not in storyboard_actions
    assert "existingShotAssetCardNodeIds" not in storyboard_actions
    assert "fixed_visual_asset_ids" in keyframes
    assert "missing_asset_card_node_ids" in keyframes
    assert "candidate_asset_card_node_ids" in keyframes
    assert "ready_without_fixed_assets" in keyframes
    assert "asset.params?.visualAssets" in keyframes
    assert "needs_fixed_assets" not in node_actions
    assert "shouldCollectConnectedUploads" in optimizer_contract
    assert '["keyframe_generation", "asset_card_draft"].includes(node?.params?.nodeRole)' in optimizer_contract
    assert "asset_card_draft" in optimizer_contract
    assert "character_asset_candidate" in visible_assets
    assert "prop_asset_candidate" in visible_assets
    assert 'kind.endsWith("_candidate")' in lifecycle
    assert 'kind === "prop_asset"' in lifecycle
    assert "connect(store, scriptNode.id, keyframeNode.id)" in keyframes


def test_script_node_menu_hides_generic_retry_generation() -> None:
    node_menu = (STUDIO_ROOT / "src" / "panels" / "node-menu.js").read_text(encoding="utf-8")
    node_actions = (STUDIO_ROOT / "src" / "node-actions.js").read_text(encoding="utf-8")
    generation_actions = (STUDIO_ROOT / "src" / "node-generation-actions.js").read_text(encoding="utf-8")
    canvas_view = (STUDIO_ROOT / "src" / "canvas-view.js").read_text(encoding="utf-8")
    canvas_body = (STUDIO_ROOT / "src" / "canvas-node-body.js").read_text(encoding="utf-8")
    prompt_bar = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")
    keyboard = (STUDIO_ROOT / "src" / "studio-keyboard.js").read_text(encoding="utf-8")

    assert "canRetryGeneration(node)" in node_menu
    assert "function canRetryGeneration(node)" in node_menu
    assert "return canRunNodeGeneration(node);" in node_menu
    assert "export function canRunNodeGeneration(node)" in node_actions
    assert "return canStartGenerationForNode(node);" in node_actions
    assert "return [\"image\", \"video\"].includes(node?.type);" in generation_actions
    assert "if (!canRunNodeGeneration(node))" in canvas_view
    assert 'runBtn.dataset.action = "run-disabled";' in canvas_view
    assert "if (node && canRunNodeGeneration(node)) startNodeGeneration" in keyboard
    assert "if (!canRunNodeGeneration(fresh)) return;" in prompt_bar
    assert "当前节点不支持直接生成，请使用该节点的专用操作" in prompt_bar
    assert "处理失败，请检查该节点的专用操作或错误详情" in canvas_body
    assert 'if (node.type === "script")' in node_menu
    assert "identifyScriptAssets(store, runtime, fresh)" in node_menu
    assert "createStoryboardKeyframeLayer(store, fresh)" in node_menu


def test_script_asset_recognition_replaces_stale_structured_shot_cards() -> None:
    script = r'''
import { identifyScriptAssets } from "./apps/studio/src/storyboard-node-actions.js";

const state = {
  nodes: {
    script_1: {
      id: "script_1",
      type: "script",
      title: "故事脚本",
      x: 0,
      y: 0,
      w: 320,
      h: 240,
      prompt: "黑色小狗在吃狗粮",
      content: "黑色小狗在吃狗粮",
      status: "complete",
      params: {
        scriptSegmentIndex: 1,
        structuredShot: {
          shot_id: "shot_01",
          index: 1,
          description: "@机器人 @夜晚城市屋顶。白色圆头机器人站在夜晚城市屋顶，手里拿着发光芯片",
          source_text: "白色圆头机器人站在夜晚城市屋顶，手里拿着发光芯片",
          asset_refs: [
            { label: "机器人", asset_type: "character", status: "candidate" },
            { label: "夜晚城市屋顶", asset_type: "scene", status: "candidate" },
          ],
        },
      },
    },
    stale_asset: {
      id: "stale_asset",
      type: "image",
      title: "角色资产 · @机器人",
      content: "资产名称：@机器人",
      params: {
        assetCardDraft: {
          source_script_node_id: "script_1",
          label: "机器人",
          asset_type: "character",
        },
      },
    },
  },
  edges: { edge_1: { id: "edge_1", from: "script_1", to: "stale_asset" } },
  order: ["script_1", "stale_asset"],
  groups: {},
  selection: { nodeIds: ["script_1"], edgeId: null },
  ui: {},
};

let nextId = 0;
const store = {
  get: () => state,
  set: (mutator) => mutator(state),
  nextId: (prefix) => `${prefix}_${++nextId}`,
};

await identifyScriptAssets(store, null, state.nodes.script_1);

process.stdout.write(JSON.stringify({
  nodes: state.nodes,
  order: state.order,
  edges: state.edges,
  structuredShot: state.nodes.script_1.params.structuredShot,
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    visible_text = json.dumps(payload, ensure_ascii=False)

    assert "stale_asset" not in payload["nodes"]
    assert "stale_asset" not in payload["order"]
    assert "edge_1" not in payload["edges"]
    assert payload["structuredShot"]["source_text"] == "黑色小狗在吃狗粮"
    assert "机器人" not in visible_text
    assert "夜晚城市屋顶" not in visible_text


def test_script_asset_recognition_does_not_create_template_cards_when_runtime_plan_fails() -> None:
    script = r'''
import { identifyScriptAssets } from "./apps/studio/src/storyboard-node-actions.js";

const state = {
  nodes: {
    script_1: {
      id: "script_1",
      type: "script",
      title: "分镜 01",
      x: 0,
      y: 0,
      w: 320,
      h: 240,
      prompt: "镜号：01 画面描述：@小明 @橘猫。小明轻抚怀中橘猫脊背，猫呼噜声轻柔持续。",
      content: "镜号：01 画面描述：@小明 @橘猫。小明轻抚怀中橘猫脊背，猫呼噜声轻柔持续。",
      status: "complete",
      params: { scriptSegmentIndex: 1 },
    },
  },
  edges: {},
  order: ["script_1"],
  groups: {},
  selection: { nodeIds: ["script_1"], edgeId: null },
  ui: {},
};

const store = {
  get: () => state,
  set: (mutator) => mutator(state),
  nextId: (prefix) => `${prefix}_should_not_be_used`,
};
const runtime = {
  planShotAssets: async () => {
    const error = new Error("authentication_required");
    error.status = 401;
    throw error;
  },
};

const created = await identifyScriptAssets(store, runtime, state.nodes.script_1);

process.stdout.write(JSON.stringify({
  created,
  nodes: state.nodes,
  order: state.order,
  scriptStatus: state.nodes.script_1.status,
  result: state.nodes.script_1.result,
  blockedReason: state.nodes.script_1.params.generationBlockedReason,
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    visible_text = json.dumps(payload, ensure_ascii=False)

    assert payload["created"] == []
    assert list(payload["nodes"].keys()) == ["script_1"]
    assert payload["order"] == ["script_1"]
    assert payload["scriptStatus"] == "error"
    assert "资产规划暂时不可用" in visible_text
    assert "可复用角色，身份与外观待确认" not in visible_text


def test_keyframe_generation_carries_connected_asset_card_images_as_local_refs() -> None:
    script = r'''
import { buildKeyframeGenerationRequest } from "./apps/studio/src/optimizer-contract.js";

const keyframe = {
  id: "keyframe_01",
  type: "image",
  title: "关键帧 · 分镜01",
  prompt: "根据分镜生成关键帧：@孙悟空 @金刚狼 @金箍棒",
  params: {
    nodeRole: "keyframe_generation",
    visualAssets: [{
      asset_id: "vas_swk",
      label: "孙悟空",
      status: "fixed",
      image_asset_refs: ["img_swk_fixed"],
    }],
    spec: { ratio: "16:9", count: 1 },
  },
};
const wolverine = {
  id: "asset_wolverine",
  type: "image",
  title: "角色资产 · @金刚狼",
  params: {
    nodeRole: "asset_card_draft",
    assetCardDraft: { label: "金刚狼", asset_type: "character", status: "draft" },
    uploads: [
      { asset_id: "img_wolverine_candidate", role: "character_reference" },
      { asset_id: "img_old_keyframe_history", role: "generated_keyframe_reference" },
    ],
  },
};
const scene = {
  id: "asset_scene",
  type: "image",
  title: "场景资产 · @雨夜码头",
  params: {
    nodeRole: "asset_card_draft",
    assetCardDraft: { label: "雨夜码头", asset_type: "scene", status: "draft" },
    uploads: [{ asset_id: "img_scene_candidate", role: "scene_reference" }],
  },
};
const unrelated = {
  id: "unrelated_image",
  type: "image",
  params: { uploads: [{ asset_id: "img_unrelated_history", role: "generated_keyframe_reference" }] },
};
const state = {
  nodes: { keyframe_01: keyframe, asset_wolverine: wolverine, asset_scene: scene, unrelated_image: unrelated },
  edges: {
    e1: { id: "e1", from: "asset_wolverine", to: "keyframe_01" },
    e2: { id: "e2", from: "asset_scene", to: "keyframe_01" },
    e3: { id: "e3", from: "unrelated_image", to: "keyframe_01" },
  },
};
const request = buildKeyframeGenerationRequest(state, keyframe);
process.stdout.write(JSON.stringify({
  refs: request.asset_refs,
  contextRefs: request.context_subgraph.nodes.find((node) => node.id === "keyframe_01").image_asset_refs,
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["refs"] == ["img_swk_fixed", "img_wolverine_candidate", "img_scene_candidate"]
    assert "img_old_keyframe_history" not in payload["refs"]
    assert "img_unrelated_history" not in payload["refs"]
    assert payload["contextRefs"] == ["img_swk_fixed"]


def test_storyboard_asset_identification_uses_runtime_plan_and_allows_manual_asset_nodes() -> None:
    runtime_client = (STUDIO_ROOT / "src" / "runtime-client.js").read_text(encoding="utf-8")
    storyboard_actions = (STUDIO_ROOT / "src" / "storyboard-node-actions.js").read_text(encoding="utf-8")
    canvas_action_handler = (STUDIO_ROOT / "src" / "canvas-node-action-handler.js").read_text(encoding="utf-8")
    node_actions = (STUDIO_ROOT / "src" / "node-actions.js").read_text(encoding="utf-8")
    asset_nodes = (STUDIO_ROOT / "src" / "shot-asset-nodes.js").read_text(encoding="utf-8")
    node_menu = (STUDIO_ROOT / "src" / "panels" / "node-menu.js").read_text(encoding="utf-8")
    add_asset_modal = (STUDIO_ROOT / "src" / "panels" / "add-asset-modal.js").read_text(encoding="utf-8")

    assert "planShotAssets(payload)" in runtime_client
    assert "shot-asset-plans" in runtime_client
    assert "identifyScriptAssets(store, runtime, node)" in storyboard_actions
    assert "runtime?.planShotAssets" in storyboard_actions
    assert "handleNodeIntent(store, runtime, node, actionEl.dataset.intent)" in canvas_action_handler
    assert "identifyScriptAssets(store, runtime, node)" in node_actions
    assert "identifyScriptAssets(store, null, node)" not in node_actions
    assert "createManualShotAssetNode" in asset_nodes
    assert "openAddAssetModal" in node_menu
    assert "新增资产" in node_menu
    assert "添加角色资产" not in node_menu
    assert "添加场景资产" not in node_menu
    assert "添加道具资产" not in node_menu
    assert "inferManualAssetType" in add_asset_modal
    assert "createManualShotAssetNode(store, fresh, assetType, label)" in add_asset_modal
    assert "金刚狼" in add_asset_modal


def test_asset_mentions_scope_fixed_project_assets_and_tree_candidates() -> None:
    candidates = (STUDIO_ROOT / "src" / "asset-reference-candidates.js").read_text(encoding="utf-8")
    mentions = (STUDIO_ROOT / "src" / "mention-suggestions.js").read_text(encoding="utf-8")
    prompt_bar = (STUDIO_ROOT / "src" / "prompt-bar.js").read_text(encoding="utf-8")
    canvas_body = (STUDIO_ROOT / "src" / "canvas-node-body.js").read_text(encoding="utf-8")
    node_menu = (STUDIO_ROOT / "src" / "panels" / "node-menu.js").read_text(encoding="utf-8")

    assert "assetReferenceCandidates" in candidates
    assert "fixedProjectAssets" in candidates
    assert "treeCandidateAssets" in candidates
    assert "connectedNodeIds" in candidates
    assert '"project_fixed"' in candidates
    assert '"shot_tree_candidate"' in candidates
    assert "isRetired(asset)" in candidates
    assert "bindAssetMentionSuggestions" in mentions
    assert "assetReferenceCandidates(store.get(), nodeId, match.query)" in mentions
    assert "MENTION_QUERY_RE" in mentions
    assert "bindAssetMentionSuggestions(textarea, store, node.id)" in prompt_bar
    assert "bindAssetMentionSuggestions(textarea, store, node.id)" in canvas_body
    assert "取消固定资产" in node_menu
    assert "openRetireAssetModal" in node_menu


def test_asset_mentions_exclude_generated_history_from_unconnected_nodes() -> None:
    script = r'''
import { assetReferenceCandidates } from "./apps/studio/src/asset-reference-candidates.js";

const state = {
  nodes: {
    isolated: { id: "isolated", type: "image", params: {} },
    shot_01: { id: "shot_01", type: "script", params: {} },
    wolverine_asset: {
      id: "wolverine_asset",
      type: "image",
      title: "角色资产 · @金刚狼",
      params: { assetCardDraft: { card_id: "card_wolverine", label: "金刚狼", asset_type: "character", status: "draft" } },
    },
    staff_asset: {
      id: "staff_asset",
      type: "image",
      title: "道具资产 · @金箍棒",
      params: { assetCardDraft: { card_id: "card_staff", label: "金箍棒", asset_type: "prop", status: "draft" } },
    },
  },
  edges: {
    e1: { from: "shot_01", to: "wolverine_asset" },
  },
  assets: [
    { kind: "visual_asset", asset_id: "visual_swk", visual_asset_id: "visual_swk", label: "孙悟空", asset_type: "character", status: "fixed" },
    { kind: "image_reference", asset_id: "img_old", title: "candidate_001.png", role: "generated_keyframe_reference", status: "ready", source_node_id: "shot_01" },
  ],
};

process.stdout.write(JSON.stringify({
  isolated: assetReferenceCandidates(state, "isolated").map((item) => item.label),
  connected: assetReferenceCandidates(state, "shot_01").map((item) => item.label),
}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["isolated"] == ["孙悟空"]
    assert payload["connected"] == ["孙悟空", "金刚狼"]
    assert "candidate_001.png" not in payload["isolated"] + payload["connected"]
    assert "金箍棒" not in payload["connected"]


def test_visual_asset_cards_support_prop_assets_across_frontend_and_runtime_contract() -> None:
    visual_render = (STUDIO_ROOT / "src" / "panels" / "visual-asset-panel-render.js").read_text(encoding="utf-8")
    visual_defaults = (STUDIO_ROOT / "src" / "panels" / "visual-asset-defaults.js").read_text(encoding="utf-8")
    asset_summary = (STUDIO_ROOT / "src" / "asset-reference-summary.js").read_text(encoding="utf-8")
    runtime_models = (STUDIO_ROOT.parent / "api" / "runtime_models.py").read_text(encoding="utf-8")
    algorithm = (STUDIO_ROOT.parent.parent / "agentflow" / "algorithms" / "asset_card_drafting" / "__init__.py").read_text(encoding="utf-8")

    assert "PROP_FIELDS" in visual_render
    assert "道具资产" in visual_render
    assert "propDefaults" in visual_defaults
    assert 'if (type === "prop") return "道具";' in asset_summary
    assert 'Literal["character", "scene", "prop", "video"]' in runtime_models
    assert 'Literal["character", "scene", "prop"]' in runtime_models
    assert '"prop"' in algorithm


def test_visual_asset_draft_and_existing_asset_edit_show_inline_loading() -> None:
    visual_panel = (STUDIO_ROOT / "src" / "panels" / "visual-asset-panel.js").read_text(encoding="utf-8")
    promotion_request = (STUDIO_ROOT / "src" / "panels" / "visual-asset-promotion-request.js").read_text(encoding="utf-8")
    visual_render = (STUDIO_ROOT / "src" / "panels" / "visual-asset-panel-render.js").read_text(encoding="utf-8")
    node_actions = (STUDIO_ROOT / "src" / "node-actions.js").read_text(encoding="utf-8")
    asset_detail = (STUDIO_ROOT / "src" / "panels" / "asset-detail-popover.js").read_text(encoding="utf-8")
    styles = _styles()

    assert "existingAsset" in visual_panel
    assert "supersedesAssetId" in visual_panel
    assert "supersedes_asset_id" in promotion_request
    assert "image_asset_refs" in visual_panel
    assert "seedFromExistingAsset" in visual_panel
    assert "is-drafting" in visual_panel
    assert "visualAssetFieldShimmer" in styles
    assert "data-drafting" in visual_render
    assert "lastFixedVisualAsset" in node_actions
    assert "imageAssetFromVisualAsset" in node_actions
    assert "调整资产" in asset_detail
    assert "openVisualAssetPanel" in asset_detail
    assert "existingAsset" in asset_detail
    assert "cancelFixedAsset" in asset_detail
    assert "retireVisualAsset" in asset_detail
    assert "applyRetiredVisualAssetToStore" in asset_detail
