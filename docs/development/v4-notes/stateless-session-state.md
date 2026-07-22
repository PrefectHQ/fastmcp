# Stateless session state (2026-07-28)

> Design spec. Status: building.

## Problem

The `2026-07-28` era is stateless by protocol construction: each request builds a
fresh `Connection`, `connection.session_id` is always `None`, and
`connection.state` is a new dict discarded when the request returns. So
`ctx.session_id` mints a throwaway `uuid4` per request and `ctx.set_state` /
`ctx.get_state` **silently never round-trip** — no error, just lost data. A user
who wants cross-call state (a cart, a conversation, accumulated context) has no
safe mechanism, and the failure is invisible.

The one identifier every modern request carries that is stable and
**non-spoofable** is the authenticated principal — `get_access_token().claims["sub"]`,
or the `(client_id, issuer, subject)` triple. Everything else on the wire is
client-declared and forgeable.

## The model

State lives **server-side** in the one `AsyncKeyValue` (py-key-value) store the
server already holds (`session_state_store`). The framework calls `get`/`put`/
`delete` and **never imposes a TTL** — retention is entirely the store's
(configure it on the store you pass: a Redis TTL, a py-key-value TTL wrapper,
whatever). There is no second store and no framework-owned TTL knob.

Isolation comes from the **authenticated principal, not from the session id.**
State is keyed by `(principal, session_id)`. A request under principal B keys
into B's own namespace — it can never address A's keys no matter what
`session_id` it passes. The id only organizes sessions *within* a principal. The
handle is a bare `uuid4` string; it is **not sealed** — the principal prefix is
the wall, and an unknown id simply resolves to an empty session in the caller's
own namespace.

## Two explicit patterns

A tool opts into exactly one, on purpose. There is deliberately **no** optional
"id if given, else default" parameter — that would silently misroute a call
whose id the agent forgot to pass into the shared per-user bucket, which is the
invisible-degradation failure this whole feature exists to remove.

### Per-user state — injected

```python
from fastmcp.server.sessions import UserSession

@mcp.tool
async def remember(fact: str, session: UserSession) -> str:
    await session.set("fact", fact)
    return "noted"
```

`session: UserSession` is **dependency-injected** (like `ctx: Context`): keyed by
the request's authenticated principal, not present in the input schema, nothing
for the agent to pass. Requires auth — with no principal it raises a clear error.
Use it when one bucket per user is what you want. `UserSession` is only the
injection annotation — the value the handler receives is an ordinary `Session`,
so its `get`/`set`/`delete`/`clear` accessors work as usual.

### Distinct sessions — an argument

```python
from fastmcp.server.sessions import SessionId
from fastmcp.server.dependencies import get_context

@mcp.tool
async def add_to_cart(item: str, session_id: SessionId) -> str:
    session = get_context().get_session(session_id)
    cart = await session.get("cart", default=[])
    cart.append(item)
    await session.set("cart", cart)
    return f"{len(cart)} items"
```

`session_id: SessionId` is a **required string argument** — it *is* in the schema,
the agent supplies it. `SessionId` is a marker type so the framework
auto-populates the argument's description with the protocol:

> "Session identifier. Call `create_session` to obtain one, then pass it here to
> persist state across calls in the same session."

The tool becomes self-teaching — an agent reads the schema and learns the
create-then-pass contract with no hand-prompting. `ctx.get_session(session_id)`
resolves it to a `Session` keyed by `(principal, session_id)` (named
`get_session`, not `session`, because `ctx.session` already exposes the raw
`ServerSession`). Use it when a user needs more than one session.

## The `Session` object

Async accessors over the server store, scoped to one `(principal, session_id)`:

- `await session.get(key, default=None)`
- `await session.set(key, value)`
- `await session.delete(key)`
- `await session.clear()`

A session's state is stored as a **single dict under one key**
(`session:{principal}:{session_id}`). `get`/`set`/`delete` read-modify-write that
dict; `clear` / `end_session` delete the one key. One key per session means one
TTL per session (the store's), refreshed on write — no key index to maintain, and
`end_session` is a single delete. (Trade-off: concurrent writes to one session
race on the read-modify-write; session state is small and typically driven
serially by one agent, so this is acceptable — noted, not hidden.)

## `SessionProvider` — auto-wired

Session ids are minted by `SessionProvider`, which contributes two tools:

- `create_session()` → mints an unguessable `uuid4` and returns it as a string.
- `end_session(session_id: SessionId)` → clears that session's state.

**Declaring a `session_id: SessionId` argument on any tool auto-registers the
default `SessionProvider`** — the developer does not have to remember
`add_provider(SessionProvider())`. The `create_session` / `end_session` tools are
real and appear in the normal tool listing; the visible `session_id` argument
implies the lifecycle plumbing, so FastMCP wires it. The decision is made when
tools are listed by scanning the server's registered tools, so it is independent
of registration order: a `session_id` tool added after construction still
activates it.

Registering a `SessionProvider` yourself still works and takes precedence — the
auto-registered one steps aside, so there is never a duplicate:

```python
from fastmcp.server.sessions import SessionProvider

mcp.add_provider(SessionProvider())
```

`SessionProvider` subclasses `Provider`, takes **no store** (uses the server's)
and **no ttl** (the store's). It exists only to hand out unguessable ids.
`create_session` matters most without auth, where an unguessable id is the only
defense against a caller *guessing* onto another session.

**Opt out** with `FastMCP(auto_session_provider=False)`. Use it for
bring-your-own-key apps: a tool takes a `session_id` whose value is the caller's
own session/user identity, so `create_session` is unwanted. With auto-wiring off
you can still `add_provider(SessionProvider())` explicitly if you want it.

## Security

Keyed by `(principal, session_id)`:

- **Authenticated → strong isolation.** `principal` is the validated token
  subject, unforgeable. B keys into B's namespace; A's data is unreachable no
  matter what id B passes. Guessing is pointless; a session id appearing in agent
  context or logs is harmless (it is not a capability without the principal).
  Caller-chosen ids are safe here.
- **Unauthenticated → single-tenant-safe only.** No principal, so the key is just
  the id in a shared namespace: the id becomes a bearer capability, and exposure
  in logs/conversation leaks the session. `create_session`'s `uuid4` gives
  guess-*resistance*, not isolation. Documented in bold: not a tenant boundary;
  without auth, force minted ids and never treat sessions as a wall between
  clients.
- **Isolation is auth; the id is organization.** No id scheme substitutes for a
  principal, which is why sealing the handle buys nothing load-bearing and is
  dropped.
- **Not FastMCP's job:** transport (use TLS), encryption at rest (the store's), a
  malicious *authorized* client acting within its rights.

## Rework plan (from the current prototype)

The prototype (`sessions.py`, `context.py`, `function_tool.py`, `server.py`) built
a `Scope` enum, a sealed `SessionCodec`, and `ctx.get_state(scope=...)`. Rework to
the above:

1. **Remove `Scope`** and the `scope=` parameter; revert `ctx.get_state`/
   `set_state` to their original request-scoped behavior.
2. **Remove the `SessionCodec`/sealing** — ids are bare `uuid4`.
3. **`Session` object** with async `get`/`set`/`delete`/`clear` over the server
   store, single-dict-per-session key scheme.
4. **`session: UserSession`** injection (principal-keyed; error without auth) —
   wire into the same parameter-detection path as `Context`. `UserSession` is the
   injection marker; the injected value is a `Session`.
5. **`session_id: SessionId`** marker type: string in the schema, auto-filled
   description, `ctx.get_session(id)` resolver.
6. **`SessionProvider(Provider)`** with `create_session` / `end_session`,
   auto-registered when a tool declares `session_id: SessionId` (or added
   explicitly via `add_provider`); opt out with `auto_session_provider=False`.
7. Rewrite the tests to cover both patterns, principal isolation, no-auth
   behavior, and `end_session`.

## Docs plan

Written against the final API once the rework verifies:

- A concept guide — why stateless removes the session, the two patterns, when to
  reach for each. Why before how.
- A security page — the two tiers, "isolation is auth, the id is organization,"
  the bold no-multitenant-without-auth warning.
- Fully runnable examples for both patterns (pass the doc-import guard, register
  in `docs.json`).
- A migration note from the old `ctx.session_id` / `set_state`.
