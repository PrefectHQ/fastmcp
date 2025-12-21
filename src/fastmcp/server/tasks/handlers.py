"""SEP-1686 task execution handlers.

Handles queuing tool/prompt/resource executions to Docket as background tasks.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import mcp.types
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from fastmcp.server.dependencies import _current_docket, get_context
from fastmcp.server.tasks.keys import build_task_key

if TYPE_CHECKING:
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.server.server import FastMCP

# Redis mapping TTL buffer: Add 15 minutes to Docket's execution_ttl
TASK_MAPPING_TTL_BUFFER_SECONDS = 15 * 60


async def handle_tool_as_task(
    server: FastMCP,
    tool_name: str,
    arguments: dict[str, Any],
    _task_meta: dict[str, Any],
) -> mcp.types.CreateTaskResult:
    """Handle tool execution as background task (SEP-1686).

    Queues the user's actual function to Docket (preserving signature for DI),
    stores raw return values, converts to MCP types on retrieval.

    Note: Client-requested TTL in task_meta is intentionally ignored.
    Server-side TTL policy (docket.execution_ttl) takes precedence for
    consistent task lifecycle management.

    Args:
        server: FastMCP server instance
        tool_name: Name of the tool to execute
        arguments: Tool arguments
        _task_meta: Task metadata from request (unused - server TTL policy applies)

    Returns:
        CreateTaskResult: Task stub with proper Task object
    """
    # Generate server-side task ID per SEP-1686 final spec (line 375-377)
    # Server MUST generate task IDs, clients no longer provide them
    server_task_id = str(uuid.uuid4())

    # Record creation timestamp per SEP-1686 final spec (line 430)
    created_at = datetime.now(timezone.utc)

    # Get session ID and Docket
    ctx = get_context()
    session_id = ctx.session_id

    docket = _current_docket.get()
    if docket is None:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="Background tasks require a running FastMCP server context",
            )
        )

    # Build full task key with embedded metadata
    task_key = build_task_key(session_id, server_task_id, "tool", tool_name)

    # Get the tool to access user's function
    tool = await server.get_tool(tool_name)

    # Store task key mapping and creation timestamp in Redis for protocol handlers
    redis_key = f"fastmcp:task:{session_id}:{server_task_id}"
    created_at_key = f"fastmcp:task:{session_id}:{server_task_id}:created_at"
    ttl_seconds = int(
        docket.execution_ttl.total_seconds() + TASK_MAPPING_TTL_BUFFER_SECONDS
    )
    async with docket.redis() as redis:
        await redis.set(redis_key, task_key, ex=ttl_seconds)
        await redis.set(created_at_key, created_at.isoformat(), ex=ttl_seconds)

    # Send notifications/tasks/created per SEP-1686 (mandatory)
    # Send BEFORE queuing to avoid race where task completes before notification
    notification = mcp.types.JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/tasks/created",
        params={},  # Empty params per spec
        _meta={  # taskId in _meta per spec
            "modelcontextprotocol.io/related-task": {
                "taskId": server_task_id,
            }
        },
    )

    ctx = get_context()
    with suppress(Exception):
        # Don't let notification failures break task creation
        await ctx.session.send_notification(notification)  # type: ignore[arg-type]

    # Queue function to Docket by key (result storage via execution_ttl)
    # Use tool.add_to_docket() which handles calling conventions
    await tool.add_to_docket(docket, arguments, key=task_key)

    # Spawn subscription task to send status notifications (SEP-1686 optional feature)
    from fastmcp.server.tasks.subscriptions import subscribe_to_task_updates

    # Start subscription in session's task group (persists for connection lifetime)
    if hasattr(ctx.session, "_subscription_task_group"):
        tg = ctx.session._subscription_task_group  # type: ignore[attr-defined]
        if tg:
            tg.start_soon(  # type: ignore[union-attr]
                subscribe_to_task_updates,
                server_task_id,
                task_key,
                ctx.session,
                docket,
            )

    # Return CreateTaskResult with proper Task object
    # Tasks MUST begin in "working" status per SEP-1686 final spec (line 381)
    return mcp.types.CreateTaskResult(
        task=mcp.types.Task(
            taskId=server_task_id,
            status="working",
            createdAt=created_at,
            lastUpdatedAt=created_at,
            ttl=int(docket.execution_ttl.total_seconds() * 1000),
            pollInterval=1000,
        )
    )


async def handle_prompt_as_task(
    server: FastMCP,
    prompt_name: str,
    arguments: dict[str, Any] | None,
    _task_meta: dict[str, Any],
) -> mcp.types.CreateTaskResult:
    """Handle prompt execution as background task (SEP-1686).

    Queues the user's actual function to Docket (preserving signature for DI).

    Note: Client-requested TTL in task_meta is intentionally ignored.
    Server-side TTL policy (docket.execution_ttl) takes precedence.

    Args:
        server: FastMCP server instance
        prompt_name: Name of the prompt to execute
        arguments: Prompt arguments
        _task_meta: Task metadata from request (unused - server TTL policy applies)

    Returns:
        CreateTaskResult: Task stub with proper Task object
    """
    # Generate server-side task ID per SEP-1686 final spec (line 375-377)
    # Server MUST generate task IDs, clients no longer provide them
    server_task_id = str(uuid.uuid4())

    # Record creation timestamp per SEP-1686 final spec (line 430)
    created_at = datetime.now(timezone.utc)

    # Get session ID and Docket
    ctx = get_context()
    session_id = ctx.session_id

    docket = _current_docket.get()
    if docket is None:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="Background tasks require a running FastMCP server context",
            )
        )

    # Build full task key with embedded metadata
    task_key = build_task_key(session_id, server_task_id, "prompt", prompt_name)

    # Get the prompt
    prompt = await server.get_prompt(prompt_name)

    # Store task key mapping and creation timestamp in Redis for protocol handlers
    redis_key = f"fastmcp:task:{session_id}:{server_task_id}"
    created_at_key = f"fastmcp:task:{session_id}:{server_task_id}:created_at"
    ttl_seconds = int(
        docket.execution_ttl.total_seconds() + TASK_MAPPING_TTL_BUFFER_SECONDS
    )
    async with docket.redis() as redis:
        await redis.set(redis_key, task_key, ex=ttl_seconds)
        await redis.set(created_at_key, created_at.isoformat(), ex=ttl_seconds)

    # Send notifications/tasks/created per SEP-1686 (mandatory)
    # Send BEFORE queuing to avoid race where task completes before notification
    notification = mcp.types.JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/tasks/created",
        params={},
        _meta={
            "modelcontextprotocol.io/related-task": {
                "taskId": server_task_id,
            }
        },
    )
    with suppress(Exception):
        await ctx.session.send_notification(notification)  # type: ignore[arg-type]

    # Queue function to Docket by key (result storage via execution_ttl)
    # Use prompt.add_to_docket() which handles calling conventions
    await prompt.add_to_docket(docket, arguments, key=task_key)

    # Spawn subscription task to send status notifications (SEP-1686 optional feature)
    from fastmcp.server.tasks.subscriptions import subscribe_to_task_updates

    # Start subscription in session's task group (persists for connection lifetime)
    if hasattr(ctx.session, "_subscription_task_group"):
        tg = ctx.session._subscription_task_group  # type: ignore[attr-defined]
        if tg:
            tg.start_soon(  # type: ignore[union-attr]
                subscribe_to_task_updates,
                server_task_id,
                task_key,
                ctx.session,
                docket,
            )

    # Return CreateTaskResult with proper Task object
    # Tasks MUST begin in "working" status per SEP-1686 final spec (line 381)
    return mcp.types.CreateTaskResult(
        task=mcp.types.Task(
            taskId=server_task_id,
            status="working",
            createdAt=created_at,
            lastUpdatedAt=created_at,
            ttl=int(docket.execution_ttl.total_seconds() * 1000),
            pollInterval=1000,
        )
    )


async def handle_resource_as_task(
    _server: FastMCP,
    uri: str,
    resource: Resource | ResourceTemplate,
    _task_meta: dict[str, Any],
) -> mcp.types.CreateTaskResult:
    """Handle resource read as background task (SEP-1686).

    Queues the user's actual function to Docket.

    Note: Client-requested TTL in task_meta is intentionally ignored.
    Server-side TTL policy (docket.execution_ttl) takes precedence.

    Args:
        _server: FastMCP server instance (unused - kept for signature consistency)
        uri: Resource URI
        resource: Resource or ResourceTemplate object
        _task_meta: Task metadata from request (unused - server TTL policy applies)

    Returns:
        CreateTaskResult: Task stub with proper Task object
    """
    # Generate server-side task ID per SEP-1686 final spec (line 375-377)
    # Server MUST generate task IDs, clients no longer provide them
    server_task_id = str(uuid.uuid4())

    # Record creation timestamp per SEP-1686 final spec (line 430)
    created_at = datetime.now(timezone.utc)

    # Get session ID and Docket
    ctx = get_context()
    session_id = ctx.session_id

    docket = _current_docket.get()
    if docket is None:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="Background tasks require Docket",
            )
        )

    # Build full task key with embedded metadata (use original URI)
    task_key = build_task_key(session_id, server_task_id, "resource", str(uri))

    # Store task key mapping and creation timestamp in Redis for protocol handlers
    redis_key = f"fastmcp:task:{session_id}:{server_task_id}"
    created_at_key = f"fastmcp:task:{session_id}:{server_task_id}:created_at"
    ttl_seconds = int(
        docket.execution_ttl.total_seconds() + TASK_MAPPING_TTL_BUFFER_SECONDS
    )
    async with docket.redis() as redis:
        await redis.set(redis_key, task_key, ex=ttl_seconds)
        await redis.set(created_at_key, created_at.isoformat(), ex=ttl_seconds)

    # Send notifications/tasks/created per SEP-1686 (mandatory)
    # Send BEFORE queuing to avoid race where task completes before notification
    notification = mcp.types.JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/tasks/created",
        params={},
        _meta={
            "modelcontextprotocol.io/related-task": {
                "taskId": server_task_id,
            }
        },
    )
    with suppress(Exception):
        await ctx.session.send_notification(notification)  # type: ignore[arg-type]

    # Queue function to Docket by key (result storage via execution_ttl)
    # Use add_to_docket() which handles calling conventions
    from fastmcp.resources.template import ResourceTemplate, match_uri_template

    if isinstance(resource, ResourceTemplate):
        params = match_uri_template(uri, resource.uri_template) or {}
        await resource.add_to_docket(docket, params, key=task_key)
    else:
        await resource.add_to_docket(docket, key=task_key)

    # Spawn subscription task to send status notifications (SEP-1686 optional feature)
    from fastmcp.server.tasks.subscriptions import subscribe_to_task_updates

    # Start subscription in session's task group (persists for connection lifetime)
    if hasattr(ctx.session, "_subscription_task_group"):
        tg = ctx.session._subscription_task_group  # type: ignore[attr-defined]
        if tg:
            tg.start_soon(  # type: ignore[union-attr]
                subscribe_to_task_updates,
                server_task_id,
                task_key,
                ctx.session,
                docket,
            )

    # Return CreateTaskResult with proper Task object
    # Tasks MUST begin in "working" status per SEP-1686 final spec (line 381)
    return mcp.types.CreateTaskResult(
        task=mcp.types.Task(
            taskId=server_task_id,
            status="working",
            createdAt=created_at,
            lastUpdatedAt=created_at,
            ttl=int(docket.execution_ttl.total_seconds() * 1000),
            pollInterval=1000,
        )
    )
