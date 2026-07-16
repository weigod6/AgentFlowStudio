import { authToken, createRuntimeClient, runtimeBaseUrl } from "../src/runtime-client.js";

export class CreatorAuthoringRequestError extends Error {
  constructor(kind, message, status = null) {
    super(message);
    this.name = "CreatorAuthoringRequestError";
    this.kind = kind;
    this.status = status;
  }
}

function classify(error) {
  const status = Number(error?.status || 0);
  if (status === 401 || status === 403) {
    return new CreatorAuthoringRequestError("auth", "登录状态已失效，或你无权访问这个项目。", status);
  }
  if (status === 404) {
    return new CreatorAuthoringRequestError("not_found", "找不到这个创作项目。", status);
  }
  if (status === 409 || status === 412) {
    return new CreatorAuthoringRequestError("stale", "项目内容已更新，请读取最新版本后继续。", status);
  }
  if (status === 400 || status === 422) {
    return new CreatorAuthoringRequestError("invalid", "这次更改与当前内容不一致，请检查后重试。", status);
  }
  return new CreatorAuthoringRequestError("server", "创作工作台暂时无法完成请求，请稍后重试。", status);
}

async function executeCreatorCommand(projectId, command, idempotencyKey) {
  const headers = { "Content-Type": "application/json", Accept: "application/json", "Idempotency-Key": idempotencyKey };
  const token = authToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${runtimeBaseUrl()}/projects/${encodeURIComponent(projectId)}/episode-production-aggregate/commands`, {
    method: "POST",
    headers,
    body: JSON.stringify(command),
  });
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = null; }
  if (!response.ok) {
    const error = new Error(payload?.detail?.message || payload?.detail || "Creator command failed");
    error.status = response.status;
    throw error;
  }
  return payload;
}

export function createCreatorAuthoringClient(projectId) {
  if (!projectId) throw new CreatorAuthoringRequestError("missing_identity", "缺少项目身份。");
  const runtime = createRuntimeClient(projectId);
  const safe = async (request) => {
    try {
      return await request();
    } catch (error) {
      if (error instanceof CreatorAuthoringRequestError) throw error;
      throw classify(error);
    }
  };
  return Object.freeze({
    loadWorkspace: () => safe(() => runtime.loadCreatorWorkspace()),
    loadStudioState: () => safe(() => runtime.loadStudioState()),
    saveStudioState: (state, expectedVersion = "") => safe(() => runtime.saveStudioState(state, expectedVersion)),
    executeCommand: (command, idempotencyKey) => safe(() => executeCreatorCommand(projectId, command, idempotencyKey)),
    previewShotImpact: (payload) => safe(() => runtime.previewShotImpact(payload)),
    previewShotRestore: (payload) => safe(() => runtime.previewShotRestore(payload)),
    diffShotVersions: (payload) => safe(() => runtime.diffShotVersions(payload)),
  });
}
