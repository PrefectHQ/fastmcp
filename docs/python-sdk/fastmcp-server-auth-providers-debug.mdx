---
title: debug
sidebarTitle: debug
---

# `fastmcp.server.auth.providers.debug`


Debug token verifier for testing and special cases.

This module provides a flexible token verifier that delegates validation
to a custom callable. Useful for testing, development, or scenarios where
standard verification isn't possible (like opaque tokens without introspection).

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.debug import DebugTokenVerifier

    # Accept all tokens (default - useful for testing)
    auth = DebugTokenVerifier()

    # Custom sync validation logic
    auth = DebugTokenVerifier(validate=lambda token: token.startswith("valid-"))

    # Custom async validation logic
    async def check_cache(token: str) -> bool:
        return await redis.exists(f"token:{token}")

    auth = DebugTokenVerifier(validate=check_cache)

    mcp = FastMCP("My Server", auth=auth)
    ```


## Classes

### `DebugTokenVerifier` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/providers/debug.py#L40" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Token verifier with custom validation logic.

This verifier delegates token validation to a user-provided callable.
By default, it accepts all non-empty tokens (useful for testing).

Use cases:
- Testing: Accept any token without real verification
- Development: Custom validation logic for prototyping
- Opaque tokens: When you have tokens with no introspection endpoint

WARNING: This bypasses standard security checks. Only use in controlled
environments or when you understand the security implications.


**Methods:**

#### `verify_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/providers/debug.py#L77" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
verify_token(self, token: str) -> AccessToken | None
```

Verify token using custom validation logic.

**Args:**
- `token`: The token string to validate

**Returns:**
- AccessToken if validation succeeds, None otherwise

