---
name: pr-queue
description: Work down the open pull request queue. Use when the maintainer wants fewer open PRs ("get us from 50 to 40", "clear the queue"), a batch of merge and close recommendations, or duplicate and stale PRs found. Recommends only; every merge, close, and comment waits for the maintainer's approval.
---

# Working down the PR queue

Read AGENTS.md and CONTRIBUTING.md first; CONTRIBUTING defines what gets closed without review.

1. **Snapshot every open PR** with the fields that decide its fate:

   ```bash
   gh pr list -R PrefectHQ/fastmcp --state open --limit 200 \
     --json number,title,author,isDraft,createdAt,updatedAt,additions,deletions,reviewDecision,mergeable,labels,statusCheckRollup,closingIssuesReferences
   ```

   Note which failing checks are policy (`check-issue-link`, the labeler refusing a bot) and which fork PRs never ran tests at all (`action_required`, waiting for maintainer approval). A green rollup on a fork PR can mean nothing ran.

2. **Group before judging.** PRs that close the same issue are one decision: the issue reporter's PR has first claim. A maintainer PR that carries a contributor's commits supersedes theirs. Filter out `[DNM]` and draft markers up front.

3. **Review each group** on correctness, spec conformance, and downstream impact (see AGENTS.md, Downstream consumers). Run the PR's tests in your own worktree; never stash. Rebase-free checks: confirm main has not touched the PR's files since its CI base, and merge it onto current main locally for the affected test areas before calling it ready.

4. **Give each PR one verdict:** merge, merge after a small fix, needs changes, close as duplicate, close as superseded (name the PR or commit), close as stale, close as out of scope (cite CONTRIBUTING), or punt as a product decision. Question the premise, not only the diff (AGENTS.md, Premise and blast radius).

5. **Present batches for approval.** Lead with the count this batch removes from the queue, then closes with drafted comments, green merges, and merges blocked on fork CI. Drafted close comments credit the contributor, give the reason in one sentence, and read the thread first so they do not repeat what the author already said.

Only after the maintainer approves a batch: close with the approved comment, approve fork workflow runs, and merge. A review verdict from a single agent is a lead; verify it yourself before recommending a merge.
