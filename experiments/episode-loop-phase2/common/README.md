# Phase 2 Common Prototype Harness

This directory is neutral shared infrastructure for three isolated prototypes.
It is not mounted by Runtime and is not production Studio code.

Lane write boundaries:

- main owner only: `common/`, the evaluation protocol, and shared tests;
- guided lane only: `prototypes/guided/`;
- storyboard lane only: `prototypes/storyboard/`;
- hybrid lane only: `prototypes/hybrid/`.

Prototype lanes must not modify the common harness, representative fixtures,
`apps/`, `package.json`, `tools/`, or shared tests. They must not import the old
product shell, Studio persistence, review-delivery state, production delivery
controller, entity-status vocabulary, or layout CSS. Those surfaces encode
legacy structure and would bias the comparison.

Verification:

```powershell
node tools/check-phase2-prototype-js.mjs
D:\Projects\AgentFlowStudio\.venv\Scripts\python.exe -m pytest tests\test_episode_loop_phase2_common_harness.py -q
```
