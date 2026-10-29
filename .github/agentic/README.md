# Agentic maintenance

Experimental FastMCP maintenance automation for Prefect Cloud. This directory owns
the execution plans, deterministic controller, tests and deployment definitions.
It is an isolated Python 3.12 project; Prefect is not a FastMCP library dependency.
Cloud workers fetch an immutable commit from `PrefectHQ/fastmcp` and execute here.

## Review outputs

The connected triage plan is built by `flows.agentic_triage_plan.build_triage_plan`
after registering its two worker deployments. Its Luna assessment selects an
existing-contribution verification, a real reproduction, or a recorded decision.
All branches join a deterministic finalizer; failed agents or workers retain an
operational outcome. The finalizer is executable code, not a human-input placeholder.
The managed intake still supplies bounded GitHub evidence and pins the admission.
Review and MRE batches require the connected plan; a missing configuration returns
an operational failure instead of falling back to the old Python role routing.
Workers resolve that immutable admission and verify their native parent, node,
deployment source, parameters and selected outputs before accepting a result.

Native triage reuses an entire completed result only for the same evidence,
instructions, probes, release baseline, plan version and worker source. It verifies
the original parent and finalizer runs and rechecks current GitHub evidence before
settling the intake. Failed or stale results are not reused. The current execution
engine cannot skip a root agent by omitting an optional plan input, so partial
assessment reuse is not used in the connected plan. Transient verification retries
remain within the same assessment run. Preparation and independent review retain their individual role caches. Fix batches
use the same connected assessment and reproduction as review and MRE batches; the
standalone assessment and reproduction plans are no longer dispatched by the controller.

An optional independent triage reviewer challenges the retained reproduction or
verified contribution, using the exact source and executed checks. Its disposition
and concrete next action lead each review item. The configured experiment uses
GPT-6.1 Sol for this review while retaining Luna for assessment and reproduction.
Serialized regression patches must materialize as tests on their stated baseline
before a reproduced result can advance. Agent claims alone do not establish that
those saved bytes are reusable. Review recommendations do not grant write permission.

Contribution-review freshness follows code revisions, contribution state, holds and
substantive discussion. CI progress and PR activity timestamps do not invalidate a
review; exact acknowledged reviewer receipts are excluded from its own discussion
comparison. Draft-fix publication keeps its stricter evidence comparison. Posted
reviews remain visible and counted when later evidence becomes stale, including
reviews delivered on generated drafts. Native triage retains its evidence durably
and returns that exact evidence to independent review without an immediate lookup.

The draft-fix pilot accepts a manually selected issue in `fix` mode. Only a
connected reproduction of a supported defect can reach preparation, full validation,
independent review and deterministic draft publication. A review queue alone does
not establish that this write path produced an accepted fix.

A manually selected batch contains at most six issues in two coherent groups.
`gh` collects existing labels, full issue discussion, all-state related PRs,
reviews and source revisions. Deterministic policy routes existing contributions,
claims, security reports and outstanding reproduction requests before model work.
It never writes labels. Existing automation retains ownership of its timers and
contribution gates.

The original intake remains an immutable artifact. Assessment receives a compact
view with full discussion text, event identities instead of repeated issue bodies,
and summarized successful checks. For unmerged contributions it also receives bounded
current diffs and changes since the original reviewed commits. Moved heads stop
collection; missing patches and comparison limits are explicit. A stale review
does not establish a current defect, and diff inspection does not count as a test.
The assessor loads contribution-review skills; test-authoring guidance is reserved
for the reproduction, preparation and independent review roles.

Before starting another assessment, the controller looks up a durable fingerprint
index, falling back to the twelve most recent completed role runs for older
unindexed results. Reuse requires identical skills and projected inputs,
the same complete intake revision and active plan version, and a successful
non-operational decision. Only the raw snapshot hash, which includes collection
time, is excluded from comparison. Revision hashes ignore repository-wide counts,
size and activity timestamps embedded in cross-reference events; those remain in
the retained raw evidence. Changed contribution history, revisions, labels or
skills invalidate reuse. Reused assessments retain provenance and add zero new model
usage to the batch; historical usage is not counted again.

Explicit Luna roles use complete repository skills pinned to the source revision:
assessment, reproduction, preparation and independent review. Each role has one
attempt and a 180-second platform limit. The batch allows one repair round and a
two-hour window. Haiku is configured as a comparison candidate, not an automatic
fallback. Missing telemetry remains unknown; these are execution bounds, not a
hard token or dollar cap.

One qualified patch becomes an independent draft PR. Two or three coherent patches
become a draft GitHub Stack through the same validation, review and publication path. Each layer is
validated on its actual parent with the full FastMCP suite and static checks.
Immutable Prefect artifacts retain the exact commits, patches, credit and review.
Publication rechecks ownership, issue history, holds and branch heads; uncertain
writes reconcile with existing GitHub objects. Validation retains per-layer commands, exit codes, elapsed time and bounded output.
Stack validation uses the same unprivileged process boundary as contribution
verification before fetching, installing or executing candidate repository code. Other results are grouped as existing-work
reviews, evidence, pruning proposals, maintainer decisions or operational failures.

Fork PRs are reviewed in place. Adoption and supersession require a maintainer's
decision, with original authorship and issue credit preserved. Closed PRs are
evidence: distinguish assignment-gate closure from explicit maintainer rejection
or supersession. See [maintenance history](docs/fastmcp-maintenance-history.md) and
[architecture decisions](docs/agentic-stack-architecture.md).

Existing-contribution assessments dispatch to `cloud-contribution-verification`.
Assessment selects at most three candidates in `verification_prs`, independently
of the complete PR history supplied for review. Historical discovery does not
schedule every unmerged PR for execution. An empty selection retains a review
decision and explicitly records that no tests ran.
An unmerged contribution can be verified while closed, including assignment-gated
PRs; verification does not reopen it. Its current diff and changes since review
are supplied to assessment just as for an open contribution. Merged contributions
remain in the history assessment without launching verification of their old heads.
The verifier fetches immutable commits, derives changed test modules from their
merge base, and runs them on both the pinned current release target and proposed
head. Optional independent
probes run on both as well. Immediate sibling test modules run on the head as
explicit file paths; verification never expands a parent directory into the full
suite. More than twenty sibling modules are omitted with a recorded limitation.
An omitted adjacent stage is `not_run`, not a duplicated passing head result. The repository
lockfile is installed without re-resolution; JUnit cases distinguish assertion
failures, setup errors, skips and missing tests. The managed supervisor launches
verification as UID/GID 65534 with no supplementary groups, capabilities, or
inherited credentials, and with privilege escalation disabled. The supervisor's
runner and input files remain root-owned; verification has its own home and
scratch directory. A network-disabled container canary checks access to a dummy
supervisor environment and private file. Linux, a root supervisor, and `setpriv`
are required; an unavailable boundary fails closed. This isolates supervisor
credentials, not all networking or world-readable files. Command process groups
have timeouts; the managed container deadline is the final lifetime bound.

Results retain the source revisions, probe source, commands, exit codes and test
transitions. The controller checks deployment version and exact inputs before
accepting an artifact, rechecks contribution evidence afterward, and retains stale
results as historical evidence. Matching requests reuse the same verification run.
This deterministic stage uses no model tokens. Provider compute cost remains
unknown. Passing contributor tests is not a merge recommendation: contract review,
current-branch composition and full-suite/static checks remain separate evidence.

When only the release baseline advances during an existing-contribution review,
the controller retains the completed assessment and runs at most two incremental
refreshes. Each refresh requires unchanged contribution history and pinned skill
contents, a complete bounded fast-forward diff, a new Luna assessment of that diff
against the prior contract, and deterministic verification at the new baseline.
The prior assessment is historical context, not automatic approval of new commits.
Interrupted stages resume their saved inputs and child runs. Final freshness checks
use the accepted refreshed snapshot; the original intake remains available.
Changed PR revisions, human discussion, holds or policy require full reassessment.
Incomplete or divergent baseline history also returns to full assessment.

## Bounded intake and review queue

`cloud-triage-intake` selects work without a hand-authored batch. It reads one
30-row page of the oldest open GitHub items and one 30-row page of recent updates,
filters out PRs, and retains at most sixty unique pending issues before dispatch.
It drains that queue in batches of at most three, then advances the backlog page.
The queue and page are saved before starting the child controller. An interrupted
intake recovers the same child by its stable identity and exact inputs. Saved
results, including a completed group without final review bins, survive a later
child failure. Fresh evidence is required before finalizing queued work. The
finalized decision snapshot is saved before receipts and presentation artifacts,
so retrying an interrupted commit completes the same snapshot; a later intake
recovery revalidates the child evidence before making a new snapshot.

Intake has no schedule. Independent event targets have separate checkpoints;
rolling backlog checkpoints remain scoped to their queue and release branch.
Prefect leases serialize overlapping queue updates and issue work across intake,
PR events and manual fix batches. Issue ownership is recorded before work starts.
A takeover stops the prior controller and its proven children, waits for terminal
acknowledgement and shared verification, then records its new ownership. The
active-controller budget is separate from issue ownership, so waiting for another
owner does not occupy an execution slot. Lease renewal failure stops the worker.
This bounds controllers, not all child compute: orphan children may temporarily
outlive a failed controller. Same-issue takeover drains those children before
starting replacement work.

Intake uses `mre` mode: existing
contributions go to verification, new work can reach reproduction, and fix-stack
publication is unavailable through this entrypoint. Public GitHub mutations and
bot-owned label behavior remain unchanged. Independent probes for arbitrary
reports still require the reproduction handler; intake does not manufacture them.

GitHub PR events refresh referenced issues; they do not request a review of the
PR itself. The webhook excludes drafts, PR descriptions without an issue-reference
hint (`#` or a repository issue URL), and edits that do not change the description.
Intake resolves references and discards closed issues and references to other PRs.
Events for the same GitHub item are limited to one launch per sixty seconds;
intake fetches current GitHub state when it starts. Slack announces the issues
accepted for triage only when their controller starts. Empty intake runs stay quiet.
Failure alerts follow the intake's terminal outcome once, with issue-specific
reasons in its state message. Child failures remain in the linked review queue
and run graph rather than producing separate alerts for every orchestration layer.

Successful receipts defer unchanged issue metadata for sixty minutes by default.
This is a cooldown, not a claim that associated PR history is unchanged. Full
history and PR revisions are refreshed when an item is processed, and again before
finalizing its result. Three unsuccessful admissions retain an operational receipt;
the same metadata and implementation are not automatically retried again unless
`retry_failed` is explicitly set. Changed metadata or implementation can retry it.
Baseline churn is retained as pending refresh work, moved behind other queued
items, and does not consume that operational-failure allowance. It never counts
as an acknowledged issue. Review and MRE intake can select a freshly collected
baseline if the branch moved during provisioning; fix publication retains its
original pinned target and freshness gates.

Rolling pages are not a snapshot census: concurrent closes and new activity can
shift entries between pages. Repeated sweeps revisit them. Coverage artifacts
report raw rows, unique issues, deferred work, selected/triaged/pending counts and
retry exhaustion. They do not claim whole-backlog coverage or measured hours saved.
Each controller also creates a Markdown review queue with existing contribution
links, holds, executed regressions, closure proposals, missing evidence, concrete
questions and observed usage. Evidence stays bound to the recorded revisions.

For the general backlog, use `{"branch":"main","max_items":3}` as the config.
An optional `issues` list bounds a trial to named issues and has a separate queue:

```sh
bash scripts/agentic_stack.sh intake config.json runtime.json \
  CONTROLLER_DEPLOYMENT_ID INTAKE_DEPLOYMENT_ID unique-operation-id
```

Reuse the operation ID if the launch response is uncertain. Inspect the intake's
coverage and review queue artifacts and its linked controller's results. This
command does not enable recurring runs.

GitHub Actions owns labels, duplicate marking and closure cooldowns. Prefect owns
bounded contract assessment, executable reproduction/contribution verification,
and the explicitly selected draft-fix pilot. Existing GitHub investigation comments
are supplied as evidence; Prefect verifies claims needed for a patch and does not
publish a second investigation comment or close an issue.

## Local verification

From this directory:

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```

The tests use temporary Git repositories, a local HTTP fault server and a real
temporary Prefect server. They need `git`, `gh` and `jq` on PATH. They do not need
Cloud credentials and do not publish GitHub objects. The separate CI workflow
runs these checks without adding automation dependencies to the library suite.

## Cloud setup and manual operation

Use an authenticated Prefect CLI profile or `PREFECT_API_KEY`. Set
`PREFECT_API_URL` to the intended Cloud workspace URL. The launcher rejects
self-hosted URLs. Configure that workspace's provider connection ID and explicit
models in `workflows/stack-models.json`, then regenerate the plans:

```sh
uv run python -m flows.agentic_stack_plans
bash scripts/agentic_stack.sh validate
```

Publish the four new role plans through the Agentic Workflows tooling. Registration
requires a clean checkout at a commit already available in `PrefectHQ/fastmcp`:

```sh
bash scripts/agentic_stack.sh register
```

This registers `cloud-triage-intake`, `cloud-stack-batch`, `cloud-stack-validation`,
and `cloud-contribution-verification` in the managed pool, with no schedules. The
publisher loads the existing Cloud Secret block
`fastmcp-triage-gh-token`. Agent roles receive no GitHub write credential.

Managed execution accepts only its supported image tags, not arbitrary patch
version tags. The deployment uses its supported Python 3.12 image and installs
the pinned Prefect version and FastAPI compatibility constraint before pull steps.

Prepare a batch matching `flows.agentic_stack_policy.Batch` and runtime inputs
matching `flows.agentic_stack_runtime.Runtime`. Runtime inputs name the four
published role flow/version IDs, validator deployment ID, contribution verification
deployment ID (`verification_id`) and controller source SHA. Once the named batch
is configured for connected triage, also supply `triage_plan` (flow and version),
`triage_verify_id`, and `triage_finalize_id`; all three must be supplied together.
The connected plan handles assessment and verification/reproduction for `mre`,
`review` and `fix` items. The bounded controller continues a successful `fix`
reproduction through preparation, validation, independent review and publication. Once the named batch
and its external effects are approved:

```sh
bash scripts/agentic_stack.sh run /absolute/path/batch.json /absolute/path/runtime.json CONTROLLER_DEPLOYMENT_ID
bash scripts/agentic_stack.sh results CONTROLLER_RUN_ID
```

Registration is not a successful trial. This implementation has not yet produced
a live stack. Before unattended use, verify the managed runtime, actual PR/stack
readback, cancellation and observed token usage on a bounded batch. Cancellation
stops further publication; an already-started child can run to its own limit.
Public comments, closures, assignments, ready transitions, merges and recurring
schedules are outside this initial automation's authority.

### Delivering reviews and draft fixes

Runtime `prepare_fixes: true` admits automatic issues in fix mode. A reproduced,
independently recommended unclaimed issue proceeds through the existing preparation,
credential-isolated validation, independent patch review and draft publication path.
The triage review's `prepare_fix` disposition carries its implementation controls
into preparation; unresolved reproduction or product decisions must use
`repair_evidence` or `maintainer`. Draft publication still requires final patch
approval without findings, fresh ownership and contribution checks, and reporter credit.

Public reviews are separate from retained investigation records. Existing-contribution
reviewers return a PR-bound `public_reviews` entry with a short finding, requested
change and check evidence (at most 120 words). Missing or invalid public feedback
stops delivery; internal `reason`, `findings` and `next_action` are never a fallback.
Generated-draft reviews summarize successful validation without publishing the
reviewer's internal report or command transcripts. Full evidence remains in the
original Prefect artifacts; revision markers bind the public review to that evidence.

Runtime `publish_reviews: true` posts independent existing-contribution and generated-patch reviews as
GitHub `COMMENT` reviews at the exact verified head. Configure `reviewer_app` with
`app_id`, `slug` and `private_key_block`. The current reviewer is GitHub App
`fastmcp-triage-reviewer` (5514681), owned by zzstoatzz and installed only on FastMCP.
Its signing key lives in the Prefect Secret `fastmcp-triage-reviewer-private-key`;
it is loaded only by trusted delivery code. Agent nodes and verification subprocesses
receive no App credential. Each delivery mints a short-lived repository-scoped token
with PR write and contents/issues/checks/statuses read permissions, then revokes it.
Draft authorship continues through the separate existing publishing credential.

PR claims serialize delivery across issues. A retained revision marker reconciles
retries and lost responses without reposting. External drafts, explicit holds, changed heads,
new discussion and self-authored PRs block posting. The review records executed
checks, findings and next actions; it neither approves nor merges. Exact acknowledged
App reviews are excluded from subsequent freshness comparisons, while new human
or other bot evidence still invalidates an old result. Posted review URLs are retained
alongside the original Cloud review evidence.

After draft publication, the same delivery client posts the retained independent
patch review and validator commands on each exact generated head. Only drafts whose
author, body, branch, base and head match the validated publication qualify for this
path. They remain drafts. Publication is recorded before a separate delivery task,
which retries transient failures and reconciles lost POST responses. New PR comments,
inline comments or reviews stop delivery; only the exact acknowledged App review is
ignored on retry. Exhausted delivery failures retain the published PR receipt and an
operational failure rather than discarding the useful patch or claiming review delivery.


### Reviewing mutation batches

Each review queue also retains a `fastmcp-mutation-batch/1` JSON artifact under
`<batch-id>-mutation-batch`. Read one with `agentic_stack.sh mutations <controller-run-id>`.
The queue presents the exact proposed target, action, revisions, public comment or
assignee, source runs, scope, norm evidence and remaining gates. Conflicting proposals
for one target are blocked. No command in this path executes the proposals.

The existing independent triage reviewer may propose assignment, localized PR finishing,
PR review, duplicate PR consolidation, duplicate/completed issue closures or a closure backed by settled policy. Assignment
requires explicit public-history and reporter-claim review; passing tests alone cannot
qualify a contributor. Closure candidates now receive that same independent review.
A merged reference alone cannot establish that the whole report is fixed: current-fix,
revert and scope checks remain explicit. Missing canonical or merged-fix identities,
negative norm checks, existing owners, security restrictions and holds block proposals.
Duplicate automation and human interventions retain their existing authority.

PR review/finishing proposals retain static checks, current CI, contributor credit and
batch composition as separate gates before merge. Older retained verification outputs
project to concrete per-PR review or regression-repair steps; their prose is never parsed
into assignments or closures. Every proposal requires approval and a fresh claimed
execution against its recorded evidence and referenced resolution snapshots.

The retained-intake digest identifies the immutable original snapshot; its normalized
`evidence_revision` supports freshness comparisons without collection-time churn.
Acknowledged App review receipts are carried so only those exact own reviews can be
excluded. Resolution fingerprints omit unrelated repository counters. The expanded
Markdown view uses `<batch-id>-mutation-review-queue`, preserving older queue artifacts.

PR consolidation uses explicit `close_pr` proposals with a named `canonical_pr` and
both target and canonical revisions. The independent reviewer must compare coverage,
read prior closed-PR discussions and preserve earlier maintainer canonical choices;
matching titles or a newer pair of open PRs are not selection evidence. Both heads need
retained verification. An open canonical PR is allowed; no PR is closed automatically.

Assignment proposals always retain a live `platform_assignment_eligibility` gate.
The executing maintainer must check repository assignee eligibility (HTTP 204), then
read back issue assignees: `gh issue edit` can exit successfully while ignoring an
ineligible nonparticipant. Public-history qualification and reporter ownership do not
establish platform eligibility. No automatic gate bypass or claim comment is introduced.

Closed unmerged canonical PRs retain a reopen/coordination gate; administrative closure
is not a rejection. Bulk proposals block canonical closure chains/cycles and assigning
a contribution that the same batch proposes closing. Resolve the surviving canonical
before approval rather than applying individually plausible actions together.

Evidence collection follows at most three one-hop, same-repository canonical or
superseding PR hints from repository-associated human discussion. It verifies each
number is a PR, retains full discussion and source-comment provenance, and rechecks
source and canonical revisions after enrichment. Hints are discovery evidence,
never selection authority. Foreign references, ordinary contributor comments and
bot comments do not expand discovery; existing candidate and byte bounds remain.
