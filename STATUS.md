# FastMCP maintenance status

As of 2026-09-26T08:37:26Z. Generated twice a day by [maintenance-status](https://github.com/PrefectHQ/fastmcp/actions/workflows/maintenance-status.yml); machine-readable as [status.json](status.json). How the project is run: [MAINTAINING.md](https://github.com/PrefectHQ/fastmcp/blob/main/MAINTAINING.md).

| automation | state | last ok | runs on | cadence |
|---|---|---|---|---|
| [issue-link gate](https://github.com/PrefectHQ/fastmcp/actions/workflows/require-issue-link.yml) | ok | 2026-09-26 | github-actions | on each external PR change and each issue assignment |
| [labeling](https://github.com/PrefectHQ/fastmcp/actions/workflows/marvin-label-triage.yml) | ok | 2026-09-26 | github-actions | on each new issue or PR |
| [duplicate detection](https://github.com/PrefectHQ/fastmcp/actions/workflows/marvin-dedupe-issues.yml) | ok | 2026-09-25 | github-actions | on each new issue |
| [auto-close](https://github.com/PrefectHQ/fastmcp/actions/workflows/auto-close-duplicates.yml) | ok | 2026-09-25 | github-actions | daily |
| [bug investigation](https://github.com/PrefectHQ/fastmcp/actions/workflows/marvin-triage-issue.yml) | idle | — | github-actions | on each qualifying new issue |
| [CI failure analysis](https://github.com/PrefectHQ/fastmcp/actions/workflows/marvin-test-failure.yml) | ok | 2026-09-26 | github-actions | after each failed PR run |
| [maintainer commands](https://github.com/PrefectHQ/fastmcp/actions/workflows/marvin-comment-on-issue.yml) | ok | 2026-09-26 | github-actions | on a maintainer's comment |
| [upgrade checks](https://github.com/PrefectHQ/fastmcp/actions/workflows/run-upgrade-checks.yml) | ok | 2026-09-26 | github-actions | nightly |
| [release publishing](https://github.com/PrefectHQ/fastmcp/actions/workflows/publish-fastmcp-slim.yml) | ok | 2026-09-25 | github-actions | on each release |
| [docs deploy](https://github.com/PrefectHQ/fastmcp/actions/workflows/deploy-docs.yml) | ok | 2026-09-25 | github-actions | on each docs publication |
| [contributor queue](https://github.com/PrefectHQ/fastmcp/pulls?q=is%3Apr+is%3Aopen+label%3Amissing-issue-link) | ok | 2026-09-26 | github-actions | twice daily |

**Contributor queue:** 33 PRs waiting on assignment; the oldest has waited 26 days, and 14 have waited more than a week.

**Needs a maintainer's judgment:**

- whether to assign an external contributor, which reopens their gated PR
- merging, and marking agent-opened draft PRs ready
- changes to documented behavior or compatibility, and any API addition
- cutting a release, and its title and notes
- classifying and disclosing security reports

States: `ok` last run succeeded; `idle` nothing to do in the window; `degraded` last run failed; `off` disabled.
