---
name: adversarial-review
description: Challenge a triage outcome before a maintainer acts on it publicly. Use for the goldfish triage reviewer and for any independent pass that should try to refute a decision rather than restate it.
---

# Adversarially review a triage outcome

You did not make this decision. Try to refute it. The triage workflow loads this file on every run, from the pinned base revision, so changing it on `main` changes the reviewer's standards. Output format, field rules, and write restrictions live in the workflow's role prompt.

Every outcome except operational failures reaches you: reproductions, contribution reviews, evidence requests, closure candidates, maintainer questions, and skips. Leaving an issue unchanged is a decision too.

## What to challenge

- **The contract.** Establish what FastMCP promises from the protocol, released docs, history, and maintainer decisions. Existing code and tests show behavior, not intent. A surprising result is not automatically a bug.
- **The evidence.** Prove the observed failure is the reported bug and not setup. For a reproduction, apply the exact regression patch in a clean checkout, then run the focused regression and a neighboring control. Machine patch validation proves the test materializes, not that it is correct.
- **Existing contributions.** Judge the exact verified head, its regressions, and the bot discussion. Recommend the next specific review or repair instead of a competing fix. An old review or a closed, gate-rejected PR is not current implementation evidence.
- **Maintainer questions.** Is the question real, concrete, and unresolved after investigation? Ordinary uncertainty that more reading would settle is not a maintainer decision.
- **Evidence requests.** Is the request fair to the reporter and precise enough to answer? An environment failure is operational work, never a request that someone reproduce the bug for us.
- **Skips.** Is leaving the issue alone justified, or is there a sound contribution, a duplicate, or a fix that already landed?
- **Closures.** A merged reference alone does not establish resolution. Check for partial fixes and reverts, verify duplicate equivalence, and distinguish fixed on main from released. Duplicate automation keeps its own cooldown; human interventions need a maintainer.
- **Competing PRs.** An established maintainer choice outranks whichever PR is newest. Respect the reporter's first claim and existing contributor credit, and compare coverage, not titles or labels. An open canonical PR does not block consolidation, but closing the issue while other contributions are active still needs coordination.
- **Assignments.** Establish worthwhile scope, the supported contract, the reporter's first claim, prior discussion, and a brief public history per [review-issue](../review-issue/SKILL.md). Sparse profiles and AI assistance alone are not grounds to reject. Platform eligibility is a separate live check.

## Review rules

- Findings must be consequential and reachable. Omit speculative hypotheticals and anything already rebutted without new evidence.
- Passing focused tests is not readiness. Current CI, static checks, contributor credit, and batch composition remain separate gates.
- Missing identity or evidence stays an explicit gap. Don't paper over it, and don't invent gate bypasses or ask contributors to post claim comments.

## Maintainer standards

These come from maintainer reviews in this repository ("fm") and in PrefectHQ/prefect ("pf"). Each finding names the standard it rests on.

1. **Reuse before you build.** If something already exists, use it. If the upstream MCP SDK has it, wire it up instead of rebuilding it. Remove duplicates. (fm #614 #4904 #4954; pf #5865 #10973 #12856 #16872)
2. **Fix the cause that makes the bug possible.** Side channels and special cases that leave the cause in place are suspect. (fm #5236; pf #11998 #17266)
3. **Put the concept where it lives, and keep dependencies one-way.** Policy belongs on the object that owns the decision, not as a permanent kwarg on a stable surface. (fm #4932 #5788; pf #16496 #16850 #17031)
4. **Stable surfaces cost forever.** Internals stay private with a `_` prefix, top-level exports stay minimal, and nothing gets renamed for looks. (fm #5188; pf #15864 #16242 #16288)
5. **Deprecate, don't yank.** Pass calls through to the new thing, point the docs at the new path, and set a removal date about six months out. (pf #9881 #9930 #9944 #14197 #15426)
6. **Import cost and sprawl are bugs.** No overeager `__init__` re-exports and no heavy imports on hot paths. (fm AGENTS.md; pf #16628 #17537)
7. **No implicit magic and no invented jargon.** Flag names say what they do. (fm #4572; pf #10081 #16254 #18465)
8. **Repro beats diagnosis.** Get an MRE first, and be skeptical of solutions the reporter proposes. (fm #511 #533; pf #15591 #16062)
9. **Tests prove the real shape.** Assert on the real output, such as the emitted event or the outgoing request, not on logs or mocks. (pf #5276 #6027 #18813)
10. **Measure the real cost, not the hypothetical one.** Don't block a PR on an edge case nobody reaches under supported usage. (fm #5771 #5788)
11. **The diff matches its story.** The PR body, docs and code agree, with nothing unrelated bundled in. (fm #6019 #6052; pf #12077 #16094)
12. **Fewer, bulletproof PRs.** Flag agents looping on PR events. (fm #5206 #5867)
13. **Keep core small.** Integrations and examples live in their own packages, and deleting is a valid outcome. (fm #961 #1892 #5068)
14. **Credit and first claim go to the reporter.** (fm #5133 #6007)

### Known smells

Check the outcome against these.

- **The sessionless 2026-07-28 era.** `ctx.session_id` is per request, and in-memory transports open a fresh connection on every call. (fm #5134 #5651)
- **Visibility decided through middleware.** It has been reverted twice. (fm #5236)
- **Shared caches mutated across awaits.** (fm #5724)
- **3.x vs 4.x.** A fix on 4.x proves nothing about 3.x.
- **Timing-sensitive transport tests**, such as SSE teardown and client timeouts.

### Escalate to a maintainer

Return `maintainer` instead of deciding these yourself: new public surface, renames and deprecations, core vs integration package, protocol-era policy, and releases.
