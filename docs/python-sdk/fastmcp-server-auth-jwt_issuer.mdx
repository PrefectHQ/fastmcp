---
title: jwt_issuer
sidebarTitle: jwt_issuer
---

# `fastmcp.server.auth.jwt_issuer`


JWT token issuance and verification for FastMCP OAuth Proxy.

This module implements the token factory pattern for OAuth proxies, where the proxy
issues its own JWT tokens to clients instead of forwarding upstream provider tokens.
This maintains proper OAuth 2.0 token audience boundaries.


## Functions

### `derive_jwt_key` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/jwt_issuer.py#L37" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
derive_jwt_key() -> bytes
```


Derive JWT signing key from a high-entropy or low-entropy key material and server salt.


## Classes

### `JWTIssuer` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/jwt_issuer.py#L74" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Issues and validates FastMCP-signed JWT tokens using HS256.

This issuer creates JWT tokens for MCP clients with proper audience claims,
maintaining OAuth 2.0 token boundaries. Tokens are signed with HS256 using
a key derived from the upstream client secret.


**Methods:**

#### `issue_access_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/jwt_issuer.py#L100" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
issue_access_token(self, client_id: str, scopes: list[str], jti: str, expires_in: int = 3600) -> str
```

Issue a minimal FastMCP access token.

FastMCP tokens are reference tokens containing only the minimal claims
needed for validation and lookup. The JTI maps to the upstream token
which contains actual user identity and authorization data.

**Args:**
- `client_id`: MCP client ID
- `scopes`: Token scopes
- `jti`: Unique token identifier (maps to upstream token)
- `expires_in`: Token lifetime in seconds

**Returns:**
- Signed JWT token


#### `issue_refresh_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/jwt_issuer.py#L147" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
issue_refresh_token(self, client_id: str, scopes: list[str], jti: str, expires_in: int) -> str
```

Issue a minimal FastMCP refresh token.

FastMCP refresh tokens are reference tokens containing only the minimal
claims needed for validation and lookup. The JTI maps to the upstream
token which contains actual user identity and authorization data.

**Args:**
- `client_id`: MCP client ID
- `scopes`: Token scopes
- `jti`: Unique token identifier (maps to upstream token)
- `expires_in`: Token lifetime in seconds (should match upstream refresh expiry)

**Returns:**
- Signed JWT token


#### `verify_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/jwt_issuer.py#L195" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
verify_token(self, token: str) -> dict[str, Any]
```

Verify and decode a FastMCP token.

Validates JWT signature, expiration, issuer, and audience.

**Args:**
- `token`: JWT token to verify

**Returns:**
- Decoded token payload

**Raises:**
- `JoseError`: If token is invalid, expired, or has wrong claims

