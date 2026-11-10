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
the wall. Sessions are also create-then-validate (below): an id that was never
minted by `create_session` under this principal is rejected outright, not
resolved to an empty session.

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
from fastmcp.server.dependencies import get_session

@mcp.tool
async def add_to_cart(item: str, session_id: SessionId) -> str:
    session = await get_session(session_id)
    cart = await session.get("cart", default=[])
    cart.append(item)
    await session.set("cart", cart)
    return f"{len(cart)} items"
```

`session_id: SessionId` is a **required string argument** — it *is* in the schema,
the agent supplies it. `SessionId` is a marker type so the framework
auto-populates the argument's description with the protocol:

> "Session identifier. Use a tool to create a session, then pass the resulting id
> here to persist state across calls in the same session."

The tool becomes self-teaching — an agent reads the schema and learns the
create-then-pass contract with no hand-prompting. The description names no
specific tool: composition can rename the lifecycle tool (mounting under a
namespace exposes it as `child_create_session`), so it points at the
*capability* rather than a name that may not exist under that mount.

The standalone `await get_session(session_id)` resolves the id to a `Session`
keyed by `(principal, session_id)`, **validating** that it was created under this
principal — an unknown or foreign id raises `InvalidSession` rather than opening a
fresh bucket. It is a plain function, not a `Context` method, so it needs no
foreground context and works from a `task=True` tool's worker. Use this pattern
when a user needs more than one session.

## The `Session` object

Async accessors over the server store, scoped to one `(principal, session_id)`:

- `session.id` — the session's id (set for a `session_id`-resolved session; `None`
  for an injected `UserSession`, which has no distinct id).
- `await session.get(key, default=None)`
- `await session.set(key, value)`
- `await session.delete(key)`
- `await session.clear()` — empties user state but **keeps the session valid**.
- `await session.end()` — deletes the session (what `end_session` calls).

A session's state is stored as a **single dict under one key**
(`session:{sha256(principal)}:{session_id}`, and `session:anon:{session_id}` when
unauthenticated — the principal is hashed into a fixed-length, delimiter-safe
segment, never embedded raw). That dict holds user state in a `state` sub-dict
alongside a small `_created` marker, so a created-but-empty session is
distinguishable from a missing one even if the store collapses empty dicts.
`get`/`set`/`delete` read-modify-write the sub-dict and never touch the marker;
`clear` resets the sub-dict but leaves the marker (the session still resolves);
`end` deletes the key. Namespacing user state under `state` is what keeps a user
key named `_created` from colliding with the marker. One key per session means
one TTL per session (the store's), refreshed on write — no key index to maintain,
and `end` is a single delete. (Trade-off: concurrent writes to one session race
on the read-modify-write; session state is small and typically driven serially by
one agent, so this is acceptable — noted, not hidden.)

## `SessionProvider`

Session ids are minted by `SessionProvider`, which contributes two tools:

- `create_session()` → mints an unguessable `uuid4`, **records** the session
  under the current principal, and returns the id as a string.
- `end_session(session_id: SessionId)` → validates the id, then deletes the
  session so it no longer resolves.

Register it whenever your tools take a `session_id` — providers are the idiomatic
way to add functionality like this:

```python
from fastmcp.server.sessions import SessionProvider

mcp.add_provider(SessionProvider())
```

There is **no enforcement** that a provider is registered, and there was: an
earlier version scanned the tool set at list/resolve time and raised if a
`session_id` tool had no provider. That check had to reason about the whole
composition pipeline — `isinstance` on providers, unwrapping namespaced ones,
tool transforms, session visibility, enabled state — and produced false
positives that broke valid servers (a namespaced provider, a session-disabled
tool). It was deleted. The guarantee never needed it: `get_session` validates
that an id was recorded (create-then-validate), so a server with no provider
simply cannot mint ids, and every `get_session` rejects — a misconfiguration
caught the first time the tools run, not a security hole.

`SessionProvider` subclasses `Provider`, takes **no store** (uses the server's)
and **no ttl** (the store's). It exists to mint and end owned ids.
`create_session` matters most without auth, where an unguessable id is the only
defense against a caller *guessing* onto another session.

When an application already mints its own identifiers — conversation ids, workflow
ids — take them as ordinary string arguments rather than `SessionId`, and register
no provider; `SessionId` is specifically the create-then-pass contract backed by
`create_session`.

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
   description, standalone `await get_session(id)` resolver that validates the id
   (works from a task worker — no foreground context needed).
6. **`SessionProvider(Provider)`** with `create_session` (records the session) /
   `end_session` (deletes it), registered explicitly via `add_provider`. No
   enforcement that it is present — `get_session`'s validation is the guarantee.
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
