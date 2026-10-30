# Agentic maintenance

Experimental FastMCP maintenance automation for Prefect Cloud. This directory owns
the execution plans, deterministic controller, tests and deployment definitions.
It is an isolated Python 3.12 project; Prefect is not a FastMCP library dependency.
Cloud workers fetch an immutable commit from `PrefectHQ/fastmcp` and execute here.

## Maintainer review with EGM

Use Google's [Elephant–Goldfish Model](https://github.com/google/elephant-goldfish-model)
in the maintainer's interactive session to review a saved triage proposal. Install
the **complete upstream plugin**, following its [Codex integration guide](https://github.com/google/elephant-goldfish-model/blob/main/docs/codex.md),
then start a fresh session. The plugin includes the reviewer personas and
document standards its skills load during review.

Use the installed `egm-review-loop` skill with the proposal as `TargetFile` and
`full file` as `SemanticTarget`. Configure its upstream reviewers and standards
through that skill's configuration step. Supply FastMCP's contribution policy,
applicable review skills, and the proposal's pinned source and evidence as
`GoldfishContext`. Follow upstream's review and resolution gates; retain its
report with the proposal for the maintainer's decision. The maintainer authorizes
the selected GitHub actions after reviewing the proposal.

Cloud nodes receive role-specific FastMCP instruction files through
`instructions.files`, loaded at the issue's pinned base revision by
`scripts/load_fastmcp_skills.sh`. The maintainer session loads EGM from the
installed plugin. Running EGM inside Cloud requires access to the full upstream
bundle and verified support for fresh subagents.

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
remain within the same assessment run. The older fix-stack path retains its
individual role cache while it is migrated separately.

The first milestone is complete triage: collect evidence, assess it, execute the
appropriate verification, and return an actionable review queue. Fix stacks are a
later capability. Classification alone does not satisfy this milestone.

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

Qualified patches become two- or three-layer draft GitHub Stacks. Each layer is
validated on its actual parent with the full FastMCP suite and static checks.
Immutable Prefect artifacts retain the exact commits, patches, credit and review.
Publication rechecks ownership, issue history, holds and branch heads; uncertain
writes reconcile with existing GitHub objects. A single useful patch is retained
without manufacturing a second layer. Other results are grouped as existing-work
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

The deployment is serial and has no schedule. Intake uses `mre` mode: existing
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
The connected plan handles `mre` and `review` items. The existing bounded stack
controller continues to handle explicit `fix` batches. Once the named batch
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
