# AFS Episode Loop Phase 2 Evaluation Protocol

Status: frozen same-task evidence-refresh protocol. It evaluates the exact
isolated prototype revisions recorded below; it does not authorize production
frontend integration or claim human acceptance.

The formal frontend direction is already `frontend_direction_decided`:
**Project/Episode shell + Storyboard-centered production workspace + contextual
decision inspector**. This comparison repairs provenance, validates each
prototype's task semantics, and records structure risks. It must not select a
winner, reopen the architecture vote, or combine the prototypes into a new
unvalidated structure.

## Required inputs

The evaluator must read these before scoring:

1. [Phase 1 evidence and AFS gap matrix](research/AFS_PHASE1_EVIDENCE_MATRIX.md)
2. [Episode production fact contract v0.1.1](architecture/AFS_EPISODE_PRODUCTION_FACT_CONTRACT.md)
   - exact contract commit `bf44e54edaf53a32917522ae7cfbf43563277ded`
   - contract revision `v0.1.1`
   - wire schema literal `afs_episode_production_aggregate.v0.1`
3. `examples/representative_episode/episode_package.json`
   - SHA256 `0AF7D008C39074765E60057F5D80F92F59CE45999EF3E216FD8562A963B9A2A2`
4. `examples/representative_episode/episode_revision_v2.json`
   - SHA256 `0796D47C465C0467201D41863CE5ABF9A01703E9B7DDB22DE0F47BE350AEA45B`

If a fixture, common harness, scenario overlay, protocol, or prototype changes
after an evaluation, only the evidence that consumed the changed artifact may
remain valid. The evaluator must publish the invalidation map before reusing any
result. The three variants must be presented anonymously as A, B, and C during
task execution; the final provenance record may map those labels back to the
exact revisions below.

## Frozen prototype provenance

All prototype revisions descend from common harness commit
`9bda0a284bdee18866cf4b3c99764065af10fa61`:

| Variant | Exact revision | Evaluation status before refresh |
|---|---|---|
| Guided flow | `a704a1cc1ba69e745d185ea3098209eaa419bee5` | semantic repair; fresh browser comparison required |
| Storyboard/canvas-first | `9edd03f68e9e5c77819985e2ffc4605fc75d4332` | frozen; re-evaluate against the refreshed hard gates |
| Project shell with an internal professional creation space | `3346f5d4813d2e87a0abf113e73f9757a9eb5531` | frozen; re-evaluate against the refreshed hard gates |

The old Guided head `72c1431d0b02d102457fc7a152d0eb305f568291`
displayed incorrect creator-visible continuity facts. It and the old anonymous
no-winner comparison remain audit evidence, but they are invalid for equal-task
semantic claims. Prototype code stays in isolated non-production experiment
directories.

Contract v0.1.1 adds explicit proposal application provenance while preserving
the aggregate wire literal. It invalidates stale contract-provenance claims but
does not change the frozen representative fixture bytes or the five task goals.
The Guided semantic repair is a separate evidence change and invalidates Guided
browser/task evidence plus any comparison that assumed all three variants had
equal creator-visible semantics.

## Common scenario overlay

Do not edit the representative fixture. The harness injects the same defects:

- Shot 6 is temporarily assigned to the wrong scene and must be restored to the
  old archive tower.
- Shot 7 has the lamp buckle on the wrong shoulder and a mirrored scar.
- Shot 8 is a decoy and must not be flagged as the same contradiction.
- Shot 11 has v1 and v2 text/storyboard alternatives. Only shot 11 may change;
  the other 14 shots must retain their facts.
- Delivery remains blocked by 25 missing assets. No prototype may fabricate a
  playable result or imply provider generation occurred.

Creator-visible continuity is a hard fixture projection, not prototype copy:

- the continuity subject is **林遥**, not 小祈;
- 林遥's authoritative **铜制提灯扣位于右肩**;
- 林遥's **左眉疤不可镜像**;
- the injected Shot 7 conflict therefore puts the buckle on the left shoulder
  and mirrors the scar to the right brow;
- Shot 8 remains unchanged.

## Same-task script

1. Start from the truthful project state with active Shot 6 matching the
   suggested next action. Inspect another shot and verify that the UI clearly
   distinguishes "currently viewing" from "suggested next".
2. Inspect the imported script breakdown and repair Shot 6's scene assignment.
3. Find the real Shot 7 continuity conflict, inspect affected work, and leave
   the Shot 8 decoy unchanged.
4. Before tasks 2 and 3 are complete, freely inspect Shot 11 and compare v1/v2,
   but verify that selecting v2 and reconfirming are disabled and that a direct
   handler call fails closed without mutation. Then complete tasks 2 and 3,
   choose v2 for eight affected downstream items, stop after three
   reconfirmations, reload, recover exactly, and finish all eight.
5. Inspect delivery, explain why it is blocked, and identify what remains
   without exposing raw IDs, provider/runtime vocabulary, or fake progress.
6. Request reset, cancel it, and prove the complete task/checkpoint state is
   unchanged. Request reset again and confirm explicitly before any state is
   cleared.

## Research-derived evaluation criteria

| Criterion | Evidence reason | Observable test |
|---|---|---|
| New-user orientation and expert direct access | LibTV/WorkRally/LTX show dense professional spaces; density alone does not establish a usable first path | Can a new user name the next action, while an expert reaches a known shot without replaying onboarding? |
| One object across contexts | WorkRally, Celtx, LTX, and current AFS gaps show the cost of split project/scene/shot/reference stores | Does the same Shot 11 retain one semantic identity in breakdown, storyboard, review, and delivery? |
| Change impact and creator scope | No reviewed product publicly proves the full change-to-impact-to-rollback chain | Does a continuity change show affected shots, what changes, what remains, and let the creator select scope before execution? |
| Exact-version review and rollback | Frame.io demonstrates mature comparison; AFS needs object-aware traceability beyond files | Can the creator compare v1/v2, understand the selected version, reload mid-task, and restore without drift? |
| Local versus global action clarity | Runway workflows expose local runs and locks; current AFS actions are fragmented | Is every action's scope visible before commit, and can a local refusal avoid unrelated changes? |
| Truthful blocked delivery | 万镜 job recovery and current AFS fixed delivery show why technical completion and creator completion differ | Does the prototype state 25 missing assets and next actions without fake media, fake percentage, or provider jargon? |
| Data-use clarity | Official policies vary by plan/surface and often remain incomplete | Does default project language remain private/no-training and avoid implying feedback becomes training or memory? |

## Metrics

Record per task and per participant:

- final task state;
- active completion time;
- meaningful activations, excluding duplicate click events caused by keyboard;
- explicit screen/context transitions;
- critical decision errors;
- recoverable decision errors;
- lostness or inability to name the next action;
- reload recovery fidelity;
- keyboard/focus failures and page overflow at desktop, `390x844`, and `360x800`.

The common event log must record semantic task, action, object, previous/current
view, input method, monotonic duration, and a safe state summary. Raw IDs may be
used in internal `data-testid` values but not displayed in the default UI.

## Hard gates and evidence rule

All variants must:

- complete at least 90% of scripted tasks;
- have no critical errors in tasks 2-5;
- project the authoritative creator-visible facts for 林遥, the copper lamp
  buckle on the right shoulder, and the non-mirrored left-brow scar;
- start with the active shot equal to the suggested next shot; after free
  navigation, expose separate current-view and suggested-next meanings;
- allow Shot 11 inspection before Shot 6 and Shot 7 are complete, while both
  the visible controls and action handler fail closed for v2 selection and
  reconfirmation until those prerequisites are satisfied;
- recover the exact 3/8 reconfirmation checkpoint after reload;
- require explicit reset confirmation and prove cancel leaves state unchanged;
- avoid fabricated media/progress and engineering-language leaks;
- support keyboard operation, visible focus, live error/recovery feedback, and
  no page-level horizontal overflow at both mobile sizes.

Active time, meaningful activations, and context transitions remain comparative
risk signals, not a winner formula. Machine-only and simulated-role evaluation
may establish task semantics and structure risks, but cannot claim real creator
acceptance. The selected production direction remains subject to fresh browser,
authenticated E2E, independent evaluator, and later real-creator evidence.

## Evaluator return

Return per variant:

- exact revision and anonymous test label;
- `evidence_valid` or `evidence_invalid` with the exact failed hard gate;
- task outcomes and measurements;
- critical/recoverable errors and creator-visible fact findings;
- recovery, focus, state-gate, reset, accessibility, and mobile results;
- evidence level and residual risks.

Then return one comparative risk record covering orientation, narrative
context, supervision, mobile companion use, recovery, and state-gate clarity.
The record may recommend productization safeguards for the already-decided
Hybrid shell and Storyboard-centered workspace, but must not output a winner,
reopen architecture selection, or claim human acceptance.
