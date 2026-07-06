"""Temporary in-place patches for gaps in the pinned MCP SDK.

## SEP-1686 task methods missing from the SDK method registries

`mcp==2.0.0b1` ships the task types (`CreateTaskResult`, `GetTaskResult`,
`GetTaskPayloadResult`, `ListTasksResult`, `CancelTaskResult`) but its
`mcp_types.methods` registries have no `tasks/*` rows, and every `tools/call`
result row is a plain `CallToolResult` (2025 eras) or `CallToolResult |
InputRequiredResult` (2026) with no `CreateTaskResult` arm.

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

This module widens the registries IN PLACE (the maps are `MappingProxyType`
views over private dicts, so we reach the backing dict via `gc.get_referents`
and mutate it, which the already-bound default-argument references in
`mcp_types.methods` observe). `install()` is idempotent.

# TODO(sdk-upstream): remove when mcp>=2.0.0bX includes SEP-1686 in method registries
"""

from __future__ import annotations

import gc
from types import MappingProxyType, UnionType

import mcp_types
from mcp_types import methods as _methods

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

    versions_with_tools_call = {
        version for (method, version) in server_results if method == "tools/call"
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
