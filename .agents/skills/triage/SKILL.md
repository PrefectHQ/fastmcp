---
name: triage
description: Find worthwhile FastMCP issues to work on in a backlog or release window, verify promising candidates, and present concrete picks with existing PR links.
---

# Find worthwhile work

Read AGENTS.md and CONTRIBUTING.md. Honor the requested scope. For "since the last patch," identify the release timestamp, then look for open issues created or substantively updated afterward. Use a bounded inventory internally; report truncation if it affects coverage.

Pick work with concrete user impact, a supported contract being violated, and a tractable causal fix. Do not rank solely by recency, comment count, or how small a proposed patch looks. Automated dashboard churn is not itself useful work.

For promising candidates:

- Read the report, comments, maintainer decisions, and prior bot investigation.
- Search related issues and PRs in all states, including gate-closed contributions. Closure alone says nothing about a fix's merit.
- Trace the relevant code and git history. Distinguish fixed on main, released on the affected line, and merely proposed.
- Verify the claimed contract and enough evidence to explain the cause. A proposed solution is a lead, not a diagnosis. Consider compatibility before calling a fix straightforward.

Lead with the strongest pick as soon as evidence supports it: link, what breaks, why it matters, where the cause is, and the next action. Include a few alternatives when useful. Do not turn selection into an exhaustive audit or a full review of every linked PR. Save continuation notes only when needed.

When the user chooses implementation, use [fix-issue](../fix-issue/SKILL.md). For an assignment decision on an external contribution, use [review-issue](../review-issue/SKILL.md). Triage alone does not authorize public mutations or implementation.
