---
name: review-pr
description: Follow an existing FastMCP PR through CI and automated review, evaluate feedback, and verify the current head before reporting readiness.
---

# Follow a PR

Read AGENTS.md for publication, review-thread, and merge rules. Track the PR's current head SHA. A push invalidates evidence tied only to an earlier head.

## CI and review are separate

After opening or updating a PR, establish a monitor using the host's scheduling capability. Keep it quiet while pending without a meaningful change. Report failures, requested decisions, and completion; pause it after the terminal report. If persistent monitoring is unavailable, watch during the active task and state that limitation rather than promising a future notification.

Fetch checks and reviews together:

```bash
gh pr view <number> --repo PrefectHQ/fastmcp \
  --json headRefOid,statusCheckRollup,reviews,comments,isDraft,labels

gh api --paginate repos/PrefectHQ/fastmcp/pulls/<number>/comments
```

Read relevant CodeRabbit, Copilot, Codex, and maintainer feedback, including inline threads and replies. Codex may update a summary issue comment instead of creating a formal review. Match its reported commit and completion status; zero formal reviews does not prove it has not run. Drafts may not trigger reviews. Do not mark ready or request reviews solely to satisfy a polling loop without authorization.

Green CI means checks passed, not that review finished. A generic request for human review is not a concrete defect; explain any actual unresolved compatibility decision. Distinguish substantive findings from status messages and stylistic notes.

## Act on evidence

Use [code-review](../code-review/SKILL.md) to assess findings. Fix real defects together, verify adjacent paths, run required checks, and push. Resolve a thread when its fix is verified. When declining a finding, reply with the reason if posting is authorized. Do not loop indefinitely on speculative follow-ups.

For CI failures, inspect the failed job's logs. Distinguish assertions, worker crashes, dependency resolution, and infrastructure failures. A test name or unrelated diff alone does not establish the cause. Retry when evidence supports a transient failure; recurring failures need diagnosis.

The final report names the head checked, CI outcome, outstanding review findings or decisions, and any pending work. Preserve draft status unless the user authorizes changing it. Merging is separate: recheck title, body, labels, head, and checks immediately before an authorized merge; obey all DNM markers and branch protections.
