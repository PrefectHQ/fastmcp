---
title: auth
sidebarTitle: auth
---

# `fastmcp.server.auth.auth`

## Classes

### `AccessToken` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L43" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


AccessToken that includes all JWT claims.


### `TokenHandler` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L49" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


TokenHandler that returns OAuth 2.1 compliant error responses.

The MCP SDK returns `unauthorized_client` for client authentication failures.
However, per RFC 6749 Section 5.2, authentication failures should return
`invalid_client` with HTTP 401, not `unauthorized_client`.

This distinction matters: `unauthorized_client` means "client exists but
can't do this", while `invalid_client` means "client doesn't exist or
credentials are wrong". Claude's OAuth client uses this to decide whether
to re-register.

This handler transforms 401 responses with `unauthorized_client` to use
`invalid_client` instead, making the error semantics correct per OAuth spec.


**Methods:**

#### `handle` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L65" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
handle(self, request: Any)
```

Wrap SDK handle() and transform auth error responses.


### `AuthProvider` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L91" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Base class for all FastMCP authentication providers.

This class provides a unified interface for all authentication providers,
whether they are simple token verifiers or full OAuth authorization servers.
All providers must be able to verify tokens and can optionally provide
custom authentication routes.


**Methods:**

#### `verify_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L118" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
verify_token(self, token: str) -> AccessToken | None
```

Verify a bearer token and return access info if valid.

All auth providers must implement token verification.

**Args:**
- `token`: The token string to validate

**Returns:**
- AccessToken object if valid, None if invalid or expired


#### `get_routes` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L131" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_routes(self, mcp_path: str | None = None) -> list[Route]
```

Get all routes for this authentication provider.

This includes both well-known discovery routes and operational routes.
Each provider is responsible for creating whatever routes it needs:
- TokenVerifier: typically no routes (default implementation)
- RemoteAuthProvider: protected resource metadata routes
- OAuthProvider: full OAuth authorization server routes
- Custom providers: whatever routes they need

**Args:**
- `mcp_path`: The path where the MCP endpoint is mounted (e.g., "/mcp")
This is used to advertise the resource URL in metadata, but the
provider does not create the actual MCP endpoint route.

**Returns:**
- List of all routes for this provider (excluding the MCP endpoint itself)


#### `get_well_known_routes` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L154" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_well_known_routes(self, mcp_path: str | None = None) -> list[Route]
```

Get well-known discovery routes for this authentication provider.

This is a utility method that filters get_routes() to return only
well-known discovery routes (those starting with /.well-known/).

Well-known routes provide OAuth metadata and discovery endpoints that
clients use to discover authentication capabilities. These routes should
be mounted at the root level of the application to comply with RFC 8414
and RFC 9728.

Common well-known routes:
- /.well-known/oauth-authorization-server (authorization server metadata)
- /.well-known/oauth-protected-resource/* (protected resource metadata)

**Args:**
- `mcp_path`: The path where the MCP endpoint is mounted (e.g., "/mcp")
This is used to construct path-scoped well-known URLs.

**Returns:**
- List of well-known discovery routes (typically mounted at root level)


#### `get_middleware` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L186" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_middleware(self) -> list
```

Get HTTP application-level middleware for this auth provider.

**Returns:**
- List of Starlette Middleware instances to apply to the HTTP app


### `TokenVerifier` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L220" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Base class for token verifiers (Resource Servers).

This class provides token verification capability without OAuth server functionality.
Token verifiers typically don't provide authentication routes by default.


**Methods:**

#### `verify_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L241" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
verify_token(self, token: str) -> AccessToken | None
```

Verify a bearer token and return access info if valid.


### `RemoteAuthProvider` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L246" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Authentication provider for resource servers that verify tokens from known authorization servers.

This provider composes a TokenVerifier with authorization server metadata to create
standardized OAuth 2.0 Protected Resource endpoints (RFC 9728). Perfect for:
- JWT verification with known issuers
- Remote token introspection services
- Any resource server that knows where its tokens come from

Use this when you have token verification logic and want to advertise
the authorization servers that issue valid tokens.


**Methods:**

#### `verify_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L287" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
verify_token(self, token: str) -> AccessToken | None
```

Verify token using the configured token verifier.


#### `get_routes` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L291" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_routes(self, mcp_path: str | None = None) -> list[Route]
```

Get routes for this provider.

Creates protected resource metadata routes (RFC 9728).


### `OAuthProvider` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L319" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


OAuth Authorization Server provider.

This class provides full OAuth server functionality including client registration,
authorization flows, token issuance, and token verification.


**Methods:**

#### `verify_token` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L382" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
verify_token(self, token: str) -> AccessToken | None
```

Verify a bearer token and return access info if valid.

This method implements the TokenVerifier protocol by delegating
to our existing load_access_token method.

**Args:**
- `token`: The token string to validate

**Returns:**
- AccessToken object if valid, None if invalid or expired


#### `get_routes` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L397" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_routes(self, mcp_path: str | None = None) -> list[Route]
```

Get OAuth authorization server routes and optional protected resource routes.

This method creates the full set of OAuth routes including:
- Standard OAuth authorization server routes (/.well-known/oauth-authorization-server, /authorize, /token, etc.)
- Optional protected resource routes

**Returns:**
- List of OAuth routes


#### `get_well_known_routes` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/auth/auth.py#L477" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
get_well_known_routes(self, mcp_path: str | None = None) -> list[Route]
```

Get well-known discovery routes with RFC 8414 path-aware support.

Overrides the base implementation to support path-aware authorization
server metadata discovery per RFC 8414. If issuer_url has a path component,
the authorization server metadata route is adjusted to include that path.

For example, if issuer_url is "http://example.com/api", the discovery
endpoint will be at "/.well-known/oauth-authorization-server/api" instead
of just "/.well-known/oauth-authorization-server".

**Args:**
- `mcp_path`: The path where the MCP endpoint is mounted (e.g., "/mcp")

**Returns:**
- List of well-known discovery routes

