---
name: review-pr
description: Assess a FastMCP pull request for justified behavior, compatibility, and correctness, then follow CI and review feedback to a revision-specific verdict. Use for draft or ready PRs and self-review before publication.
---

# Review a FastMCP PR

Review whether the change should ship, with evidence for its intended behavior as well as its implementation. Use the same procedure for maintainer, contributor, and agent drafts. For an unpublished change, record the base and local diff and skip GitHub-only steps. For an external contributor's assignment decision, start with [review-issue](../review-issue/SKILL.md).

For review-only tasks, assess and report; repair, push, and monitoring steps apply only when that follow-through is in scope. Automated reviewers use the same substantive checks and their required findings format. Do not wait for your own bot review or manage the PR lifecycle. If the review environment cannot run tests or delegate an adversarial pass, state that limit rather than claiming those checks passed.

## Procedure

1. **Establish the scope.** Read AGENTS.md, the contribution policy in [the development guide](../../../docs/development/contributing.mdx), the issue's full discussion, the entire diff against the merge base, and prior review threads and replies. Record the base and head revisions. Find related issues and PRs in all states, and inspect open PRs touching the same files for overlapping lines or behavior even when they do not link the issue. Distinguish released behavior, fixes already on main, and competing proposals. When proposals compete, record the issue reporter, original implementation author, maintainer direction, correctness, and distinct coverage. Recommend which proposal should survive and preserve carried-forward credit; submission order and green CI do not select the winner. Call a sound overlapping proposal redundant rather than spurious.

2. **Establish the contract before judging the fix.** Use the protocol, released docs, history, and maintainer decisions to explain what FastMCP promises for the reported inputs. Existing code and tests are evidence of behavior, not sufficient proof that it is intended. A reproducer can demonstrate surprising behavior without demonstrating a bug; a regression test can assert the wrong result. Classify the change as a contract-restoring bug fix, intentional behavior change, public API addition or modification, documentation, or tests; a PR can span categories. Name existing inputs whose outcomes change, including defaults and explicit overrides. A bug title does not establish the category. Decide whether the behavior and ongoing support belong in FastMCP before assessing implementation quality. If the contract is unresolved, surface the precise maintainer decision and do not recommend approval or readiness on the strength of passing tests.

   Maintainers own design opinions. An unresolved preference about scope, API shape, defaults, or framework policy is a maintainer decision, not a contributor defect. Bring the precise choice and your recommendation to the maintainer; do not submit a formal changes-requested review solely because that choice is unsettled, or ask the contributor to obtain or settle it. Once the maintainer chooses, assess the implementation against that direction. When the intended behavior is established and the correction is small, make the localized repair within authorization rather than submitting a changes-requested review. Reserve formal changes requests for a materially wrong approach or behavior that needs rethinking; explain the concrete behavioral correction after the maintainer settles the direction.

   For protocol-facing changes (wire fields, auth challenges, capabilities, errors, or cache semantics), cite the applicable specification revision and normative primary source. Distinguish what the protocol requires or permits, what the SDK implements or defaults, and what policy FastMCP chooses. SDK support alone does not settle framework policy; do not strengthen a protocol hint into a guarantee.

3. **Trace the cause and compatibility.** Read every changed file in context, its callers, producers and consumers, and shared abstractions. For a bug fix, state the causal ownership: the contract is X, the failure originates in Y, and this patch repairs Y. Check existing configuration, extension hooks, and shared abstractions before accepting new framework behavior. If the patch compensates elsewhere, explain why the owning layer cannot address the cause. Search for the same bug pattern and trace affected tools, resources, templates, and prompts; use canonical component identity. Compare omitted defaults, explicit overrides, errors, serialization, and supported configurations. State which existing inputs change behavior, why that is necessary, and any migration preserving the old behavior. Keep breaking changes out of a patch recommendation. Check API ergonomics, docs, and whether dependency minimums support newly used APIs; test the old minimum when compatibility is claimed and keep lockfile changes scoped.

4. **Verify behavior independently.** For bug fixes, use [python-tests](../python-tests/SKILL.md) to run the regression on the unchanged base and proposed head yourself. Confirm it fails for the claimed reason and asserts the intended value and type, not merely that a result exists. For other changes, verify the promised behavior with appropriate checks. Exercise neighboring supported paths sharing the changed branch. For shared dispatch, security boundaries, or behavior other components rely on, get an independent adversarial pass: give a fresh agent the PR and paths to investigate, have it run reproducers in a throwaway worktree, and retain confirmed findings. Repeat that pass after substantive rework. Record revisions and checks actually run; unavailable validation remains pending evidence.

5. **Evaluate feedback and resolve findings.** Fetch CI, review summaries, inline threads, and replies together using the commands below. Evaluate CodeRabbit, Copilot, Codex, and maintainer feedback on its merits. Collect all independent consequential findings in one pass, with reachable triggers and consequences; do not repeat resolved or convincingly rebutted findings without new evidence. Avoid cosmetic blockers and speculative scope expansion. Within authorization, maintainer repairs should be localized finishing touches on a contribution whose scope and design are accepted and which is nearly ready. A nearly ready fix that only needs small corrections to match established behavior should be finished by the maintainer, including targeted regression coverage. After the maintainer settles the direction, substantive API, protocol, behavior, or architectural rework remains the contributor's responsibility; explain that work rather than taking over, even when maintainer edits are enabled. For the small repairs, fix real defects together, verify adjacent behavior, run required checks, and push. Resolve verified fixes; reply with a reason when declining a finding if posting is authorized. Do not iterate indefinitely on hypothetical follow-ups.

6. **Follow the current revision to a verdict.** A push invalidates evidence tied only to the old head. After publication or an update within the task, establish quiet monitoring using the host's scheduling capability; notify only on meaningful changes, decisions, failures, or completion, and pause after the terminal report. For a one-time review, report pending checks without creating an ongoing monitor unless requested. If persistent monitoring is unavailable, watch during the active task and state the limitation. Inspect failed CI logs before diagnosing or retrying; distinguish assertions, worker crashes, dependency resolution, and infrastructure failures. Reproduce supposedly unrelated failures on the base. Report scope and design acceptance, implementation correctness, and remaining validation separately. An approval with pending CI can be appropriate when the user has authorized it and the scope, design, and code are accepted; approval does not mean merge readiness. Pending CI cannot resolve an unsettled design decision, and a process gate is not a code defect.

## GitHub evidence

```bash
gh pr view <number> --repo PrefectHQ/fastmcp \
  --json baseRefOid,headRefOid,title,body,statusCheckRollup,reviews,comments,isDraft,labels

gh api --paginate repos/PrefectHQ/fastmcp/pulls/<number>/comments
```

Read review threads and author replies, not just summary verdicts. Codex can update a summary issue comment without creating a formal review; match its reported revision and completion status. Zero formal reviews does not prove it has not run. Drafts may not trigger reviews. Do not mark ready or request reviews solely to satisfy a polling loop. A generic request for human review is not a concrete defect, and green CI does not establish review completion or settle a compatibility decision.

Preserve draft status unless the user authorizes changing it. Approval, publication, and merging remain subject to AGENTS.md and existing authorization. Immediately before an authorized merge, recheck the title, body, labels, head, checks, and branch protections; obey all draft and DNM stops.

For maintainer implementations of community issues, check [Contributor credit](../../../docs/development/contributing.mdx#contributor-credit) as part of readiness: the issue author's verified co-author trailer must be in the implementation commit and PR description. Missing community attribution is unfinished PR preparation. Maintainers are generally exempt: do not flag missing credit for another maintainer or ask for guidance about it. Routine backports preserve existing community attribution without a fresh credit audit. In review-only work, report an applicable omission rather than editing the PR.

Preserve attribution through whichever merge strategy is chosen. For a squash merge, write the exact final commit body to a temporary file, including the issue link and all human co-author trailers from the PR description and commits. Inspect it and pass it explicitly with `gh pr merge <number> --squash --body-file /tmp/merge-body.md --match-head-commit <reviewed-head>`, adding any already-authorized merge flags. Do not rely on GitHub's default squash message. For merge or rebase, verify the attributed commits will be retained. After merging, inspect the landed commit(s) and confirm the co-authors are present. These steps do not authorize a merge or prescribe its strategy.

## Verdict template

Keep the report proportional to the change, with these facts explicit:

> **Verdict:** ready for maintainer consideration / changes needed / maintainer decision needed / validation pending — at `<head>`, against `<base>`.
>
> **Scope and design:** change category, promised behavior and supporting source; whether it belongs in FastMCP and any precise maintainer decision still needed.
>
> **Implementation:** whether the patch repairs the causal layer, with consequential defects or competing-proposal selection.
>
> **Compatibility:** inputs whose behavior changes, adjacent behavior preserved, and any migration or release constraint.
>
> **Validation:** regression on base/head, neighboring checks, adversarial result or why it was not required, CI and bot status at this revision. Distinguish a code approval with pending CI from a merge-ready recommendation.
>
> **Outstanding:** consequential findings, decisions, and checks still pending.

## Check before finishing

Can another maintainer tell why this behavior is desirable, what existing behavior changes, and which claims were independently verified at the reported revision? If any answer is missing, qualify the verdict instead of calling the PR ready.
