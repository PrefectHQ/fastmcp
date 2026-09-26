# Maintaining FastMCP

FastMCP is maintained by a small team with a lot of automation. Automations handle the routine work: labeling, gating external PRs, releases. Maintainers make the calls that need judgment. This page says which is which.

**Current state:** [STATUS.md](https://github.com/PrefectHQ/fastmcp/blob/status/STATUS.md) on the `status` branch, regenerated twice a day, with the same data as [status.json](https://raw.githubusercontent.com/PrefectHQ/fastmcp/status/status.json).

## What runs on its own

Everything below runs as a GitHub Actions workflow in this repository, so its runs are public.

- **Contributions:** the issue-link gate holds an external PR until its author is assigned to an issue it links. Assigning the author reopens a PR the gate closed.
- **Issues:** new issues and PRs are labeled and checked for duplicates. Issues marked as duplicates, and issues still missing a reproducible example after 7 days without a reply from the author, close on their own.
- **Pull requests:** failed test runs get an explanation, and maintainers can ask the bot to act with `/marvin`.
- **Health:** the test suite runs nightly against the newest dependency releases and opens an issue when it fails.
- **Releases:** a tagged release publishes all four packages to PyPI, and publishing the docs deploys gofastmcp.com.
- **Contributor queue:** twice a day, the gated PRs are sorted into worth assigning, needs a human, or decline. Only the counts are published.

Maintainers also run a triage agent that may open **draft** PRs for bugs nobody has claimed. It never merges, comments, labels, or marks a PR ready, and it skips any issue that is assigned or already has a PR.

## What needs a person

- Assigning an external contributor, which reopens their gated PR.
- Merging, and marking an agent-opened draft ready.
- Any change to documented behavior or compatibility, and any new API.
- Cutting a release, including its title and notes.
- Classifying and disclosing security reports.

## For agents

Start at [AGENTS.md](AGENTS.md). It holds the workflow, the required checks, and a table of the repository's skills, one per job: triage, contributor review, fixing a bug, code review, following a PR, tests, docs, security reports, and releases.

`status.json` uses the schema `fastmcp-maintenance/1`. Each automation carries `state` (`ok`, `idle`, `degraded`, or `off`), `cadence`, `last_ok_day`, and counts over a stated `window`. Compare `as_of` and `cadence` before calling something broken: an automation with nothing to do reads `idle`, not `degraded`.
