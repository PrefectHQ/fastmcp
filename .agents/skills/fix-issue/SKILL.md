---
name: fix-issue
description: Carry a selected FastMCP bug from reproduction through a scoped fix, compatibility review, validation, and a monitored pull request.
---

# Fix a selected issue

Read AGENTS.md and the contribution policy in [the development guide](../../../docs/development/contributing.mdx). Read the issue's full discussion, relevant history, and associated PRs in all states. Evaluate existing contributions before writing a competing fix; use [review-issue](../review-issue/SKILL.md) when assignment is the next decision. Preserve contributor authorship when carrying existing work forward.

## Establish the contract

Trace the public request to the failing operation. Confirm supported behavior is broken, not merely surprising. State the scope and compatibility boundary before implementation: which inputs must change, which adjacent behavior must remain, and any migration needed. If the intended contract is unresolved, bring that precise question to the maintainer.

## Red, green, review

Use [python-tests](../python-tests/SKILL.md). Reproduce the failure on unchanged code, confirm it fails for the right reason, then make the smallest causal fix. Test explicit overrides and neighboring supported paths that share the changed branch. Avoid expanding the fix to unrelated standards gaps.

Self-review the full change with [review-pr](../review-pr/SKILL.md), including docs and dependency bounds. Separate necessary compatibility changes from accidental regressions and say whether a breaking change remains. Do not infer compatibility from passing tests.

Run the repository's required dependency sync, full tests, and static checks before committing. Honor branch and attribution conventions. If a required check fails, investigate and report evidence rather than hiding the failure.

## Publish and follow through

Within existing authorization, update the existing PR or open a focused one, using draft status when appropriate. Follow repository PR-body conventions and the user's `pr-body` skill when available. Describe the actual user-visible change and any migration, not the development diary.

For a maintainer implementation of a community issue, apply [Contributor credit](../../../docs/development/contributing.mdx#contributor-credit) before committing and publishing. Read the issue author's GitHub profile to establish their account identity; prefer the GitHub-provided noreply address, and ask if identity is unclear. Add their `Co-authored-by: Name <email>` trailer to the implementation commit and at the end of the PR description, alongside existing contributor attribution. Reporting the problem is enough; do not require code or root-cause analysis. Honor opt-outs and do not credit bot accounts as human issue contributors.

Maintainers are generally exempt from supplemental credit checks. For maintainer reports and routine backports, preserve existing community attribution and proceed; do not add a maintainer merely because they opened the original PR or ask for guidance on that basis. A backport does not need a fresh credit audit.

Start [review-pr](../review-pr/SKILL.md) monitoring after publication. CI completion and review completion are distinct. Resolve real findings and report material compatibility decisions; a green check does not settle them. Do not merge or mark ready without authorization.
