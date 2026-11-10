---
title: Client Groups
---

**Status: In review (#4904).** `ClientGroup` coordinates independent FastMCP clients — one managed connection per server — with collision-checked tool namespacing and call routing. User-facing usage is documented at [Client Groups](https://gofastmcp.com/clients/client-groups).

## TL;DR

Applications that hand tools from several servers to one agent previously chose between two bad shapes. `Client(config)` composes servers behind an in-process proxy, so every backend shares one negotiated protocol era — one handshake-era backend reconnects every modern backend under the handshake era — and concurrent calls funnel through one frontend session. One `Client` per server keeps each negotiated era, but nothing owns the composition: callers hand-roll namespacing, collision detection, lifecycle, and routing.

`ClientGroup` is the object between those two: a mapping of named clients where each connection negotiates independently, tools are published as `{server}_{tool}`, and calls route back through the client that advertised the tool. It lives at `fastmcp.client.group` — in the main namespace, since the surface is additive and small.

## Motivation

The immediate driver was per-server protocol pinning: agent integrations need `mode="legacy"` on one server and `mode="auto"` on another, which a single aggregate connection cannot express. Namespacing requests (a prefix for a single server's tools) kept arriving alongside it, because the same integrations feed multiple servers' tools to one model.

## Decisions

**Group over kwarg.** A `tool_name_prefix` kwarg on `Client` was fully implemented as a comparison (#4932, closed). It was rejected because a prefix is collision policy, and collision policy belongs with the composition object that detects collisions. It also would have made the stable single-server client present names the server never advertised. The group's dict keys are the namespace; a one-client group is the supported way to get namespacing alone.

**No synthetic frontend.** There is no aggregate session and no protocol translation. `resolve_tool()` returns the server name, the owning `Client`, and the upstream tool name, because tool adapters need to bind per-tool behavior (session-driven input loops, handlers, interceptors) to the real connection. The group never stands between an adapter and the client.

**Invariants over flexibility.** Membership is immutable after construction (routes hold the advertising client and would go stale under mutation). Group entry is race-guarded and connects clients concurrently, unwinding successes on partial failure. A known route only requires its own client to be connected, so one dead server does not fail calls routed to healthy servers; full-fleet connectivity is required only for catalog discovery, which queries everyone.

**Tool-only for now.** Resources have URI-based identity with different collision rules, and prompts a different invocation surface. Expanding beyond tools should follow concrete usage rather than declaring those policies by analogy.

## Performance

Measured against two real local streamable-HTTP servers (ratios are the finding, not the absolute numbers): routed calls through the group are indistinguishable from direct client calls (p50 1.89ms both), while the `Client(config)` proxy adds ~65% per call. Under concurrency the difference compounds — 200 concurrent calls across both servers completed ~6x faster through the group (~145ms vs ~875ms), because the group fans out over independent sessions while the proxy serializes through one. Group entry connects clients concurrently, so entry latency stays roughly one handshake deep instead of growing linearly with server count.

## Relationship to the upstream SDK group

The MCP Python SDK's `ClientSessionGroup` is the prior art. Its `connect_to_server()` owns lifecycle but only speaks the classic handshake; `connect_with_session()` can register an externally negotiated modern session but does not own it. `ClientGroup` combines independent modern negotiation with group-owned lifecycle while keeping calls on the configured FastMCP client surface (auth, tracing, caching, result parsing, progress, multi-round tools).
