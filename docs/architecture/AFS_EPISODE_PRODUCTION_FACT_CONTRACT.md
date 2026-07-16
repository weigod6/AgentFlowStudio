# AFS Episode Production Fact Contract v0.1.1

Status: frozen v0.1 contract plus the additive v0.1.1 continuity-operation
evidence repair and Wave 1 production-control provenance fields. The wire
aggregate schema literal remains
`afs_episode_production_aggregate.v0.1`; old payloads omit the additive fields
and receive empty or null defaults. This contract is schema-first and is not
yet a claim that the Runtime, Studio, provider path, or delivery path has
implemented the complete episode loop.

## Purpose

AFS uses one recoverable project fact chain for the first vertical product
slice:

```text
Project -> Series -> Episode -> Scene -> Shot
  -> Character / Scene / Prop continuity state
  -> Asset candidate -> Selected version
  -> Review decision -> Delivery version
```

The executable Pydantic contract is
`apps/api/runtime_episode_domain_contract.py`. Rainlight is a conformance
fixture for evaluation; its 15 shots, 135 seconds, and 25 missing asset slots
are not limits or defaults in this contract.

## Identity and version rules

- Every mutable production fact has a stable `entity_id` and an immutable
  `version_id`.
- Every reference resolves an exact `entity_type + entity_id + version_id`.
- A changed fact creates a new revision. It does not overwrite locked content.
- The aggregate retains a complete contiguous history for each entity. Revision
  numbers are unique; revision 1 has no parent and every later revision points
  to the immediately preceding `version_id`.
- Revision timestamps are timezone-qualified, strictly increase inside one
  entity history, and cannot be later than the aggregate `evaluated_at`.
- The highest revision is the entity head. Project policy is read from the
  latest retained project revision, while older exact refs remain resolvable.
- Every fact and consent record carries `org_id + project_id + actor_id`.
- A mutation command carries both `expected_aggregate_version` and an
  `idempotency_key`.

## Separate state axes

Content lifecycle:

```text
draft -> candidate -> approved -> locked
                    -> rejected -> retired
```

`locked -> approved` requires an exact, finalized unlock decision against the
locked version. Unlock and retire revisions preserve the locked content digest;
a later candidate revision carries any content change. Retiring locked content
also requires an exact finalized retire decision. `retired` is terminal.
The authorizing decision must occur after the locked fact and no later than the
successor it authorizes.

Review state is separate:

```text
not_requested | needs_review | approved | rejected
```

Provider or queue execution is also separate:

```text
queued | running | paused | succeeded | failed | cancelled
```

`running` therefore cannot become a content lifecycle value, and `locked`
content must carry an approved review state.

## Continuity and impact

A continuity state is a versioned fact for a character, scene, or prop. It may
contain identity baselines, temporary scene state, prohibited changes, and
approved asset-selection refs. Shots reference the exact continuity-state
versions they use. This shot-to-continuity edge is the only affected-shot
authority; continuity facts do not keep a second `applies_to` list.

Character, scene, and prop references use the same candidate, selection, review,
and version chain as shot media. They are not unversioned files attached beside
the shared production facts.

Changing a continuity fact must follow this command flow when the Runtime
implements it:

```text
propose new fact version
  -> calculate affected shot refs
  -> show impact to the creator
  -> choose update scope
  -> create new dependent versions
  -> review
  -> optionally lock
  -> retain rollback refs
```

No silent destructive cascade is allowed.

### Proposal application and undo evidence (v0.1.1)

`AgentProposal.impact_refs` remains the predicted, creator-visible set of exact
shot facts before an operation. It is not overwritten with the applied result.
`AgentProposal.applied_refs` records the exact successor shot facts actually
created by one executed proposal revision. Each applied successor carries the
same exact proposal revision in `ShotVersion.source_proposal_ref`.

The aggregate validates this membership in both directions. Applied refs must
be exact successors of impact refs for the same shot entities, the proposal,
target continuity state, and shots must share exact organization, project, and
actor scope, and a successor may only replace the old exact continuity ref with
the target exact continuity ref. Scene, sequence, duration, and source evidence
remain unchanged. Every successor re-enters `candidate + needs_review`; applying
a proposal cannot silently preserve approval or lock state.

The parent-to-successor edge is also validated independently of those declared
memberships. Every non-initial shot resolves its exact parent shot. Any change
to `continuity_refs` requires an exact `source_proposal_ref`, and the successor
must occur in that proposal revision's `applied_refs`. Conversely, a successor
whose continuity refs are unchanged cannot claim a continuity-operation
backlink. Coordinated removal of both an applied ref and its shot backlink
therefore leaves an invalid orphan rather than rewriting operation history.

Proposal states have separate meanings:

- `pending`, `accepted`, `partially_accepted`, and `rejected` express a creator
  decision and cannot claim applied refs;
- `executed` is an operation fact with one or more applied successors; its
  applied shot entities may be a subset of the predicted impact;
- `undone` is the next revision of the exact executed proposal. Its impact refs
  equal the executed revision's applied refs, and it must restore the complete
  applied set through new review-required successors targeting the exact prior
  continuity version.

This makes partial apply creator-visible and makes undo atomic. A later proposal
may advance one of the same shots, but its operation membership remains separate;
Runtime must still enforce latest-head comparison-and-swap before attempting an
undo. `content_digest` remains integrity evidence. It is not the source of
proposal ownership or undo authorization.

## Candidate, review, and delivery rules

- An asset candidate targets one exact shot or continuity-state version.
- A production-control-originated asset candidate may carry exact task, run,
  attempt, and writeback provenance plus explicit affected and protected refs.
  This provenance is evidence only when the candidate revision is finalized
  with approved lifecycle/review state and an exact approval review decision.
- A selected version can only select a candidate for that same target version.
  Approved or locked selections require a non-rejected, non-retired candidate
  with approved review state.
- Shot selections use storyboard/image/video/audio purposes. Continuity
  selections use character/scene/prop/voice/style reference purposes.
- A review decision targets one exact version; it is not a free-floating chat
  message.
- When an already approved fact changes content or a candidate changes artifact,
  the successor cannot inherit the old approval. A new exact, finalized
  approval decision is required.
- An agent proposal targets one exact fact and lists explicit impact refs. Its
  creator decision is pending, accepted, partially accepted, rejected,
  executed, or undone.
- A locked delivery requires a playable preview artifact, locked selections,
  and a finalized approved review-decision fact for each selected version.
  Draft, `needs_review`, future, or post-delivery approval text cannot authorize
  delivery.

## Data policy rules

New projects default to `private`, `training_use=denied_by_default`, and
`product_improvement_use=denied_by_default`.

Service processing, sharing, product improvement, and training are distinct
purposes. Feedback is not training consent. Consent records capture actor,
scope, purpose, data classes, provider, policy version, time, expiry, and
withdrawal state.

Every source's declared sharing, product-improvement, or training use is checked
against the current project policy and a matching active consent record,
including the provider surface. The aggregate's timezone-qualified
`evaluated_at` makes granted, expired, and withdrawn decisions deterministic. A
source-level flag cannot bypass a private or no-training project policy.

Provider policies are surface-specific. A no-training API statement cannot be
applied to a consumer web product or a different plan. Before any later
provider dispatch, Runtime must compare the project policy with a provider data
contract and block incompatible routes before uploading content.

Deletion is a real state machine:

```text
requested -> access_revoked -> live_data_purged
  -> provider_purge_confirmed -> backup_tombstoned
  -> completed | exception
```

Project export must eventually contain portable facts, assets, version chains,
review decisions, provenance, permissions, consent evidence, provider/model
records, and a machine-verifiable manifest. A rendered final video alone is
not a complete project export.

## Migration boundary

The existing project manifest, Studio metadata, production-run checkpoints,
visual-asset binding, domain crew records, and representative-episode binding
remain adapters or legacy sources until a test-proven migration names this
aggregate as their authority. Frontend modules must not create new competing
episode, scene, shot, selection, review, or delivery state stores.
