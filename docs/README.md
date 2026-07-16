# AgentFlow Studio Docs

This directory is the current documentation entrypoint. Start new work from
this file, `docs/AOS_CURRENT_STATE.md`, `docs/company_operating_model.md`,
`docs/GFR_EXECUTION_PROJECTION.md`, and the current architecture docs.

Historical trackers, devlogs, and handoff archives are not active startup
context. Use Git history or a targeted restored reference only when a task needs
specific historical evidence. Do not make old loop records the default read
scope.

Do not resume from retired Web/Workbench handoffs or old smoke logs. Provider,
model, media, runtime, human-acceptance, business, legal, or public-readiness
claims need explicit gates and matching evidence.

## Current Required Reading

- [Contributor onboarding](CONTRIBUTOR_ONBOARDING.md)
- [AOS current state](AOS_CURRENT_STATE.md)
- [Single-episode loop Program](AFS_EPISODE_LOOP_PROGRAM.md)
- [Phase 1 evidence matrix](research/AFS_PHASE1_EVIDENCE_MATRIX.md)
- [Phase 2 same-task evaluation protocol](AFS_EPISODE_LOOP_PHASE2_EVALUATION_PROTOCOL.md)
- [Company operating projection](company_operating_model.md)
- [GFR execution projection](GFR_EXECUTION_PROJECTION.md)
- [Current architecture](current_architecture.md)
- [Project Manifest contract](project_manifest_contract.md)

## Current Product Surface

- [AFS Studio frontend architecture](architecture/AFS_STUDIO_FRONTEND_ARCHITECTURE_V1.zh-CN.md)
- Current Web entry: `http://127.0.0.1:8790/studio/`
- Current frontend source: `apps/studio/`

AFS Studio is the only current user-facing frontend. Retired Workbench and
static memory-workbench paths are not task entrypoints.

## Architecture And Contracts

- [Current architecture](current_architecture.md)
- [Episode production fact contract](architecture/AFS_EPISODE_PRODUCTION_FACT_CONTRACT.md)
- [Production Control Contract v0.1](architecture/AFS_PRODUCTION_CONTROL_CONTRACT_V0.1.md)
- [Studio entity/status vocabulary contract](architecture/AFS_STUDIO_ENTITY_STATUS_VOCABULARY_CONTRACT.md)
- [Node prompt optimizer contract](architecture/AFS_NODE_PROMPT_OPTIMIZER_CONTRACT.zh-CN.md)
- [Creative intent control agent engineering summary](architecture/AFS_CREATIVE_INTENT_CONTROL_AGENT_ENGINEERING_SUMMARY.zh-CN.md)
- [Provider adapter contract](provider_adapter_contract.md)
- [Skill contract](agentflow_skill_contract.md)
- [Router contract](agentflow_router_contract.md)

## Useful Commands

```powershell
.\.venv\Scripts\python.exe -m apps.cli.main --help
.\.venv\Scripts\python.exe -m apps.cli.main version
.\.venv\Scripts\python.exe -m pytest
git diff --check
```

Maintenance cleanup:

```powershell
.\.venv\Scripts\python.exe tools\maintenance_audit.py
```

Runtime Service:

```powershell
.\.venv\Scripts\python.exe -m apps.cli.main runtime-service --host 127.0.0.1 --port 8790
```

OpenAPI:

```text
http://127.0.0.1:8790/docs
http://127.0.0.1:8790/openapi.json
docs/openapi/afs-runtime-service.openapi.json
```

## Cleanup Policy

Old, unused, or misleading docs should be deleted once replacement paths and
tests are clear. Keep only current architecture, contracts, verification routes,
and concise current state that helps land the MVP.
