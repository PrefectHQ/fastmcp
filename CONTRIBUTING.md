# Contributing to FastMCP

FastMCP is an actively maintained, high-traffic project. We welcome contributions — but the most impactful way to contribute might not be what you expect.

## The best contribution is a great issue

FastMCP is an opinionated framework, and its maintainers use AI-assisted tooling that is deeply tuned to those opinions — the design philosophy, the API patterns, the way the framework is meant to evolve. A well-written issue with a clear problem description is often more valuable than a pull request, because it lets maintainers produce a solution that isn't just correct, but consistent with how the framework wants to work. That matters more than speed, though it's faster too.

**A great issue looks like this:**

1. A short, motivating description of the problem or gap
2. A minimal reproducible example (for bugs) or a concrete use case (for enhancements)
3. A brief note on expected vs. actual behavior

That's it. No need to diagnose root causes, propose API designs, or suggest implementations. If you've done genuine investigation and have a non-obvious insight, include it.

## Using AI to contribute

We encourage you to use LLMs to help identify bugs, write MREs, and prepare contributions. But if you do, your LLM must take into account the conventions and contributing guidelines of this repo — including how we want issues formatted and when it's appropriate to open a PR. Generic LLM output that ignores these guidelines tells us the contribution wasn't made thoughtfully, and we will close it. A good AI-assisted contribution is indistinguishable from a good human one. A bad one is obvious.

## When to open a pull request

An open issue is not an invitation to submit a PR. Issues track problems; whether and how to solve them is a separate decision. If you want to work on something, propose your approach in the issue first and ask a maintainer to assign it to you — especially for anything beyond a trivial fix. External PRs that reference an issue not assigned to their author are closed automatically (see [PR guidelines](#pr-guidelines)).

**Bug fixes** — PRs are welcome for simple, well-scoped bug fixes where the problem and solution are both straightforward. "The function raises `TypeError` when passed `None` because of a missing guard" is a good candidate. If the fix requires design decisions or touches multiple subsystems, open an issue with a design proposal instead.

**Documentation** — Typo fixes, clarifications, and improvements to examples are always welcome as PRs.

**Enhancements and features** — We welcome enhancement PRs, but our experience is that most contributors — even when using LLMs — implement fixes that address the one instance of a problem they encountered rather than understanding why the framework produces that problem and fixing it at the right layer. This creates branching, patch-style code that's difficult to maintain and makes it impossible to reason about the framework as a coherent system. For this reason, enhancements need a design proposal in the issue before code is written. The proposal doesn't need to be long — just enough to show you've thought about how the change fits into the framework, not just how it solves your immediate case.

**Integrations** — FastMCP generally does not accept PRs that add third-party integrations (custom middleware, provider-specific adapters, etc.). If you're building something for your users, ship it as a standalone package — that's a feature, not a limitation. Authentication providers are an exception, since auth is tightly coupled to the framework.

## PR guidelines

If you do open a PR:

- **Reference an issue you're assigned to.** Every PR must reference a tracked issue using an auto-close keyword (`Fixes #123`, `Closes #123`, or `Resolves #123`), and the referenced issue must be assigned to you. If there isn't an issue, open one; then comment to ask a maintainer to assign it to you. This lets us deconflict effort and steer the approach before you invest time in code. External PRs that don't meet both conditions are automatically labeled `missing-issue-link` and closed; they reopen automatically once the link is present and you're assigned.
- **Keep it focused.** One logical change per PR. Don't bundle unrelated fixes or refactors.
- **Match existing patterns.** Follow the code style, type annotation conventions, and test patterns you see in the codebase. Run `uv run prek run --all-files` before submitting.
- **Write tests.** Bug fixes should include a test that fails without the fix. Enhancements should include tests for the new behavior.
- **Fix the cause, not the symptom.** If the bug is that a code path skips a step, the fix should make it stop skipping that step — not add compensation elsewhere. Workaround-style fixes will be sent back for revision.
- **Don't submit generated boilerplate.** We review every line. PRs that read like unedited LLM output — verbose descriptions, speculative changes, shotgun-style fixes — will be closed.

## What we'll close without review

To keep the project maintainable, we will close PRs that:

- Don't reference an issue or address a clearly self-evident bug
- Make sweeping changes without prior discussion
- Add third-party integrations that belong in a separate package
- Are difficult to review due to size, scope, or generated content

This isn't personal — contributing to a framework is different from contributing to an application. In an application, a fix that works is a good fix. In a framework, a fix that works but doesn't fit the framework's design creates maintenance burden that compounds over time. Every patch that works around a problem instead of solving it at the right layer makes the system harder for *everyone* to reason about — maintainers, contributors, and users. We hold contributions to this standard because the alternative is a codebase that's a series of patches rather than a coherent system. A good issue is often the best thing you can do for the project.
