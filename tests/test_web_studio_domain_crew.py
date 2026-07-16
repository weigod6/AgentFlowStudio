from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.studio_domain_crew_browser_qa import prepare_provider_free_domain_crew_qa


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


def test_domain_crew_runtime_client_matches_authenticated_project_routes() -> None:
    source = (STUDIO / "src" / "runtime-client.js").read_text(encoding="utf-8")
    expected = (
        "getDomainCrew()",
        "createDomainCrew(payload)",
        "createDomainCrewTask(payload)",
        "claimDomainCrewTask(taskId, payload)",
        "sendDomainCrewMessage(payload)",
        "createDomainCrewHandoff(payload)",
        "decideDomainCrewHandoff(handoffId, payload)",
        "createDomainCrewConflict(payload)",
        "arbitrateDomainCrewConflict(conflictId, payload)",
        "reconfirmDomainCrewPropagation(affectedRefId, payload)",
        "`/projects/${encoded}/domain-crew`",
        "`/projects/${encoded}/domain-crew/tasks/${encodeURIComponent(taskId)}/claim`",
        "`/projects/${encoded}/domain-crew/handoffs/${encodeURIComponent(handoffId)}/decisions`",
        "`/projects/${encoded}/domain-crew/conflicts/${encodeURIComponent(conflictId)}/arbitrations`",
        "`/projects/${encoded}/domain-crew/propagation-reconfirmations/${encodeURIComponent(affectedRefId)}/actions`",
    )
    for marker in expected:
        assert marker in source
    assert "Authorization" in source
    assert "projectId" in source


def test_domain_crew_controller_uses_exact_latest_version_and_reloads_on_success_or_conflict() -> None:
    payload = _node_json(
        r'''
import { createDomainCrewController } from "./apps/studio/src/domain-crew-controller.js";

let crew = { project_id: "project-a", state_version: 7, agents: [], tasks: [], propagation_reconfirmations: [] };
const writes = [];
let runtime = {
  projectId: "project-a",
  reads: 0,
  async getDomainCrew() { this.reads += 1; return { crew: structuredClone(crew) }; },
  async createDomainCrewTask(request) {
    writes.push(structuredClone(request));
    crew = { ...crew, state_version: 8, tasks: [{ task_id: request.task_id }] };
    return { crew: { ...crew, state_version: 999 } };
  },
  async sendDomainCrewMessage(request) {
    writes.push(structuredClone(request));
    crew = { ...crew, state_version: 9 };
    const error = new Error("domain crew state version changed");
    error.status = 409;
    throw error;
  },
};
const controller = createDomainCrewController({ getRuntime: () => runtime });
controller.setContext({ runtime, userId: "user-a" });
await controller.load();
await controller.createTask({ task_id: "task-001", expected_state_version: 1000 });
const afterSuccess = structuredClone(controller.snapshot());
try {
  await controller.sendMessage({ message_id: "message-001", expected_state_version: 1000 });
} catch {}
const afterConflict = structuredClone(controller.snapshot());
const readsAfterConflict = runtime.reads;
controller.setContext({ runtime, userId: "user-b" });
const afterAuthSwitch = structuredClone(controller.snapshot());
runtime = { projectId: "project-b", async getDomainCrew() { return { crew: { project_id: "project-b", state_version: 1 } }; } };
controller.setContext({ runtime, userId: "user-b" });
const afterProjectSwitch = structuredClone(controller.snapshot());
process.stdout.write(JSON.stringify({
  writes,
  afterSuccessVersion: afterSuccess.crew.state_version,
  afterSuccessAuthority: afterSuccess.authorityValid,
  afterConflictVersion: afterConflict.crew.state_version,
  afterConflictAuthority: afterConflict.authorityValid,
  afterConflictError: afterConflict.error,
  readsAfterConflict,
  authReset: afterAuthSwitch.crew === null && afterAuthSwitch.userId === "user-b",
  projectReset: afterProjectSwitch.crew === null && afterProjectSwitch.projectId === "project-b",
}));
'''
    )

    assert payload["writes"] == [
        {"task_id": "task-001", "expected_state_version": 7},
        {"message_id": "message-001", "expected_state_version": 8},
    ]
    assert payload["afterSuccessVersion"] == 8
    assert payload["afterSuccessAuthority"] is True
    assert payload["afterConflictVersion"] == 9
    assert payload["afterConflictAuthority"] is True
    assert "state version changed" in payload["afterConflictError"]
    assert payload["readsAfterConflict"] == 3
    assert payload["authReset"] is True
    assert payload["projectReset"] is True


def test_domain_crew_controller_invalidates_stale_authority_when_mandatory_reload_fails() -> None:
    payload = _node_json(
        r'''
import { createDomainCrewController } from "./apps/studio/src/domain-crew-controller.js";

function unavailable(message) {
  const error = new Error(message);
  error.status = 503;
  return error;
}

let successCrew = { project_id: "success-project", state_version: 7, agents: [], tasks: [] };
let successMode = "ready";
const successWrites = [];
const successRuntime = {
  projectId: "success-project",
  async getDomainCrew() {
    if (successMode === "reload-503") throw unavailable("authoritative success reload unavailable");
    return { crew: structuredClone(successCrew) };
  },
  async createDomainCrewTask(request) {
    successWrites.push(structuredClone(request));
    successCrew = { ...successCrew, state_version: 8, tasks: [{ task_id: request.task_id }] };
    successMode = "reload-503";
    return { crew: structuredClone(successCrew) };
  },
  async claimDomainCrewTask(taskId, request) {
    successWrites.push({ task_id: taskId, ...structuredClone(request) });
    successCrew = { ...successCrew, state_version: 9 };
    return { crew: structuredClone(successCrew) };
  },
};
const successController = createDomainCrewController({ getRuntime: () => successRuntime });
successController.setContext({ runtime: successRuntime, userId: "owner" });
await successController.load();
let successThrown = "";
try { await successController.createTask({ task_id: "task-001" }); } catch (error) { successThrown = error.message; }
const afterSuccessReload503 = structuredClone(successController.snapshot());
let blockedAfterSuccess = "";
try { await successController.claimTask("task-001", "agent-001"); } catch (error) { blockedAfterSuccess = error.message; }
const successWritesWhileInvalid = structuredClone(successWrites);
successMode = "ready";
await successController.load({ allowMissing: false });
const recoveredAfterSuccess = structuredClone(successController.snapshot());

let conflictCrew = { project_id: "conflict-project", state_version: 10, agents: [], tasks: [] };
let conflictMode = "ready";
let conflictMutationCalls = 0;
const conflictRuntime = {
  projectId: "conflict-project",
  async getDomainCrew() {
    if (conflictMode === "reload-503") throw unavailable("authoritative conflict reload unavailable");
    return { crew: structuredClone(conflictCrew) };
  },
  async sendDomainCrewMessage() {
    conflictMutationCalls += 1;
    conflictCrew = { ...conflictCrew, state_version: 11 };
    conflictMode = "reload-503";
    const error = new Error("original state conflict");
    error.status = 409;
    throw error;
  },
};
const conflictController = createDomainCrewController({ getRuntime: () => conflictRuntime });
conflictController.setContext({ runtime: conflictRuntime, userId: "owner" });
await conflictController.load();
let conflictThrown = "";
try { await conflictController.sendMessage({ message_id: "message-001" }); } catch (error) { conflictThrown = error.message; }
const afterConflictReload503 = structuredClone(conflictController.snapshot());
let blockedAfterConflict = "";
try { await conflictController.sendMessage({ message_id: "message-002" }); } catch (error) { blockedAfterConflict = error.message; }
const callsWhileInvalid = conflictMutationCalls;
conflictMode = "ready";
await conflictController.load({ allowMissing: false });
const recoveredAfterConflict = structuredClone(conflictController.snapshot());

process.stdout.write(JSON.stringify({
  successThrown,
  afterSuccessReload503,
  blockedAfterSuccess,
  successWritesWhileInvalid,
  recoveredAfterSuccess,
  conflictThrown,
  afterConflictReload503,
  blockedAfterConflict,
  callsWhileInvalid,
  recoveredAfterConflict,
}));
'''
    )

    success_failed = payload["afterSuccessReload503"]
    assert "success reload unavailable" in payload["successThrown"]
    assert success_failed["authorityValid"] is False
    assert success_failed["busyAction"] == ""
    assert "success reload unavailable" in success_failed["error"]
    assert success_failed["crew"]["state_version"] == 7
    assert "reload required before mutation" in payload["blockedAfterSuccess"]
    assert payload["successWritesWhileInvalid"] == [{"task_id": "task-001", "expected_state_version": 7}]
    assert payload["recoveredAfterSuccess"]["authorityValid"] is True
    assert payload["recoveredAfterSuccess"]["crew"]["state_version"] == 8

    conflict_failed = payload["afterConflictReload503"]
    assert "conflict reload unavailable" in payload["conflictThrown"]
    assert conflict_failed["authorityValid"] is False
    assert conflict_failed["busyAction"] == ""
    assert "conflict reload unavailable" in conflict_failed["error"]
    assert "original state conflict" not in conflict_failed["error"]
    assert conflict_failed["crew"]["state_version"] == 10
    assert "reload required before mutation" in payload["blockedAfterConflict"]
    assert payload["callsWhileInvalid"] == 1
    assert payload["recoveredAfterConflict"]["authorityValid"] is True
    assert payload["recoveredAfterConflict"]["crew"]["state_version"] == 11


def test_domain_crew_panel_disables_mutations_until_authority_is_restored() -> None:
    payload = _node_json(
        r'''
import { domainCrewMutationsDisabled } from "./apps/studio/src/panels/domain-crew-panel.js";
process.stdout.write(JSON.stringify({
  invalid: domainCrewMutationsDisabled({ authorityValid: false, status: "error", busyAction: "" }),
  loading: domainCrewMutationsDisabled({ authorityValid: false, status: "loading", busyAction: "" }),
  busy: domainCrewMutationsDisabled({ authorityValid: true, status: "ready", busyAction: "create_task" }),
  restored: domainCrewMutationsDisabled({ authorityValid: true, status: "ready", busyAction: "" }),
}));
'''
    )

    assert payload == {"invalid": True, "loading": True, "busy": True, "restored": False}


def test_domain_crew_surface_preserves_api_authority_and_mobile_accessibility_contract() -> None:
    main = (STUDIO / "src" / "main.js").read_text(encoding="utf-8")
    drawer = (STUDIO / "src" / "panels" / "drawer.js").read_text(encoding="utf-8")
    panel = (STUDIO / "src" / "panels" / "domain-crew-panel.js").read_text(encoding="utf-8")
    controller = (STUDIO / "src" / "domain-crew-controller.js").read_text(encoding="utf-8")
    styles = (STUDIO / "styles" / "domain-crew.css").read_text(encoding="utf-8")
    index = (STUDIO / "index.html").read_text(encoding="utf-8")

    assert "afs:studio-open-domain-crew" in drawer
    assert "openDomainCrewPanel(domainCrewController)" in main
    assert "syncDomainCrewContext()" in main
    assert "projectController.authUser?.user_id" in main
    assert "domainCrewController.setContext" in main
    assert "domain_crew_ledger_pass" in panel
    assert "不证明节点已由真实智能体运行时自主推进" in panel
    assert "propagation_reconfirmations" in panel
    assert "propagation_basis" in panel
    assert "affected_work_refs" in panel
    assert "画布连线不会在此生成受影响工作" in panel
    assert "edges" not in controller
    assert "state.edges" not in panel
    assert "expected_state_version: expectedStateVersion" in controller
    assert 'Number(error?.status) === 409' in controller
    assert "await load({ allowMissing: false })" in controller
    assert "authorityValid" in controller
    assert "reload required before mutation" in controller
    assert "domainCrewMutationsDisabled(state)" in panel
    assert panel.count("domainCrewMutationsDisabled(state)") >= 5
    assert "所有写操作已锁定" in panel
    assert 'ariaLabel: "制作团队控制台"' in panel
    assert 'initialFocus: ".domain-crew-close"' in panel
    assert "overflow-y: auto" in styles
    assert "overscroll-behavior: contain" in styles
    assert "@media (max-width: 520px)" in styles
    assert "width: 100vw" in styles
    assert "height: 100dvh" in styles
    assert '<link rel="stylesheet" href="./styles/domain-crew.css" />' in index


def test_provider_free_domain_crew_browser_fixture_is_authenticated_and_replayable(tmp_path) -> None:
    result = prepare_provider_free_domain_crew_qa(tmp_path)

    assert result["project_id"] == "studio-domain-crew-qa"
    assert result["agent_count"] == 9
    assert result["task_count"] == 3
    assert result["provider_calls_started"] is False
    assert result["evidence_boundary"] == "domain_crew_ledger_pass"
    assert "does not prove agent-controlled execution" in result["non_claim"]
    preflight = result["browser_preflight"]
    assert preflight["ready"] is True
    assert preflight["desktop_viewport"] == {"width": 1440, "height": 960}
    assert preflight["mobile_viewport"] == {"width": 390, "height": 844}
    refs = json.loads(preflight["affected_work_refs_json"])
    assert [item["downstream_task_id"] for item in refs] == ["task-storyboard", "task-art"]
    assert preflight["expected_pending_task_ids"] == ["task-storyboard", "task-art"]
    assert any("reload" in step for step in preflight["flow"])
