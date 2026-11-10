---
title: "Releases"
description: "FastMCP versioning and release process"
icon: "truck-fast"
---

FastMCP releases frequently to deliver features quickly in the rapidly evolving MCP ecosystem. We use semantic versioning pragmatically - the Model Context Protocol is young, patterns are still emerging, and waiting for perfect stability would mean missing opportunities to empower developers with better tools.

## Versioning Policy

### Semantic Versioning

**Major (x.0.0)**: Complete API redesigns

Major versions represent fundamental shifts. FastMCP 2.x is entirely different from 1.x in both implementation and design philosophy.

**Minor (2.x.0)**: New features and evolution

<Warning>
Unlike traditional semantic versioning, minor versions **may** include [breaking changes](#breaking-changes) when necessary for the ecosystem's evolution. This flexibility is essential in a young ecosystem where perfect backwards compatibility would prevent important improvements.
</Warning>

FastMCP always targets the most current MCP Protocol version. Breaking changes in the MCP spec or MCP SDK automatically flow through to FastMCP - we prioritize staying current with the latest features and conventions over maintaining compatibility with older protocol versions.

**Patch (2.0.x)**: Bug fixes and refinements

Patch versions contain only bug fixes without breaking changes. These are safe updates you can apply with confidence.

### Breaking Changes

We permit breaking changes in minor versions because the MCP ecosystem is rapidly evolving. Refusing to break problematic APIs would accumulate design debt that eventually makes the framework unusable. Each breaking change represents a deliberate decision to keep FastMCP aligned with the ecosystem's evolution.

When breaking changes occur:
- They only happen in minor versions (e.g., 2.3.x to 2.4.0)
- Release notes explain what changed and how to migrate
- We provide deprecation warnings at least 1 minor version in advance when possible
- Changes must substantially benefit users to justify disruption

The public API is what's covered by our compatibility guarantees - these are the parts of FastMCP you can rely on to remain stable within a minor version. The public API consists of:
- `FastMCP` server class, `Client` class, and FastMCP `Context`
- Core MCP components: `Tool`, `Prompt`, `Resource`, `ResourceTemplate`, and transports
- Their public methods and documented behaviors

Everything else (utilities, private methods, internal modules) may change without notice. This boundary lets us refactor internals and improve implementation details without breaking your code. For production stability, pin to specific versions.

<Warning>
The `fastmcp.server.auth` module was introduced in 2.12.0 and is exempted from this policy temporarily, meaning it is *expected* to have breaking changes even on patch versions. This is because auth is a rapidly evolving part of the MCP spec and it would be dangerous to be beholden to old decisions. Please pin your FastMCP version if using authentication in production.

We expect this exemption to last through at least the 2.12.x and 2.13.x release series. 
</Warning>

### Production Use

Pin to exact versions:
```
fastmcp==2.11.0  # Good
fastmcp>=2.11.0  # Bad - will install breaking changes
```

## Creating Releases

Our release process is intentionally simple:

1. Create GitHub release with tag `vMAJOR.MINOR.PATCH` (e.g., `v2.11.0`)
2. Generate release notes automatically, and curate or add additional editorial information as needed
3. GitHub releases automatically trigger PyPI deployments

This automation lets maintainers focus on code quality rather than release mechanics.

### Release Cadence

We follow a feature-driven release cadence rather than a fixed schedule. Minor versions ship approximately every 3-4 weeks when significant functionality is ready.

Patch releases ship promptly for:
- Critical bug fixes
- Security updates (immediate release)
- Regression fixes

This approach means you get improvements as soon as they're ready rather than waiting for arbitrary release dates.
