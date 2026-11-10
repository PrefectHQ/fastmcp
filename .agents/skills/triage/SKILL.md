---
name: triage
description: Find worthwhile FastMCP issues to work on in a backlog or release window, verify promising candidates, and present concrete picks with existing PR links.
---

# Find worthwhile work

Read AGENTS.md and the contribution policy in [the development guide](../../../docs/development/contributing.mdx). Honor the requested scope. For "since the last patch," identify the release timestamp, then look for open issues created or substantively updated afterward. Use a bounded inventory internally; report truncation if it affects coverage.

Start from an inventory, then link each candidate to the PRs that mention it:

```bash
gh release list --repo PrefectHQ/fastmcp --limit 1
gh issue list --repo PrefectHQ/fastmcp --state open --limit 30 \
  --json number,title,author,createdAt,updatedAt,labels,comments
gh pr list --repo PrefectHQ/fastmcp --state all --search "<issue-number> in:body" \
  --json number,title,state,author
```

Where `search`, GraphQL or `--paginate` are blocked (cloud sessions), page repo-scoped REST by hand (`gh api "repos/PrefectHQ/fastmcp/pulls?state=all&per_page=100&page=N"`), dump it once, and join locally.

Pick work with concrete user impact, a supported contract being violated, a tractable causal fix, and a change FastMCP wants to carry. Apply [triage and merge decisions](../../../docs/development/contributing.mdx#triage-and-merge-decisions) before ranking anything: labels and confident diagnoses are claims, and the behavior may already be fixed or decided. Mark each pick want / marginal / don't want. Do not rank solely by recency, comment count, or how small a proposed patch looks. Automated dashboard churn is not itself useful work.

For promising candidates:

- Read the report, comments, maintainer decisions, and prior bot investigation.
- Search related issues and PRs in all states, including gate-closed contributions. Closure alone says nothing about a fix's merit.
- Trace the relevant code and git history. Distinguish fixed on main, released on the affected line, and merely proposed.
- Verify the claimed contract and enough evidence to explain the cause. A proposed solution is a lead, not a diagnosis. Consider compatibility before calling a fix straightforward.

For a cleanup pass, the mechanical wins are open PRs whose linked issue is already closed, issues still open after a merged "fixes #N" (check for a revert first), and competing PRs for one issue.

Lead with the strongest pick as soon as evidence supports it: link, what breaks, why it matters, where the cause is, and the next action. Include a few alternatives when useful. Do not turn selection into an exhaustive audit or a full review of every linked PR. Save continuation notes only when needed.

When the user chooses implementation, use [fix-issue](../fix-issue/SKILL.md). For an assignment decision on an external contribution, use [review-issue](../review-issue/SKILL.md). Triage alone does not authorize public mutations or implementation.

For a batch of maintainer decisions, save a self-contained proposal naming each target, exact action or patch, pinned revisions, evidence and executed checks, remaining questions, and dependencies between items. Include contributor ownership and the canonical issue or PR for proposed closures. Use [upstream EGM in the maintainer session](../../../.github/agentic/README.md#maintainer-review-with-egm) when independent batch review is requested. A Cloud node returns its assigned stage's declared output; the maintainer-facing session assembles the proposal and obtains sign-off.
