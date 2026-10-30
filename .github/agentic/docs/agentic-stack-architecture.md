# Stack controller architecture

Design decisions from Nate's requested source review on October 21, 2026.
These refine the approved stack workflow; they do not describe completed Cloud
integration. The first implementation remains a bounded batch of ordinary
Prefect tasks plus Agentic Workflows for judgment.

## Bind decisions to the state that justified them

Microscope gives mutable debugger state one owner and publishes immutable
snapshots. Its execution identity names an accepted operation; its stop identity
names the observed state. `validate_public_stop` rejects a supplied obsolete
stop ID, and a real debugger test verifies that jumping creates a new stop and
rejects a later command carrying the old ID.

Apply that boundary here: agents return typed proposals; the deterministic
controller owns routing, budget admission, and publication. A review identifies
the issue/contribution evidence revision, policy and skill revisions, validated
artifact, and exact parent/head chain. Recollect the facts required for that
action before publication. A new claim, relevant comment, hold label, competing
contribution, or changed parent/head invalidates the affected decision. An
approval being recent is insufficient. A validation run ID identifies an attempt;
it is not interchangeable with the identity of the validated bytes.

Keep immutable skills, source and patch artifacts separately from changing
GitHub observations. Define the evidence fields required by each decision before
optimizing revision hashes. The current whole-snapshot hash is conservative;
do not remove fields merely to improve cache hit rate.

Microscope also separates cheap state summaries from expensive inspection. The
notes' bounded-scan lesson makes this directly relevant to cost: fetching six
items is not cheap if discovering them requires inspecting the entire backlog.
Use bounded metadata intake, then load discussion, review and CI details only for
the selected handler. Preserve all-state contribution discovery and explicit
coverage markers. Unfetched, truncated, failed, and absent are different states;
missing evidence must never become a cheap negative answer.

The current new collector eagerly loads PR reviews/checks and stack inventory.
Split its routing envelope from handler detail before scaling intake. Existing
labels remain evidence owned by the repository's bots; this controller must not
invent or manually apply workflow labels.

## Commit evidence before advancing progress

Stream holds its cursor behind unfinished sequences and fails the pipeline when
cursor persistence fails. Its admission script verifies a receipt for an exact
image digest. Together these provide the useful rule: progress describes a
verified durable boundary, rather than work that was started or appeared to finish.

For our pipeline, collected, proposed, validated, reviewed and published are
separate facts with retained evidence. Validation must durably retain the exact
Git chain and its checks before a later stage may consume it. Publication is
complete only after GitHub readback verifies branch heads, PR bases and ordered
native stack membership. A green orchestration run alone does not establish a
reviewable stack.

Before external writes, retain a stable publication identity and the intended
refs, commits and membership. A timeout leaves an unknown outcome. Reconcile it
through reads and recover the same operation instead of creating a second stack.
Keep partial publication visible and resumable. Publication and abandonment must
not both succeed for one operation; a late worker cannot revive an abandoned one.
Use enforced execution deadlines and ownership checks at the write boundary.
An expired heartbeat alone does not prove remote work stopped or free its budget.

The local implementation retains exact commits in a bundle, stores immutable
Prefect artifacts, binds independent review to the validated chain, and reconciles
partial publication. Tests use real Git, an HTTP fault server, and a temporary
Prefect server: lost responses after either PR or stack creation recover the same
objects; changed holds and cancelled ownership block further writes. A resumed
batch restores completed group outcomes instead of repeating them. These component
checks do not establish the full managed controller's runtime behavior. Verify
that boundary in the authorized Cloud/GitHub trial, including cancellation while
a child run is active.

## Scope

The notes explicitly distinguish portable primitives from someone else's
constraints. We need revision checks, durable receipts and recoverable effects.
We do not need Stream's storage engine, a debugger-style runtime, a new general
agent framework, or a full deterministic simulator to process the first batch.
Keep Prefect's changing plan/model/result APIs in small adapters around ordinary
Python policy. Add abstractions only when a demonstrated second use requires them.

## Source record

Read source and behavioral tests, not only project descriptions. Stream and
notes were inspected in their existing local checkouts; Microscope was refreshed
through the librarian checkout. Existing unrelated local changes were untouched.

- Stream at `d1a3118074c7bf72db1a66f1f01f46b79b3b109a`:
  [repository](https://tangled.org/zat.dev/stream),
  `docs/architecture.md`, `docs/invariants.md`,
  `src/internal/ingest/pipeline.zig` (`cursorAfterBatch`, cursor callback ordering),
  `src/internal/ingest/backfill/lifecycle.zig`, and `scripts/admit`.
  The current cursor implementation uses the minimum inflight sequence minus one,
  otherwise the global accepted high-water mark; some prose describes a batch
  maximum instead. The code is the evidence for this mechanism.
- Notes at `2c2ed474eab6b26b94423c0fb984ba55d0cc98ed`:
  [repository](https://tangled.org/zzstoatzz.io/notes),
  `systems/prior-art-encodes-someone-elses-constraints.md`,
  `systems/watermark-semantics.md`, `systems/ownership-must-expire.md`,
  `systems/publish-then-free.md`, `operations/bounded-scans.md`,
  `systems/stateful-property-testing.md`, and
  `systems/deterministic-simulation-testing.md`.
- Microscope at `1ef7c29dacf6c26d3cd8f553c947307f46ce9de9`:
  [architecture](https://github.com/microscope-dev/microscope/blob/1ef7c29dacf6c26d3cd8f553c947307f46ce9de9/dev/architecture.md),
  [state validation](https://github.com/microscope-dev/microscope/blob/1ef7c29dacf6c26d3cd8f553c947307f46ce9de9/src/backend/linux.rs#L2937),
  [stale-operation test](https://github.com/microscope-dev/microscope/blob/1ef7c29dacf6c26d3cd8f553c947307f46ce9de9/tests/debugger/writes.rs#L344),
  and `dev/simulator.md`. `jcalabro/microscope` redirects to this repository.
  Some internal operations permit an omitted stop ID; the lesson is explicit
  validation of supplied state identity, not a claim that every API requires it.
