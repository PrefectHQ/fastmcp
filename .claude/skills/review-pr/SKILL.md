---
name: review-pr
description: Monitor and respond to automated PR reviews (Codex bot). Use when pushing a PR, checking review status, or responding to bot feedback. Handles the full cycle of push -> wait for review -> evaluate comments -> fix -> re-push.
---

# PR Review Workflow

This repo has `chatgpt-codex-connector[bot]` configured as an automated reviewer. After every push to a PR branch, Codex reviews the diff and either:
- Reacts with a thumbs-up on its review body (no suggestions — PR is clean)
- Posts inline comments with suggestions (each tagged with a priority badge)

## Checking review status

After pushing, check whether Codex has reviewed the latest commit:

```bash
# Get the latest commit SHA on the branch
LATEST=$(git rev-parse HEAD)

# Check if Codex has reviewed that specific commit
gh api repos/PrefectHQ/fastmcp/pulls/{PR_NUMBER}/reviews \
  | jq "[.[] | select(.user.login == \"chatgpt-codex-connector[bot]\" and .commit_id == \"$LATEST\")] | length"
```

If the count is 0, Codex hasn't reviewed the latest push yet. Wait and check again.

If the count is > 0, check for inline comments on the latest review:

```bash
# Get the review body to check for thumbs-up
gh api repos/PrefectHQ/fastmcp/pulls/{PR_NUMBER}/reviews \
  | jq '[.[] | select(.user.login == "chatgpt-codex-connector[bot]") | {state, body: .body[:300], commit_id: .commit_id}] | last'
```

A clean review from Codex looks like a review body that contains a thumbs-up reaction or says "no suggestions." If the body contains "Here are some automated review suggestions," there are inline comments to evaluate.

## Evaluating Codex comments

Fetch all inline comments from Codex:

```bash
gh api repos/PrefectHQ/fastmcp/pulls/{PR_NUMBER}/comments \
  | jq '[.[] | select(.user.login == "chatgpt-codex-connector[bot]") | {body, path, line, created_at}]'
```

Codex comments include priority badges:
- `P0` (red) — Critical issue, likely a real bug
- `P1` (orange) — Important, worth fixing
- `P2` (yellow) — Moderate, evaluate on merit

**How to evaluate Codex comments:**

1. **Treat Codex as a competent but sometimes overzealous reviewer.** It catches real bugs (cache eviction ordering, silent data loss, missing validation) but also suggests scope expansions and hypothetical improvements.

2. **Fix real bugs** — issues in code you actually changed where behavior is incorrect or data is silently lost.

3. **Dismiss scope expansion** — if a comment points out a pre-existing limitation unrelated to your diff, note it as a potential follow-up but don't block the PR.

4. **Dismiss speculative concerns** — if a comment describes a scenario that requires very specific conditions and the existing behavior is acceptable, dismiss it.

5. **When fixing, be proactive** — if Codex found one instance of a pattern bug (e.g., missing role validation in one handler), check all similar code paths before pushing. Codex will find the next instance on the next review cycle, so get ahead of it.

## Responding to every comment

**Every Codex comment must get a visible response** — either a fix or a reply explaining why it was dismissed. The maintainer can't see your reasoning otherwise.

- **If fixing**: The fix itself is the response. No reply needed unless the fix is non-obvious.
- **If dismissing**: Reply to the comment thread with a brief explanation of why. Keep it to 1-2 sentences. Examples:
  - "This is pre-existing behavior unrelated to this diff — the scope lookup fallback existed before caching was added. Worth a follow-up issue but not blocking this PR."
  - "The AsyncExitStack handles cleanup when the session exits, so the subprocess isn't leaked — just kept alive slightly longer than necessary in this edge case."
  - "Gemini supports a much wider range of media types than OpenAI/Anthropic, so a restrictive allowlist would be inaccurate here."

Use `gh api` to reply (note: use `in_reply_to`, not a `/replies` sub-path):

```bash
# Reply to a specific review comment
gh api repos/PrefectHQ/fastmcp/pulls/{PR_NUMBER}/comments \
  -f body="Your reply here" \
  -F in_reply_to={COMMENT_ID}
```

## The fix-push-review cycle

After evaluating comments:

1. Fix all real issues in one batch
2. Reply to all dismissed comments with reasoning
3. Think about what patterns Codex might flag next — check similar code paths proactively
4. Commit and push
5. Check that Codex reviews the new commit
6. Repeat until Codex gives a clean review (thumbs-up) or only has dismissible comments

## Responding to stale comments

Codex sometimes re-posts old comments that reference code you've already fixed (they appear on the old commit's diff). These are stale — verify the fix is in the latest commit and reply noting the fix is already in place.

## When a PR is ready

A PR is ready for human review when:
- All Codex comments are either fixed or replied to with dismissal reasoning
- CI checks pass
- The diff is clean and focused on the stated purpose
