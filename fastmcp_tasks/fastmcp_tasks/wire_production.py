"""Server-side production of the tasks extension's claimed `tools/call` result.

The MCP SDK ships the *consumption* half of SEP-2133 claimed results — a client
`ResultClaim` resolves an extension's result shape on a core method — but not
the *production* half: nothing lets a server emit one. On the modern protocol
the runner revalidates every `tools/call` result against
`SERVER_RESULTS[("tools/call", "2026-07-28")]`, which admits only
`CallToolResult | InputRequiredResult`. A returned `CreateTaskResult` is coerced
through those `extra="ignore"` models and stripped to nothing — the `taskId`
never reaches the client, so the tasks extension cannot create a task over the
wire even though its `tasks/*` methods (being custom methods) serialize freely.

This module supplies the missing production half. It wraps
`mcp_types.methods.serialize_server_result` — which the runner looks up on the
module at call time — so that a modern `tools/call` result tagged
`resultType: "task"` is validated against `CreateTaskResult` and dumped as-is,
routed by the discriminator rather than the ambiguous result union (an untagged
task dict would otherwise be swallowed by the all-optional `InputRequiredResult`
arm). Every other result delegates to the original serializer unchanged.

The wrap is process-global but inert for anything that is not a tasks server: a
server that never emits `resultType: "task"` never takes the task branch. It is
installed and reference-counted by `TasksExtension.lifespan()` so it is present
exactly while at least one tasks extension is running, and removed after the
last one stops. It is gated to modern protocol versions because claimed result
shapes exist only there.

Removal trigger: when the SDK grows a first-class server-side claim-production
API (mirroring the client `ResultClaim`), this wrap is deleted and
`TasksExtension` declares its produced claim through that API instead. See the
upstream report in the migration notes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import mcp_types.methods as _methods
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

_TASK_RESULT_TYPE = "task"
_TASK_AUGMENTED_METHOD = "tools/call"

# Sentinel distinguishing "caller passed no surface" (the runner's path, which we
# may divert) from an explicit surface another caller supplied (never diverted).
_STOCK: Any = object()

# The original module function, captured once. `None` until the first install.
_original_serialize: Any = None
_active_holds: int = 0


def _serialize_with_task_production(
    method: str,
    version: str,
    data: Mapping[str, Any],
    *,
    surface: Any = _STOCK,
) -> dict[str, Any]:
    """Serialize a server result, letting a tagged task result through.

    A modern `tools/call` result carrying `resultType: "task"` is returned as
    the producer already dumped it, rather than being validated against — and
    stripped by — the stock `CallToolResult | InputRequiredResult` surface. This
    is the same bypass the runner already applies to custom-method results
    (which skip surface validation entirely); the producer built this dict from
    a validated `CreateTaskResult`, so its shape is already correct. Every other
    result — and any call that supplies an explicit `surface` — delegates to the
    SDK's original serializer unchanged.
    """
    if (
        surface is _STOCK
        and method == _TASK_AUGMENTED_METHOD
        and version in MODERN_PROTOCOL_VERSIONS
        and isinstance(data, Mapping)
        and data.get("resultType") == _TASK_RESULT_TYPE
    ):
        return dict(data)
    if surface is _STOCK:
        return _original_serialize(method, version, data)
    return _original_serialize(method, version, data, surface=surface)


def install() -> None:
    """Install the task claim-production wrap (reference-counted, idempotent).

    Safe to call from every `TasksExtension.lifespan()`: the first call captures
    and replaces the SDK serializer, later calls only bump the reference count.
    """
    global _original_serialize, _active_holds
    _active_holds += 1
    if _original_serialize is not None:
        return
    _original_serialize = _methods.serialize_server_result
    # Runtime attribute swap: the wrapper is call-compatible (it forwards
    # `surface` when supplied and only diverts the runner's no-surface task
    # path), but ty cannot verify a monkeypatch's signature match.
    _methods.serialize_server_result = _serialize_with_task_production  # ty: ignore[invalid-assignment]


def uninstall() -> None:
    """Release one hold; restore the SDK serializer when the last one exits."""
    global _original_serialize, _active_holds
    _active_holds -= 1
    if _active_holds > 0:
        return
    _active_holds = 0
    if _original_serialize is not None:
        _methods.serialize_server_result = _original_serialize
        _original_serialize = None
