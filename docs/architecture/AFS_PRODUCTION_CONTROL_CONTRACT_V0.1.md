# AFS Production Control Contract v0.1

Status: frozen contract and deterministic harness evidence.

This contract is an additive control domain in front of the frozen Episode
Production Fact Contract v0.1.1. It does not extend or replace the existing
episode aggregate, continuity, selection, review, or delivery facts. It does
not implement a scheduler, database, provider adapter, HTTP route, Studio UI,
deployment, or production runtime.

Executable contract:
`agentflow_studio/production_control/contract.py`

Deterministic in-memory/file-safe harness:
`agentflow_studio/production_control/harness.py`

## Scope and authority

Every control object and exact reference is bound to `org_id + project_id`.
Every command also carries an exact actor identity and authority reference.
Stable object identity is separate from immutable revision identity. Display
copy, frontend state, localStorage keys, provider job IDs, and actor aliases are
not domain identity.

The contract owns:

- Mission, MissionRevision, and ReferenceConstraint;
- ProductionPlan, PlanRevision, PlanTask, and PlanApprovalDecision;
- AgentAssignment, ProductionRun, and RunAttempt;
- BudgetEnvelope, CostEstimate, CostEntry, and ProviderGateDecision;
- Blocker, HumanDecisionRequest, and HumanDecision;
- ArtifactCandidateRegistration, ArtifactWriteback,
  SelectiveRevisionRequest, and ImpactAssessment.

The frozen episode contract continues to own Project, Episode, Scene, Shot,
ContinuityState, AssetCandidate, SelectedVersion, ReviewDecision,
AgentProposal, and DeliveryVersion.

## Command envelope

Every command uses one `CommandEnvelope` containing:

```text
command_id
command_type
org_id + project_id
actor_id + actor_type + authority_ref
expected_version
idempotency_key
correlation_id
causation_id
capability
exact_object_refs
budget_authorization (explicit value or null)
provider_authorization (explicit value or null)
payload + canonical payload_digest
```

The gateway contract is fail closed:

- same idempotency key and same canonical command intent returns the original
  receipt, event IDs, result refs, and accepted version without appending;
- same key with a different canonical intent is an idempotency conflict;
- receipt lookup occurs before CAS so a successful stale replay is safe;
- a new command with a stale `expected_version` is rejected;
- foreign org, project, complete ActorIdentity/authority, capability, nested exact ref, or unresolved
  causation is rejected;
- new writes against known locked, retired, cancelled, or terminal control
  objects are rejected, while exact replay of a prior success remains valid;
- failed authorization, validation, state, CAS, or atomic-commit checks create
  no success receipt, event, outbox record, projection change, or version bump.

`plan.approve` additionally requires exact budget authorization. Provider gate
evaluation requires an explicit provider authorization object even when the
answer is closed.

## Plan approval atomicity

One approved PlanRevision contains an estimated range, currency/unit,
assumption, maximum budget, and at least one exact CostEstimate. The plan
contains at least three unique, bounded task specifications.

`plan.approve` is one command batch:

```text
PlanApproved
TaskQueued (1)
TaskQueued (2)
TaskQueued (3..n)
one receipt
one matching outbox record per event
```

The decision's exact task refs must equal the task batch, and every task's
stable ID, boundary, capability, and dependency graph must equal its immutable
PlanRevision specification. Two tasks, duplicate stable IDs, foreign refs,
budget mismatch, stale CAS, or any staged failure
rejects the entire batch. No projection may expose an approved plan without
all approved tasks.

## Run and control state

ProductionRun is a cross-step production control object. It is not
`AssetCandidate.JobState`, which remains a single provider/queue job state in
the frozen episode contract.

Execution state:

```text
queued | running | waiting-human | retrying | blocked | completed | cancelled
```

Control state:

```text
active | pause-requested | paused | resume-requested | cancel-requested
```

The axes are validated independently and as a cross-product. Pause is a
control transition and does not create a Blocker. A retry creates a new,
contiguous RunAttempt with the exact prior attempt and does not itself create a
CostEntry or ArtifactWriteback. Completed and cancelled execution is terminal.
Cancel preserves all attempts, costs, artifacts, receipts, and provenance.

`waiting-human` requires one exact HumanDecisionRequest with at least two
options, exact impact refs, and a timezone-qualified deadline. Resuming
requires an exact HumanDecision selecting an offered option and acknowledging
impact. `blocked` requires an owner and exact clearance evidence; returning to
queued or running requires the exact evidence declared by the active Blocker,
with control-domain evidence resolving in the ledger.

## Budget and provider gate

BudgetEnvelope freezes the pre-approval estimated range, unit, assumption, and
maximum budget. Cost records remain separate as `estimated`, `committed`, or
`actual`. Stable cost identity and charge fingerprint prevent a retry or a new
command key from recording the same charge twice.

ProviderGateDecision is allowed only when the command carries a matching exact
BudgetAuthorization and all of these are true:

```text
capability authorization
budget admission
privacy policy satisfied
no-training policy satisfied
exact authorization reference
```

The default is closed. The v0.1 harness has no dispatch command or provider
adapter and persists `provider_dispatch_count=0`. A positive gate decision
still cannot dispatch anything in this lane.

## Ledger, projection, and atomic outbox

The project ledger is append-only and ordered exactly as committed. Each event
contains:

```text
schema_version
org_id + project_id
project_sequence
project_version
batch_index + batch_count
event_id
event_type
correlation_id + causation_id
command_receipt_id
previous_event_digest
payload
integrity_digest
```

Event IDs and integrity digests are derived from canonical JSON; there is no
wall-clock, random, or localStorage identity. Project sequences start at one
and are contiguous. Command batches share one project version and receipt.
Projection rebuild consumes ledger order as-is and never sorts, repairs, or
silently deduplicates invalid events.

The file-safe harness stores ledger events, receipts, pending outbox records,
the committed tail seal, projection digest, and provider-dispatch counter in
one canonical commit envelope. A temporary file is flushed and atomically
replaced. Thus a harness commit exposes the old envelope or the complete new
envelope, not separate ledger-only, receipt-only, or outbox-only files.

On load it rejects:

- invalid JSON, unsupported schema, or invalid event payload;
- foreign scope, sequence gaps, reorder, duplicate event ID, or broken hash;
- batch truncation, event deletion, or receipt/event membership mismatch;
- tail count/hash/digest mismatch;
- missing, reordered, duplicate, foreign, identity/type-divergent, or
  payload-divergent outbox records;
- receipt scope, version, semantic/command digest, deterministic receipt ID,
  result-ref, or batch-membership mismatch;
- a projection digest that does not rebuild from zero;
- any non-zero provider dispatch count.

The tail seal detects truncation relative to the committed envelope. It is not
an external cryptographic timestamp, remote anti-rollback anchor, production
database, multi-process transaction manager, or commercial durability claim.
Production storage selection, external anchoring, compaction, and snapshot
policy are deferred to a later Runtime Lane.

## Fact chain

The deterministic contract supports this additive chain:

```text
MissionRecorded
-> PlanProposed / PlanRevised
-> PlanApproved
-> TaskQueued x3+
-> RunStarted / RunProgressed / RunWaitingHuman / RunRetried
   / RunBlocked / RunCompleted / RunCancelled
-> ArtifactCandidateRegistered
-> ArtifactWrittenBack
-> SelectiveRevisionRequested
-> ImpactAssessed
-> existing exact Shot successor when an existing typed operation authorizes it
-> existing Continuity / Review / Delivery facts
```

The contract does not create a second continuity, review, or delivery truth.

## Artifact writeback adapter boundary

Artifact writeback is command/event-only. UI code and external agents may not
write the episode aggregate directly. Every registration and writeback carries
exact PlanTask, ProductionRun, and RunAttempt provenance plus a safe artifact
ID and digest. The Task must own the Run, the Attempt must belong to that Run,
and final writeback provenance must exactly equal candidate-registration
provenance. CostEntry enforces the same Run-to-Attempt ownership.

The adapter interface has two modes:

1. `asset_candidate` is the safe default. It asks a later Runtime adapter to
   invoke the existing candidate `create_version` semantics against one exact
   Shot or ContinuityState target. Provenance remains in the control ledger
   because the frozen AssetCandidate has no PlanTask/Run/Attempt fields.
2. `shot_successor` may only delegate to an already-existing typed operation:
   `shot.reassign_scene` or `continuity.apply_proposal`. It cannot carry a bare
   visual artifact because the frozen Shot schema has no artifact field.

For continuity, the adapter must preserve the frozen complete predicted
`impact_refs`, actual `applied_refs`, and bidirectional exact
`source_proposal_ref`. The resulting Shot successor must retain stable Shot
identity, name its exact parent, and re-enter `candidate + needs_review` under
the existing service rules.

Protected refs are explicit. ImpactAssessment cannot mark a protected ref as
affected and must include every protected ref in its preserved set. In the
conformance scenario, a Shot7 writeback or
typed successor must prove that Shot8's exact ref and history are unchanged.
The adapter may emit the existing exact output ref in its receipt/outbox result
but may not duplicate AgentProposal, ReviewDecision, DeliveryVersion, or
continuity facts.

If a Runtime implementation requires Plan/Run provenance inside frozen episode
fields, a new EntityType or aggregate collection, new JobState values, a Shot
artifact field, altered continuity membership, or parallel Review/Delivery
facts, it must stop with `decision_needed`; this v0.1 contract does not
authorize that schema change.

## Deterministic evidence

Focused tests cover:

- atomic 3+ task approval, injected rollback, replay conflict, and stale CAS;
- cross-scope/actor/ref authorization;
- execution/control transitions, pause versus block, waiting-human, clearance,
  retry, and terminal behavior;
- duplicate CostEntry charge and ArtifactWriteback prevention;
- candidate registration, additive writeback provenance, selective revision,
  ImpactAssessment, and Shot7/Shot8 protection;
- closed ProviderGateDecision and zero dispatch;
- deterministic rebuild, restart replay, ledger tail truncation, corruption,
  atomic outbox membership, and concurrent same-version conflict;
- frozen PR #155 contract blob hashes.

## Frozen compatibility baseline

At Gate commit `44ce5280533ab351f9aac0d3fc7b3ef6320525a0`, PR #155 head
`2249f0bfab171ee6199410a0ca6348f58a0adb31` is an ancestor. The frozen
episode files are unchanged:

```text
runtime_episode_domain_contract.py        407020f1bdd34d8eb8df9de642d88dc9e88a8d9b
runtime_episode_domain_store.py           a861a04b4479c551528a74fcd1dae2dd589b230e
runtime_episode_continuity_service.py     aa6fad1437c246ab581e93e26b7f2c206380e295
runtime_episode_review_delivery_service.py 79c4dd0ae174c102c3ed36a911c0e805ccf76ba6
runtime_episode_command_routes.py         8120d11b7c9431ea1517aecc6eb1721ea6f49b3a
AFS_EPISODE_PRODUCTION_FACT_CONTRACT.md    6496d9fb11cdd7b12d5386a22ff7454a609e1b0d
```

## Runtime Lane inputs

A later Runtime Lane may consume:

- the exact Pydantic command/object/event schemas;
- the reducer, canonical digest, receipt-before-CAS, batch, and tail-seal test
  oracles;
- the one-envelope file harness as deterministic evidence, not as the chosen
  production database;
- the additive episode adapter request and frozen compatibility hashes;
- the mandatory zero-provider default and explicit provider/budget/privacy
  admission fields.

It must separately choose and prove a production ledger/outbox persistence
strategy, scheduler semantics, process recovery, multi-process concurrency,
existing episode-store adapter transaction, and runtime/API authorization. It
must not inherit a provider, deployment, or public-release authorization from
this contract task.

## Non-claims

This artifact supports contract/structure and deterministic runtime-harness
evidence only. It does not prove a real scheduler, production database,
provider dispatch, provider/media QA, HTTP/API integration, Studio UX, server
deployment, human acceptance, business validation, legal readiness,
SaaS/public readiness, production readiness, or durable Company OS promotion.
