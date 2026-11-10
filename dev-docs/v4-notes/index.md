---
title: v4.0 Development Notes
---

This directory is the working map of FastMCP v4.0: the complete register of user-facing changes from the MCP Python SDK v2 migration ([PR #4437](https://github.com/PrefectHQ/fastmcp/pull/4437)), plus the forward v4 feature program. It plays three roles at once.

1. **A change register.** Every user-visible change from the migration, organized by subsystem, with a note on how FastMCP handles it (absorbed, bridged, breaking, or deprecated) and where to find it in the diff. This is the [Change Register](change-register.md).
2. **A feature program.** The forward v4 work — sampling removal, multi-round-trip elicitation, the first-class 2026 client, a FastMCP-native extension API, the SEP-2663 background-tasks rebuild, and the SDK-delegation round-two convergence — now a mix of shipped, designed, and pending. Multi-round-trip guard tools (#4544), the client's `mode="auto"` default with a partial SDK-composition (#4572/#4574, full composition blocked upstream), the extension API (#4602), and background tasks on SEP-2663 (#4603) have shipped; sampling removal and SDK delegation remain ahead. Each carries an explicit status in the [Feature Program](feature-program.md). The shipped side — what a v4 deployment provides on the modern protocol today, including the complete server-side SEP-990 identity assertion implementation — is cataloged in [2026-07-28 Protocol Support](protocol-2026.md).
3. **A review lens.** Because the migration PR is too large to review line by line, the change register is organized so a reviewer can take one subsystem, read its claimed changes, and verify each against the diff. The [Known Gaps](known-gaps.md) page collects the deliberate xfails and the upstream dependencies that gate the follow-up work.

## Why v4 exists

FastMCP v4.0 is an engine swap. Three forces drive the major version:

**The MCP Python SDK v2 rebuild.** The SDK v2 makes two sweeping changes to the protocol layer: it splits the protocol types out of `mcp.types` into a standalone `mcp_types` package, and it renames every protocol field from camelCase to snake_case (`inputSchema` → `input_schema`, `mimeType` → `mime_type`, `isError` → `is_error`). It also rewrites the server request-handling model — handlers are now registered by method string and return bare result models, there is no `request_ctx` ContextVar, and server-side middleware is a first-class SDK concept. FastMCP absorbs almost all of this so that a typical server needs zero code changes.

**Protocol version 2026-07-28.** The SDK v2 serves multiple protocol eras from one server. Alongside the session-based handshake eras, it introduces the sessionless `2026-07-28` era, which discovers capabilities through `server/discover` and removes server-initiated requests (SEP-2577). This formally supersedes FastMCP's earlier "latest protocol only" stance: a single server now works with clients across the protocol transition.

**Sampling and roots removed from the server API.** The `2026-07-28` era removes the server's ability to push a request back to the client mid-call, which takes `ctx.sample`, `ctx.sample_step`, and `ctx.list_roots` off the table. Rather than leave them half-working against old clients only, 4.0 removes them from the server API entirely — a real architectural shift for servers that borrowed the client's model, and one that justifies the major bump. Client-side handlers stay, because a modern client still has to answer a legacy server.

## Release strategy

The migration lives on `main`, which now depends on the stable MCP Python SDK 2.0 line. FastMCP continues cutting prereleases while the v4 APIs soak, then ships 4.0.0 from the same branch.

- **`main` owns FastMCP 4.** It carries stable `mcp>=2.0.0` and `mcp-types>=2.0.0` dependencies. Beta 4 is the current prerelease target; the [Known Gaps](known-gaps.md) page tracks the remaining decisions before 4.0.0.
- **`release/3.x` is the maintenance line.** A `release/3.x` branch is cut from pre-merge `main`. It stays on the SDK v1 line, receives upstream security patches, and serves users who cannot move to the SDK v2 beta yet.

### Release codenames

Following the pun-title convention (`v<version>: <pun>`), the v4 line runs a single "four" motif across the whole cycle, holding the headline name for the stable release the way v3 did ("Three at Last" for `3.0.0`, stage puns for its betas):

| Release | Codename | The nod |
| --- | --- | --- |
| `4.0.0a1` (alpha) | **Fourst Contact** | _first contact_ — the first, cautious look at the new engine |
| `4.0.0a2` (alpha) | **Back and Fourth** | _back and forth_ — the second pass, where background tasks and stateless state land |
| `4.0.0b1` (beta) | **Fourgone Conclusion** | _foregone conclusion_ — once the MCP SDK went v2, v4 was inevitable |
| `4.0.0b2` (beta) | **Four the Better** | _for the better_ — a hardening release focused on correctness, compatibility, and security |
| `4.0.0b3` (beta) | **Fast Fourward** | _fast forward_ — the accumulated v4 work enters its GA soak |
| `4.0.0b4` (beta) | **Calm Befour the Storm** | _calm before the storm_ — one last hardening pass before the stable release |
| `4.0.0` (stable) | **Fourmidable** | _formidable_ — the stable release of the new protocol foundation |

## How to read the register

Each subsystem section in the [Change Register](change-register.md) tags its changes with one of four dispositions:

- **Absorbed** — the SDK changed underneath, but FastMCP's public surface is identical. Nothing for users to do.
- **Bridged** — a compatibility shim keeps old code working, usually with a `FastMCPDeprecationWarning`. Users should migrate but are not forced to.
- **Breaking** — user code must change. These are the headline migration items.
- **Deprecated** — still works, warns now, slated for removal in a later release.

The user-facing summary of the migration lives in the published [Upgrading from FastMCP 3](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3) guide. These development notes are the exhaustive version behind it.
