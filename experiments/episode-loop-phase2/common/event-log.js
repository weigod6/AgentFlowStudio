const ALLOWED_INPUTS = new Set(["mouse", "keyboard", "touch", "system"]);

export function createEventLogger({
  variant,
  sessionId,
  clock = () => performance.now(),
  existingEvents = [],
}) {
  if (!Array.isArray(existingEvents)) throw new Error("已有事件日志格式错误。");
  const events = existingEvents.map((event) => ({ ...event }));
  let priorEventElapsed = 0;
  for (const [index, event] of events.entries()) {
    if (
      event.sequence !== index + 1
      || event.variant !== variant
      || event.session_id !== sessionId
      || !Number.isFinite(Number(event.elapsed_ms))
      || Number(event.elapsed_ms) < priorEventElapsed
    ) {
      throw new Error("已有事件日志不连续或不属于当前会话。");
    }
    priorEventElapsed = Number(event.elapsed_ms);
  }
  const priorElapsed = events.length ? Number(events.at(-1).elapsed_ms) : 0;
  if (!Number.isFinite(priorElapsed) || priorElapsed < 0) throw new Error("已有事件时间无效。");
  const sessionStart = Number(clock());
  let lastElapsed = priorElapsed;
  const activationIds = new Set(
    events.map((event) => event.activation_id).filter((value) => typeof value === "string"),
  );

  function record({
    task,
    action,
    objectType,
    objectKey,
    fromView,
    toView,
    inputMethod,
    stateSummary,
    meaningfulActivation = true,
    activationId = null,
  }) {
    if (!ALLOWED_INPUTS.has(inputMethod)) throw new Error("未知输入方式。");
    if (meaningfulActivation && (typeof activationId !== "string" || !activationId.trim())) {
      throw new Error("可计数操作必须提供语义 activationId。");
    }
    const measured = Number(clock());
    const sessionDelta = Number.isFinite(measured) && Number.isFinite(sessionStart)
      ? Math.max(0, measured - sessionStart)
      : 0;
    const elapsedMs = Math.max(lastElapsed, priorElapsed + sessionDelta);
    lastElapsed = elapsedMs;
    const duplicateActivation = meaningfulActivation && activationIds.has(activationId);
    if (meaningfulActivation && !duplicateActivation) activationIds.add(activationId);
    const event = Object.freeze({
      sequence: events.length + 1,
      variant,
      session_id: sessionId,
      task,
      action,
      object_type: objectType,
      object_key: objectKey,
      from_view: fromView,
      to_view: toView,
      input_method: inputMethod,
      elapsed_ms: elapsedMs,
      activation_id: activationId,
      meaningful_activation: Boolean(meaningfulActivation && !duplicateActivation),
      screen_transition: Boolean(
        !duplicateActivation && fromView && toView && fromView !== toView,
      ),
      state_summary: stateSummary,
    });
    events.push(event);
    return event;
  }

  return {
    record,
    snapshot: () => events.map((event) => ({ ...event })),
  };
}

export function summarizeEvents(events) {
  return {
    meaningful_activations: events.filter((event) => event.meaningful_activation).length,
    context_transitions: events.filter((event) => event.screen_transition).length,
    elapsed_ms: events.length ? Math.max(0, Number(events.at(-1).elapsed_ms) || 0) : 0,
  };
}
