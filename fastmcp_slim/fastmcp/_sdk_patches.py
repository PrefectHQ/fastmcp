"""Temporary in-place patches for gaps in the pinned MCP SDK.

## SEP-1686 task methods missing from the handshake-era method registries

This shim compensates for a genuine gap in the SDK's *handshake-era*
(2025-11-25 and earlier) task registry. In the 2025-11-25 SEP-1686 model, tasks
are a first-class part of the core protocol: `CallToolRequestParams` carries a
`task: TaskMetadata` field and a task-augmented `tools/call` returns a
`CreateTaskResult`. `mcp==2.0.0b1` ships those task types (`CreateTaskResult`,
`GetTaskResult`, `GetTaskPayloadResult`, `ListTasksResult`, `CancelTaskResult`)
and the `task` request field, but its `mcp_types.methods` registries were never
wired for them: there are no `tasks/*` rows, and the handshake-era `tools/call`
result rows are a plain `CallToolResult` with no `CreateTaskResult` arm.

The lowlevel server runner (`mcp.server.runner`) serializes a handler's result
through `serialize_server_result(method, version, ...)` for any method in
`SPEC_CLIENT_METHODS`. `tools/call` is such a method, so when a FastMCP tool is
submitted as a background task (`client.call_tool(..., task=True)`) the handler
returns a `CreateTaskResult`, which fails validation against the un-widened
`tools/call` surface row -> the client sees "Handler returned an invalid
result". The `tasks/*` methods themselves are NOT in `SPEC_CLIENT_METHODS`, so
their handler results already bypass serialization and reach the wire
unvalidated; we still register their result rows here for symmetry and so the
maps are consistent if a future SDK adds them to the spec method set.

## Scope: handshake-era versions only

The widening + `tasks/*` registration is gated to
`HANDSHAKE_PROTOCOL_VERSIONS` (2025-11-25 and earlier) because those are the
versions where the 2025 SEP-1686 task model actually applies and where the
SDK's registry has the genuine gap we compensate for.

The 2026-07-28 protocol is intentionally NOT patched here. Tasks left the core
protocol in 2026-07-28 and became the separate `io.modelcontextprotocol/tasks`
extension; `CreateTaskResult` and the `task` field on `CallToolRequestParams`
do not exist in that schema (a task-augmented `tools/call` was replaced by the
mutually-recursive `CallToolResult | InputRequiredResult` result). Injecting the
2025-era `CreateTaskResult` into the 2026 `tools/call` union would assert the
wrong task model onto that protocol, so we leave its rows untouched.

This module widens the registries IN PLACE (the maps are `MappingProxyType`
views over private dicts, so we reach the backing dict via `gc.get_referents`
and mutate it, which the already-bound default-argument references in
`mcp_types.methods` observe). `install()` is idempotent.

# TODO(sdk-upstream): remove when mcp>=2.0.0bX wires SEP-1686 into the
# handshake-era method registries.
"""

from __future__ import annotations

import gc
from types import MappingProxyType, UnionType

import mcp_types
from mcp_types import methods as _methods
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

# Result type for each task method, keyed by the client request method name.
_TASK_RESULT_TYPES: dict[str, type] = {
    "tasks/get": mcp_types.GetTaskResult,
    "tasks/result": mcp_types.GetTaskPayloadResult,
    "tasks/list": mcp_types.ListTasksResult,
    "tasks/cancel": mcp_types.CancelTaskResult,
}

_installed = False


def _backing_dict(proxy: object) -> dict:
    """Return the mutable dict a MappingProxyType wraps.

    The `mcp_types.methods` surface maps are `MappingProxyType` views; their
    sole dict referent is the backing store the module's functions read through
    their default `surface=` arguments.
    """
    referents = [r for r in gc.get_referents(proxy) if isinstance(r, dict)]
    if len(referents) != 1:
        raise RuntimeError(
            "expected exactly one backing dict for the method registry proxy, "
            f"found {len(referents)}"
        )
    return referents[0]


def install() -> None:
    """Widen the SDK's server-result registry for SEP-1686 task methods.

    Idempotent. Safe to call at import time before any client/server use.
    """
    global _installed
    if _installed:
        return

    if not isinstance(_methods.SERVER_RESULTS, MappingProxyType):
        # Registry shape changed upstream; the shim no longer applies.
        _installed = True
        return

    server_results = _backing_dict(_methods.SERVER_RESULTS)

    # Gate to handshake-era versions only: the 2025 SEP-1686 task model applies
    # there, and 2026-07-28 tasks are the separate io.modelcontextprotocol/tasks
    # extension (see module docstring) — its rows must stay untouched.
    versions_with_tools_call = {
        version
        for (method, version) in server_results
        if method == "tools/call" and version in HANDSHAKE_PROTOCOL_VERSIONS
    }

    for version in versions_with_tools_call:
        # (a) widen tools/call so a CreateTaskResult validates (task submission).
        existing = server_results[("tools/call", version)]
        arms = get_union_arms(existing)
        if mcp_types.CreateTaskResult not in arms:
            server_results[("tools/call", version)] = (
                existing | mcp_types.CreateTaskResult
            )

        # (b) register the tasks/* result rows for the same versions.
        for method, result_type in _TASK_RESULT_TYPES.items():
            server_results.setdefault((method, version), result_type)

    _installed = True


def get_union_arms(row: type | UnionType) -> tuple[type, ...]:
    """Return the member types of a result row, whether a single type or union."""
    if isinstance(row, UnionType):
        return tuple(row.__args__)
    return (row,)
