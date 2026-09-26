---
name: review-issue
description: Review an incoming external issue (and any gated-closed PR behind it) and decide whether to assign the contributor or decline. Use when the maintainer says "look at this issue", "review issue #N", "should we take this", or asks whether to assign someone. Assigning the author auto-reopens their PR for normal review. This is the entry point for incoming-issue triage — distinct from review-pr, which responds to bot reviews on your own open PR.
---

# Review an incoming issue

This skill ends in one of two recommendations for the maintainer:

- **Assign**: the issue is valid, an external PR suits it, and a sound PR exists. Assign the author, which clears the gate, then review the code.
- **Decline**: close the open contribution, or leave a closed one closed, and explain why on the issue once authorized.

Assignment commits us to reviewing the PR, not to merging it. A gate closure is administrative, so a closed PR gets the same bar as an open one.

## How the gate works

[require-issue-link.yml](../../../.github/workflows/require-issue-link.yml) checks every external PR:

- It needs `Fixes/Closes/Resolves #N`. PRs without a valid link are closed.
- A linked PR stays open with a failing check until its author is assigned to that issue. Issues labeled `prs welcome` waive assignment but still need the link.
- Assigning the author (`gh issue edit N --add-assignee <login>`) re-runs the check and reopens a gate-closed PR. If that run fails, the gate misbehaved: investigate the run rather than re-assigning.
- Maintainer PRs are exempt, and so are authors with the `trusted-contributor` label. Reopening a PR by hand or removing `missing-issue-link` applies a sticky `bypass-issue-check`, so prefer assignment.
- `marvin-triage-issue`, `marvin-dedupe-issues`, `auto-close-duplicates`, and `auto-close-needs-mre` have usually commented already. Read them as leads.

## Procedure

1. **Orient.** Read the issue, its bot comments, and every PR that references it, in all states:

   ```bash
   gh issue view N --repo PrefectHQ/fastmcp \
     --json number,title,state,author,body,labels,assignees,comments
   gh pr list --repo PrefectHQ/fastmcp --state all --search "#N in:body" \
     --json number,title,state,author,url,labels
   gh pr view <pr> --repo PrefectHQ/fastmcp --json title,body,labels,files,additions,deletions
   gh pr view <pr> --repo PrefectHQ/fastmcp --comments
   ```

2. **Decide whether the issue describes a bug.** Reproduce the MRE, then ask whether the behavior violates a contract FastMCP intends to hold. Behavior that only appears by mutating construction-time state or relying on internals is a property of the code, not a defect. The code shows what FastMCP does, not what it promises, so ask the maintainer "is X supported?" when the contract is unclear. Check `main`, duplicates, and prior maintainer decisions in related issues and closed PRs. If the issue is not a bug, recommend declining without reviewing the PR.

3. **Check the category against CONTRIBUTING.md.** Simple bug fixes, docs, and auth providers are assignable. Enhancements need a maintainer-approved design in the issue first; approve the approach, then assign. Third-party integrations and sweeping changes without discussion are declined.

4. **Investigate the PR in context.** Read the full diff, then open each touched file. Trace the values and functions it changes to where they are produced and consumed. From the MRE, state in one line what was broken, where, and whether this change fixes it there. Compare with how adjacent code handles the same case, and check that the tests fail without the fix. Treat style issues as review comments; a wrong layer, a broken adjacent path, or an unfixed MRE changes the verdict.

5. **Check the contributor.** For an unfamiliar account, look at its public history:

   ```bash
   gh api users/<login>
   gh search prs --author <login> --limit 20 --sort created --order desc \
     --json repository,title,state,createdAt,url
   ```

   Merged fixes elsewhere and substantive replies to reviewers show follow-through. Decline obvious spam or unattended automation: mass unrelated boilerplate, repeated nonresponsive replies. A new account, a sparse profile, or disclosed AI assistance is not a reason to decline. When evidence is thin, lean toward goodwill for a sound, scoped contribution and mention the uncertainty.

6. **Recommend.** Give the maintainer the verdict, one or two sentences of reasoning, and the exact command. Act within existing authorization and bring borderline calls back.

## Acting on the verdict

**Assign:**

```bash
gh issue edit N --repo PrefectHQ/fastmcp --add-assignee <login>
```

Confirm the `require-issue-link` run passes and the PR reopens, then review it with [code-review](../code-review/SKILL.md) and follow it with [review-pr](../review-pr/SKILL.md). If the PR's head branch was deleted, assignment cannot reopen it; the workflow asks the author for a fresh PR.

**Decline:** with authorization, comment on the issue with the reason and the relevant CONTRIBUTING.md section. Write the body to a file first:

```bash
gh issue comment N --repo PrefectHQ/fastmcp --body-file /tmp/triage-reply.md
```

## Check before finishing

The recommendation names the contract the issue does or does not violate, the layer where the cause lives, and whether the PR fixes it there.
