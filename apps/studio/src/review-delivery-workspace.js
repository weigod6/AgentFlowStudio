import { productionDeliveryUnavailableChecks } from "./production-delivery-view.js";
import { selectedDeliverySubmission } from "./review-delivery-state.js";

const canonicalPreviewObjectUrls = new Set();
const canonicalPreviewRequests = new Set();

const QUALITY_FIELDS = [
  ["story_intent_preserved", "叙事意图", "确认故事重点、情绪走向和信息层级没有偏离。", "narrative"],
  ["character_continuity_checked", "画面一致性", "确认角色、场景与关键视觉设定保持连续。", "consistency"],
  ["shot_coverage_checked", "镜头覆盖", "确认必要镜头与交付构图已覆盖。", "coverage"],
  ["revision_addressed", "改版要求", "确认本轮修改原因已经被处理。", "revision"],
];

export function renderReviewDeliveryWorkspace(root, state, handlers = {}) {
  releaseCanonicalDeliveryPreviews();
  root.replaceChildren();
  root.dataset.state = state.stale ? "stale" : state.phase;
  const shell = el("div", "review-shell");
  shell.append(buildHeader(state, handlers));
  if (["loading", "empty", "read_error"].includes(state.phase)) {
    shell.append(stateView(state, handlers));
  } else if (state.phase === "ready") {
    shell.append(buildWorkspace(state, handlers));
  }
  root.appendChild(shell);
}

function buildHeader(state, handlers) {
  const header = el("header", "review-header");
  const brand = el("div", "review-brand");
  const home = el("a", "review-back", "制作总览");
  home.href = "/studio/";
  home.setAttribute("aria-label", "返回 AgentFlow Studio 制作总览");
  brand.append(home, el("span", "review-divider", "/"), el("strong", "", "审核与交付"));

  const context = el("div", "review-context");
  if (state.workspace?.projects?.length) {
    const label = el("label", "project-picker");
    label.appendChild(el("span", "sr-only", "切换项目"));
    const select = el("select");
    select.setAttribute("aria-label", "切换审核项目");
    for (const project of state.workspace.projects) {
      const option = el("option", "", project.name || "未命名项目");
      option.value = project.project_id;
      option.selected = project.project_id === state.projectId;
      select.appendChild(option);
    }
    select.addEventListener("change", () => handlers.onProjectChange?.(select.value));
    label.appendChild(select);
    context.appendChild(label);
  }
  const account = el("button", "review-account", userLabel(state.authUser));
  account.type = "button";
  account.setAttribute("aria-label", "安全退出当前账户");
  account.addEventListener("click", () => handlers.onAction?.("signout"));
  context.appendChild(account);
  header.append(brand, context);
  return header;
}

function stateView(state, handlers) {
  const main = el("main", `review-state review-state-${state.phase}`);
  main.id = "review-main";
  main.tabIndex = -1;
  if (state.phase === "loading") {
    main.setAttribute("role", "status");
    main.setAttribute("aria-live", "polite");
    main.append(el("span", "review-spinner"), el("h1", "", "正在读取最新审核状态"), el("p", "", "正在核对候选、当前修订和交付记录。"));
    return main;
  }
  if (state.phase === "empty") {
    main.append(el("span", "state-mark", "○"), el("h1", "", "暂时没有可审核的制作版本"), el("p", "", "候选进入制作运行后，会在这里形成可比较、可追溯的主创审核。"));
    const home = el("a", "primary-button", "返回制作总览");
    home.href = "/studio/";
    main.appendChild(home);
    return main;
  }
  main.setAttribute("role", "alert");
  main.append(el("span", "state-mark state-mark-error", "!"), el("h1", "", "暂时无法读取审核状态"), el("p", "", state.error || "请检查连接后重试。"));
  const actions = el("div", "state-actions");
  actions.append(actionButton("retry", "重新读取", handlers), actionButton("reload", "刷新页面", handlers, "quiet-button"));
  main.appendChild(actions);
  return main;
}

function buildWorkspace(state, handlers) {
  const main = el("main", "review-workspace");
  main.id = "review-main";
  main.tabIndex = -1;
  main.append(buildPageHeading(state, handlers));
  if (state.stale || state.writeError || state.notice) main.append(buildNotice(state, handlers));
  main.append(buildEpisodeCanon(state, handlers));
  main.append(buildEpisodeCrew(state));

  const layout = el("div", "review-layout");
  const stage = el("section", "review-stage");
  stage.setAttribute("aria-labelledby", "candidate-heading");
  stage.append(buildCandidateHeading(state), buildComparison(state, handlers));
  const aside = el("aside", "decision-rail");
  aside.setAttribute("aria-label", "版本状态与主创决定");
  aside.append(
    versionPanel(state),
    annotationPanel(state),
    qualityPanel(state),
    actionPanel(state, handlers),
    deliveryPanel(state),
    lineagePanel(state),
  );
  layout.append(stage, aside);
  main.appendChild(layout);
  return main;
}

function buildEpisodeCrew(state) {
  const execution = state.project?.crew?.episode_execution;
  const section = el("section", "episode-crew-board");
  section.setAttribute("aria-labelledby", "episode-crew-heading");
  const header = el("header", "episode-crew-head");
  const copy = el("div");
  const title = el("h2", "", "本集制作团队");
  title.id = "episode-crew-heading";
  copy.append(
    title,
    el("p", "", execution?.role_count === 9
      ? "九个制作岗位的当前责任、批准版本与下游确认均来自项目服务器记录。"
      : "本集尚未形成完整的制作团队责任链，不会把缺失岗位显示为已完成。"),
  );
  const complete = execution?.propagation_complete === true;
  header.append(copy, el("span", `crew-state ${complete ? "ready" : "pending"}`, complete ? "版本传播已完成" : "等待下游确认"));
  section.appendChild(header);
  if (!execution?.responsibilities?.length) {
    section.appendChild(el("div", "crew-empty", "制作团队责任状态暂不可用"));
    return section;
  }
  const summary = el("div", "crew-summary");
  summary.append(
    factRow("当前批准版本", execution.approved_version || "当前批准版本"),
    factRow("岗位覆盖", `${execution.role_count}/9`),
    factRow("下游重确认", `${execution.reconfirmed_count}/8`),
  );
  section.appendChild(summary);
  const list = el("ol", "crew-responsibility-list");
  list.setAttribute("aria-label", "本集九个制作团队岗位责任");
  for (const item of execution.responsibilities) {
    const row = el("li", `crew-responsibility ${item.reconfirmed ? "reconfirmed" : item.pending_reconfirmation ? "pending" : "active"}`);
    const head = el("div", "crew-responsibility-head");
    head.append(el("strong", "", item.role), el("span", "crew-version", item.approved_version));
    row.append(
      head,
      el("p", "", item.responsibility),
      el("div", "crew-responsibility-state", item.propagation_state),
    );
    list.appendChild(row);
  }
  section.appendChild(list);
  return section;
}

function buildEpisodeCanon(state, handlers) {
  const canon = state.episodeCanon;
  const section = el("section", "episode-canon-board");
  section.setAttribute("aria-labelledby", "episode-canon-heading");
  const header = el("header", "episode-canon-head");
  const copy = el("div");
  const title = el("h2", "", "本集制作规范");
  title.id = "episode-canon-heading";
  copy.append(
    title,
    el("p", "", canon
      ? "镜头顺序、版本、连续性与音频覆盖均来自当前项目的服务器制作记录。"
      : "本集尚未绑定权威制作规范，镜头与素材状态不会被推断为已完成。"),
  );
  header.append(copy, el("span", "canon-status", canon ? "15/15 镜已绑定" : "等待绑定"));
  section.appendChild(header);
  if (!canon) {
    const empty = el("div", "canon-empty");
    empty.setAttribute("role", "status");
    empty.append(el("strong", "", "制作规范暂不可用"), el("span", "", "请先在当前项目中建立本集角色、场景、镜头与音频版本。"));
    section.appendChild(empty);
    return section;
  }

  const metrics = el("div", "canon-metrics");
  metrics.append(
    canonMetric("本集版本", versionLabel(canon.episode_version_id)),
    canonMetric("镜头时长", `${canon.duration_seconds} 秒 · 15 镜`),
    canonMetric("画面素材", canon.all_assets_ready ? "已齐" : `${canon.pending_media_count} 项待补齐`, canon.all_assets_ready),
    canonMetric("音频覆盖", canon.audio.all_audio_ready ? "15/15 已齐" : `15/15 已规划 · ${canon.audio.pending_asset_count} 项待制作`, canon.audio.all_audio_ready),
    canonMetric("下游确认", canon.propagation_complete ? "已完成" : "待制作团队确认", canon.propagation_complete),
  );
  section.appendChild(metrics);
  section.appendChild(buildCanonicalMediaBoard(canon.media_delivery, handlers.canonicalPreviewSession));

  const canonDetails = el("div", "canon-identity-grid");
  canonDetails.append(
    canonIdentityGroup("角色设定", canon.characters),
    canonIdentityGroup("场景设定", canon.scenes),
  );
  section.appendChild(canonDetails);

  const timeline = el("ol", "episode-shot-timeline");
  timeline.setAttribute("aria-label", "本集十五镜制作时间线");
  for (const shot of canon.shots) timeline.appendChild(shotCanonCard(shot));
  section.appendChild(timeline);
  return section;
}

function buildCanonicalMediaBoard(media, previewSession) {
  const board = el("section", "canonical-media-board");
  board.setAttribute("aria-labelledby", "canonical-media-heading");
  const head = el("header", "canonical-media-head");
  const copy = el("div");
  const title = el("h3", "", "规范媒体与技术交付");
  title.id = "canonical-media-heading";
  copy.append(
    title,
    el("p", "", "素材必须与当前第 2 版规范、十五镜顺序和责任链一致；结构检查不等于内容质量验收。"),
  );
  const ready = media?.accepted_count === 25;
  head.append(copy, el("span", `media-intake-status ${ready ? "ready" : "pending"}`, ready ? "25/25 已接纳" : `${media?.accepted_count || 0}/25 待接纳`));
  board.appendChild(head);

  const metrics = el("div", "canonical-media-metrics");
  metrics.append(
    canonMetric("角色/场景/镜头", ready ? `${media.visual_count}/21 已接纳` : "等待规范素材", ready && media.visual_count === 21),
    canonMetric("对白/音乐/音效/母版", ready ? `${media.audio_count}/4 已接纳` : "等待音频素材", ready && media.audio_count === 4),
    canonMetric("结构连续性", continuityLabel(media?.continuity_status), media?.continuity_status === "structural_checked"),
    canonMetric("技术成片", media?.assembly_status === "technical_qa_passed" ? "135 秒技术检查通过" : "素材齐备后可组装", media?.assembly_status === "technical_qa_passed"),
  );
  board.appendChild(metrics);

  if (media?.continuity_checks?.length) {
    const checks = el("ul", "canonical-continuity-list");
    checks.setAttribute("aria-label", "规范媒体结构连续性检查");
    for (const item of media.continuity_checks) {
      const row = el("li", item.status === "structural_checked" ? "checked" : "blocked");
      row.append(el("strong", "", item.label), el("span", "", continuityLabel(item.status)));
      checks.appendChild(row);
    }
    board.appendChild(checks);
  }

  if (media?.delivery_preview_url && media?.assembly_status === "technical_qa_passed") {
    const preview = el("video", "canonical-delivery-preview");
    preview.controls = true;
    preview.preload = "metadata";
    preview.setAttribute("aria-label", "本集 135 秒技术交付预览");
    preview.dataset.previewState = "loading";
    board.appendChild(preview);
    void hydrateCanonicalDeliveryPreview(preview, media.delivery_preview_url, previewSession);
  }
  board.appendChild(el("p", "media-evidence-note", "当前仅证明媒体接纳、结构连续性与技术组装机制；代表性内容质量、人工验收和商业验证尚未开始。"));
  return board;
}

export async function hydrateCanonicalDeliveryPreview(preview, route, session = {}) {
  const readToken = session.readToken;
  const resolveUrl = session.resolveUrl;
  const readBaseUrl = session.baseUrl;
  const clearToken = session.clearToken;
  const fetchImpl = session.fetch || globalThis.fetch;
  const urlApi = session.URL || globalThis.URL;
  const eventTarget = session.eventTarget || globalThis.window;
  const abortController = session.abortController || new AbortController();
  const raw = String(route || "").trim();
  const resolved = resolveUrl?.(raw) || "";
  const token = readToken?.() || "";
  if (!preview || !token || !safeCanonicalDeliveryRoute(raw, resolved, readBaseUrl?.() || "")) {
    if (preview) preview.dataset.previewState = "unavailable";
    return "";
  }
  canonicalPreviewRequests.add(abortController);
  try {
    const response = await fetchImpl(resolved, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: abortController.signal,
    });
    if (response.status === 401) {
      clearToken?.("");
      preview.dataset.previewState = "session_expired";
      try {
        eventTarget?.dispatchEvent(new CustomEvent("afs:auth-session-expired", {
          detail: { route: raw, status: 401 },
        }));
      } catch {
        // Tests may execute without a browser CustomEvent implementation.
      }
      return "";
    }
    const contentType = String(response.headers.get("content-type") || "").toLowerCase();
    if (!response.ok || !contentType.startsWith("video/")) {
      preview.dataset.previewState = "unavailable";
      return "";
    }
    const blob = await response.blob();
    if (abortController.signal.aborted) return "";
    const objectUrl = urlApi.createObjectURL(blob);
    canonicalPreviewObjectUrls.add(objectUrl);
    preview.src = objectUrl;
    preview.dataset.previewState = "ready";
    preview.load?.();
    return objectUrl;
  } catch {
    if (!abortController.signal.aborted) preview.dataset.previewState = "unavailable";
    return "";
  } finally {
    canonicalPreviewRequests.delete(abortController);
  }
}

export function releaseCanonicalDeliveryPreviews(dependencies = {}) {
  const urlApi = dependencies.URL || globalThis.URL;
  for (const request of canonicalPreviewRequests) request.abort();
  canonicalPreviewRequests.clear();
  for (const objectUrl of canonicalPreviewObjectUrls) urlApi.revokeObjectURL(objectUrl);
  canonicalPreviewObjectUrls.clear();
}

function safeCanonicalDeliveryRoute(raw, resolved, base) {
  if (!raw.startsWith("/projects/") || raw.startsWith("//") || !resolved || !base) return false;
  try {
    const resolvedUrl = new URL(resolved);
    const baseUrl = new URL(base);
    return resolvedUrl.origin === baseUrl.origin
      && resolvedUrl.pathname.startsWith("/projects/")
      && resolvedUrl.pathname.endsWith("/representative-episode-media/delivery/preview");
  } catch {
    return false;
  }
}

function continuityLabel(value) {
  if (value === "structural_checked") return "结构已核对";
  if (value === "blocked") return "存在结构阻塞";
  return "尚未评估";
}

function canonMetric(label, value, passed = false) {
  const item = el("div", `canon-metric ${passed ? "ready" : "pending"}`);
  item.append(el("span", "", label), el("strong", "", value));
  return item;
}

function canonIdentityGroup(title, items) {
  const group = el("section", "canon-identity-group");
  group.appendChild(el("h3", "", title));
  const list = el("ul");
  for (const item of items) {
    const row = el("li");
    row.append(
      el("strong", "", item.name || "待命名"),
      el("span", "", versionLabel(item.version_id)),
      el("small", "", item.continuity[0] || "连续性要求待补充"),
    );
    list.appendChild(row);
  }
  group.appendChild(list);
  return group;
}

function shotCanonCard(shot) {
  const card = el("li", "episode-shot-card");
  const head = el("header", "shot-card-head");
  const title = el("div");
  title.append(
    el("strong", "", shot.label),
    el("span", "shot-time", `${formatTime(shot.start_seconds)}–${formatTime(shot.end_seconds)}`),
  );
  head.append(title, el("span", "shot-version", versionLabel(shot.version_id)));
  const context = el("p", "shot-context", `${shot.scene || "场景待确认"} · ${shot.characters.join("、") || "角色待确认"}`);
  const action = el("p", "shot-action", shot.visual_action || "画面动作待补充");
  const craft = el("dl", "shot-craft");
  craft.append(
    definition("镜头", shot.camera || "待确认"),
    definition("运动", shot.motion || "待确认"),
    definition("连续性", shot.continuity),
  );
  const statuses = el("div", "shot-statuses");
  statuses.append(
    el("span", shot.media.all_ready ? "shot-chip ready" : "shot-chip pending", shot.media.status),
    el("span", shot.audio.status === "音频已齐" ? "shot-chip ready" : "shot-chip pending", shot.audio.status),
  );
  card.append(head, context, action, craft, statuses);
  return card;
}

function definition(term, description) {
  const wrap = el("div");
  wrap.append(el("dt", "", term), el("dd", "", description));
  return wrap;
}

function formatTime(seconds) {
  const safe = Math.max(0, Math.trunc(Number(seconds) || 0));
  return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(safe % 60).padStart(2, "0")}`;
}

function versionLabel(value) {
  const match = String(value || "").match(/-v(\d+)$/i);
  return match ? `第 ${Number(match[1])} 版` : "当前批准版本";
}

function buildPageHeading(state, handlers) {
  const head = el("div", "review-page-heading");
  const copy = el("div");
  copy.append(
    el("span", "eyebrow", "主创审核工作台"),
    el("h1", "", state.project?.name || "审核与交付"),
    el("p", "", `${state.project?.episode || "当前制作单元"} · ${state.project?.current_stage || "等待审核"}`),
  );
  const refresh = actionButton("refresh", state.busy === "refresh" ? "正在读取" : "读取最新状态", handlers, "quiet-button");
  refresh.setAttribute("aria-label", state.busy === "refresh" ? "正在读取最新状态" : "读取最新审核状态");
  refresh.disabled = Boolean(state.busy);
  head.append(copy, refresh);
  return head;
}

function buildNotice(state, handlers) {
  const stale = Boolean(state.stale);
  const notice = el("section", `review-notice ${stale || state.writeError ? "notice-error" : "notice-ok"}`);
  notice.setAttribute("role", stale || state.writeError ? "alert" : "status");
  notice.setAttribute("aria-live", "polite");
  const text = stale
    ? "版本已发生变化。批准与导出已暂停，请读取最新状态后继续。"
    : state.writeError || state.notice;
  notice.append(el("strong", "", stale ? "需要重新核对" : state.writeError ? "操作未完成" : "状态已更新"), el("span", "", text));
  if (stale) notice.appendChild(actionButton("refresh", "读取最新状态", handlers, "notice-button"));
  return notice;
}

function buildCandidateHeading(state) {
  const head = el("div", "candidate-heading");
  const copy = el("div");
  const title = el("h2", "", "候选对比");
  title.id = "candidate-heading";
  copy.append(title, el("p", "", "并排核对画面、节奏与本轮修改方向。选择仅对当前制作版本生效。"));
  const count = el("span", "candidate-count", `${state.candidates.length} 个方案`);
  head.append(copy, count);
  return head;
}

function buildComparison(state, handlers) {
  const grid = el("div", "candidate-comparison");
  grid.setAttribute("role", "radiogroup");
  grid.setAttribute("aria-label", "制作候选方案");
  for (const [index, candidate] of state.candidates.entries()) {
    const focused = candidate.candidate_id === state.focusedCandidateId;
    const selected = candidate.candidate_id === state.selectedCandidateId && !state.rejected;
    const card = el("article", `candidate-card ${focused ? "focused" : ""} ${selected ? "selected" : ""}`);
    card.dataset.candidateId = candidate.candidate_id;
    card.setAttribute("role", "radio");
    card.setAttribute("aria-checked", focused ? "true" : "false");
    card.tabIndex = focused || (!state.focusedCandidateId && index === 0) ? 0 : -1;
    card.setAttribute("aria-label", `${candidate.label}${selected ? "，当前已选" : ""}`);
    card.addEventListener("click", () => handlers.onCandidateFocus?.(candidate.candidate_id));
    card.addEventListener("keydown", (event) => handleCardKeydown(event, state.candidates, candidate.candidate_id, handlers));

    const media = el("div", "candidate-media");
    if (candidate.available) {
      const element = document.createElement(candidate.media_kind === "video" ? "video" : "img");
      element.src = candidate.preview_url;
      if (candidate.media_kind === "video") {
        element.controls = true;
        element.preload = "metadata";
        element.setAttribute("aria-label", `${candidate.label}视频预览`);
      } else {
        element.alt = `${candidate.label}画面预览`;
        element.loading = "eager";
      }
      element.addEventListener("error", () => {
        media.classList.add("media-unavailable");
        media.replaceChildren(el("span", "", "预览暂不可用"), el("small", "", "不会据此标记为已检查"));
      }, { once: true });
      media.appendChild(element);
    } else {
      media.classList.add("media-unavailable");
      media.append(el("span", "", "预览暂不可用"), el("small", "", "请返回制作页恢复媒体后再做通过决定"));
    }
    const meta = el("div", "candidate-meta");
    const badge = selected ? "当前版本" : focused ? "正在查看" : "待比较";
    meta.append(el("strong", "", candidate.label), el("span", selected ? "status-chip selected" : "status-chip", badge));
    card.append(media, meta);
    grid.appendChild(card);
  }
  return grid;
}

function versionPanel(state) {
  const revision = state.run?.selected_revision || {};
  const decisionCount = Array.isArray(state.run?.creator_decisions) ? state.run.creator_decisions.length : 0;
  const panel = surface("当前版本", state.rejected ? "已退回" : state.selectedCandidateId ? `第 ${Math.max(1, decisionCount)} 版` : "尚未选择");
  panel.body.append(
    factRow("当前方案", state.candidates.find((item) => item.candidate_id === state.selectedCandidateId)?.label || "未选择"),
    factRow("修改原因", state.rejected ? "等待新的选择或返修" : String(revision.revision_intent || "尚未记录修改原因")),
  );
  return panel.wrap;
}

function annotationPanel(state) {
  const panel = surface("本轮意见", "随决定保存");
  const label = el("label", "annotation-field");
  label.append(el("span", "", "给制作团队的修改说明"));
  const textarea = el("textarea");
  textarea.rows = 4;
  textarea.maxLength = 800;
  textarea.dataset.revisionNote = "true";
  textarea.placeholder = "例如：保留构图，降低背景亮度，让人物表情更清楚。";
  textarea.disabled = Boolean(state.busy || state.stale);
  label.appendChild(textarea);
  panel.body.append(label, el("small", "surface-note", "说明只会在“要求返修”或“退回候选”提交成功后进入权威版本记录。"));
  return panel.wrap;
}

function qualityPanel(state) {
  const panel = surface("交付检查", state.quality?.approved ? "已通过" : "待检查");
  const readiness = reviewDeliveryActionReadiness(state);
  const fieldset = el("fieldset", "quality-checklist");
  fieldset.disabled = Boolean(state.busy || state.stale || !readiness.canSubmitDelivery || state.quality?.approved);
  const legend = el("legend", "sr-only", "质量门禁检查项");
  fieldset.appendChild(legend);
  for (const [name, title, copy, key] of QUALITY_FIELDS) {
    const row = el("label", "quality-row");
    const input = el("input");
    input.type = "checkbox";
    input.dataset.qualityCheck = name;
    input.checked = state.quality?.[key] === "passed";
    const text = el("span", "quality-copy");
    text.append(el("strong", "", title), el("small", "", copy));
    row.append(input, text, stateChip(state.quality?.[key]));
    fieldset.appendChild(row);
  }
  panel.body.appendChild(fieldset);
  for (const check of productionDeliveryUnavailableChecks()) {
    panel.body.appendChild(unavailableCheck(check.label, check.status, check.message));
  }
  panel.body.append(el("small", "surface-note", "不可用不会被显示为通过，也不会伪造为已检查。"));
  return panel.wrap;
}

function actionPanel(state, handlers) {
  const panel = surface("主创决定", state.busy ? "处理中" : "");
  const readiness = reviewDeliveryActionReadiness(state);
  const select = actionButton("select", "选为当前版本", handlers, "primary-button");
  select.disabled = Boolean(state.busy || state.stale || !state.reviewSnapshot || !readiness.focusedMediaAvailable);
  const revise = actionButton("revise", "要求返修", handlers, "quiet-button");
  revise.disabled = Boolean(state.busy || state.stale || !state.reviewSnapshot || !state.selectedCandidateId || !readiness.focusedMediaAvailable);
  const reject = actionButton("reject", "退回候选", handlers, "danger-button");
  reject.disabled = Boolean(state.busy || state.stale || !state.reviewSnapshot);
  const approve = actionButton("approve", state.quality?.approved ? "质量门禁已通过" : "批准当前修订", handlers, "primary-button");
  approve.disabled = Boolean(state.busy || state.stale || !readiness.canSubmitDelivery || state.quality?.approved);
  const exportButton = actionButton("export", state.exports.length ? "再次生成交付包" : "生成交付包", handlers, "export-button");
  exportButton.disabled = Boolean(state.busy || state.stale || !readiness.canSubmitDelivery || !state.quality?.approved);
  panel.body.append(select, el("div", "split-actions", ""));
  panel.body.querySelector(".split-actions").append(revise, reject);
  panel.body.append(approve, exportButton);
  return panel.wrap;
}

function deliveryPanel(state) {
  const readiness = reviewDeliveryActionReadiness(state);
  const exportReady = readiness.canSubmitDelivery && state.quality?.approved && !state.rejected;
  const panel = surface("交付状态", state.exports.length ? "已生成" : exportReady ? "可导出" : "未就绪");
  const statusCard = el("div", "delivery-status-card");
  statusCard.append(
    factRow("候选版本", state.selectedCandidateId && !state.rejected ? "已选择" : "待选择"),
    factRow("质量门禁", state.quality?.approved ? "已通过" : "待通过"),
    factRow("导出准备", exportReady ? "已就绪" : "未就绪"),
  );
  panel.body.appendChild(statusCard);
  if (!state.exports.length) panel.body.appendChild(el("p", "empty-delivery", "尚未生成当前版本的交付包。"));
  for (const item of state.exports) {
    const row = el("article", "delivery-history-row");
    row.append(el("strong", "", item.label), el("time", "", item.created_at));
    panel.body.appendChild(row);
  }
  return panel.wrap;
}

export function reviewDeliveryActionReadiness(state) {
  const focused = state?.candidates?.find((item) => item.candidate_id === state.focusedCandidateId);
  const selected = state?.candidates?.find((item) => item.candidate_id === state.selectedCandidateId);
  const focusedMediaAvailable = Boolean(focused?.available);
  const selectedMediaAvailable = Boolean(selected?.available);
  return {
    focusedMediaAvailable,
    selectedMediaAvailable,
    canSubmitDelivery: selectedMediaAvailable && Boolean(selectedDeliverySubmission(state)),
  };
}

function lineagePanel(state) {
  const details = el("details", "lineage-panel");
  const summary = el("summary", "", "查看版本沿革");
  details.appendChild(summary);
  const list = el("ol", "lineage-list");
  for (const item of state.lineage) {
    const row = el("li");
    row.append(el("span", "lineage-dot"), el("span", "", item.label));
    list.appendChild(row);
  }
  details.append(list, el("small", "surface-note", "这里展示制作关系，不展示内部标识或原始记录。"));
  return details;
}

function unavailableCheck(title, status, copy) {
  const row = el("div", "quality-row quality-unavailable");
  const mark = el("span", "unavailable-mark", "—");
  mark.setAttribute("aria-hidden", "true");
  const text = el("span", "quality-copy");
  text.append(el("strong", "", title), el("small", "", copy));
  row.append(mark, text, el("span", "check-state unavailable", status));
  return row;
}

function stateChip(value) {
  const passed = value === "passed";
  return el("span", `check-state ${passed ? "passed" : "unchecked"}`, passed ? "已检查" : "未检查");
}

function surface(title, badge = "") {
  const wrap = el("section", "review-surface");
  const head = el("header", "review-surface-head");
  head.appendChild(el("h2", "", title));
  if (badge) head.appendChild(el("span", "surface-badge", badge));
  const body = el("div", "review-surface-body");
  wrap.append(head, body);
  return { wrap, body };
}

function factRow(label, value) {
  const row = el("div", "fact-row");
  row.append(el("span", "", label), el("strong", "", value));
  return row;
}

function actionButton(action, label, handlers, className = "") {
  const button = el("button", className, label);
  button.type = "button";
  button.dataset.action = action;
  button.addEventListener("click", () => handlers.onAction?.(action, button));
  return button;
}

function handleCardKeydown(event, candidates, currentId, handlers) {
  if (["Enter", " "].includes(event.key)) {
    event.preventDefault();
    handlers.onCandidateFocus?.(currentId);
    return;
  }
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
  const current = candidates.findIndex((item) => item.candidate_id === currentId);
  const delta = ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1;
  const next = candidates[(current + delta + candidates.length) % candidates.length];
  event.preventDefault();
  handlers.onCandidateFocus?.(next.candidate_id, { focus: true });
}

function userLabel(user) {
  return String(user?.display_name || user?.email || "账户").slice(0, 24);
}

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
}
