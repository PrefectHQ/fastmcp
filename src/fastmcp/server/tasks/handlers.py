"""SEP-1686 task execution handlers.

Handles queuing tool/prompt/resource executions to Docket as background tasks.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

import mcp.types
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from fastmcp.server.dependencies import _current_docket, get_access_token, get_context
from fastmcp.server.tasks.config import TaskMeta
from fastmcp.server.tasks.keys import build_task_key
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.tool import Tool

logger = get_logger(__name__)

# Redis mapping TTL buffer: Add 15 minutes to Docket's execution_ttl
TASK_MAPPING_TTL_BUFFER_SECONDS = 15 * 60


async def submit_to_docket(
    task_type: Literal["tool", "resource", "template", "prompt"],
    key: str,
    component: Tool | Resource | ResourceTemplate | Prompt,
    arguments: dict[str, Any] | None = None,
    task_meta: TaskMeta | None = None,
) -> mcp.types.CreateTaskResult:
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
    # Generate server-side task ID per SEP-1686 final spec (line 375-377)
    # Server MUST generate task IDs, clients no longer provide them
    server_task_id = str(uuid.uuid4())

    # Record creation timestamp per SEP-1686 final spec (line 430)
    created_at = datetime.now(timezone.utc)

    # Get session ID - use "internal" for programmatic calls without MCP session
    ctx = get_context()
    try:
        session_id = ctx.session_id
    except RuntimeError:
        session_id = "internal"

    docket = _current_docket.get()
    if docket is None:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="Background tasks require a running FastMCP server context",
            )
        )

    # Build full task key with embedded metadata
    task_key = build_task_key(session_id, server_task_id, task_type, key)

    # Determine TTL: use task_meta.ttl if provided, else docket default
    if task_meta is not None and task_meta.ttl is not None:
        ttl_ms = task_meta.ttl
    else:
        ttl_ms = int(docket.execution_ttl.total_seconds() * 1000)
    ttl_seconds = int(ttl_ms / 1000) + TASK_MAPPING_TTL_BUFFER_SECONDS

    # Store task metadata in Redis for protocol handlers
    task_meta_key = docket.key(f"fastmcp:task:{session_id}:{server_task_id}")
    created_at_key = docket.key(
        f"fastmcp:task:{session_id}:{server_task_id}:created_at"
    )
    poll_interval_key = docket.key(
        f"fastmcp:task:{session_id}:{server_task_id}:poll_interval"
    )
    origin_request_id_key = docket.key(
        f"fastmcp:task:{session_id}:{server_task_id}:origin_request_id"
    )
    poll_interval_ms = int(component.task_config.poll_interval.total_seconds() * 1000)
    origin_request_id = (
        str(ctx.request_context.request_id) if ctx.request_context is not None else None
    )

    # Snapshot the current access token (if any) for background task access (#3095)
    access_token = get_access_token()
    access_token_key = docket.key(
        f"fastmcp:task:{session_id}:{server_task_id}:access_token"
    )

    async with docket.redis() as redis:
        await redis.set(task_meta_key, task_key, ex=ttl_seconds)
        await redis.set(created_at_key, created_at.isoformat(), ex=ttl_seconds)
        await redis.set(poll_interval_key, str(poll_interval_ms), ex=ttl_seconds)
        if origin_request_id is not None:
            await redis.set(origin_request_id_key, origin_request_id, ex=ttl_seconds)
        if access_token is not None:
            await redis.set(
                access_token_key, access_token.model_dump_json(), ex=ttl_seconds
            )

    # Register session for Context access in background workers (SEP-1686)
    # This enables elicitation/sampling from background tasks via weakref
    # Skip for "internal" sessions (programmatic calls without MCP session)
    if session_id != "internal":
        from fastmcp.server.dependencies import register_task_session

        register_task_session(session_id, ctx.session)

    # Send an initial tasks/status notification before queueing.
    # This guarantees clients can observe task creation immediately.
    notification = mcp.types.TaskStatusNotification.model_validate(
        {
            "method": "notifications/tasks/status",
            "params": {
                "taskId": server_task_id,
                "status": "working",
                "statusMessage": "Task submitted",
                "createdAt": created_at,
                "lastUpdatedAt": created_at,
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
    server_notification = mcp.types.ServerNotification(notification)
    with suppress(Exception):
        # Don't let notification failures break task creation
        await ctx.session.send_notification(server_notification)

    # Queue function to Docket by key (result storage via execution_ttl)
    # Use component.add_to_docket() which handles calling conventions
    # `fn_key` is the function lookup key (e.g., "child_multiply")
    # `task_key` is the task result key (e.g., "fastmcp:task:{session}:{task_id}:tool:child_multiply")
    # Resources don't take arguments; tools/prompts/templates always pass arguments (even if None/empty)
    if task_type == "resource":
        await component.add_to_docket(docket, fn_key=key, task_key=task_key)  # type: ignore[call-arg]
    else:
        await component.add_to_docket(docket, arguments, fn_key=key, task_key=task_key)  # type: ignore[call-arg]

    # Spawn subscription task to send status notifications (SEP-1686 optional feature)
    from fastmcp.server.tasks.subscriptions import subscribe_to_task_updates

    # Start subscription in session's task group (persists for connection lifetime)
    if hasattr(ctx.session, "_subscription_task_group"):
        tg = ctx.session._subscription_task_group
        if tg:
            tg.start_soon(  # type: ignore[union-attr]
                subscribe_to_task_updates,
                server_task_id,
                task_key,
                ctx.session,
                docket,
                poll_interval_ms,
            )

    # Start notification subscriber for distributed elicitation (idempotent)
    # This enables ctx.elicit() to work when workers run in separate processes
    # Subscriber forwards notifications from Redis queue to client session
    from fastmcp.server.tasks.notifications import (
        ensure_subscriber_running,
        stop_subscriber,
    )

    try:
        await ensure_subscriber_running(session_id, ctx.session, docket, ctx.fastmcp)

        # Register cleanup callback on session exit (once per session)
        # This ensures subscriber is stopped when the session disconnects
        if (
            hasattr(ctx.session, "_exit_stack")
            and ctx.session._exit_stack is not None
            and not getattr(ctx.session, "_notification_cleanup_registered", False)
        ):

            async def _cleanup_subscriber() -> None:
                await stop_subscriber(session_id)

            ctx.session._exit_stack.push_async_callback(_cleanup_subscriber)
            ctx.session._notification_cleanup_registered = True  # type: ignore[attr-defined]
    except Exception as e:
        # Non-fatal: elicitation will still work via polling fallback
        logger.debug("Failed to start notification subscriber: %s", e)

    # Return CreateTaskResult with proper Task object
    # Tasks MUST begin in "working" status per SEP-1686 final spec (line 381)
    return mcp.types.CreateTaskResult(
        task=mcp.types.Task(
            taskId=server_task_id,
            status="working",
            createdAt=created_at,
            lastUpdatedAt=created_at,
            ttl=ttl_ms,
            pollInterval=poll_interval_ms,
        )
    )
