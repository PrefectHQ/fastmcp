---
title: Auth Provider Environment Variables
---

## Decision: Remove automatic environment variable loading from auth providers

You can still use environment variables for configuration - you just read them yourself with `os.environ` instead of relying on FastMCP's automatic loading.

**Status:** Implemented in v3.0.0

### Background

Auth providers in v2.x used `pydantic-settings` to automatically load configuration from environment variables with a `FASTMCP_SERVER_AUTH_<PROVIDER>_` prefix. For example, `GitHubProvider` would read from:

- `FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID`
- `FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET`
- `FASTMCP_SERVER_AUTH_GITHUB_BASE_URL`
- etc.

This was implemented via a `*ProviderSettings(BaseSettings)` class in each provider, combined with a `NotSet` sentinel pattern to distinguish between "not provided" and `None`.

### Why remove it

1. **Maintenance burden**: Every new provider needed to implement the settings class, validators, and the `NotSet` merging logic. This was ~50-100 lines of boilerplate per provider.

2. **Documentation complexity**: Each provider needed documentation explaining both the parameter and the corresponding environment variable. This doubled the surface area to document and maintain.

3. **Contributor friction**: New contributors adding providers had to understand and replicate this pattern, which was a source of inconsistency and bugs.

4. **Marginal user value**: Python developers are comfortable with `os.environ["VAR"]` or `os.environ.get("VAR", default)`. The automatic loading saved a single line of code per parameter while adding significant complexity.

5. **Implicit behavior**: Magic environment variable loading makes it harder to understand where values come from. Explicit `os.environ` calls are more traceable.

### Migration path

The migration is trivial - users add explicit environment variable reads:

```python
# Before (v2.x)
auth = GitHubProvider()  # Relied on env vars

# After (v3.0)
import os

auth = GitHubProvider(
    client_id=os.environ["GITHUB_CLIENT_ID"],
    client_secret=os.environ["GITHUB_CLIENT_SECRET"],
    base_url=os.environ["MY_BASE_URL"],
)
```

Users can also use `os.environ.get()` with defaults, or any other configuration library they prefer (dotenv, dynaconf, etc.).

### Backwards compatibility

We chose not to provide backwards compatibility because:

1. This is a major version bump (v3.0), which is the appropriate time for breaking changes
2. The migration is straightforward (add `os.environ` calls)
3. Maintaining compatibility would require keeping all the boilerplate we're trying to remove
4. The pattern was likely not heavily used - most production deployments pass secrets explicitly rather than relying on magic prefixes

### What was removed

- `*ProviderSettings(BaseSettings)` classes from all auth providers
- `NotSet` sentinel usage in provider constructors
- `pydantic-settings` dependency for auth providers
- Environment variable documentation from provider docs
- Related test cases for env var loading

### Result

Provider constructors are now simple and explicit. Required parameters are actually required (Python raises `TypeError` if missing), and optional parameters have clear defaults. The code is more readable and easier to maintain.
