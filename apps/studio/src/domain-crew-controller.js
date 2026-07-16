const EMPTY_STATE = Object.freeze({
  status: "idle",
  projectId: "",
  userId: "",
  crew: null,
  authorityValid: false,
  error: "",
  busyAction: "",
});

export function createDomainCrewController({ getRuntime, onNavigateNode } = {}) {
  let state = { ...EMPTY_STATE };
  let contextKey = "";
  let requestSequence = 0;
  const listeners = new Set();

  function snapshot() {
    return state;
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(state);
    return () => listeners.delete(listener);
  }

  function setContext({ runtime = getRuntime?.(), userId = "" } = {}) {
    const projectId = String(runtime?.projectId || "");
    const safeUserId = String(userId || "");
    const nextKey = `${projectId}\u0000${safeUserId}`;
    if (nextKey === contextKey) return false;
    contextKey = nextKey;
    requestSequence += 1;
    replace({ ...EMPTY_STATE, projectId, userId: safeUserId });
    return true;
  }

  async function load({ allowMissing = true } = {}) {
    const runtime = requireRuntime();
    const activeKey = contextKey;
    const sequence = ++requestSequence;
    patch({ status: "loading", authorityValid: false, error: "" });
    try {
      const payload = await runtime.getDomainCrew();
      if (!isCurrent(activeKey, sequence)) return state;
      replaceCrew(payload?.crew);
      return state;
    } catch (error) {
      if (!isCurrent(activeKey, sequence)) return state;
      if (allowMissing && Number(error?.status) === 404) {
        replace({ ...state, status: "missing", crew: null, authorityValid: true, error: "", busyAction: "" });
        return state;
      }
      replace({ ...state, status: "error", authorityValid: false, error: safeError(error), busyAction: "" });
      throw error;
    }
  }

  async function createCrew(crewId) {
    const runtime = requireRuntime();
    requireMutationAuthority();
    const activeKey = contextKey;
    patch({ busyAction: "create_crew", error: "" });
    try {
      await runtime.createDomainCrew({ crew_id: String(crewId || "") });
      if (activeKey !== contextKey) return state;
      return await load({ allowMissing: false });
    } catch (error) {
      return handleMutationError(error, activeKey);
    }
  }

  const createTask = (payload) => mutate("create_task", (runtime, expected) =>
    runtime.createDomainCrewTask(withExpected(payload, expected)));
  const claimTask = (taskId, agentId) => mutate("claim_task", (runtime, expected) =>
    runtime.claimDomainCrewTask(taskId, { agent_id: agentId, expected_state_version: expected }));
  const sendMessage = (payload) => mutate("send_message", (runtime, expected) =>
    runtime.sendDomainCrewMessage(withExpected(payload, expected)));
  const createHandoff = (payload) => mutate("create_handoff", (runtime, expected) =>
    runtime.createDomainCrewHandoff(withExpected(payload, expected)));
  const decideHandoff = (handoffId, payload) => mutate("decide_handoff", (runtime, expected) =>
    runtime.decideDomainCrewHandoff(handoffId, withExpected(payload, expected)));
  const createConflict = (payload) => mutate("create_conflict", (runtime, expected) =>
    runtime.createDomainCrewConflict(withExpected(payload, expected)));
  const arbitrateConflict = (conflictId, payload) => mutate("arbitrate_conflict", (runtime, expected) =>
    runtime.arbitrateDomainCrewConflict(conflictId, withExpected(payload, expected)));
  const reconfirmPropagation = (affectedRefId, payload) => mutate("reconfirm_propagation", (runtime, expected) =>
    runtime.reconfirmDomainCrewPropagation(affectedRefId, withExpected(payload, expected)));

  async function mutate(action, request) {
    const runtime = requireRuntime();
    const expected = stateVersion();
    const activeKey = contextKey;
    patch({ busyAction: action, error: "" });
    try {
      await request(runtime, expected);
      if (activeKey !== contextKey) return state;
      return await load({ allowMissing: false });
    } catch (error) {
      return handleMutationError(error, activeKey);
    }
  }

  async function handleMutationError(error, activeKey) {
    if (activeKey !== contextKey) return state;
    if (Number(error?.status) === 409) {
      try {
        await load({ allowMissing: false });
      } catch (reloadError) {
        // load() already invalidated stale authority and recorded the read failure.
        throw reloadError;
      }
    }
    if (activeKey === contextKey) {
      const status = Number(error?.status);
      const authorityAmbiguous = !status || status >= 500;
      patch({
        busyAction: "",
        authorityValid: authorityAmbiguous ? false : state.authorityValid,
        error: safeError(error),
      });
    }
    throw error;
  }

  function navigateToNode(nodeId) {
    const safeNodeId = String(nodeId || "");
    if (safeNodeId) onNavigateNode?.(safeNodeId);
  }

  function stateVersion() {
    requireMutationAuthority();
    const version = Number(state.crew?.state_version);
    if (!Number.isInteger(version) || version < 1) {
      throw new Error("Domain crew must be loaded before mutation");
    }
    return version;
  }

  function requireMutationAuthority() {
    if (!state.authorityValid) {
      throw new Error("Domain crew authority is unavailable; reload required before mutation");
    }
  }

  function requireRuntime() {
    const runtime = getRuntime?.();
    if (!runtime?.projectId || runtime.projectId !== state.projectId) {
      throw new Error("Domain crew runtime context changed");
    }
    return runtime;
  }

  function replaceCrew(crew) {
    if (!crew || crew.project_id !== state.projectId) {
      throw new Error("Domain crew response does not match the active project");
    }
    replace({ ...state, status: "ready", crew, authorityValid: true, error: "", busyAction: "" });
  }

  function isCurrent(activeKey, sequence) {
    return activeKey === contextKey && sequence === requestSequence;
  }

  function replace(next) {
    state = next;
    for (const listener of listeners) listener(state);
  }

  function patch(values) {
    replace({ ...state, ...values });
  }

  return {
    snapshot,
    subscribe,
    setContext,
    load,
    createCrew,
    createTask,
    claimTask,
    sendMessage,
    createHandoff,
    decideHandoff,
    createConflict,
    arbitrateConflict,
    reconfirmPropagation,
    navigateToNode,
  };
}

function withExpected(payload, expectedStateVersion) {
  const { expected_state_version: _ignored, ...safePayload } = payload || {};
  return { ...safePayload, expected_state_version: expectedStateVersion };
}

function safeError(error) {
  return String(error?.message || error || "制作团队请求失败").slice(0, 240);
}
