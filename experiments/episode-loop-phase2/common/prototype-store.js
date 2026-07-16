import { HARNESS_SCHEMA_VERSION, assertTruthfulState, assertVariant } from "./prototype-contract.js";

export function storageKey(variant) {
  return `afs:episode-loop:phase2:${assertVariant(variant)}:v1`;
}

export function createEnvelope({ variant, fixtureSha256, sessionId, state, eventLog }) {
  if (fixtureSha256 !== state?.fixture_sha256) throw new Error("状态与信封素材版本不一致。");
  if (state?.variant !== variant) throw new Error("状态与信封原型类型不一致。");
  if (typeof sessionId !== "string" || !sessionId.trim()) throw new Error("恢复会话标识不能为空。");
  if (!isValidEventLog(eventLog, variant, sessionId)) {
    throw new Error("事件日志不连续或不属于当前原型会话。");
  }
  return {
    schema_version: HARNESS_SCHEMA_VERSION,
    variant: assertVariant(variant),
    fixture_sha256: fixtureSha256,
    session_id: sessionId,
    state: assertTruthfulState(state),
    event_log: eventLog.map((event) => ({ ...event })),
  };
}

export function saveEnvelope(storage, envelope) {
  storage.setItem(storageKey(envelope.variant), JSON.stringify(envelope));
  return envelope;
}

export function loadEnvelope(storage, { variant, fixtureSha256 }) {
  const raw = storage.getItem(storageKey(variant));
  if (!raw) return null;
  try {
    const envelope = JSON.parse(raw);
    if (envelope.schema_version !== HARNESS_SCHEMA_VERSION) return null;
    if (Object.hasOwn(envelope, "checkpoint")) return null;
    if (envelope.variant !== variant || envelope.fixture_sha256 !== fixtureSha256) return null;
    if (typeof envelope.session_id !== "string" || !envelope.session_id.trim()) return null;
    if (!envelope.state || envelope.state.variant !== variant) return null;
    if (envelope.state.fixture_sha256 !== fixtureSha256) return null;
    if (!isValidEventLog(envelope.event_log, variant, envelope.session_id)) return null;
    assertTruthfulState(envelope.state);
    return envelope;
  } catch {
    return null;
  }
}

export function clearEnvelope(storage, variant) {
  storage.removeItem(storageKey(variant));
}

function isValidEventLog(events, variant, sessionId) {
  if (!Array.isArray(events)) return false;
  let previousElapsed = 0;
  return events.every((event, index) => {
    const elapsed = Number(event?.elapsed_ms);
    const valid = event?.sequence === index + 1
      && event?.variant === variant
      && event?.session_id === sessionId
      && Number.isFinite(elapsed)
      && elapsed >= previousElapsed;
    previousElapsed = elapsed;
    return valid;
  });
}
