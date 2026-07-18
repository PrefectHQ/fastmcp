"""SEP-1686 task execution handlers.

Handles queuing tool/prompt/resource executions to Docket as background tasks.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

import mcp_types
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR

from fastmcp.server.dependencies import (
    _current_docket,
    get_context,
)
from fastmcp.server.tasks.config import TaskMeta
from fastmcp.server.tasks.context import (
    TaskContextSnapshot,
    get_task_scope,
    register_task_server,
    register_task_session,
)
from fastmcp.server.tasks.keys import build_task_key, task_redis_prefix
from fastmcp.tools.function_tool import _strict_input_validation
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.prompts.base import Prompt
    from fastmcp.resources.base import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.base import Tool

logger = get_logger(__name__)

# Redis mapping TTL buffer: Add 15 minutes to Docket's execution_ttl
TASK_MAPPING_TTL_BUFFER_SECONDS = 15 * 60


async def submit_to_docket(
    task_type: Literal["tool", "resource", "template", "prompt"],
    key: str,
    component: Tool | Resource | ResourceTemplate | Prompt,
    arguments: dict[str, Any] | None = None,
    task_meta: TaskMeta | None = None,
) -> mcp_types.CreateTaskResult:
    """Submit any component to Docket for background execution (SEP-1686).

    Unified handler for all component types. Called by component's internal
    methods (_run, _read, _render) when task metadata is present and mode allows.

    Queues the component's method to Docket, stores raw return values,
    and converts to MCP types on retrieval.

    Args:
        task_type: Component type for task key construction
        key: The component key as seen by MCP layer (with namespace prefix)
        component: The component instance (Tool, Resource, ResourceTemplate, Prompt)
        arguments: Arguments/params (None for Resource which has no args)
        task_meta: Task execution metadata. If task_meta.ttl is provided, it
            overrides the server default (docket.execution_ttl).

    Returns:
        CreateTaskResult: Task stub with proper Task object
    """
    # Validate and coerce arguments before creating any task state. A failure
    # here must surface before the Redis metadata and initial "working"
    # notification below are written, otherwise an invalid input would orphan a
    # task the client has already observed (#4349).
    #
    # Honor the server's strict_input_validation setting so a strict tool
    # rejects lax coercions (e.g. {"n": "1"} for n: int) at submission just as
    # it does on the synchronous call path — otherwise task=True would bypass
    # strict validation entirely.
    if arguments is not None:
        arguments = component.coerce_task_arguments(
            arguments, strict=_strict_input_validation()
        )

    # Generate server-side task ID per SEP-1686 final spec (line 375-377)
    # Server MUST generate task IDs, clients no longer provide them
    server_task_id = str(uuid.uuid4())

    # Record creation timestamp per SEP-1686 final spec (line 430). SDK v2
    # types `Task.created_at` / `TaskStatusNotificationParams.created_at` as ISO
    # strings, so carry a serialized copy for wire-crossing models.
    created_at = datetime.now(timezone.utc)
    created_at_iso = created_at.isoformat()

    ctx = get_context()

    # Authorization scope for task isolation (auth identity, or None for anonymous)
    task_scope = get_task_scope()

    # Transport session ID for notification delivery
    try:
        session_id = ctx.session_id
    except RuntimeError:
        session_id = None

    # Try the server's own Docket first; fall back to the ContextVar for
    # mounted children (whose parent server owns the Docket instance).
    docket = ctx.fastmcp._docket or _current_docket.get()
    if docket is None:
        raise MCPError(
            code=INTERNAL_ERROR,
            message="Background tasks require a running FastMCP server context",
        )

    # Register the current server so background workers resolve
    # CurrentFastMCP() / ctx.fastmcp to the correct (child) server
    # for mounted tasks. At this point ctx.fastmcp is the child because
    # we're inside the child's call_tool dispatch.
    register_task_server(server_task_id, ctx.fastmcp)

    # Build full task key with embedded metadata
    task_key = build_task_key(task_scope, server_task_id, task_type, key)

    # Determine TTL: use task_meta.ttl if provided, else docket default
    if task_meta is not None and task_meta.ttl is not None:
        ttl_ms = task_meta.ttl
    else:
        ttl_ms = int(docket.execution_ttl.total_seconds() * 1000)
    ttl_seconds = int(ttl_ms / 1000) + TASK_MAPPING_TTL_BUFFER_SECONDS

    # Store task metadata in Redis for protocol handlers
    prefix = task_redis_prefix(task_scope)
    task_meta_key = docket.key(f"{prefix}:{server_task_id}")
    created_at_key = docket.key(f"{prefix}:{server_task_id}:created_at")
    poll_interval_key = docket.key(f"{prefix}:{server_task_id}:poll_interval")
    poll_interval_ms = int(component.task_config.poll_interval.total_seconds() * 1000)

    # Snapshot all context (access token, headers, origin request ID,
    # and session_id for notification delivery in background workers)
    snapshot = TaskContextSnapshot.capture()

    async with docket.redis() as redis:
        await redis.set(task_meta_key, task_key, ex=ttl_seconds)
        await redis.set(created_at_key, created_at.isoformat(), ex=ttl_seconds)
        await redis.set(poll_interval_key, str(poll_interval_ms), ex=ttl_seconds)

    await snapshot.save(docket, task_scope, server_task_id, ttl_seconds)

    # Register session for Context access in background workers (SEP-1686)
    # This enables elicitation/sampling from background tasks via weakref
    # Skip when there is no session (programmatic calls without MCP session)
    if session_id is not None:
        register_task_session(session_id, ctx.session)

    # Send an initial tasks/status notification before queueing.
    # This guarantees clients can observe task creation immediately.
    notification = mcp_types.TaskStatusNotification.model_validate(
        {
            "method": "notifications/tasks/status",
            "params": {
                "taskId": server_task_id,
                "status": "working",
                "statusMessage": "Task submitted",
                "createdAt": created_at_iso,
                "lastUpdatedAt": created_at_iso,
                "ttl": ttl_ms,
                "pollInterval": poll_interval_ms,
            },
            "_meta": {
                "io.modelcontextprotocol/related-task": {
                    "taskId": server_task_id,
                }
            },
        }
    )
    # SDK v2: `ServerNotification` is a union type, not a wrapper class;
    # `send_notification` takes the bare notification model directly.
    with suppress(Exception):
        # Don't let notification failures break task creation
        await ctx.session.send_notification(notification)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

    # Queue function to Docket by key (result storage via execution_ttl)
    # Use component.add_to_docket() which handles calling conventions
    # `fn_key` is the function lookup key (e.g., "child_multiply")
    # `task_key` is the task result key (e.g., "fastmcp:task:{task_scope}:{task_id}:tool:child_multiply")
    # Resources don't take arguments; tools/prompts/templates always pass arguments (even if None/empty)
    if task_type == "resource":
        await component.add_to_docket(docket, fn_key=key, task_key=task_key)  # type: ignore[call-arg]  # ty:ignore[missing-argument]
    else:
        await component.add_to_docket(docket, arguments, fn_key=key, task_key=task_key)  # type: ignore[call-arg]  # ty:ignore[invalid-argument-type, too-many-positional-arguments]

    # Spawn subscription task to send status notifications (SEP-1686 optional feature).
    # SDK v2 constructs a ServerSession per request and exposes no per-connection
    # task group, so the subscription runs as a standalone asyncio task that
    # outlives the submitting request; it is cancelled when the connection closes.
    # Deferred: subscriptions and notifications depend on docket at import time
    from fastmcp.server.tasks.subscriptions import subscribe_to_task_updates

    subscription_task = asyncio.create_task(
        subscribe_to_task_updates(
            server_task_id,
            task_key,
            ctx.session,
            docket,
            poll_interval_ms,
        ),
        name=f"task-subscription-{server_task_id[:8]}",
    )
    connection = getattr(ctx.session, "_connection", None)
    if connection is not None:

        async def _cancel_subscription() -> None:
            if not subscription_task.done():
                subscription_task.cancel()
                with suppress(asyncio.CancelledError):
                    await subscription_task

        connection.exit_stack.push_async_callback(_cancel_subscription)

    # Deferred: notifications depends on docket at import time
    from fastmcp.server.tasks.notifications import (
        ensure_subscriber_running,
        stop_subscriber,
    )

    if session_id is not None:
        try:
            await ensure_subscriber_running(
                session_id, ctx.session, docket, ctx.fastmcp
            )

            # Register cleanup callback on connection exit (once per session).
            # SDK v2 constructs ServerSession per request, so the stable
            # per-connection lifecycle hook lives on the underlying Connection
            # (`connection.exit_stack`), not the session. The registration flag
            # is likewise stashed on the connection's `state` so it survives
            # across requests.
            connection = getattr(ctx.session, "_connection", None)
            if connection is not None and not connection.state.get(
                "_notification_cleanup_registered"
            ):

                async def _cleanup_subscriber() -> None:
                    await stop_subscriber(session_id)  # type: ignore[arg-type]

                connection.exit_stack.push_async_callback(_cleanup_subscriber)
                connection.state["_notification_cleanup_registered"] = True
        except Exception as e:
            # Non-fatal: elicitation will still work via polling fallback
            logger.debug("Failed to start notification subscriber: %s", e)

    # Return CreateTaskResult with proper Task object
    # Tasks MUST begin in "working" status per SEP-1686 final spec (line 381)
    return mcp_types.CreateTaskResult(
        task=mcp_types.Task(
            task_id=server_task_id,
            status="working",
            created_at=created_at_iso,
            last_updated_at=created_at_iso,
            ttl=ttl_ms,
            poll_interval=poll_interval_ms,
        )
    )
