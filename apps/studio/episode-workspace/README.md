# Episode workspace

Authenticated `review_and_recovery_workspace_candidate` for the single-episode fact chain:

```text
Project / Episode shell
  -> Storyboard-centered workspace
  -> Contextual inspector
```

The existing `/studio/` root remains unchanged. This surface is served by the same
authenticated Runtime static tree at:

```text
/studio/episode-workspace/?project=<project_id>&episode=<episode_id>&version=<version_id>
```

This candidate does not yet provide the complete AI-native production control
surface from creator intent through Agent decomposition/parallel execution,
progress/cost/blockers, and artifact writeback. It is retained as review/recovery
evidence while the control thread prepares a new Task Packet; it must not be treated
as final frontend authority or used to reopen a simple canvas/non-canvas vote.

It reads the exact creator-safe workspace projection, sends typed commands with one
durable `Idempotency-Key`, and stores UI-only recovery state under the
`episode_workspace` namespace of authenticated Studio state. Domain completion,
next action, delivery blockers, exact refs, and artifact availability are never
derived from that UI state.

The surface contains no representative fixture, fake project, local business-state
persistence, provider call, generated thumbnail, inferred script, invented progress,
or invented playability. The browser auth token follows the existing Studio Runtime
client convention; it is not episode business state.

## Visual contract

- Deep rain-blue project shell, true white/neutral storyboard paper, copper decision
  accent.
- Desktop: compact scene/problem rail, dominant storyboard, contextual inspector.
- Mobile: review, selection, comments, and recovery companion without wide tables or
  horizontal clipping.
- Guided behavior is limited to the server-projected next action, recovery, and safe
  errors. Infinite canvas, NLE, and global agent chat are absent.

Accepted visual specification:

```text
C:\Users\chenzy\.codex\generated_images\019f6302-d4f4-7493-b58a-a3f6af85286d\exec-7fa7cb22-ba4d-40db-95b6-e6df461f1554.png
```

Local runtime and deterministic tests do not constitute provider/media QA, human
acceptance, business validation, public readiness, or release delivery.
