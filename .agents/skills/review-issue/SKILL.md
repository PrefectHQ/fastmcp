---
name: review-issue
description: Review an incoming external issue (and any gated-closed PR behind it) and decide whether to assign the contributor or decline. Use when the maintainer says "look at this issue", "review issue #N", "should we take this", or asks whether to assign someone. Assigning the author auto-reopens their PR for normal review. This is the entry point for incoming-issue triage — distinct from review-pr, which responds to bot reviews on your own open PR.
---

# Triaging contributions under the issue-link gate

FastMCP gates external PRs on a valid issue link and **assignment to a referenced issue**
(see [require-issue-link.yml](../../../.github/workflows/require-issue-link.yml)). Linked PRs
stay open with a failing check while maintainers decide on assignment. PRs without a valid
issue link are closed. **The issue is the decision point**: assignment re-runs the check
and reopens previously gate-closed PRs. Inspect both open and closed contributions.

This skill turns "look at this issue" into one of two outcomes:
- **Assign** — the issue is valid, we want it fixed, an external PR is appropriate, and a sound
  PR already exists → assign the author, clear the gate, and queue it for code review.
- **Decline** — recommend closing an open contribution or leaving a closed one closed,
  and explain why on the issue once authorized.

Assignment is a commitment to review, not a promise to merge. Evaluate the contribution and
the contributor with goodwill. An automatic gate closure is administrative, not a rejection
on the merits; it must not raise the bar for an otherwise sound contribution.

## How the gate works (the part that matters here)

- External PRs need `Fixes/Closes/Resolves #N` and assignment to that issue. Missing links
  cause closure; missing assignment alone leaves the PR open with a failing check. Issues
  marked `prs welcome` waive assignment, but still require the link.
- **Assigning the author to the issue auto-reopens their closed PR** and re-runs the check —
  this is the lever you pull. `gh issue edit N --add-assignee <login>`. The assignment fires a
  `require-issue-link` run; expect it to pass. If it fails, the gate itself misbehaved (not the
  PR) — investigate the run, don't re-assign.
- Maintainer-authored PRs are exempt. A `trusted-contributor` label exempts a contributor up
  front. Reopening the PR or removing the `missing-issue-link` label applies a sticky
  `bypass-issue-check`.
- Sibling bots have usually already run on the issue: `marvin-triage-issue` (investigates +
  recommends), `marvin-dedupe-issues` / `auto-close-duplicates` (dupes), `auto-close-needs-mre`
  (missing MRE). Read their comments before re-deriving anything.

## Step 1 — Orient

Read the issue, its bot triage, and any PR behind it. Run these together:

```bash
gh issue view N --repo PrefectHQ/fastmcp \
  --json number,title,state,author,body,labels,assignees,comments
# Find PRs the author opened that reference this issue (they're likely CLOSED):
gh pr list --repo PrefectHQ/fastmcp --state all --search "author:<login> #N in:body" \
  --json number,title,state,url,labels
```

If a PR exists, pull its metadata and any review-bot comments (CodeRabbit, Codex). Treat the bot
comments as leads, not conclusions — they often don't run on closed PRs at all, and even when
they do you still owe the PR your own read:

```bash
gh pr view <pr> --repo PrefectHQ/fastmcp --json number,title,body,labels,files,additions,deletions
gh pr view <pr> --repo PrefectHQ/fastmcp --comments
```

### Check the contributor before assignment

Make a brief public-account check before assigning an unfamiliar contributor. Look at account
age, a sample of contributions elsewhere, and how they respond to review. Merged fixes and
substantive exchanges with maintainers are useful evidence of follow-through. GitHub's `User`
account type does not establish that a human operates the account.

```bash
gh api users/<login>
gh search prs --author <login> --limit 20 --sort created --order desc \
  --json repository,title,state,createdAt,url
```

Avoid assigning obvious spam or unattended bot accounts: look for concrete patterns such as
mass unrelated boilerplate, repeated nonresponsive replies, or explicit unattended automation.
A new account, sparse profile, low follower count, or disclosed AI assistance alone is not a
reason to decline. Do not demand identity proof or infer legitimacy from profile claims alone.
When evidence is limited, say so and lean toward goodwill for a sound, scoped contribution;
bring a material concern to the maintainer before assigning.

## Step 2 — Classify the issue (is it valid AND a real bug?)

- Is there a real, reproducible problem? For bugs, demand an MRE that shows FastMCP misbehaving
  — not user config error, not a question, not an upstream-SDK issue.
- Is it a duplicate or already fixed on `main`? Check the dedupe bot's comment and recent commits.
- Search earlier issues, PRs (including closed ones), and relevant git history before deciding.
  Read maintainer decisions and why prior approaches were accepted or rejected.
- If the issue itself is weak, **stop here and decline** — don't evaluate the PR. A good PR
  attached to a bad issue is still declined.

**A reproducible MRE is not the same as a bug.** This is the trap that produces wrong verdicts:
an MRE can demonstrate real, observable behavior that is nonetheless *not a bug*, because it
violates no contract the framework intends to hold. The decisive question is not "does this
reproduce?" but "does the demonstrated behavior violate the intended contract for this API?" A
shared-mutable-state MRE only matters if callers are *supposed* to mutate that state; an
ordering/timing MRE only matters if the framework promises an order; a "wrong" value only matters
relative to what the API guarantees. An MRE that has to reach past the supported surface to
trigger the behavior (mutating a field meant to be set only at construction, depending on an
internal that isn't part of the public contract) is showing you a property, not a defect.

You usually cannot read the intended contract off the code — the code shows what it *does*, not
what it *promises*. **The maintainer is often the only authoritative source for the contract, so
stopping to ask is legitimate and expected here.** Ask "is X a supported pattern / does this API
promise Y?" before sinking time into investigating a fix. If the behavior is in-contract correct,
decline — no matter how cleanly the PR fixes it, and no matter how real the MRE looks.

## Step 3 — Investigate the PR (mandatory; do NOT skip if a PR exists)

The most common failure of this skill is judging a PR from the diff hunk and the PR description
alone. That is a cursory review and it produces wrong verdicts — a redundant-looking conditional
can be a real bug fix; a tidy-looking diff can patch the wrong layer. **You cannot assess a PR
without reading the code it changes in context.** Reading `gh pr diff` is necessary but never
sufficient.

Do all of this before forming any opinion on quality:

1. **Read the diff in full**, then **open every file it touches in the repo** (`Read`, not just
   the patch). The hunk shows *what changed*; the file shows *what it changed into*.
2. **Trace the functions and values the change depends on.** Grep for the called functions,
   the fields being set, and the defaults. If the PR overrides or replaces a value, find what
   produced the original value and what consumes it downstream.
3. **Establish the actual root cause from the issue's MRE**, then check whether the change fixes
   *that* — at the layer where the bug originates, not a compensating patch elsewhere.
4. **Check consistency with adjacent code.** Does the new value/behavior match how nearby code
   already handles the same case? An inconsistency is a real finding; a match is evidence the fix
   is correct.
5. **Run or read the tests** the PR adds/changes — do they actually exercise the bug, and would
   they fail without the fix?

Write down, for yourself, a one-line answer to: *what was broken, where, and does this change fix
it there?* If you can't answer from evidence you've actually read, you haven't investigated yet.

Then separate findings by severity: a **cosmetic** nit (style, a redundant-but-harmless line) is a
review comment, not a blocker. A **substantive** defect (wrong layer, breaks an adjacent path,
doesn't actually fix the MRE) changes the verdict. Don't let a cosmetic nit read as a reason to
decline, and don't let a clean style read as evidence of correctness.

## Step 4 — Decide if an external PR is appropriate (CONTRIBUTING.md)

This is the gate CONTRIBUTING.md actually enforces. Map the change to a category:

- **Simple, well-scoped bug fix** → external PR welcome. Assignable.
- **Docs / typo / example fix** → welcome. Assignable.
- **Auth provider** → assignable (auth is the one integration exception).
- **Enhancement / feature** → needs a maintainer-approved design proposal *in the issue first*.
  Do **not** assign just because code exists. If the proposal is sound, the path is "approve the
  approach in the issue, then assign" — not "assign because they were fast."
- **Third-party integration** (middleware, provider adapters, non-auth) → decline; belongs in a
  separate package.
- **Sweeping / multi-subsystem change with no prior discussion** → decline.

Combine the category with the Step 3 investigation: does it fix the cause or paper over a symptom?
Does it read like unedited LLM output (verbose body, speculative/shotgun changes)? CONTRIBUTING.md
says we close those — a closed PR that reads that way is staying closed.

## Step 5 — Recommend, then act

Present a short verdict to the maintainer before mutating anything: **assign** or **decline**,
one or two sentences of reasoning, and the exact command you'll run. Act within the maintainer's
existing authorization; do not ask again for an action already approved. Bring borderline
calls back to the maintainer, and obtain approval for public actions not yet authorized.

**Assign** (valid issue + appropriate external contribution + sound PR exists):

```bash
gh issue edit N --repo PrefectHQ/fastmcp --add-assignee <login>
```

Verify that the assignment workflow clears the check and reopens the PR if it was closed.
If it does not, inspect the run and the PR timeline. When reopening is authorized and the gate caused the closure, reopen the
existing PR; do not override a substantive maintainer closure. A manual reopen applies the
workflow's sticky bypass, so prefer the normal assignment path and do not manipulate labels.

Then hand off to code review — invoke the `code-review` /
`review-pr` skills on the reopened PR. Assignment is not approval; the code still gets the normal
pass.

If a PR's head branch was deleted, assignment can't reopen it — the workflow comments asking the
author to open a fresh PR. Don't try to force it.

**Decline** (invalid issue, wrong contribution type, or low-quality PR): with authorization,
close the open contribution or leave it closed and comment on the **issue** explaining the decision, pointing to the relevant CONTRIBUTING.md
section. Per repo rules, use `--body-file`, never inline `--body`, for any comment that could
contain `$`, backticks, or code:

```bash
gh issue comment N --repo PrefectHQ/fastmcp --body-file /tmp/triage-reply.md
```

Keep the reply short and point to the relevant CONTRIBUTING.md section. (If a `github-reply`
skill is available for maintainer voice/tone, use it — but it isn't required.)

## What this skill does NOT do

- It doesn't grant blanket trust via `trusted-contributor` or manually apply bypass labels.
  An explicitly authorized reopen of a gate-closed PR is scoped to that PR.
- It doesn't merge. Assignment → reopen → review → (maybe) merge are distinct steps.
- It doesn't re-run the first-pass triage the bots already did; read their output instead.
