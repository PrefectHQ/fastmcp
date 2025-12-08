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
    from fastmcp.server.server import FastMCP

# Redis mapping TTL buffer: Add 15 minutes to Docket's execution_ttl
TASK_MAPPING_TTL_BUFFER_SECONDS = 15 * 60


async def handle_tool_as_task(
    server: FastMCP,
    tool_name: str,
    arguments: dict[str, Any],
    task_meta: dict[str, Any],
) -> mcp.types.CallToolResult:
    """Handle tool execution as background task (SEP-1686).

    Queues the user's actual function to Docket (preserving signature for DI),
    stores raw return values, converts to MCP types on retrieval.

    Args:
        server: FastMCP server instance
        tool_name: Name of the tool to execute
        arguments: Tool arguments
        task_meta: Task metadata from request (contains ttl)

    Returns:
        CallToolResult: Task stub with task metadata in _meta
    """
    # Generate server-side task ID per SEP-1686 final spec (line 375-377)
    # Server MUST generate task IDs, clients no longer provide them
    server_task_id = str(uuid.uuid4())

    # Record creation timestamp per SEP-1686 final spec (line 430)
    # Format as ISO 8601 / RFC 3339 timestamp
    created_at = datetime.now(timezone.utc).isoformat()

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
        await redis.set(created_at_key, created_at, ex=ttl_seconds)

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

    # Queue function to Docket by name (result storage via execution_ttl)
    # Use tool.key which matches what was registered - prefixed for mounted tools
    await docket.add(
        tool.key,
        key=task_key,
    )(**arguments)

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

    # Return task stub
    # Tasks MUST begin in "working" status per SEP-1686 final spec (line 381)
    return mcp.types.CallToolResult(
        content=[],
        _meta={
            "modelcontextprotocol.io/task": {
                "taskId": server_task_id,
                "status": "working",
            }
        },
    )


async def handle_prompt_as_task(
    server: FastMCP,
    prompt_name: str,
    arguments: dict[str, Any] | None,
    task_meta: dict[str, Any],
) -> mcp.types.GetPromptResult:
    """Handle prompt execution as background task (SEP-1686).

    Queues the user's actual function to Docket (preserving signature for DI).

    Args:
        server: FastMCP server instance
        prompt_name: Name of the prompt to execute
        arguments: Prompt arguments
        task_meta: Task metadata from request (contains ttl)

    Returns:
        GetPromptResult: Task stub with task metadata in _meta
    """
    # Generate server-side task ID per SEP-1686 final spec (line 375-377)
    # Server MUST generate task IDs, clients no longer provide them
    server_task_id = str(uuid.uuid4())

    # Record creation timestamp per SEP-1686 final spec (line 430)
    # Format as ISO 8601 / RFC 3339 timestamp
    created_at = datetime.now(timezone.utc).isoformat()

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
        await redis.set(created_at_key, created_at, ex=ttl_seconds)

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

    # Queue function to Docket by name (result storage via execution_ttl)
    # Use prompt.key which matches what was registered - prefixed for mounted prompts
    await docket.add(
        prompt.key,
        key=task_key,
    )(**(arguments or {}))

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

    # Return task stub
    # Tasks MUST begin in "working" status per SEP-1686 final spec (line 381)
    return mcp.types.GetPromptResult(
        description="",
        messages=[],
        _meta={
            "modelcontextprotocol.io/task": {
                "taskId": server_task_id,
                "status": "working",
            }
        },
    )


async def handle_resource_as_task(
    server: FastMCP,
    uri: str,
    resource,  # Resource or ResourceTemplate
    task_meta: dict[str, Any],
) -> mcp.types.ServerResult:
    """Handle resource read as background task (SEP-1686).

    Queues the user's actual function to Docket.

    Args:
        server: FastMCP server instance
        uri: Resource URI
        resource: Resource or ResourceTemplate object
        task_meta: Task metadata from request (contains ttl)

    Returns:
        ServerResult with ReadResourceResult stub
    """
    # Generate server-side task ID per SEP-1686 final spec (line 375-377)
    # Server MUST generate task IDs, clients no longer provide them
    server_task_id = str(uuid.uuid4())

    # Record creation timestamp per SEP-1686 final spec (line 430)
    # Format as ISO 8601 / RFC 3339 timestamp
    created_at = datetime.now(timezone.utc).isoformat()

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
        await redis.set(created_at_key, created_at, ex=ttl_seconds)

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

    # Queue function to Docket by name (result storage via execution_ttl)
    # Use resource.name which matches what was registered - prefixed for mounted resources
    # For templates, extract URI params and pass them to the function
    from fastmcp.resources.template import FunctionResourceTemplate, match_uri_template

    if isinstance(resource, FunctionResourceTemplate):
        params = match_uri_template(uri, resource.uri_template) or {}
        await docket.add(
            resource.name,
            key=task_key,
        )(**params)
    else:
        await docket.add(
            resource.name,
            key=task_key,
        )()

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

    # Return task stub
    # Tasks MUST begin in "working" status per SEP-1686 final spec (line 381)
    return mcp.types.ServerResult(
        mcp.types.ReadResourceResult(
            contents=[],
            _meta={
                "modelcontextprotocol.io/task": {
                    "taskId": server_task_id,
                    "status": "working",
                }
            },
        )
    )
