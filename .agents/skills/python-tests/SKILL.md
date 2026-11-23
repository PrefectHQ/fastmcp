---
name: python-tests
description: Write regression tests for FastMCP changes using its pytest fixtures, in-memory clients, and protocol boundaries.
---

# FastMCP tests

Read nearby tests, the [testing guide](../../../docs/development/tests.mdx), and the repository's validation requirements first. Extend the owning test module when it remains readable; use a focused module when the existing one is already too large.

For a bug fix, run the regression against unchanged code and confirm it fails for the reported reason, then run it with the fix. An import error or broken fixture is not a reproduced bug. To run a PR's test against main, copy it into a main worktree: `pythonpath` in pyproject shadows `PYTHONPATH`. Include supported adjacent behavior that the same branch could change, especially omitted defaults and explicit overrides.

- Async tests need no marker: `asyncio_mode = "auto"` is configured globally.
- Use `Client(server)` for real MCP dispatch without a network server. Use HTTP transport only when network behavior is the subject.
- Test the default protocol mode; pin `mode="legacy"` only when the handshake era is the subject.
- Mock external boundaries, not the FastMCP functions under test. For OpenAPI requests, capture the outgoing request with `httpx2.MockTransport` and assert its bytes, headers, or decoded parameters.
- Keep imports at module scope. Prefer function-scoped fixtures and `tmp_path` for files.
- Parameterize variations of one contract; separate unrelated behavior. For JSON, equality alone does not distinguish `True` from `1`: check the resulting type when type preservation matters.
- Use existing `inline_snapshot` conventions for complex schema assertions. Inspect generated snapshots rather than accepting them blindly.
- The default timeout is five seconds. Use the repository's integration conventions for tests that legitimately need more time.

When a change touches a module with properties in `tests/pbt/` (URI templates, JSON Schema handling, components and mounting, tool transforms, OpenAPI requests, the OAuth proxy), run `uv run pytest tests/pbt -k <area>` as well. If the change adds behavior the property's model does not know about (a new config branch, a new rejection), extend the model or strategy in the same PR; a state machine that cannot express the change cannot catch its regression. For new parsing, serialization, or matching code with a large input space, prefer one property with a planted-bug check from `tests/pbt/mutants.py` over a list of examples. Do not add properties to light changes.

Run relevant tests and applicable static checks before committing. Broaden local
testing when a concrete dependency or failure warrants it; leave routine full-suite
and platform-matrix runs to CI. Do not repeat the full suite for each layer in a
stack. Follow AGENTS.md for the local verification policy. If a failure appears
unrelated, reproduce it on the unchanged base; do not call it pre-existing from
the test name alone.
