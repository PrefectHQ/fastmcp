---
name: review-pr
description: Assess a FastMCP pull request for justified behavior, compatibility, and correctness, then follow CI and review feedback to a revision-specific verdict. Use for draft or ready PRs and self-review before publication.
---

# Review a FastMCP PR

Review whether the change should ship, with evidence for its intended behavior as well as its implementation. Use the same procedure for maintainer, contributor, and agent drafts. For an unpublished change, record the base and local diff and skip GitHub-only steps. For an external contributor's assignment decision, start with [review-issue](../review-issue/SKILL.md).

For review-only tasks, assess and report; repair, push, and monitoring steps apply only when that follow-through is in scope. Automated reviewers use the same substantive checks and their required findings format. Do not wait for your own bot review or manage the PR lifecycle. If the review environment cannot run tests or delegate an adversarial pass, state that limit rather than claiming those checks passed.

## Procedure

1. **Establish the scope.** Read AGENTS.md, CONTRIBUTING.md, the issue's full discussion, the entire diff against the merge base, and prior review threads and replies. Record the base and head revisions. Find related issues and PRs in all states, and inspect open PRs touching the same files for overlapping lines or behavior even when they do not link the issue. Distinguish released behavior, fixes already on main, and competing proposals.

2. **Establish the contract before judging the fix.** Use the protocol, released docs, history, and maintainer decisions to explain what FastMCP promises for the reported inputs. Existing code and tests are evidence of behavior, not sufficient proof that it is intended. A reproducer can demonstrate surprising behavior without demonstrating a bug; a regression test can assert the wrong result. State whether this restores an established contract or proposes a behavior change. If the contract is unresolved, surface the precise maintainer decision and do not recommend readiness on the strength of passing tests.

3. **Trace the cause and compatibility.** Read every changed file in context, its callers, producers and consumers, and shared abstractions. Check whether the fix changes the causal path or compensates elsewhere. Search for the same bug pattern and trace affected tools, resources, templates, and prompts; use canonical component identity. Compare omitted defaults, explicit overrides, errors, serialization, and supported configurations. State which existing inputs change behavior, why that is necessary, and any migration preserving the old behavior. Keep breaking changes out of a patch recommendation. Check API ergonomics, docs, and whether dependency minimums support newly used APIs; test the old minimum when compatibility is claimed and keep lockfile changes scoped.

4. **Verify behavior independently.** For bug fixes, use [python-tests](../python-tests/SKILL.md) to run the regression on the unchanged base and proposed head yourself. Confirm it fails for the claimed reason and asserts the intended value and type, not merely that a result exists. For other changes, verify the promised behavior with appropriate checks. Exercise neighboring supported paths sharing the changed branch. For shared dispatch, security boundaries, or behavior other components rely on, get an independent adversarial pass: give a fresh agent the PR and paths to investigate, have it run reproducers in a throwaway worktree, and retain confirmed findings. Repeat that pass after substantive rework. Record revisions and checks actually run; unavailable validation remains pending evidence.

5. **Evaluate feedback and resolve findings.** Fetch CI, review summaries, inline threads, and replies together using the commands below. Evaluate CodeRabbit, Copilot, Codex, and maintainer feedback on its merits. Collect all independent consequential findings in one pass, with reachable triggers and consequences; do not repeat resolved or convincingly rebutted findings without new evidence. Avoid cosmetic blockers and speculative scope expansion. Within authorization, fix real defects together, verify adjacent behavior, run required checks, and push. Resolve verified fixes; reply with a reason when declining a finding if posting is authorized. Do not iterate indefinitely on hypothetical follow-ups.

6. **Follow the current revision to a verdict.** A push invalidates evidence tied only to the old head. After publication or an update within the task, establish quiet monitoring using the host's scheduling capability; notify only on meaningful changes, decisions, failures, or completion, and pause after the terminal report. For a one-time review, report pending checks without creating an ongoing monitor unless requested. If persistent monitoring is unavailable, watch during the active task and state the limitation. Inspect failed CI logs before diagnosing or retrying; distinguish assertions, worker crashes, dependency resolution, and infrastructure failures. Reproduce supposedly unrelated failures on the base. Report remaining decisions separately from CI and bot status.

## GitHub evidence

```bash
gh pr view <number> --repo PrefectHQ/fastmcp \
  --json baseRefOid,headRefOid,title,body,statusCheckRollup,reviews,comments,isDraft,labels

gh api --paginate repos/PrefectHQ/fastmcp/pulls/<number>/comments
```

Read review threads and author replies, not just summary verdicts. Codex can update a summary issue comment without creating a formal review; match its reported revision and completion status. Zero formal reviews does not prove it has not run. Drafts may not trigger reviews. Do not mark ready or request reviews solely to satisfy a polling loop. A generic request for human review is not a concrete defect, and green CI does not establish review completion or settle a compatibility decision.

Preserve draft status unless the user authorizes changing it. Approval, publication, and merging remain subject to AGENTS.md and existing authorization. Immediately before an authorized merge, recheck the title, body, labels, head, checks, and branch protections; obey all draft and DNM stops.

## Verdict template

Keep the report proportional to the change, with these facts explicit:

> **Verdict:** ready for maintainer consideration / changes needed / maintainer decision needed / validation pending — at `<head>`, against `<base>`.
>
> **Contract:** promised behavior and supporting source; why this is a correction or a proposed change.
>
> **Compatibility:** inputs whose behavior changes, adjacent behavior preserved, and any migration or release constraint.
>
> **Evidence:** regression on base/head, neighboring checks, adversarial result or why it was not required, CI and bot status at this revision.
>
> **Outstanding:** consequential findings, decisions, and checks still pending.

## Check before finishing

Can another maintainer tell why this behavior is desirable, what existing behavior changes, and which claims were independently verified at the reported revision? If any answer is missing, qualify the verdict instead of calling the PR ready.
