# Stateless session state (2026-07-28)

> Design proposal. Status: building.

## Problem

The `2026-07-28` era is stateless by protocol construction: each request builds a
fresh `Connection`, `connection.session_id` is always `None`, and
`connection.state` is a new dict that is discarded when the request returns. So
`ctx.session_id` mints a throwaway `uuid4` per request, and `ctx.set_state` /
`ctx.get_state` **silently never round-trip** — no error, just lost data.

Users who want cross-call state on a modern connection (a cart, a conversation,
accumulated context) have no safe mechanism today, and the failure is invisible:
the code reads as "works" while quietly returning the wrong thing.

The one thing every modern request carries that is stable and *non-spoofable* is
the authenticated principal — `get_access_token().claims["sub"]`, or the
`(client_id, issuer, subject)` triple. Everything else on the wire
(`client_info`, `client_capabilities`) is client-declared and forgeable.

## Goals

- Offer secure, cross-request **session-scoped state** on modern connections.
- Work with **any client** — no reliance on client-side `_meta` cooperation. The
  session handle travels as a normal tool argument.
- **Non-spoofable isolation** when authenticated; an **unforgeable** handle even
  without auth.
- Reuse what exists: the SDK's AES-GCM sealed envelope
  (`mcp.server.request_state`) and FastMCP's `AsyncKeyValue` store abstraction.
- Replace silent data loss with **clear errors**.

## Non-goals

- Server→client push or notifications for session lifecycle (that is the
  subscriptions workstream).
- Reconstructing handshake-era session semantics on modern connections.

## Design

### One surface: scoped state

Extend the existing `ctx.get_state` / `ctx.set_state` with a `scope`:

```python
ctx.get_state("cart", scope=Scope.SESSION)   # sealed session handle; many per principal
ctx.get_state("prefs", scope=Scope.USER)     # authenticated principal; spans sessions
ctx.get_state("scratch", scope=Scope.REQUEST) # per-request; dies with the request (today)
```

The scope selects the storage key prefix and the isolation guarantee:

| Scope | Key prefix | Isolation |
| --- | --- | --- |
| `REQUEST` | the per-request connection | request lifetime only |
| `SESSION` | `(principal, session_id)` | sealed + principal-bound (auth) / unforgeable (no auth) |
| `USER` | `principal` | auth-bound; requires authentication |

`USER` scope *is* the auth-tuple option; `SESSION` scope *is* the sealed token.
`set_state`/`get_state` already exist and already key off `session_id` — this
makes the scope **explicit** instead of implicitly-session and silently broken on
modern.

### The session handle (sealed token)

A session is identified by a token minted server-side:

- payload = `{session_id, principal, exp}`, AES-GCM sealed via the SDK's
  `AESGCMRequestStateCodec` under a `RequestStateSecurity` key.
- On use, the framework unseals, verifies expiry, and — when auth is present —
  verifies the token's principal matches the request's authenticated principal.
  Foreign, expired, or tampered → rejected with a clear error; the client never
  learns why.

Unlike the MRTR `request_state` seal, the session token binds **principal +
expiry only** (it drops the SDK boundary's per-request binding), so it survives
across *different* tool calls rather than a single round.

### Transport: tool argument (decided)

The handle travels as a **tool argument**, not `_meta` — `_meta` would require
client-side cooperation, and the point is to work with any client. The framework
may peek `_meta` first as an optimization, but the contract is argument-based. An
annotation marks the parameter and owns the crypto:

```python
from typing import Annotated
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from fastmcp.sessions import Session, Scope

mcp = FastMCP("shop")


@mcp.tool
def add_to_cart(item: str, session: Annotated[str, Session()]) -> str:
    ctx = get_context()
    cart = ctx.get_state("cart", scope=Scope.SESSION, default=[])
    cart.append(item)
    ctx.set_state("cart", cart, scope=Scope.SESSION)
    return f"{len(cart)} items in cart"
```

The `Session()` annotation names the parameter carrying the sealed token. The
framework unseals + verifies it once, binds the session identity to the request
context, and the tool body reads/writes through `ctx.get_state(scope=SESSION)`.
Authors never touch crypto or key schemes.

### SessionProvider

An opt-in provider that supplies the lifecycle:

- Registers `create_session` and `end_session` tools. The client calls
  `create_session`, receives an opaque sealed token, and threads it into later
  calls.
- Owns the backing store — an `AsyncKeyValue` (default `MemoryStore`; a Redis
  store for multi-replica), keyed by `(principal, session_id)`.
- Applies a TTL so abandoned sessions clear without an explicit `end_session`.

```python
mcp = FastMCP("shop", session_provider=SessionProvider(store=redis_store, ttl=3600))
```

### Guarantee tiers

- **Authenticated:** the token binds the principal; a token echoed under a
  different principal is rejected by the seal. Strong cross-client isolation,
  multiple sessions per user.
- **Unauthenticated:** no principal to bind, but the seal is still unforgeable —
  a client cannot fabricate a valid token or guess into another session. This is
  single-tenant-safe; there is no cross-client wall beyond unforgeability, and
  the docs say so plainly.

### Fallback

When the transport genuinely has a session (handshake / stateful, an
`mcp-session-id` is present), `SESSION` scope can key off it directly with no
token round-trip. The sealed-token path is the modern/stateless story.

### Gating: no more silent loss

On a modern connection with no valid handle:

- `SESSION` access raises a clear error naming the fix ("no active session — call
  `create_session` and pass its token").
- `USER` access without auth raises a clear auth-required error.
- `REQUEST` always works.

This replaces today's silent per-request `uuid4`.

## Build plan

1. **Scoped store.** `Scope` enum; `get_state` / `set_state(scope=)` over a
   pluggable `AsyncKeyValue`; `REQUEST` keeps current behavior; `SESSION` / `USER`
   error-gate when unavailable.
2. **Session codec.** Seal / unseal `{session_id, principal, exp}` via the SDK
   AES-GCM codec; verify principal + expiry; reject foreign / expired / tampered.
3. **SessionProvider.** `create_session` / `end_session` tools, `AsyncKeyValue`
   store, TTL, `(principal, session_id)` key scheme.
4. **`Session()` annotation + boundary.** Unseal / verify the token argument once,
   bind identity to the request context; error-gate cleanly.
5. **Docs.** The scoped-state guide, the two-tier guarantee doc, and the migration
   note from the old `set_state`.

## Open questions

- Handle shape: inject a `SessionHandle` object vs. keep it pure
  `ctx.get_state(scope=SESSION)`? (Proposed: context-primary; the annotation only
  establishes identity.)
- `USER` key: `sub` alone, or the full `(client_id, issuer, subject)` triple?
- `create_session` return shape — the sealed token as a plain string the model
  echoes back on later calls.
