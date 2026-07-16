import { createRuntimeClient } from "../src/runtime-client.js";

export class EpisodeWorkspaceRequestError extends Error {
  constructor(kind, message, status = null, errorCode = "") {
    super(message);
    this.name = "EpisodeWorkspaceRequestError";
    this.kind = kind;
    this.status = status;
    this.errorCode = errorCode;
  }
}

function classifyFailure(error) {
  const status = Number(error?.status || 0);
  if (status === 401 || status === 403) {
    return new EpisodeWorkspaceRequestError("auth", "登录状态已失效或无权访问此项目。", status, error?.errorCode);
  }
  if (status === 404) {
    return new EpisodeWorkspaceRequestError("not_found", "找不到这个单集，或你已无权访问。", status, error?.errorCode);
  }
  if (status === 409 || status === 412) {
    return new EpisodeWorkspaceRequestError("stale", "项目事实已更新，已重新读取最新状态。", status, error?.errorCode);
  }
  if (status === 422 || status === 400) {
    return new EpisodeWorkspaceRequestError("invalid", "这项操作与当前精确版本不一致。", status, error?.errorCode);
  }
  return new EpisodeWorkspaceRequestError("server", "工作区暂时无法完成请求，请稍后重试。", status, error?.errorCode);
}

function requireIdentity(value, label) {
  if (!value || typeof value !== "string") {
    throw new EpisodeWorkspaceRequestError("missing_identity", `未指定${label}。`);
  }
  return value;
}

export function createEpisodeWorkspaceClient(projectId, episodeId, episodeVersionId) {
  const runtime = createRuntimeClient(requireIdentity(projectId, "项目"));
  const episode = requireIdentity(episodeId, "单集");
  const version = requireIdentity(episodeVersionId, "单集版本");
  const safe = async (request) => {
    try {
      return await request();
    } catch (error) {
      if (error instanceof EpisodeWorkspaceRequestError) throw error;
      throw classifyFailure(error);
    }
  };
  return Object.freeze({
    loadWorkspace() {
      return safe(() => runtime.loadEpisodeWorkspace(episode, version));
    },
    loadStudioState() {
      return safe(() => runtime.loadStudioState());
    },
    saveStudioState(state, expectedVersion = "") {
      return safe(() => runtime.saveStudioState(state, expectedVersion));
    },
    executeCommand(command, idempotencyKey) {
      requireIdentity(idempotencyKey, "命令标识");
      return safe(() => runtime.executeEpisodeCommand(command, idempotencyKey));
    },
  });
}
