"""SEP-2663 task creation: enqueue an augmented tool call to Docket.

Adapted from the SEP-1686 ``submit_to_docket`` path. The wire surface changed
(a flat ``CreateTaskResult`` with ``ttlMs``/``pollIntervalMs``, no client-supplied
task id or ttl) and the SEP-1686 push machinery — the initial status
notification, the per-task subscription, and the notification subscriber — is
gone, because SEP-2663 in-task input and status are polled, not pushed. The
operational core is preserved: strict argument coercion up front, a
server-generated high-entropy task id, the auth-scoped compound key, the context
snapshot restored in the worker, and durable creation (metadata is written
before the result is returned, so a subsequent ``tasks/get`` always resolves).
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR

from fastmcp.tools.base import Tool
from fastmcp.tools.function_tool import _strict_input_validation
from fastmcp.utilities.logging import get_logger
from fastmcp_tasks.components import add_component_to_docket, coerce_task_arguments
from fastmcp_tasks.context import (
    TaskContextSnapshot,
    get_task_scope,
    register_task_server,
)
from fastmcp_tasks.dependencies import _current_docket
from fastmcp_tasks.input_store import save_current_leg, save_task_args
from fastmcp_tasks.keys import build_task_key, task_redis_prefix
from fastmcp_tasks.models import CreateTaskResult

if TYPE_CHECKING:
    from docket import Docket

    from fastmcp.server.context import Context
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)

# Redis mapping TTL buffer: keep task metadata a little longer than the Docket
# execution TTL so a client polling right at the edge still resolves the task.
TASK_MAPPING_TTL_BUFFER_SECONDS = 15 * 60

# Bounded read-your-writes wait so durable creation holds on distributed
# backends where the enqueued execution may not be immediately visible.
_DURABLE_CREATE_TIMEOUT_SECONDS = 5.0
_DURABLE_CREATE_POLL_SECONDS = 0.02


async def create_task(
    tool: Tool,
    arguments: dict[str, object] | None,
    context: Context,
) -> CreateTaskResult:
    """Run an augmented ``tools/call`` as a background task (SEP-2663).

    Coerces and validates arguments (honoring strict input validation), mints a
    server-generated task id, snapshots the request context, enqueues the tool's
    callable on Docket under the auth-scoped compound key, and returns a
    ``CreateTaskResult`` in ``working`` status. Does not return until the task's
    metadata is durably written and its execution is visible, so an immediately
    following ``tasks/get`` resolves.
    """
    # The interceptor resolves the tool via get_tool(), which for a mounted tool
    # returns a provider wrapper — but Docket registered the underlying component
    # from get_tasks() under the same key, with that component's calling
    # convention (a FunctionTool splats **kwargs; a base Tool takes the dict
    # positionally). Execute against the registered component so coercion and
    # argument-splatting match what the worker will invoke.
    component = await _registered_task_component(context, tool)

    raw_arguments = dict(arguments or {})
    coerced = coerce_task_arguments(
        component, raw_arguments, strict=_strict_input_validation()
    )

    task_id = secrets.token_urlsafe(32)
    created_at = datetime.now(timezone.utc).isoformat()

    task_scope = get_task_scope()

    docket = context.fastmcp._docket or _current_docket.get()
    if docket is None:
        raise MCPError(
            code=INTERNAL_ERROR,
            message="Background tasks require a running tasks extension (Docket).",
        )

    # Resolve mounted tasks to the owning (child) server in the worker, so
    # CurrentFastMCP()/ctx.fastmcp inside the task point at the server the tool
    # lives on rather than the root the interceptor ran on (#3571).
    register_task_server(task_id, _owning_server(tool, context.fastmcp))

    key = component.key
    task_key = build_task_key(task_scope, task_id, "tool", key)

    ttl_ms = int(docket.execution_ttl.total_seconds() * 1000)
    ttl_seconds = int(ttl_ms / 1000) + TASK_MAPPING_TTL_BUFFER_SECONDS
    poll_interval_ms = int(component.task_config.poll_interval.total_seconds() * 1000)

    prefix = task_redis_prefix(task_scope)
    task_meta_key = docket.key(f"{prefix}:{task_id}")
    created_at_key = docket.key(f"{prefix}:{task_id}:created_at")
    poll_interval_key = docket.key(f"{prefix}:{task_id}:poll_interval")

    snapshot = TaskContextSnapshot.capture(
        owning_tool_name=tool.name, owning_tool_version=tool.version
    )

    async with docket.redis() as redis:
        await redis.set(task_meta_key, task_key, ex=ttl_seconds)
        await redis.set(created_at_key, created_at, ex=ttl_seconds)
        await redis.set(poll_interval_key, str(poll_interval_ms), ex=ttl_seconds)

    # End-and-reenter state: the raw (wire) arguments feed every leg, re-coerced
    # per leg, and the leg pointer starts at leg 1 (the base task key). A guard
    # return re-enters by enqueuing the next leg with these same arguments (see
    # handlers.enqueue_next_leg).
    await save_task_args(docket, task_scope, task_id, raw_arguments, ttl_seconds)
    await save_current_leg(docket, task_scope, task_id, task_key, 1, ttl_seconds)

    await snapshot.save(docket, task_scope, task_id, ttl_seconds)

    await add_component_to_docket(
        component, docket, coerced, fn_key=key, task_key=task_key
    )

    await _await_durable_creation(docket, task_key)

    return CreateTaskResult(
        task_id=task_id,
        status="working",
        created_at=created_at,
        last_updated_at=created_at,
        ttl_ms=ttl_ms,
        poll_interval_ms=poll_interval_ms,
    )


def _owning_server(tool: Tool, fallback: FastMCP) -> FastMCP:
    """The server a mounted tool lives on, for worker context resolution.

    A mounted tool is a ``FastMCPProviderTool`` that references the child server
    it came from, so ``CurrentFastMCP()``/``ctx.fastmcp`` inside the task point at
    that server rather than the root the interceptor ran on (#3571). Resolution
    is single-level: a tool reached through several nested mounts resolves to the
    outermost mounted child (the mount point the call arrived through), which
    still reaches deeper components through its own mounts. A non-mounted tool
    falls back to the server the call arrived on.
    """
    from fastmcp.server.providers.fastmcp_provider import FastMCPProviderTool

    if isinstance(tool, FastMCPProviderTool):
        return tool._server
    return fallback


async def registered_component_for_key(server: FastMCP, component_key: str) -> Tool:
    """Return the Docket-registered task component matching ``component_key``.

    ``get_tasks()`` yields the same components registered with Docket (the
    underlying ``FunctionTool`` for a mounted tool, not a provider wrapper), so
    matching by ``key`` recovers the component whose calling convention agrees
    with the worker. Used when re-entering a task leg, where only the stored
    compound key (not the original ``Tool`` object) is available.
    """
    for component in await server.get_tasks():
        if component.key == component_key and isinstance(component, Tool):
            return component
    raise MCPError(
        code=INTERNAL_ERROR,
        message=f"No task-enabled component found for {component_key!r}.",
    )


async def enqueue_task_leg(
    server: FastMCP,
    docket: Docket,
    component: Tool,
    raw_arguments: dict[str, object],
    leg_key: str,
) -> None:
    """Enqueue a fresh Docket execution (the next leg) for a re-entered task.

    Re-coerces the stored wire arguments (each leg validates independently, as a
    foreground retry would) and adds the component's registered callable — the
    capture wrapper — under ``leg_key``. Waits for the execution to become
    durable so a ``tasks/get`` immediately after ``tasks/update`` resolves.
    """
    coerced = coerce_task_arguments(
        component, dict(raw_arguments), strict=_strict_input_validation()
    )
    await add_component_to_docket(
        component, docket, coerced, fn_key=component.key, task_key=leg_key
    )
    await _await_durable_creation(docket, leg_key)


async def _registered_task_component(context: Context, tool: Tool) -> Tool:
    """Return the component Docket registered for ``tool``'s key.

    ``get_tasks()`` yields the same components that were registered with Docket
    (the underlying ``FunctionTool`` for a mounted tool, not the provider
    wrapper the interceptor's ``get_tool`` returns). Matching by ``key`` recovers
    the registered component so the calling convention agrees with the worker.
    Falls back to the interceptor's tool if no match is found (e.g. a dynamically
    added tool not present at registration time).
    """
    for component in await context.fastmcp.get_tasks():
        if component.key == tool.key and isinstance(component, Tool):
            return component
    return tool


async def _await_durable_creation(docket: Docket, task_key: str) -> None:
    """Block until the enqueued execution is visible (durable-create MUST).

    The metadata write above already makes ``tasks/get`` resolvable; this extra
    check guards distributed backends where the execution record propagates
    slightly behind the enqueue. Bounded so a backend hiccup can't hang creation.
    """
    deadline = asyncio.get_event_loop().time() + _DURABLE_CREATE_TIMEOUT_SECONDS
    while True:
        execution = await docket.get_execution(task_key)
        if execution is not None:
            return
        if asyncio.get_event_loop().time() >= deadline:
            # SEP-2663 durable-create: a CreateTaskResult MUST NOT be returned
            # unless a subsequent tasks/get would resolve. Returning a handle
            # that can 404 is the exact failure the requirement forbids, so a
            # backend that never surfaces the execution is a create error.
            raise MCPError(
                code=INTERNAL_ERROR,
                message=(
                    "Task creation did not become durable in time; the task "
                    "backend did not surface the enqueued execution."
                ),
            )
        await asyncio.sleep(_DURABLE_CREATE_POLL_SECONDS)
