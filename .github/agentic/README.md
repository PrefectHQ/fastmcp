# Agentic maintenance

FastMCP's triage automation for Prefect Cloud. It's an isolated Python 3.12
project (Prefect is not a FastMCP dependency). Cloud workers fetch an immutable
`PrefectHQ/fastmcp` commit and run the flows here.

## What runs

Two event automations (`fastmcp-triage-on-github-issue` and `-pr`) start
`cloud-triage-intake` when an issue changes or a ready PR that references an issue
changes. Intake collects bounded GitHub evidence and hands a batch to
`cloud-stack-batch`. The connected triage plan (`flows.agentic_triage_plan`)
assesses each item and, on the reproduce branch, reproduces the report. The batch then
verifies existing contributions through `cloud-contribution-verification` itself (a
DeploymentNode in the plan can't wait on the assessment), and sends every
public-facing outcome through the Sol `triage_review` node, which loads
`.agents/skills/adversarial-review/SKILL.md` from the pinned revision.
The batch records the outcome, and accepted reviews are posted as
COMMENT reviews by the triage review app. Two reviewed outcomes also become GitHub
labels (`flows/agentic_labels.py`), which the repository's closers act on: a request for a
reproduction gets `needs MRE` (closed after 7 days without the author) and a duplicate gets
`potential-duplicate` (closed after a 3-day cooldown). Nothing in this path assigns, closes
or merges.

`cloud-triage-sweep` (`flows/agentic_sweep.py`, no model) labels inactive outside issues and
PRs `sweep:close` every Monday and lists them in Slack. Removing the label keeps an item; a
daily run closes the rest once the label has been on for 48 hours with no comment from a
person.

`cloud-triage-memory-sync` (`flows/agentic_memory_sync.py`, no model) keeps a turbopuffer index of
every issue, PR and human comment current, every 30 minutes. While `memory.mode` is "on", each
assessment gets the closest past issues and PRs as `related` evidence: leads for duplicates, prior
fixes and earlier decisions, never findings on their own. A lookup failure leaves `related` empty and
triage carries on. The key is the `turbopuffer-api-key` Secret block; run the flow once with
`backfill=true` to index the whole repository.

Agents claim work through the triage MCP server (`memory_server.py`, on Horizon) so interactive
sessions and triage runs don't step on each other: `claim_work` takes `issue:<n>`, `pr:<n>` or a
deploy name and either refuses with who holds it or grants a fenced 30-minute lease and the item's
context; `report_progress` renews it; `release_work` ends it with `pr`, `handed_back` (with a
reason) or `abandoned`. Claims are public atproto records on prefect.io (`flows/agentic_work.py`),
the triage floor draws them, and the `work.activity` MCP event serves the log where fastmcp has the
events extension. With `work.mode` "on", the stack controller claims each item the same way and
skips items someone else holds. The server needs `WORK_ATPROTO_PASSWORD`; without it the tools
answer that claims are off.

The Prefect Variable `fastmcp_triage_config` holds `github_writes` ("off", "dry_run" or "on",
or per feature: `{"labels": "on", "sweep": "dry_run"}`; default "dry_run"), the sweep
thresholds under `sweep`, `memory` (`{"mode": "on"}`; default off), and `work` (claims, `{"mode": "on"}`; default off). All are read on every run, so changing them needs no deploy.
Writes go out as the triage GitHub App, which needs Issues: write. Under `burst`, `max_items` (default 5) and
`window_hours` (default 24) pause triage for anyone who opens more issues and PRs than that in
the window; Slack gets one line when they cross it.

Intake skips events sent by anyone with write access to the repository, so
maintainers' own edits and their agents' activity don't start triage. If the
permission lookup fails, triage runs anyway. When a finished intake leaves issues
for a maintainer (an existing PR to review, a decision, a proposed close or missing
repro info), it emits `fastmcp.triage.outcome` and the `fastmcp-triage-outcome`
automation posts one line per issue to Slack. Intakes with nothing to decide stay
quiet; failures go through the failure alert.

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
runtime parameters. Run the [Deploy agentic triage](../workflows/deploy-agentic.yml)
workflow from main to deploy main's tip, or pass a commit on main as `ref`. It uses the
`PREFECT_API_KEY` repo secret against `prefect-technologies/marvin-bot` and runs:

```sh
bash scripts/agentic_stack.sh deploy
```

`deploy` validates the role plans, waits for no triage runs to be scheduled or in
flight, registers the deployments from `prefect.yaml`, and immediately repins
`source_sha` in both triage automations. Flows refuse to run when their deployment
version differs from the pinned `source_sha`, so those two steps stay back to back.
It is a no-op when the commit is already live.

`deploy` refuses when the execution plans or `stack-models.json` changed since the
live commit. Then, from a clean checkout of the new commit, rebuild the plans
(`uv run python -m flows.agentic_stack_plans`), publish them with the Agentic
Workflows tooling, run `agentic_stack.sh register`, and update `source_sha` and the
plan versions in both automations right away. If the alert template changed, re-run
`agentic_stack.sh alerts`.

The connected triage plan has no committed JSON, so the guard also refuses when
`flows/agentic_triage_plan.py` or `flows/agentic_stack_plans.py` changed. Rebuild it
with `uv run python -m flows.agentic_triage_plan --output triage.plan.json` (add
`--tier` only if the live runtime uses one), diff it against the active version of
the `runtime.triage_plan` flow, validate, publish it to that flow, and update
`runtime.triage_plan.version_id` in both automations.

## Roll back

Run the deploy workflow with the previous commit as `ref`. If the plans differ between
the two commits, roll back by hand: re-register from the previous commit and point
`source_sha` and the plan versions in both automations back.
