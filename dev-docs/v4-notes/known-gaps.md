---
title: Known Gaps and Upstream Dependencies
---

The migration ships with a small set of deliberate compatibility boundaries and expected test gaps. FastMCP now depends on the stable MCP Python SDK 2.0 line; this page tracks what remains for the beta-to-stable transition and the advisory relationship with the SDK team.

## The xfail register

The unit suite has three expected xfails. Two are strict SDK compatibility checks, so an upstream fix turns them into failures and prompts us to remove the markers.

**Stateless HTTP elicitation (`tests/client/test_streamable_http.py`).** One parametrized case exercises server-initiated elicitation over stateless HTTP. The sessionless protocol has no server-to-client back-channel, so the case is expected to xfail by construction. Guard-mode elicitation is the supported modern path.

**MCP Apps (`tests/test_apps.py`).** Two strict xfails track **sdk-feedback #2**: the SDK strips `capabilities.extensions` at pre-2026 negotiated versions, so the UI extension cannot be advertised to legacy-era clients. Modern clients receive the extension normally.

Credential-gated GitHub integration suites also use conditional xfail markers when their environment variables are absent. Those are test-environment controls rather than product gaps and are not part of the GA decision.

## Shims and their removal triggers

Every shim in the migration is temporary and carries a documented removal trigger.

| Shim | Location | Removal trigger |
| --- | --- | --- |
| `_compat.py` — camelCase field bridge | `fastmcp_slim/fastmcp/_compat.py` | User-migration aid; removed in a future release after users migrate reads to snake_case. Users can preview removal with `mcp_camelcase_compat = False`. |
| `FastMCPRequestContext` ContextVar | `fastmcp_slim/fastmcp/server/dependencies.py` | The SDK deliberately passes context as an argument with no ContextVar; FastMCP's public `get_context()` needs ambient access, and the shim also lifts `_meta`, which the SDK's `TypedDict` drops. No planned removal — this is a permanent boundary, not a beta gap. |
| `FastMCPServerMiddleware` | `fastmcp_slim/fastmcp/server/low_level.py` | Already the native SDK `ServerMiddleware` path; no cleaner hook exists. Permanent. |
| Client `get_session_id` header sniff | `fastmcp_slim/fastmcp/client/transports/http.py` | SDK exposes session id (or an `on_session_created` callback) from `streamable_http_client`, at parity with `sse_client` (sdk-feedback #5). |
| `_sdk_context_shim.py` — generic handler aliases | `fastmcp_slim/fastmcp/client/_sdk_context_shim.py` | The SDK's `ClientRequestContext` is not subscriptable, so FastMCP keeps the public generic `SamplingHandler`/`RootsHandler`/`ElicitationHandler` aliases. Permanent unless the SDK makes the context subscriptable (sdk-feedback #7). |

## Statelessness on 2026-07-28

The `2026-07-28` era is stateless by protocol construction, and the recurring maintainer question is whether that statelessness has to be woven through FastMCP everywhere. It does not — but the honest accounting has three parts: features that are legacy-only because the protocol removed the mechanism, features that already work because they never relied on a session, and a short list of design holes where the current code *doesn't error* but also *doesn't work*. Everything below concerns `2026-07-28` connections only. Every client in the field today negotiates a handshake era, where all of this behaves exactly as it always has.

**The SDK ground truth.** On the modern paths the SDK's `Connection` is strictly per-request: a fresh `Connection` is built from each POST's envelope, its `exit_stack` unwinds when the request returns, `connection.session_id` is always `None`, and `connection.state` is a fresh dict per request. The manager's `stateless` flag never enters the picture — modern routing short-circuits ahead of it. There is no standing server→client stream: notifications emitted *during* a request ride that POST's own SSE sink, and anything emitted after the POST returns is dropped (`_NO_CHANNEL`); server→client *requests* raise `NoBackChannelError`. The only replacement is `subscriptions/listen`, which carries four list-changed / resource-updated event kinds and nothing else — no logging, progress, or task-status events, no resumability, and it is not yet wired into FastMCP. There is no `EventStore` or `Last-Event-ID` on modern paths at all; both belong to the legacy transport.

### Legacy-only by construction — document, don't build

These are not bugs. The protocol removed the mechanism they depend on, so they are simply out of scope on `2026-07-28`:

- **Per-session log levels.** `logging/setLevel` is absent from the 2026 method registry, so the `_client_log_levels` handler is unreachable. There is no per-session log-level state because there is no session.
- **`EventStore` / resumability.** `EventStore`, `SessionScopedEventStore`, and Last-Event-ID resumption are never constructed on the modern paths. Resumability presupposes a durable stream, which the era does not have.
- **Ping keepalive.** Server-initiated ping is a server→client request and is therefore structurally a no-op on modern connections; the SDK owns SSE-level pings on this transport.

### Already stateless by construction — works on 2026

These work on `2026-07-28` today because they never leaned on a protocol session:

- **`tasks/get` polling.** Task result retrieval is keyed by `task_id` and backed by Docket/Redis, so a client polls across independent requests without any session affinity. This session-free polling is exactly why the execution engine survives the SEP-1686-to-SEP-2663 rework: the SEP-2663 wire shape (poll `tasks/get`, resolve in-task input via `tasks/update`) maps onto the same durable store, and SEP-2663's `Mcp-Name: <taskId>` routing header is moot for a shared-Redis deployment where any replica can serve the poll. See [the xfail register](#the-xfail-register).
- **OAuth bearer validation.** Auth is per-request bearer validation — every POST carries and re-validates its own credential.
- **In-request progress and logging notifications.** Notifications emitted while a request is still streaming ride that POST's SSE sink and are delivered normally.

### Design holes deferred to the multi-protocol workstream

The remaining items are real holes, deferred to the [first-class 2026 client](feature-program.md#first-class-2026-client) workstream because they all reduce to one unanswered question — *what is a session when the protocol has none?* The danger in each is that the code currently returns without erroring, which reads as "works" but is actually silent degradation. Again: these affect `2026-07-28` connections only; on the handshake eras every one of them behaves correctly.

- **`ctx.session_id` and `ctx.set_state` / `ctx.get_state` (broken even single-replica).** On a modern request `ctx.session_id` mints a fresh `uuid4`, cached on the per-request `connection.state` that is discarded when the request returns. So `ctx.set_state` and `ctx.get_state` silently never round-trip across requests — no error, just lost data. The open design decision is whether `session_id` should become `None` with `set_state` documented as session-era-only, or be re-based on an app-level key (the auth subject, or a client-supplied header).
- **Task push and in-task input — resolved by the SEP-2663 design, not a statelessness hole.** This was previously framed as a hole because SEP-1686 leaned on a push back-channel (the notification/elicitation relay) that dies once the submitting request returns. SEP-2663 removes the dependency: in-task input is *poll-based* — the task enters `input_required`, surfaces its outstanding elicit/sample/roots requests in an `inputRequests` map on `tasks/get`, and the client answers via `tasks/update`. That round-trips through the durable store with no session affinity, so it is stateless-safe by construction. The SEP-1686 push relay (`server/tasks/elicitation.py`, `notifications.py`) is removed; the `fastmcp-tasks` rebuild implements the poll-based channel instead. Foreground (non-task) elicitation on 2026 remains the guard-mode `InputRequiredResult`.
- **Stateful proxy affinity (degraded).** The stateful proxy's `_caches` are keyed by the per-request `Connection`, so on modern connections the proxy collapses to stateless proxying: results stay correct, but the per-session affinity guarantee is lost. This is decided alongside the `session_id` question — same root — or gated to the legacy/stdio transports.

Multi-replica concerns (per-process rate-limiter buckets, shared Redis backends for state and tasks, a Redis `SubscriptionBus`) are deployment configuration rather than protocol gaps and are out of scope for this section.

## Upstream advisory dossier

FastMCP acts as an advisor to the SDK team. The migration produced a dossier of ten findings (`sdk-feedback.md`) — verified bugs and hard edges to report upstream, plus questions to bundle into a feedback thread. The highest-priority items:

- **#1 (bug)** — SEP-1686 task result types ship but the method registries omit them. *Moot: the SEP-1686 wire shape was removed from the spec; the SEP-2663 rebuild claims `CreateTaskResult` on `tools/call` through the extensions mechanism, which the registries already admit.*
- **#2 (bug/question)** — `capabilities.extensions` stripped at pre-2026 negotiated versions. **Elevated:** this now gates the `io.modelcontextprotocol/tasks` extension (and MCP Apps) on the modern era, so it blocks a flagship v4 feature rather than an edge case. Worth prioritizing in the upstream thread.
- **#4 (security)** — DCR redirect-URI validation accepts `javascript:`/`data:` schemes.
- **#5 (hard edge)** — `streamable_http_client` drops session-id access with no replacement.
- **#8 (hard edge)** — custom server notifications are dropped, not tee'd to `message_handler`.
- **#10 (hard edge)** — 2026 push-feature degradation error quality is inconsistent. *Resolved on the FastMCP side: `ctx.elicit` / `ctx.sample` are era-gated to raise a clear error on modern connections (#4448).*

Filing is gated on maintainer approval of each issue text.

Separately, the [SDK delegation round two](feature-program.md#sdk-delegation-round-two) work depends on **three upstream feature requests** — per-session event-store scoping, a user-middleware injection hook, and a lifespan hook — that would let FastMCP collapse its HTTP builders onto the SDK's and inherit the SDK's session-owner credential enforcement.

## GA transition checklist

The beta-to-stable transition is a small set of tracked steps:

- **Stable SDK dependencies — complete.** `fastmcp-slim` requires `mcp>=2.0.0,<3.0.0` and `mcp-types>=2.0.0,<3.0.0`; the lock resolves both to 2.0.0.
- **Re-run the full suite before GA.** Confirm the three expected xfails above remain the complete set. If either strict Apps xfail starts passing, remove the marker and the corresponding compatibility note.
- **Make the extension compatibility decision explicit.** GA can accept Apps and other extensions as modern-era capabilities, or wait for the SDK to preserve `capabilities.extensions` on legacy handshakes. Record that choice in the public protocol-support docs.
- **Prepare the stable docs.** Remove prerelease installation guidance, add the `4.0.0: Fourmidable` changelog and update entries, and merge those changes to `main` before tagging so the stable docs publication PR contains them.
- **Keep the 3.x maintenance line available — complete.** `release/3.x` is protected and continues receiving security and compatibility patches for SDK v1 users.
