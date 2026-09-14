---
name: code-review
description: Review a complete FastMCP change for correctness, supported behavior, compatibility, and maintainability. Use for self-review or reviewing a pull request.
---

# Review FastMCP code

Read AGENTS.md, CONTRIBUTING.md, the entire diff against the merge base, and prior review threads and replies. Trace the changed code in context, including callers and shared abstractions. Collect independent consequential findings in one pass.

Establish the intended contract from released docs, tests, history, and maintainer decisions. A reproducible behavior is not automatically a bug. Check whether the change fixes the cause where it occurs or adds compensation elsewhere.

## Compatibility

Assess behavior changes even for bug fixes. Compare omitted defaults, explicit overrides, errors, serialization, and supported configurations. Trace shared branches into adjacent behavior and all affected MCP component types; use the base component's canonical identity rather than inventing one.

State the compatibility conclusion plainly: what existing input changes behavior, whether that is necessary to fix the issue, and what migration preserves the old behavior. Separate intended corrections from accidental regressions. Passing tests or a clean bot review does not make that product decision.

For dependency changes, verify the declared minimum version supports every API now used. Test the old minimum when compatibility is claimed. Keep lockfile changes scoped to the dependency change.

## Findings

Report reachable defects with concrete triggers and consequences. Evaluate bot comments on their merits; do not repeat resolved or convincingly rebutted findings without new evidence. Avoid speculative cases, cosmetic blockers, and generic requests for more tests.

Review regression tests for the actual failure and affected neighboring contracts. Record which checks were run and at what revision. Keep uncertainty explicit: an inspected diff, a passing focused test, and a complete validated review are different evidence.

Use [review-pr](../review-pr/SKILL.md) to follow CI and automated feedback after publication. A merge still needs the user's authorization and the repository's current merge gates.
