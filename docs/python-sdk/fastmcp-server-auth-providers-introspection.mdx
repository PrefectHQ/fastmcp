---
title: introspection
sidebarTitle: introspection
---

# `fastmcp.server.auth.providers.introspection`


OAuth 2.0 Token Introspection (RFC 7662) provider for FastMCP.

This module provides token verification for opaque tokens using the OAuth 2.0
Token Introspection protocol defined in RFC 7662. It allows FastMCP servers to
validate tokens issued by authorization servers that don't use JWT format.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier

    # Verify opaque tokens via RFC 7662 introspection
    verifier = IntrospectionTokenVerifier(
        introspection_url="https://auth.example.com/oauth/introspect",
        client_id="your-client-id",
        client_secret="your-client-secret",
        required_scopes=["read", "write"]
    )

    mcp = FastMCP("My Protected Server", auth=verifier)
    ```


## Classes

### `IntrospectionTokenVerifierSettings` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/providers/introspection.py#L43" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Settings for OAuth 2.0 Token Introspection verification.


### `IntrospectionTokenVerifier` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/providers/introspection.py#L65" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


OAuth 2.0 Token Introspection verifier (RFC 7662).

This verifier validates opaque tokens by calling an OAuth 2.0 token introspection
endpoint. Unlike JWT verification which is stateless, token introspection requires
a network call to the authorization server for each token validation.

The verifier authenticates to the introspection endpoint using HTTP Basic Auth
with the provided client_id and client_secret, as specified in RFC 7662.

Use this when:
- Your authorization server issues opaque (non-JWT) tokens
- You need to validate tokens from Auth0, Okta, Keycloak, or other OAuth servers
- Your tokens require real-time revocation checking
- Your authorization server supports RFC 7662 introspection


**Methods:**

#### `verify_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/providers/introspection.py#L184" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
verify_token(self, token: str) -> AccessToken | None
```

Verify a bearer token using OAuth 2.0 Token Introspection (RFC 7662).

This method makes a POST request to the introspection endpoint with the token,
authenticated using HTTP Basic Auth with the client credentials.

**Args:**
- `token`: The opaque token string to validate

**Returns:**
- AccessToken object if valid and active, None if invalid, inactive, or expired

