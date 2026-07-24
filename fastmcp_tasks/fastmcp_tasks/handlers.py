"""SEP-2663 task query/management handlers: tasks/get, tasks/update, tasks/cancel.

Adapted from the SEP-1686 ``requests.py``. The three CRUD-ish handlers survive,
reshaped to the new wire:

- ``tasks/get`` merges the old ``tasks/get`` and ``tasks/result``: the finished
  result is *inlined* into the response for a completed task, a JSON-RPC-shaped
  ``error`` for a failed one, and the outstanding ``inputRequests`` for a task
  waiting on input.
- ``tasks/update`` is new: it delivers ``inputResponses`` to the in-task input
  store, resuming a parked worker.
- ``tasks/cancel`` returns an empty ack (SEP-2663) instead of a task snapshot.
- ``tasks/list`` and ``tasks/result`` are gone (removed by SEP-2663).

The auth-scoped compound key is the authorization boundary: a request resolves a
task only under its own scope's Redis prefix, so a scope mismatch is
indistinguishable from a missing task (both raise -32602 "Task not found"),
which avoids leaking task existence across callers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal

import mcp_types
from docket.execution import ExecutionState
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from fastmcp.exceptions import NotFoundError
from fastmcp.tools.base import InputRequiredToolResult, Tool, ToolResult
from fastmcp.utilities.tasks import DEFAULT_POLL_INTERVAL_MS
from fastmcp.utilities.versions import VersionSpec
from fastmcp_tasks.context import get_task_scope
from fastmcp_tasks.creation import (
    TASK_MAPPING_TTL_BUFFER_SECONDS,
    enqueue_task_leg,
    registered_component_for_key,
)
from fastmcp_tasks.input_store import (
    acquire_update_lock,
    acquire_update_lock_blocking,
    clear_outstanding,
    is_cancelled,
    load_current_leg,
    load_task_args,
    mark_cancelled,
    read_outstanding_inputs,
    refresh_current_leg_ttl,
    release_update_lock,
    save_current_leg,
    store_input_responses,
    translate_responses,
)
from fastmcp_tasks.keys import (
    leg_execution_key,
    parse_task_key,
    task_redis_prefix,
)
from fastmcp_tasks.models import (
    CancelTaskResult,
    GetTaskResult,
    UpdateTaskResult,
)

if TYPE_CHECKING:
    from docket import Docket

    from fastmcp.server.server import FastMCP

# Docket execution state -> SEP-2663 task status. `input_required` is not a
# Docket state; it is derived from the in-task input store (see tasks_get).
DOCKET_TO_MCP_STATE: dict[ExecutionState, str] = {
    ExecutionState.SCHEDULED: "working",
    ExecutionState.QUEUED: "working",
    ExecutionState.RUNNING: "working",
    ExecutionState.COMPLETED: "completed",
    ExecutionState.FAILED: "failed",
    ExecutionState.CANCELLED: "cancelled",
}


def _task_not_found(task_id: str) -> MCPError:
    """The single "not found" error for missing, expired, or cross-scope ids.

    Uses one message for all three so a caller cannot probe another scope's task
    ids by distinguishing "not yours" from "does not exist".
    """
    return MCPError(code=INVALID_PARAMS, message=f"Task {task_id} not found")


def _normalize_iso_timestamp(stored: str | None) -> str:
    """Return an ISO 8601 timestamp for createdAt, tolerating a missing value."""
    if stored:
        try:
            return datetime.fromisoformat(stored.replace("Z", "+00:00")).isoformat()
        except (ValueError, AttributeError):
            pass
    return datetime.now(timezone.utc).isoformat()


def _parse_key_version(key_suffix: str) -> tuple[str, str | None]:
    """Split a component key suffix into (name, version) on the last ``@``."""
    if "@" not in key_suffix:
        return key_suffix, None
    name, version = key_suffix.rsplit("@", 1)
    return name, version if version else None


def _ttl_ms(docket: Docket) -> int:
    """The task TTL in milliseconds, from Docket's execution TTL (server-set)."""
    return int(docket.execution_ttl.total_seconds() * 1000)


def _task_key_ttl_seconds(docket: Docket) -> int:
    """Wall-clock TTL for a task's Redis metadata keys.

    Docket's ``execution_ttl`` plus a buffer (matching task creation), so a key
    written or refreshed now comfortably outlives the execution-retention
    window. Sliding expiration on each poll keeps it alive for long legs.
    """
    return int(docket.execution_ttl.total_seconds()) + TASK_MAPPING_TTL_BUFFER_SECONDS


async def _lookup_task(
    docket: Docket, task_scope: str | None, task_id: str
) -> tuple[Any, str, int, str | None, int]:
    """Resolve a task's current-leg execution and metadata within the scope.

    Returns ``(execution, base_task_key, leg_number, created_at,
    poll_interval_ms)``. The execution is the *current leg* (the latest Docket
    execution), which for a re-entered task differs from the base task key.
    Raises the shared "not found" error when the scope-prefixed metadata is
    absent or the current leg's execution has expired.
    """
    prefix = task_redis_prefix(task_scope)
    meta_key = docket.key(f"{prefix}:{task_id}")
    created_at_key = docket.key(f"{prefix}:{task_id}:created_at")
    poll_key = docket.key(f"{prefix}:{task_id}:poll_interval")

    async with docket.redis() as redis:
        # Docket's Redis client mirrors redis-py's variadic ``mget(*keys)`` at
        # runtime; its type stub declares a single ``Sequence`` arg, so the
        # positional form is correct but needs a targeted ignore.
        values = await redis.mget(meta_key, created_at_key, poll_key)  # ty: ignore[too-many-positional-arguments]
    task_key_bytes, created_at_bytes, poll_bytes = values

    base_task_key = task_key_bytes.decode("utf-8") if task_key_bytes else None
    if not base_task_key:
        raise _task_not_found(task_id)

    current_leg_key, leg_number = await load_current_leg(docket, task_scope, task_id)
    execution_key = current_leg_key or base_task_key
    execution = await docket.get_execution(execution_key)
    if not execution:
        raise _task_not_found(task_id)

    # Sliding expiration: an actively-polled task refreshes its routing keys so
    # they never expire mid-execution — a resumed leg that runs longer than the
    # keys' wall-clock TTL would otherwise strand `_lookup_task` on the base leg.
    refresh_ttl = _task_key_ttl_seconds(docket)
    async with docket.redis() as redis:
        await redis.expire(meta_key, refresh_ttl)
        await redis.expire(created_at_key, refresh_ttl)
        await redis.expire(poll_key, refresh_ttl)
    await refresh_current_leg_ttl(docket, task_scope, task_id, refresh_ttl)

    created_at = created_at_bytes.decode("utf-8") if created_at_bytes else None

    try:
        poll_interval_ms = (
            int(poll_bytes.decode("utf-8")) if poll_bytes else DEFAULT_POLL_INTERVAL_MS
        )
    except (ValueError, UnicodeDecodeError):
        poll_interval_ms = DEFAULT_POLL_INTERVAL_MS

    return execution, base_task_key, leg_number, created_at, poll_interval_ms


async def _resolve_tool(server: FastMCP, task_key: str) -> Tool:
    """Resolve the Tool a task ran, from its compound key (tools-only surface)."""
    component_key = parse_task_key(task_key)["component_identifier"]
    if not component_key.startswith("tool:"):
        raise MCPError(
            code=mcp_types.INTERNAL_ERROR,
            message=f"Task component is not a tool: {component_key}",
        )
    name, version_str = _parse_key_version(component_key[len("tool:") :])
    version = VersionSpec(eq=version_str) if version_str else None
    try:
        tool = await server.get_tool(name, version)
    except NotFoundError:
        tool = None
    if tool is None:
        raise MCPError(
            code=mcp_types.INTERNAL_ERROR,
            message=f"Component not found for task: {component_key}",
        )
    return tool


def _inline_result(tool: Tool, raw_value: Any) -> dict[str, Any]:
    """Convert a completed task's raw return into an inlined CallToolResult dict.

    A completed task should never carry an ``InputRequiredResult``: a function
    tool's guard returns are captured by the end-and-reenter wrapper (see
    ``input_loop.py``), which records the leg's outstanding requests and ends the
    leg (returning ``None``), so ``tasks/get`` reports ``input_required`` rather
    than inlining. Reaching here with a guard result means a component type the
    wrapper does not wrap (e.g. a base ``Tool``) returned one, which the task
    path cannot drive — a safety net, not an expected path.
    """
    if isinstance(raw_value, mcp_types.InputRequiredResult | InputRequiredToolResult):
        raise MCPError(
            code=mcp_types.INTERNAL_ERROR,
            message=(
                f"Tool {tool.name!r} returned an input-required result as a task, "
                "but its component type is not driven by the in-task guard loop. "
                "Guard-pattern tasks are supported for function tools."
            ),
        )
    # A raised tool error arrives as an is_error ToolResult the wrapper built
    # (end-and-reenter G2); use it directly so isError round-trips. A normal
    # return is converted through the tool's own result coercion.
    if isinstance(raw_value, ToolResult):
        mcp_result = raw_value.to_mcp_result()
    else:
        mcp_result = tool.convert_result(raw_value).to_mcp_result()
    if isinstance(mcp_result, mcp_types.CallToolResult):
        call_tool_result = mcp_result
    elif isinstance(mcp_result, tuple):
        content, structured_content = mcp_result
        call_tool_result = mcp_types.CallToolResult(
            content=content, structured_content=structured_content
        )
    else:
        call_tool_result = mcp_types.CallToolResult(content=mcp_result)
    return call_tool_result.model_dump(by_alias=True, mode="json", exclude_none=True)


async def tasks_get(server: FastMCP, task_id: str) -> GetTaskResult:
    """Handle ``tasks/get``: the detailed task with its result/error/inputs inlined."""
    docket = server._docket
    if docket is None:
        raise _task_not_found(task_id)

    task_scope = get_task_scope()
    (
        execution,
        base_task_key,
        leg_number,
        created_at,
        poll_interval_ms,
    ) = await _lookup_task(docket, task_scope, task_id)
    await execution.sync()

    created_at_iso = _normalize_iso_timestamp(created_at)
    now_iso = datetime.now(timezone.utc).isoformat()
    ttl_ms = _ttl_ms(docket)

    def build(
        status: Literal[
            "working", "input_required", "completed", "failed", "cancelled"
        ],
        **payload: Any,
    ) -> GetTaskResult:
        return GetTaskResult(
            task_id=task_id,
            status=status,
            created_at=created_at_iso,
            last_updated_at=now_iso,
            ttl_ms=ttl_ms,
            poll_interval_ms=poll_interval_ms,
            **payload,
        )

    # A logical cancellation wins over the underlying execution state: a task
    # parked on input has a COMPLETED execution, so without this the branches
    # below would report input_required (or completed) for a cancelled task.
    if await is_cancelled(docket, task_scope, task_id):
        return build("cancelled")

    if execution.state == ExecutionState.COMPLETED:
        # A guard leg ends its Docket execution and records outstanding input
        # requests to Redis: a completed leg with outstanding requests is the
        # task waiting for tasks/update (input_required), not a finished task.
        outstanding = await read_outstanding_inputs(
            docket, task_scope, task_id, leg_number
        )
        if outstanding:
            return build("input_required", input_requests=outstanding)
        raw_value = await execution.get_result(timeout=timedelta(seconds=0))
        tool = await _resolve_tool(server, base_task_key)
        return build("completed", result=_inline_result(tool, raw_value))

    if execution.state == ExecutionState.FAILED:
        message = "Task failed"
        try:
            await execution.get_result(timeout=timedelta(seconds=0))
        # On a FAILED execution, get_result re-raises the exception the task
        # itself raised — an arbitrary user-defined type, so no narrower catch
        # exists. Its message becomes the task's error payload.
        except Exception as error:
            message = str(error)
        return build(
            "failed",
            status_message=message,
            error={"code": mcp_types.INTERNAL_ERROR, "message": message},
        )

    if execution.state == ExecutionState.CANCELLED:
        return build("cancelled")

    status_message = None
    if execution.progress and execution.progress.message:
        status_message = execution.progress.message
    return build("working", status_message=status_message)


async def tasks_update(
    server: FastMCP, task_id: str, input_responses: dict[str, Any]
) -> UpdateTaskResult:
    """Handle ``tasks/update``: answer a guard leg and re-enter the task.

    The responses are keyed by the surfaced keys ``tasks/get`` reported. Unknown
    or already-satisfied keys are ignored (SEP-2663). When at least one answer
    matches the current leg's outstanding requests, they are translated to the
    tool's own keys, stored for the next leg, and a fresh Docket execution (the
    next leg) is enqueued with the task's arguments. The worker is never blocked;
    re-entry is the whole mechanism. A stale or empty update is an idempotent
    no-op.
    """
    docket = server._docket
    if docket is None:
        raise _task_not_found(task_id)

    task_scope = get_task_scope()
    # Resolve within scope so a cross-scope update is a "not found", not a no-op.
    _execution, base_task_key, leg_number, _created_at, _poll = await _lookup_task(
        docket, task_scope, task_id
    )

    # Serialize concurrent updates for this task so two racing answers cannot
    # each enqueue a next leg (double execution). A loser is an idempotent no-op.
    if not await acquire_update_lock(docket, task_scope, task_id):
        return UpdateTaskResult()
    try:
        # A cancelled task never re-enters: clearing outstanding on cancel makes
        # translate return None already, but check explicitly so a cancel that
        # races between this update's lookup and lock acquisition still wins.
        if await is_cancelled(docket, task_scope, task_id):
            return UpdateTaskResult()

        translated = await translate_responses(
            docket, task_scope, task_id, leg_number, input_responses
        )
        if translated is None:
            # Nothing matched the current leg's outstanding requests: the leg was
            # already answered, or the keys are unknown. Idempotent no-op.
            return UpdateTaskResult()

        # Store the answers for the next leg to read, then enqueue that leg.
        # Ordering matters: the answers must be in Redis before the next leg's
        # worker context loads them, and current_leg must not advance to an
        # execution that is not yet durable — so enqueue (with its durable wait)
        # precedes the pointer swap.
        await store_input_responses(docket, task_scope, task_id, translated)

        component = await registered_component_for_key(
            server, parse_task_key(base_task_key)["component_identifier"]
        )
        raw_arguments = await load_task_args(docket, task_scope, task_id)
        next_leg = leg_number + 1
        next_leg_key = leg_execution_key(base_task_key, next_leg)

        await enqueue_task_leg(server, docket, component, raw_arguments, next_leg_key)
        await save_current_leg(
            docket,
            task_scope,
            task_id,
            next_leg_key,
            next_leg,
            _task_key_ttl_seconds(docket),
        )
        # The answered leg's surfaced keys are now superseded; drop them so they
        # are never reused (SEP-2663 L350).
        await clear_outstanding(docket, task_scope, task_id, leg_number)
        return UpdateTaskResult()
    finally:
        await release_update_lock(docket, task_scope, task_id)


async def tasks_cancel(server: FastMCP, task_id: str) -> CancelTaskResult:
    """Handle ``tasks/cancel``: cooperatively cancel the task, empty ack.

    A durable cancellation marker is recorded so the logical task reports
    ``cancelled`` and refuses re-entry even when it is parked on input — whose
    current Docket execution is already ``COMPLETED``, making ``docket.cancel``
    on it a no-op. The current leg's outstanding requests are cleared so a
    racing ``tasks/update`` naming them finds nothing, and the running
    execution is still cancelled cooperatively for the ``working`` case.

    Cancellation runs under the per-task update lock and re-resolves the leg
    once held, so it never cancels a stale leg while ``tasks/update`` is
    concurrently enqueuing the next one: whichever wins the lock runs to
    completion before the other, and the update rechecks the marker under the
    same lock. If the lock is wedged past its timeout, cancel proceeds
    best-effort rather than hang.
    """
    docket = server._docket
    if docket is None:
        raise _task_not_found(task_id)

    task_scope = get_task_scope()
    # Validate the task exists within scope before taking the lock.
    await _lookup_task(docket, task_scope, task_id)

    got_lock = await acquire_update_lock_blocking(docket, task_scope, task_id)
    try:
        # Re-resolve under the lock: an update that ran first has advanced the
        # current leg, so this cancels the leg that is actually live now.
        execution, _base_task_key, leg_number, _created_at, _poll = await _lookup_task(
            docket, task_scope, task_id
        )
        ttl_seconds = int(docket.execution_ttl.total_seconds())
        await mark_cancelled(docket, task_scope, task_id, ttl_seconds)
        await clear_outstanding(docket, task_scope, task_id, leg_number)
        await docket.cancel(execution.key)
    finally:
        if got_lock:
            await release_update_lock(docket, task_scope, task_id)
    return CancelTaskResult()
