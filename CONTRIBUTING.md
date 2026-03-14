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

**Bug fixes** — PRs are welcome for simple, well-scoped bug fixes where the problem and solution are both straightforward. "The function raises `TypeError` when passed `None` because of a missing guard" is a good candidate. If the fix requires design decisions or touches multiple subsystems, open an issue instead.

**Documentation** — Typo fixes, clarifications, and improvements to examples are always welcome as PRs.

**Enhancements and features** — For changes that affect the behavior or design of the framework, please open an issue first. Maintainers will typically implement these themselves. FastMCP is opinionated, and enhancements need to reflect those opinions — not just solve the problem, but solve it in a way that's consistent with the framework's design. That's hard to do from the outside, and it's why a clear problem description is more useful than a proposed solution.

**Integrations** — FastMCP generally does not accept PRs that add third-party integrations (custom middleware, provider-specific adapters, etc.). If you're building something for your users, ship it as a standalone package — that's a feature, not a limitation. Authentication providers are an exception, since auth is tightly coupled to the framework.

## PR guidelines

If you do open a PR:

- **Reference an issue.** Every PR should address a tracked issue. If there isn't one, open an issue first. This isn't a permission step — you don't need to wait for a response. But the issue gives us context on the problem, and if a maintainer is already working on it, we can let you know before you invest time in code.
- **Keep it focused.** One logical change per PR. Don't bundle unrelated fixes or refactors.
- **Match existing patterns.** Follow the code style, type annotation conventions, and test patterns you see in the codebase. Run `uv run prek run --all-files` before submitting.
- **Write tests.** Bug fixes should include a test that fails without the fix. Enhancements should include tests for the new behavior.
- **Don't submit generated boilerplate.** We review every line. PRs that read like unedited LLM output — verbose descriptions, speculative changes, shotgun-style fixes — will be closed.

## What we'll close without review

To keep the project maintainable, we will close PRs that:

- Don't reference an issue or address a clearly self-evident bug
- Make sweeping changes without prior discussion
- Add third-party integrations that belong in a separate package
- Are difficult to review due to size, scope, or generated content

This isn't personal. FastMCP receives a high volume of contributions and we need to focus maintainer time where it has the most impact — which is why a good issue is often the best thing you can do for the project.
