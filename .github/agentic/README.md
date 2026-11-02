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
