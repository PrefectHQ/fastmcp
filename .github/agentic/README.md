# Agentic maintenance

FastMCP's triage automation for Prefect Cloud. It's an isolated Python 3.12
project (Prefect is not a FastMCP dependency). Cloud workers fetch an immutable
`PrefectHQ/fastmcp` commit and run the flows here.

## What runs

Two event automations (`fastmcp-triage-on-github-issue` and `-pr`) start
`cloud-triage-intake` when an issue changes or a ready PR that references an issue
changes. Intake collects bounded GitHub evidence and hands a batch to
`cloud-stack-batch`. The connected triage plan (`flows.agentic_triage_plan`)
assesses each item, verifies existing contributions through
`cloud-contribution-verification` or reproduces the report, and sends every
public-facing outcome through the Sol `triage_review` node, which loads
`.agents/skills/adversarial-review/SKILL.md` from the pinned revision.
`cloud-triage-finalize` records the outcome, and accepted reviews are posted as
COMMENT reviews by the triage review app. Nothing in this path assigns, closes, labels or
merges.

Each controller run retains its results as `<batch-id>-review-bins`:

```sh
bash scripts/agentic_stack.sh results CONTROLLER_RUN_ID
```

Role instructions come from `workflows/stack-skills.json` and are loaded at the
item's base revision by `scripts/load_fastmcp_skills.sh`. Agent roles get no
GitHub write credential.

## Local checks

From this directory (needs `git`, `gh` and `jq`, no Cloud credentials):

```sh
uv sync --locked
uv run ruff check . && uv run ruff format --check . && uv run ty check
uv run pytest -q
```

`agentic-checks.yml` runs the same checks in CI.

## Contribution verification

Changed author tests run on baseline and head. A module that only collects on
head is retained as `baseline_collection_incompatible`, with its collection
errors and output in the review summary. Without a standalone behavior probe,
verification returns `evidence_needed`; do not retry unchanged tests as an
environment outage or count an import error as a reproduced bug.

Supply a baseline-compatible test through the item's `verification_probes` to
repair the evidence. If author-module collection blocks the combined baseline
run, the verifier runs those probes separately on both revisions, reusing their
completed setup and limiting each probe run to 60 seconds. Author head results
remain separate; only completed failure-to-pass test transitions establish
`fixed_tests`. Source/probe/configuration files, Git metadata and installed dependency
files are fingerprinted before execution and checked before/after each run and
again before returning comparisons. Added, removed or modified inputs invalidate
all transitions and produce `evidence_needed`, with bounded changed-path diagnostics.
Both environments are prepared before author tests run; each test invocation gets
a fresh HOME, temporary directory and pytest cache. Bytecode writes and user-site
packages are disabled. These checks reject persistent contamination; they do not
turn execution of contribution code into a security proof or replace contract review.
Identical verification specs reuse the retained execution.

## Public review delivery

Every selected contribution still needs fresh head/baseline verification and independent
review. Public feedback carries an explicit `no_findings`, `findings` or `inconclusive`
outcome and stable finding identities. Under the existing per-PR claim, delivery compares
the latest review from the configured bot with that fresh outcome, only when intact. Equivalent
feedback is retained as `unchanged_feedback` in `review_delivery.skipped`, with the newly
verified revisions and a separate `previous_review` receipt at its original revisions;
it is never counted as a newly published or current-revision GitHub review. Suppression
still runs the evidence freshness guard. Added, resolved or materially changed findings
produce new feedback, including changes at the same code revision. Finding ids must be
reused for the same defect and changed when impact or the required correction changes;
severity changes are also significant. Wording and verification transcripts are not
feedback identities. The body hash only detects edits to previously published feedback.

Legacy reviews and older pinned plans lack semantic identities and cannot authorize
suppression; they may produce one initial structured review after deployment. Edited,
dismissed or inconclusive reviews also cannot authorize suppression. Accepted POSTs with
lost responses still reconcile by exact revision and body. The stored plan schema and
objective are updated together here; publishing the plan and deploying the new source
remain separate steps. No live workflow changes follow from this patch.

## Deploy

Merging does not deploy. Live automations run the `source_sha` pinned in their
runtime parameters. To deploy a new main commit, from a clean checkout of it:

```sh
uv run python -m flows.agentic_stack_plans   # only if plans or models changed
bash scripts/agentic_stack.sh validate
bash scripts/agentic_stack.sh register       # registers deployments from prefect.yaml
```

Then publish any changed role plans with the Agentic Workflows tooling and update
`source_sha` (and any new plan versions) in both triage automations right away.
Flows refuse to run when their deployment version differs from the pinned
`source_sha`, so between `register` and that update, new intakes and any in-flight
run that starts a child fail. Check for running intakes first. If the alert template
changed, re-run `agentic_stack.sh alerts`.

## Roll back

Set `source_sha` in both automations back to the previous commit and re-register
from that commit. If a plan changed, also point the runtime back at the previous
plan versions.
