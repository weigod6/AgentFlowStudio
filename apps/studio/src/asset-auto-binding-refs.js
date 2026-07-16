const GRAPH_ARTIFACT_TYPE = "agentflow_asset_auto_binding_graph";

export function assetAutoBindingGraph(value) {
  return value?.artifact_type === GRAPH_ARTIFACT_TYPE ? value : null;
}

export function graphBoundVisualAssetsForShot(graph, structuredShot) {
  const safeGraph = assetAutoBindingGraph(graph);
  if (!safeGraph) return [];
  const refs = Array.isArray(structuredShot?.asset_refs) ? structuredShot.asset_refs : [];
  const result = [];
  const seen = new Set();
  for (const suggestion of Array.isArray(safeGraph.binding_suggestions) ? safeGraph.binding_suggestions : []) {
    if (suggestion?.binding_state !== "bound") continue;
    if (!suggestionMatchesShotRef(suggestion, refs)) continue;
    const assetId = safeToken(suggestion.fixed_visual_asset_id);
    if (!assetId || seen.has(assetId)) continue;
    seen.add(assetId);
    result.push(visualAssetFromSuggestion(safeGraph, suggestion));
  }
  return result;
}

export function nodeReferenceStackForGraphBoundAssets(graph, structuredShot, nodeId) {
  const safeGraph = assetAutoBindingGraph(graph);
  if (!safeGraph) return null;
  const refs = [];
  for (const visual of graphBoundVisualAssetsForShot(safeGraph, structuredShot)) {
    refs.push({
      reference_id: visual.binding_id,
      reference_type: "binding",
      studio_entity_id: "binding",
      scope: "node",
      target_slot: `asset_binding:${safeToken(visual.graph_asset_id)}`,
      target_ref: visual.asset_id,
      status: "bound",
      priority: Math.max(82, Math.round((visual.confidence || 0) * 100)),
      source: "asset_auto_binding_graph",
      source_algorithm_id: safeToken(safeGraph.algorithm_id),
      source_relationship_type: "asset_auto_binding_established",
      selected: true,
      conflict_state: "selected",
      block_reasons: [],
      label: visual.label,
      asset_label: visual.label,
      confidence: visual.confidence,
    });
  }
  if (!refs.length) return null;
  return {
    artifact_type: "studio_node_reference_stack",
    schema_version: "0.1.0",
    node_id: safeToken(nodeId),
    summary: {
      asset_auto_binding_reference_count: refs.length,
      selected_reference_count: refs.length,
      provider_calls_started: false,
      writes_long_term_memory: false,
      writes_company_kb: false,
    },
    reference_stack: refs,
    references: refs,
    non_claims: [
      "not provider smoke",
      "not generated media QA",
      "not human acceptance",
      "not fixed asset promotion",
      "not durable memory promotion",
    ],
  };
}

export function graphBoundFixedAssetIds(graph, structuredShot) {
  return graphBoundVisualAssetsForShot(graph, structuredShot).map((asset) => asset.asset_id).filter(Boolean);
}

function visualAssetFromSuggestion(graph, suggestion) {
  const lineage = suggestion.lineage_refs && typeof suggestion.lineage_refs === "object" ? suggestion.lineage_refs : {};
  const assetId = safeToken(suggestion.fixed_visual_asset_id);
  return {
    asset_id: assetId,
    visual_asset_id: assetId,
    asset_type: safeAssetType(suggestion.asset_type),
    label: safeText(suggestion.label, 80),
    status: "fixed",
    source: "asset_auto_binding_graph",
    graph_bound: true,
    graph_asset_id: safeToken(suggestion.graph_asset_id),
    binding_id: safeToken(suggestion.binding_id),
    confidence: safeConfidence(suggestion.confidence),
    source_evidence: {
      source_algorithm_id: safeToken(graph.algorithm_id),
      source_relationship_type: "asset_auto_binding_established",
      source_node_id: safeToken(lineage.fixed_source_node_id),
      source_human_gate_id: safeToken(lineage.source_human_gate_id),
      source_asset_card_candidate_id: safeToken(lineage.source_asset_card_candidate_id),
    },
  };
}

function suggestionMatchesShotRef(suggestion, refs) {
  if (!refs.length) return false;
  const graphAssetId = safeToken(suggestion.graph_asset_id);
  const assetType = safeAssetType(suggestion.asset_type);
  const labelKey = normalizedLabel(suggestion.label);
  return refs.some((ref) => {
    const refGraphId = safeToken(ref?.graph_asset_id || ref?.graphAssetId || ref?.asset_id);
    if (graphAssetId && refGraphId === graphAssetId) return true;
    return safeAssetType(ref?.asset_type) === assetType && normalizedLabel(ref?.label) === labelKey;
  });
}

function safeAssetType(value) {
  const text = String(value || "").trim();
  return ["character", "scene", "prop"].includes(text) ? text : "character";
}

function safeText(value, limit) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function safeToken(value) {
  return String(value || "").replace(/[^0-9A-Za-z_.:-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 160);
}

function safeConfidence(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(Number(value.toFixed(3)), 1));
}

function normalizedLabel(value) {
  return String(value || "").replace(/[^0-9A-Za-z\u4e00-\u9fff]+/g, "").toLowerCase();
}
