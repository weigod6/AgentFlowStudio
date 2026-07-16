from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.studio_production_delivery_browser_qa import prepare_provider_free_delivery_qa


STUDIO = Path("apps/studio")


def _node_json(script: str) -> dict:
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def test_production_delivery_panel_is_wired_to_quality_and_exact_export_actions() -> None:
    result_view = (STUDIO / "src" / "node-result-view.js").read_text(encoding="utf-8")
    action_handler = (STUDIO / "src" / "canvas-node-action-handler.js").read_text(encoding="utf-8")
    styles = (STUDIO / "styles" / "node-result.css").read_text(encoding="utf-8")

    assert 'from "./production-delivery-view.js"' in result_view
    assert "productionDeliveryView(node, candidates)" in result_view
    assert 'from "./production-delivery-controller.js"' in action_handler
    assert 'action === "production-quality-approve"' in action_handler
    assert 'action === "production-export"' in action_handler
    assert ".production-delivery-panel" in styles
    assert "@media (max-width: 520px)" in styles


def test_representative_episode_binding_summary_requires_exact_authoritative_inventory() -> None:
    payload = _node_json(
        r'''
import { representativeEpisodeBindingSummary } from "./apps/studio/src/production-delivery-controller.js";

const digest = (char) => char.repeat(64);
const entityRefs = (prefix, count) => Array.from({ length: count }, (_, index) => ({
  entity_id: `${prefix}-${String(index + 1).padStart(3, "0")}`,
  current_approved_version_id: `${prefix}-${String(index + 1).padStart(3, "0")}-v1`,
}));
const assetRefs = Array.from({ length: 25 }, (_, index) => ({
  asset_id: `asset-${String(index + 1).padStart(3, "0")}`,
  current_revision_id: `asset-${String(index + 1).padStart(3, "0")}-rev-001`,
  status: "missing",
  provider_needed: true,
}));
const binding = {
  package_sha256: digest("a"),
  binding_digest: digest("b"),
  episode_id: "ep-rainlight-001",
  episode_version_id: "ep-rainlight-001-v1",
  character_refs: entityRefs("character", 3),
  scene_refs: entityRefs("scene", 3),
  shot_refs: entityRefs("shot", 15),
  asset_refs: assetRefs,
  counts: { characters: 3, scenes: 3, shots: 15, assets: 25 },
  asset_readiness: { ready_count: 0, pending_media_count: 25, provider_needed_count: 25, all_assets_ready: false },
  creator_decision_ref: "creator-decision-episode-v1",
  propagation_complete: false,
  lineage: [
    { source_ref: digest("a"), target_ref: "ep-rainlight-001-v1", relation: "package_defined_episode_version" },
    { source_ref: "creator-decision-episode-v1", target_ref: "ep-rainlight-001-v1", relation: "creator_decision_approved_episode_version" },
  ],
};
const summary = representativeEpisodeBindingSummary({ representative_episode_binding: binding });
const failures = {};
for (const [name, mutate] of Object.entries({
  incompleteShots(value) { value.shot_refs.pop(); },
  staleCounts(value) { value.counts.shots = 14; },
  readinessDrift(value) { value.asset_readiness.pending_media_count = 24; },
  missingProviderGate(value) { value.asset_refs[0].provider_needed = false; },
})) {
  const candidate = structuredClone(binding);
  mutate(candidate);
  try { representativeEpisodeBindingSummary({ representative_episode_binding: candidate }); }
  catch (error) { failures[name] = error.code; }
}
process.stdout.write(JSON.stringify({ summary, failures }));
'''
    )

    assert payload["summary"] == {
        "authoritative_source": "runtime_production_run_checkpoint",
        "package_sha256": "a" * 64,
        "binding_digest": "b" * 64,
        "episode_id": "ep-rainlight-001",
        "episode_version_id": "ep-rainlight-001-v1",
        "character_count": 3,
        "scene_count": 3,
        "shot_count": 15,
        "asset_count": 25,
        "pending_media_count": 25,
        "provider_needed_count": 25,
        "all_assets_ready": False,
        "creator_decision_ref": "creator-decision-episode-v1",
        "propagation_complete": False,
        "lineage": [
            {
                "source_ref": "a" * 64,
                "target_ref": "ep-rainlight-001-v1",
                "relation": "package_defined_episode_version",
            },
            {
                "source_ref": "creator-decision-episode-v1",
                "target_ref": "ep-rainlight-001-v1",
                "relation": "creator_decision_approved_episode_version",
            },
        ],
    }
    assert payload["failures"] == {
        "incompleteShots": "delivery_episode_shot_refs_invalid",
        "staleCounts": "delivery_episode_counts_invalid",
        "readinessDrift": "delivery_episode_readiness_invalid",
        "missingProviderGate": "delivery_episode_provider_gate_invalid",
    }


def test_studio_delivery_displays_exact_representative_episode_checkpoint_summary() -> None:
    payload = _node_json(
        r'''
import { productionDeliveryView } from "./apps/studio/src/production-delivery-view.js";

function makeElement(tagName) {
  const element = {
    tagName: String(tagName || "").toUpperCase(), children: [], dataset: {}, attributes: {}, className: "",
    title: "", disabled: false, checked: false, textContent: "",
    appendChild(child) { this.children.push(child); return child; },
    append(...children) { children.forEach((child) => this.appendChild(child)); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
  Object.defineProperty(element, "innerText", { get() {
    return [this.textContent, ...this.children.map((child) => child.innerText || child.textContent || "")]
      .filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }});
  return element;
}
function findBinding(element) {
  if (element?.dataset?.representativeEpisodeBinding === "true") return element;
  for (const child of element?.children || []) { const found = findBinding(child); if (found) return found; }
  return null;
}
globalThis.document = { createElement: makeElement };
const digest = (char) => char.repeat(64);
const authority = {
  run_id: "production-run-001", selected_candidate_id: "candidate-001", selected_candidate_digest: digest("c"),
  selected_revision_id: "revision-001", selected_revision_digest: digest("d"), selected_parent_job_id: "job-001",
};
const node = { id: "node-001", params: {
  creatorSelection: authority,
  productionDelivery: {
    status: "ready", run_id: authority.run_id, selected_candidate_id: authority.selected_candidate_id,
    selected_candidate_digest: authority.selected_candidate_digest, selected_revision_id: authority.selected_revision_id,
    selected_revision_digest: authority.selected_revision_digest, parent_job_id: authority.selected_parent_job_id,
    representative_episode: {
      authoritative_source: "runtime_production_run_checkpoint", package_sha256: digest("a"), binding_digest: digest("b"),
      episode_id: "ep-rainlight-001", episode_version_id: "ep-rainlight-001-v1", character_count: 3,
      scene_count: 3, shot_count: 15, asset_count: 25, pending_media_count: 25, provider_needed_count: 25,
      all_assets_ready: false, creator_decision_ref: "creator-decision-episode-v1", propagation_complete: false,
      lineage: [{}, {}],
    },
  },
}};
const panel = productionDeliveryView(node, [{ candidate_id: "candidate-001" }, { candidate_id: "candidate-002" }]);
const binding = findBinding(panel);
process.stdout.write(JSON.stringify({ text: binding?.innerText || "", titles: (binding?.children || []).map((item) => item.title).filter(Boolean) }));
'''
    )

    assert "ep-rainlight-001" in payload["text"]
    assert "ep-rainlight-001-v1" in payload["text"]
    assert "3 characters / 3 scenes / 15 shots / 25 assets" in payload["text"]
    assert "25" in payload["text"]
    assert "pending reconfirmation" in payload["text"]
    assert "2 authoritative refs" in payload["text"]
    assert "a" * 64 in payload["titles"]
    assert "b" * 64 in payload["titles"]


def test_stale_delivery_authority_cannot_render_current_approval_or_enable_export() -> None:
    payload = _node_json(
        r'''
import { productionDeliveryView } from "./apps/studio/src/production-delivery-view.js";

function makeElement(tagName) {
  const element = {
    tagName: String(tagName || "").toUpperCase(),
    children: [],
    dataset: {},
    attributes: {},
    className: "",
    title: "",
    disabled: false,
    checked: false,
    textContent: "",
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    append(...children) {
      children.forEach((child) => this.appendChild(child));
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
  };
  Object.defineProperty(element, "innerText", {
    get() {
      return [this.textContent, ...this.children.map((child) => child.innerText || child.textContent || "")]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    },
  });
  return element;
}

function findByAction(element, action) {
  if (element?.dataset?.action === action) return element;
  for (const child of element?.children || []) {
    const found = findByAction(child, action);
    if (found) return found;
  }
  return null;
}

globalThis.document = { createElement: makeElement };
const digest = (char) => char.repeat(64);
const node = {
  id: "node-001",
  params: {
    creatorSelection: {
      run_id: "run-current",
      selected_candidate_id: "candidate-002",
      selected_candidate_digest: digest("c"),
      selected_revision_id: "revision-002",
      selected_revision_digest: digest("e"),
      selected_parent_job_id: "job-current",
    },
    productionDelivery: {
      status: "approved",
      run_id: "run-historical",
      selected_candidate_id: "candidate-002",
      selected_candidate_digest: digest("d"),
      selected_revision_id: "revision-002",
      selected_revision_digest: digest("e"),
      parent_job_id: "job-historical",
      quality_decision: "approve",
      message: "质量门禁已通过：这是旧审批，不得显示为当前审批。",
    },
  },
};
const panel = productionDeliveryView(node, [{ candidate_id: "candidate-001" }, { candidate_id: "candidate-002" }]);
const exportButton = findByAction(panel, "production-export");
process.stdout.write(JSON.stringify({
  exportDisabled: exportButton?.disabled,
  text: panel?.innerText || "",
}));
'''
    )

    assert payload["exportDisabled"] is True
    assert "质量门禁已通过" not in payload["text"]


def test_delivery_approval_requires_a_complete_valid_canonical_authority_tuple() -> None:
    payload = _node_json(
        r'''
import { productionDeliveryView } from "./apps/studio/src/production-delivery-view.js";

function makeElement(tagName) {
  const element = {
    tagName: String(tagName || "").toUpperCase(),
    children: [],
    dataset: {},
    attributes: {},
    disabled: false,
    checked: false,
    textContent: "",
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    append(...children) {
      children.forEach((child) => this.appendChild(child));
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
  };
  Object.defineProperty(element, "innerText", {
    get() {
      return [this.textContent, ...this.children.map((child) => child.innerText || child.textContent || "")]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    },
  });
  return element;
}

function findByAction(element, action) {
  if (element?.dataset?.action === action) return element;
  for (const child of element?.children || []) {
    const found = findByAction(child, action);
    if (found) return found;
  }
  return null;
}

globalThis.document = { createElement: makeElement };
const digest = (char) => char.repeat(64);
const baseNode = {
  id: "node-001",
  params: {
    creatorSelection: {
      run_id: "run-001",
      selected_candidate_id: "candidate-002",
      selected_candidate_digest: digest("c"),
      selected_revision_id: "revision-002",
      selected_revision_digest: digest("e"),
      selected_parent_job_id: "job-001",
    },
    productionDelivery: {
      status: "approved",
      run_id: "run-001",
      selected_candidate_id: "candidate-002",
      selected_candidate_digest: digest("c"),
      selected_revision_id: "revision-002",
      selected_revision_digest: digest("e"),
      parent_job_id: "job-001",
      quality_decision: "approve",
      quality_review_id: "quality-review-001",
      last_export_id: "export-001",
      delivery_sha256: digest("7"),
      message: "质量门禁已通过",
    },
  },
};
const candidates = [{ candidate_id: "candidate-001" }, { candidate_id: "candidate-002" }];

function inspect(node) {
  const panel = productionDeliveryView(node, candidates);
  return {
    approveDisabled: findByAction(panel, "production-quality-approve")?.disabled,
    exportDisabled: findByAction(panel, "production-export")?.disabled,
    showsApproval: (panel?.innerText || "").includes("质量门禁已通过"),
    showsDeliveryRecord: (panel?.innerText || "").includes("quality-review-001")
      || (panel?.innerText || "").includes("export-001"),
  };
}

const scenarios = {
  missing_run_id(selection, delivery) {
    delete selection.run_id;
    delete delivery.run_id;
  },
  missing_parent_job_id(selection, delivery) {
    delete selection.selected_parent_job_id;
    delete delivery.parent_job_id;
  },
  missing_candidate_id(selection, delivery) {
    delete selection.selected_candidate_id;
    delete delivery.selected_candidate_id;
  },
  missing_candidate_digest(selection, delivery) {
    delete selection.selected_candidate_digest;
    delete delivery.selected_candidate_digest;
  },
  missing_revision_id(selection, delivery) {
    delete selection.selected_revision_id;
    delete delivery.selected_revision_id;
  },
  missing_revision_digest(selection, delivery) {
    delete selection.selected_revision_digest;
    delete delivery.selected_revision_digest;
  },
  blank_run_id(selection, delivery) {
    selection.run_id = "   ";
    delivery.run_id = "   ";
  },
  null_parent_job_id(selection, delivery) {
    selection.selected_parent_job_id = null;
    delivery.parent_job_id = null;
  },
  non_scalar_candidate_id(selection, delivery) {
    selection.selected_candidate_id = { value: "candidate-002" };
    delivery.selected_candidate_id = { value: "candidate-002" };
  },
  invalid_revision_id(selection, delivery) {
    selection.selected_revision_id = "revision id with spaces";
    delivery.selected_revision_id = "revision id with spaces";
  },
  invalid_candidate_digest(selection, delivery) {
    selection.selected_candidate_digest = "not-a-sha256";
    delivery.selected_candidate_digest = "not-a-sha256";
  },
};

const results = {};
for (const [name, mutate] of Object.entries(scenarios)) {
  const node = structuredClone(baseNode);
  mutate(node.params.creatorSelection, node.params.productionDelivery);
  results[name] = inspect(node);
}
process.stdout.write(JSON.stringify({ baseline: inspect(baseNode), results }));
'''
    )

    assert payload["baseline"] == {
        "approveDisabled": True,
        "exportDisabled": False,
        "showsApproval": True,
        "showsDeliveryRecord": True,
    }
    assert set(payload["results"]) == {
        "missing_run_id",
        "missing_parent_job_id",
        "missing_candidate_id",
        "missing_candidate_digest",
        "missing_revision_id",
        "missing_revision_digest",
        "blank_run_id",
        "null_parent_job_id",
        "non_scalar_candidate_id",
        "invalid_revision_id",
        "invalid_candidate_digest",
    }
    for name, result in payload["results"].items():
        assert result == {
            "approveDisabled": True,
            "exportDisabled": True,
            "showsApproval": False,
            "showsDeliveryRecord": False,
        }, name


def test_quality_approval_and_export_bind_exact_selected_revision_and_fail_closed() -> None:
    payload = _node_json(
        r'''
import {
  buildProductionExportRequest,
  buildQualityApprovalRequest,
  productionDeliveryAuthority,
} from "./apps/studio/src/production-delivery-controller.js";

const digest = (char) => char.repeat(64);
const candidate = (id, char) => ({
  candidate_id: id,
  canonical_digest: digest(char),
  parent_job_id: "job-001",
  preview_url: `/projects/project-001/keyframe-generations/job-001/candidates/${id}/preview`,
  status: "succeeded",
});
const node = {
  id: "node-001",
  type: "image",
  params: {
    lastKeyframeJobId: "job-001",
    candidatePreviewUrls: [candidate("candidate-001", "b"), candidate("candidate-002", "c")],
    creatorSelection: {
      status: "persisted",
      run_id: "run-001",
      selected_candidate_id: "candidate-002",
      selected_candidate_digest: digest("c"),
      selected_revision_id: "revision-002",
      selected_revision_digest: digest("e"),
      selected_parent_job_id: "job-001",
      checkpoint_version: 2,
    },
  },
};
const run = {
  run_id: "run-001",
  subject_digest: digest("a"),
  checkpoint: { version: 2, state_digest: digest("f") },
  candidates: [candidate("candidate-001", "b"), candidate("candidate-002", "c")],
  selected_revision: {
    revision_id: "revision-002",
    canonical_digest: digest("e"),
    candidate_id: "candidate-002",
    candidate_digest: digest("c"),
    parent_job_id: "job-001",
    subject_digest: digest("a"),
  },
  quality_reviews: [],
  exports: [],
};
const authority = productionDeliveryAuthority(run, node);
const quality = buildQualityApprovalRequest(run, node, {
  reviewId: "quality-review-001",
  idempotencyKey: "quality-review-001",
  checklist: {
    story_intent_preserved: true,
    character_continuity_checked: true,
    shot_coverage_checked: true,
    revision_addressed: true,
  },
});
const reviewed = {
  ...run,
  checkpoint: { version: 3, state_digest: digest("9") },
  quality_reviews: [{
    review_id: "quality-review-001",
    selected_revision_id: "revision-002",
    selected_revision_digest: digest("e"),
    decision: "approve",
    human_acceptance_claimed: false,
  }],
};
const exported = buildProductionExportRequest(reviewed, node, {
  exportId: "export-001",
  idempotencyKey: "export-001",
});
const failures = {};
for (const [name, mutate] of Object.entries({
  unselected: (value) => { delete value.params.creatorSelection.selected_candidate_id; },
  stale_candidate: (value) => { value.params.creatorSelection.selected_candidate_id = "candidate-001"; },
  stale_revision: (value) => { value.params.creatorSelection.selected_revision_id = "revision-old"; },
  stale_lineage: (value) => { value.params.lastKeyframeJobId = "job-new"; },
})) {
  const changed = structuredClone(node);
  mutate(changed);
  try {
    productionDeliveryAuthority(reviewed, changed);
    failures[name] = null;
  } catch (error) {
    failures[name] = error.code;
  }
}
const unreviewed = (() => {
  try {
    buildProductionExportRequest(run, node, { exportId: "export-blocked", idempotencyKey: "export-blocked" });
    return null;
  } catch (error) {
    return error.code;
  }
})();
process.stdout.write(JSON.stringify({ authority, quality, exported, failures, unreviewed }));
'''
    )

    assert payload["authority"] == {
        "run_id": "run-001",
        "candidate_id": "candidate-002",
        "candidate_digest": "c" * 64,
        "revision_id": "revision-002",
        "revision_digest": "e" * 64,
        "parent_job_id": "job-001",
        "checkpoint_version": 2,
    }
    assert payload["quality"]["expected_checkpoint_version"] == 2
    assert payload["quality"]["reviewed_subject_digest"] == "a" * 64
    assert payload["quality"]["selected_revision_id"] == "revision-002"
    assert payload["quality"]["selected_revision_digest"] == "e" * 64
    assert payload["quality"]["decision"] == "approve"
    assert payload["quality"]["checklist"] == {
        "story_intent_preserved": True,
        "character_continuity_checked": True,
        "shot_coverage_checked": True,
        "revision_addressed": True,
    }
    assert payload["exported"] == {
        "schema_version": "afs_production_export.v0.1",
        "export_id": "export-001",
        "idempotency_key": "export-001",
        "expected_checkpoint_version": 3,
        "selected_revision_id": "revision-002",
        "selected_revision_digest": "e" * 64,
    }
    assert payload["failures"] == {
        "unselected": "delivery_selection_missing",
        "stale_candidate": "delivery_candidate_stale",
        "stale_revision": "delivery_revision_stale",
        "stale_lineage": "delivery_lineage_stale",
    }
    assert payload["unreviewed"] == "delivery_quality_required"


def test_delivery_actions_use_authoritative_readback_and_never_post_stale_export() -> None:
    payload = _node_json(
        r'''
import { handleProductionDeliveryAction } from "./apps/studio/src/production-delivery-controller.js";

const digest = (char) => char.repeat(64);
const proof = (id, char, assetId) => ({
  schema_version: "afs_studio_reusable_asset_authority.v0.1",
  asset_id: assetId,
  role: "generated_keyframe_reference",
  source_kind: "keyframe_candidate",
  source_job_id: "job-001",
  source_candidate_id: id,
  source_candidate_digest: digest(char),
  sha256: digest(char),
  status: "succeeded",
});
const visible = (id, char, assetId) => ({
  candidate_id: id,
  canonical_digest: digest(char),
  parent_job_id: "job-001",
  project_id: "project-001",
  preview_url: `/projects/project-001/keyframe-generations/job-001/candidates/${id}/preview`,
  status: "succeeded",
  reusable_asset_authority: proof(id, char, assetId),
});
const runCandidate = (id, char) => ({
  candidate_id: id,
  canonical_digest: digest(char),
  parent_job_id: "job-001",
  status: "succeeded",
});
const makeNode = () => ({
  id: "node-001",
  type: "image",
  params: {
    lastKeyframeJobId: "job-001",
    uploads: [],
    candidatePreviewUrls: [visible("candidate_001", "b", "asset-001"), visible("candidate_002", "c", "asset-002")],
    creatorSelection: {
      status: "persisted",
      run_id: "run-001",
      selected_candidate_id: "candidate_002",
      selected_candidate_digest: digest("c"),
      selected_revision_id: "revision-002",
      selected_revision_digest: digest("e"),
      selected_parent_job_id: "job-001",
      checkpoint_version: 2,
    },
  },
});
const revision = {
  revision_id: "revision-002",
  canonical_digest: digest("e"),
  candidate_id: "candidate_002",
  candidate_digest: digest("c"),
  parent_job_id: "job-001",
  subject_digest: digest("a"),
};
const base = {
  run_id: "run-001",
  subject_digest: digest("a"),
  checkpoint: { version: 2, state_digest: digest("f") },
  candidates: [runCandidate("candidate_001", "b"), runCandidate("candidate_002", "c")],
  selected_revision: revision,
  creator_decisions: [{ decision: "select", candidate_id: "candidate_002", candidate_digest: digest("c") }],
  quality_reviews: [],
  exports: [],
};
const review = {
  review_id: "quality-review-001",
  selected_revision_id: "revision-002",
  selected_revision_digest: digest("e"),
  decision: "approve",
  human_acceptance_claimed: false,
};
const reviewed = { ...base, checkpoint: { version: 3, state_digest: digest("9") }, quality_reviews: [review] };
const exportRecord = {
  export_id: "export-001",
  selected_revision_id: "revision-002",
  selected_revision_digest: digest("e"),
  delivery_sha256: digest("7"),
};
const exported = { ...reviewed, status: "exported", checkpoint: { version: 4, state_digest: digest("8") }, exports: [exportRecord] };

function makeStore(node) {
  const state = { production: { active_run_id: "run-001" }, nodes: { [node.id]: node }, assets: [] };
  return {
    state,
    store: {
      get: () => state,
      set: (mutator) => mutator(state),
      flushRuntimeSave: async () => {},
    },
  };
}
function panel() {
  const status = { dataset: {}, textContent: "" };
  const checks = new Map([
    ["story_intent_preserved", { checked: true }],
    ["character_continuity_checked", { checked: true }],
    ["shot_coverage_checked", { checked: true }],
    ["revision_addressed", { checked: true }],
  ]);
  return {
    dataset: {},
    status,
    setAttribute: () => {},
    querySelectorAll: () => [],
    querySelector: (selector) => {
      if (selector === "[data-production-delivery-status]") return status;
      const match = selector.match(/data-delivery-check="([^"]+)"/);
      return match ? checks.get(match[1]) : null;
    },
  };
}
function action(name, value) {
  return { dataset: { action: name }, closest: () => value };
}

const happy = makeStore(makeNode());
const qualityPanel = panel();
let qualityReads = 0;
let qualityRequest = null;
const qualityResult = await handleProductionDeliveryAction(happy.store, {
  getProductionRun: async () => ({ production_run: ++qualityReads === 1 ? base : reviewed }),
  recordProductionQualityReview: async (_runId, request) => { qualityRequest = request; return {}; },
}, happy.state.nodes["node-001"], action("production-quality-approve", qualityPanel));

const exportPanel = panel();
let exportReads = 0;
let exportRequest = null;
const exportResult = await handleProductionDeliveryAction(happy.store, {
  getProductionRun: async () => ({ production_run: ++exportReads === 1 ? reviewed : exported }),
  exportProductionRun: async (_runId, request) => { exportRequest = request; return { export: exportRecord }; },
}, happy.state.nodes["node-001"], action("production-export", exportPanel));

const stale = makeStore(makeNode());
stale.state.nodes["node-001"].params.lastKeyframeJobId = "job-new";
let stalePosts = 0;
const staleResult = await handleProductionDeliveryAction(stale.store, {
  getProductionRun: async () => ({ production_run: reviewed }),
  exportProductionRun: async () => { stalePosts += 1; return {}; },
}, stale.state.nodes["node-001"], action("production-export", panel()));

const unselected = makeStore(makeNode());
delete unselected.state.nodes["node-001"].params.creatorSelection.run_id;
let unselectedReads = 0;
let unselectedPosts = 0;
const unselectedResult = await handleProductionDeliveryAction(unselected.store, {
  getProductionRun: async () => { unselectedReads += 1; return { production_run: reviewed }; },
  exportProductionRun: async () => { unselectedPosts += 1; return {}; },
}, unselected.state.nodes["node-001"], action("production-export", panel()));

process.stdout.write(JSON.stringify({
  qualityResult,
  qualityRequest,
  exportResult,
  exportRequest,
  delivery: happy.state.nodes["node-001"].params.productionDelivery,
  production: happy.state.production,
  staleResult,
  stalePosts,
  unselectedResult,
  unselectedReads,
  unselectedPosts,
}));
'''
    )

    assert payload["qualityResult"]["ok"] is True, payload
    assert payload["qualityRequest"]["decision"] == "approve"
    assert payload["qualityRequest"]["selected_revision_id"] == "revision-002"
    assert payload["exportResult"]["ok"] is True
    assert payload["exportRequest"]["expected_checkpoint_version"] == 3
    assert payload["exportRequest"]["selected_revision_id"] == "revision-002"
    assert payload["delivery"]["status"] == "exported"
    assert payload["delivery"]["last_export_id"] == "export-001"
    assert payload["delivery"]["delivery_sha256"] == "7" * 64
    assert payload["production"]["selected_candidate_id"] == "candidate_002"
    assert payload["production"]["selected_revision_id"] == "revision-002"
    assert payload["production"]["last_export_id"] == "export-001"
    assert payload["staleResult"]["code"] == "delivery_lineage_stale"
    assert payload["stalePosts"] == 0
    assert payload["unselectedResult"]["code"] == "delivery_selection_missing"
    assert payload["unselectedReads"] == 0
    assert payload["unselectedPosts"] == 0


def test_review_delivery_projects_and_renders_authoritative_fifteen_shot_canon() -> None:
    payload = _node_json(
        r'''
import { composeReviewDeliveryState, createReviewDeliveryState } from "./apps/studio/src/review-delivery-state.js";
import { renderReviewDeliveryWorkspace } from "./apps/studio/src/review-delivery-workspace.js";

function makeElement(tagName) {
  const element = {
    tagName: String(tagName || "").toUpperCase(), children: [], dataset: {}, attributes: {}, className: "",
    textContent: "", disabled: false, checked: false, value: "", tabIndex: 0,
    appendChild(child) { this.children.push(child); return child; },
    append(...children) { children.forEach((child) => this.appendChild(child)); },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener() {},
    querySelector(selector) {
      const className = selector.startsWith(".") ? selector.slice(1) : "";
      if (className && String(this.className).split(/\s+/).includes(className)) return this;
      for (const child of this.children) { const found = child.querySelector?.(selector); if (found) return found; }
      return null;
    },
  };
  element.classList = { add: (...names) => { element.className = [element.className, ...names].filter(Boolean).join(" "); } };
  Object.defineProperty(element, "innerText", { get() {
    return [this.textContent, ...this.children.map((child) => child.innerText || child.textContent || "")]
      .filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }});
  return element;
}
function countClass(element, className) {
  const own = String(element?.className || "").split(/\s+/).includes(className) ? 1 : 0;
  return own + (element?.children || []).reduce((sum, child) => sum + countClass(child, className), 0);
}
globalThis.document = { createElement: makeElement };

const digest = (char) => char.repeat(64);
const timeline = Array.from({ length: 15 }, (_, index) => ({
  shot_number: index + 1,
  label: `第 ${String(index + 1).padStart(2, "0")} 镜`,
  version_id: `shot-${String(index + 1).padStart(3, "0")}-${index === 10 ? "v2" : "v1"}`,
  start_seconds: index * 9,
  end_seconds: (index + 1) * 9,
  scene: index < 5 ? "雨巷" : index < 10 ? "档案塔" : "黎明屋顶",
  characters: ["林遥", "小七", "余馆长"],
  visual_action: `第 ${index + 1} 镜的明确叙事动作`,
  dialogue: [{ speaker: "林遥", text: "继续守住这盏灯。" }],
  camera: "宽幅镜头",
  motion: "缓慢推进",
  continuity: "保持角色服装、雨势与灯光连续。",
  media: { required_count: 5, ready_count: 5, pending_count: 0, all_ready: true, status: "素材已齐" },
  audio: { covered: true, pending_asset_count: 0, status: "音频已齐" },
}));
const canonicalState = {
  status_label: "15/15", episode_title: "《雨灯失窃案》第一集：最后一盏引魂灯",
  episode_version_id: "ep-rainlight-001-v2", package_sha256: digest("a"), canon_digest: digest("b"),
  checkpoint_version: 4, duration_seconds: 135, characters: 3, scenes: 3, shots: 15, audio_items: 4,
  character_versions: ["林遥", "小七", "余馆长"].map((name, index) => ({ name, version_id: `character-${index + 1}-v1`, continuity: ["造型连续"] })),
  scene_versions: ["雨巷", "档案塔", "黎明屋顶"].map((name, index) => ({ name, version_id: `scene-${index + 1}-v1`, continuity: ["空间与光线连续"] })),
  timeline,
  audio: { covered_shot_count: 15, total_shot_count: 15, pending_asset_count: 0, all_audio_ready: true, status: "音频已齐" },
  pending_media_count: 0, all_assets_ready: true, propagation_complete: true, readiness: "25/25 制作素材已接纳",
  media_delivery: {
    status: "media_ready", accepted_count: 25, required_count: 25, visual_count: 21, audio_count: 4,
    continuity_status: "structural_checked",
    continuity_checks: ["规范版本一致", "十五镜时间线", "角色场景与镜头素材", "对白音乐音效与母版"].map((label) => ({ label, status: "structural_checked" })),
    assembly_status: "technical_qa_passed", duration_seconds: 135, shot_count: 15,
    delivery_preview_url: "/projects/afs-rainlight-project/production-runs/production-run-001/representative-episode-media/delivery/preview",
    representative_content_proof: "not_started", creative_media_quality: "not_evaluated", human_acceptance: "not_evaluated",
  },
};
const run = { project_id: "afs-rainlight-project", run_id: "production-run-001", checkpoint: { version: 4 }, candidates: [], creator_decisions: [], quality_reviews: [], exports: [] };
const project = {
  name: "雨灯制作项目", episode: "第 01 集", current_stage: "分镜", canonical_state: canonicalState,
  crew: { episode_execution: {
    role_count: 9, approved_version: "第 2 版", pending_reconfirmation_count: 0,
    reconfirmed_count: 8, propagation_complete: true,
    responsibilities: ["编剧组", "分镜组", "美术组", "导演组", "连贯性检查", "质量审核", "音频组", "后期组", "交付组"].map((role, index) => ({
      role, responsibility: `${role}的本集制作责任`, state: index ? "待开始" : "进行中",
      approved_version: "第 2 版", propagation_state: index ? "已按批准版本重确认" : "主创决定后已恢复",
      reconfirmed: index > 0, pending_reconfirmation: false,
    })),
  }},
};
const state = composeReviewDeliveryState({
  workspace: { projects: [{ project_id: "afs-rainlight-project", name: "雨灯制作项目" }] },
  project,
  runsPayload: { production_runs: [run] },
  projectId: "afs-rainlight-project",
});
state.authUser = { user_id: "creator-001", display_name: "主创" };
const root = makeElement("div");
renderReviewDeliveryWorkspace(root, state, {});
const pendingProject = JSON.parse(JSON.stringify(project));
pendingProject.crew.episode_execution.propagation_complete = false;
pendingProject.crew.episode_execution.pending_reconfirmation_count = 8;
pendingProject.crew.episode_execution.reconfirmed_count = 0;
pendingProject.crew.episode_execution.responsibilities = pendingProject.crew.episode_execution.responsibilities.map((item, index) => ({
  ...item,
  propagation_state: index ? "等待责任人重确认" : "主创决定后已恢复",
  reconfirmed: false,
  pending_reconfirmation: index > 0,
}));
const pendingState = composeReviewDeliveryState({
  workspace: { projects: [{ project_id: "afs-rainlight-project", name: "雨灯制作项目" }] },
  project: pendingProject, runsPayload: { production_runs: [run] }, projectId: "afs-rainlight-project",
});
const pendingRoot = makeElement("div");
renderReviewDeliveryWorkspace(pendingRoot, pendingState, {});

const lifecycle = createReviewDeliveryState();
lifecycle.setIdentity({ user_id: "creator-001" });
lifecycle.publish({ phase: "ready", episodeCanon: state.episodeCanon, projectId: "afs-rainlight-project" });
const staleToken = lifecycle.beginAction("refresh");
lifecycle.clearIdentity();
const latePublished = lifecycle.finishAction(staleToken, { episodeCanon: state.episodeCanon });

const mismatched = composeReviewDeliveryState({
  workspace: {}, project, runsPayload: { production_runs: [{ ...run, checkpoint: { version: 5 } }] },
  projectId: "afs-rainlight-project",
});
process.stdout.write(JSON.stringify({
  text: root.innerText,
  shotCards: countClass(root, "episode-shot-card"),
  crewCards: countClass(root, "crew-responsibility"),
  pendingCrewCards: countClass(pendingRoot, "pending"),
  pendingCrewText: pendingRoot.innerText,
  canonReady: state.episodeCanon?.shots?.length === 15,
  rawDigestVisible: root.innerText.includes(digest("a")) || root.innerText.includes(digest("b")),
  rawIdVisible: root.innerText.includes("shot-001") || root.innerText.includes("ep-rainlight-001-v1") || root.innerText.includes("ep-rainlight-001-v2"),
  clearedCanon: lifecycle.get().episodeCanon,
  clearedProject: lifecycle.get().projectId,
  latePublished,
  mismatchCanon: mismatched.episodeCanon,
}));
'''
    )

    assert payload["canonReady"] is True
    assert payload["shotCards"] == 15
    assert payload["crewCards"] == 9
    assert payload["pendingCrewCards"] >= 8
    assert "15/15 镜已绑定" in payload["text"]
    assert "第 01 镜" in payload["text"] and "第 15 镜" in payload["text"]
    assert "00:00–00:09" in payload["text"] and "02:06–02:15" in payload["text"]
    assert "保持角色服装、雨势与灯光连续" in payload["text"]
    assert "素材已齐" in payload["text"]
    assert "音频已齐" in payload["text"]
    assert "规范媒体与技术交付" in payload["text"]
    assert "25/25 已接纳" in payload["text"]
    assert "135 秒技术检查通过" in payload["text"]
    assert "代表性内容质量、人工验收和商业验证尚未开始" in payload["text"]
    assert "本集制作团队" in payload["text"]
    assert "版本传播已完成" in payload["text"]
    assert "交付组的本集制作责任" in payload["text"]
    assert "已按批准版本重确认" in payload["text"]
    assert "等待下游确认" in payload["pendingCrewText"]
    assert "等待责任人重确认" in payload["pendingCrewText"]
    assert payload["rawDigestVisible"] is False
    assert payload["rawIdVisible"] is False
    assert payload["clearedCanon"] is None
    assert payload["clearedProject"] == ""
    assert payload["latePublished"] is False
    assert payload["mismatchCanon"] is None


def test_review_delivery_canon_mobile_contract_has_single_column_and_no_raw_diagnostics() -> None:
    source = (STUDIO / "src" / "review-delivery-workspace.js").read_text(encoding="utf-8")
    styles = (STUDIO / "styles" / "review-delivery.css").read_text(encoding="utf-8")

    assert "本集制作规范" in source
    assert "15/15 镜已绑定" in source
    assert "镜头顺序、版本、连续性与音频覆盖均来自当前项目的服务器制作记录" in source
    assert "本集制作团队" in source
    assert "规范媒体与技术交付" in source
    assert "结构检查不等于内容质量验收" in source
    assert "九个制作岗位的当前责任、批准版本与下游确认均来自项目服务器记录" in source
    assert "package_sha256" not in source
    assert "canon_digest" not in source
    assert ".episode-shot-timeline" in styles
    assert ".crew-responsibility-list" in styles
    assert ".canonical-media-metrics" in styles
    assert ".canonical-continuity-list" in styles
    assert "grid-template-columns: minmax(0, 1fr)" in styles
    assert "overflow-wrap: anywhere" in styles


def test_canonical_delivery_preview_uses_authenticated_blob_and_clears_expired_session() -> None:
    payload = _node_json(
        r'''
import {
  hydrateCanonicalDeliveryPreview,
  releaseCanonicalDeliveryPreviews,
} from "./apps/studio/src/review-delivery-workspace.js";

globalThis.CustomEvent = class CustomEvent {
  constructor(type, options = {}) { this.type = type; this.detail = options.detail; }
};
const route = "/projects/project-001/production-runs/run-001/representative-episode-media/delivery/preview";
const requests = [];
const revoked = [];
const events = [];
const tokens = [];
const urlApi = {
  createObjectURL(blob) { return `blob:canonical-${blob.size}`; },
  revokeObjectURL(value) { revoked.push(value); },
};
const dependencies = {
  readToken: () => "session-token",
  baseUrl: () => "http://127.0.0.1:8876",
  resolveUrl: (value) => `http://127.0.0.1:8876${value}`,
  clearToken: (value) => tokens.push(value),
  URL: urlApi,
  eventTarget: { dispatchEvent: (event) => events.push({ type: event.type, detail: event.detail }) },
};
const preview = { dataset: {}, loadCalls: 0, load() { this.loadCalls += 1; } };
const objectUrl = await hydrateCanonicalDeliveryPreview(preview, route, {
  ...dependencies,
  fetch: async (url, options) => {
    requests.push({ url, authorization: options.headers.Authorization, cache: options.cache });
    return {
      ok: true,
      status: 200,
      headers: { get: (name) => name.toLowerCase() === "content-type" ? "video/mp4" : "" },
      blob: async () => new Blob(["canonical-video"]),
    };
  },
});
releaseCanonicalDeliveryPreviews({ URL: urlApi });

const expiredPreview = { dataset: {} };
await hydrateCanonicalDeliveryPreview(expiredPreview, route, {
  ...dependencies,
  fetch: async () => ({
    ok: false,
    status: 401,
    headers: { get: () => "application/json" },
  }),
});

let unsafeFetches = 0;
const unsafePreview = { dataset: {} };
await hydrateCanonicalDeliveryPreview(unsafePreview, "https://provider.invalid/signed", {
  ...dependencies,
  resolveUrl: (value) => value,
  fetch: async () => { unsafeFetches += 1; throw new Error("must not fetch"); },
});

process.stdout.write(JSON.stringify({
  objectUrl,
  preview,
  requests,
  revoked,
  events,
  tokens,
  expiredState: expiredPreview.dataset.previewState,
  unsafeState: unsafePreview.dataset.previewState,
  unsafeFetches,
}));
'''
    )

    assert payload["objectUrl"].startswith("blob:canonical-")
    assert payload["preview"]["src"] == payload["objectUrl"]
    assert payload["preview"]["dataset"]["previewState"] == "ready"
    assert payload["preview"]["loadCalls"] == 1
    assert payload["requests"] == [{
        "url": (
            "http://127.0.0.1:8876/projects/project-001/production-runs/run-001/"
            "representative-episode-media/delivery/preview"
        ),
        "authorization": "Bearer session-token",
        "cache": "no-store",
    }]
    assert payload["revoked"] == [payload["objectUrl"]]
    assert payload["tokens"] == [""]
    assert payload["expiredState"] == "session_expired"
    assert payload["events"] == [{
        "type": "afs:auth-session-expired",
        "detail": {"route": (
            "/projects/project-001/production-runs/run-001/"
            "representative-episode-media/delivery/preview"
        ), "status": 401},
    }]
    assert payload["unsafeState"] == "unavailable"
    assert payload["unsafeFetches"] == 0


def test_provider_free_authenticated_browser_fixture_restores_candidate_authority(tmp_path: Path) -> None:
    seed = prepare_provider_free_delivery_qa(tmp_path)

    assert seed["candidate_count"] == 2
    assert seed["selected_candidate_id"] == "candidate_002"
    assert str(seed["selected_revision_id"]).startswith("revision-")
    assert seed["episode_version_id"] == "ep-rainlight-001-v2"
    assert seed["canon_shot_count"] == 15
    assert seed["canon_checkpoint_version"] == 4
    assert seed["crew_execution"]["status"] == "episode_v2_bound"
    assert seed["crew_execution"]["role_count"] == 9
    assert seed["crew_execution"]["accepted_handoff_count"] == 8
    assert seed["crew_execution"]["reconfirmed_count"] == 8
    assert seed["crew_execution"]["propagation_complete"] is True
    assert seed["provider_calls_started"] is False
    assert seed["evidence_boundary"] == "provider-free deterministic UI/runtime verification only"
    assert seed["browser_preflight"] == {
        "ready": True,
        "missing_candidate_authority_fields": [],
        "persisted_candidate_count": 2,
        "authoritative_canon_ready": True,
        "episode_crew_ready": True,
        "stop_reason": "",
    }

    from apps.api.runtime_service import create_runtime_app
    from fastapi.testclient import TestClient

    with _seed_auth_environment(), TestClient(create_runtime_app(runtime_root=tmp_path)) as client:
        login = client.post(
            "/auth/login",
            json={"email": seed["email"], "password": seed["password"]},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['session_token']}"}
        run = client.get(
            f"/projects/{seed['project_id']}/production-runs/{seed['run_id']}",
            headers=headers,
        )
        state = client.get(f"/projects/{seed['project_id']}/studio-state", headers=headers)

    assert run.status_code == 200
    assert run.json()["production_run"]["selected_revision"]["candidate_id"] == "candidate_002"
    assert run.json()["production_run"]["quality_reviews"] == []
    canon = run.json()["production_run"]["representative_episode_binding"]["episode_canon"]
    assert len(canon["shots"]) == 15
    assert canon["shots"][0]["start_seconds"] == 0
    assert canon["shots"][-1]["end_seconds"] == 135
    assert canon["episode_version_id"] == "ep-rainlight-001-v2"
    assert canon["shots"][10]["current_approved_version_id"] == "shot-011-v2"
    candidates = state.json()["state"]["nodes"][seed["node_id"]]["params"]["candidatePreviewUrls"]
    assert len(candidates) == 2
    assert all(item["preview_url"].endswith("/preview") for item in candidates)
    assert all(len(item["canonical_digest"]) == 64 for item in candidates)
    assert all(item["parent_job_id"] == seed["job_id"] for item in candidates)
    assert all(item["project_id"] == seed["project_id"] for item in candidates)
    assert all(
        item["reusable_asset_authority"]["source_candidate_id"] == item["candidate_id"]
        and item["reusable_asset_authority"]["source_candidate_digest"] == item["canonical_digest"]
        for item in candidates
    )


class _seed_auth_environment:
    def __enter__(self):
        import os

        self.before = {key: os.environ.get(key) for key in ("AFS_AUTH_ENABLED", "AFS_INVITE_CODES")}
        os.environ["AFS_AUTH_ENABLED"] = "true"
        os.environ["AFS_INVITE_CODES"] = "delivery-qa-invite"
        return self

    def __exit__(self, *_args):
        import os

        for key, value in self.before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
