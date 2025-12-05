"""SEP-1686 task protocol handlers.

Implements MCP task protocol methods: tasks/get, tasks/result, tasks/list, tasks/cancel, tasks/delete.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import mcp.types
from docket.execution import ExecutionState
from mcp.shared.exceptions import McpError
from mcp.types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    CancelTaskResult,
    ErrorData,
    GetTaskResult,
    ListTasksResult,
)

from fastmcp.server.tasks.converters import (
    convert_prompt_result,
    convert_resource_result,
    convert_tool_result,
)
from fastmcp.server.tasks.keys import parse_task_key

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

# Map Docket execution states to MCP task status strings
# Per SEP-1686 final spec (line 381): tasks MUST begin in "working" status
DOCKET_TO_MCP_STATE: dict[ExecutionState, str] = {
    ExecutionState.SCHEDULED: "working",  # Initial state per spec
    ExecutionState.QUEUED: "working",  # Initial state per spec
    ExecutionState.RUNNING: "working",
    ExecutionState.COMPLETED: "completed",
    ExecutionState.FAILED: "failed",
    ExecutionState.CANCELLED: "cancelled",
}


async def tasks_get_handler(server: FastMCP, params: dict[str, Any]) -> GetTaskResult:
    """Handle MCP 'tasks/get' request (SEP-1686).

    Args:
        server: FastMCP server instance
        params: Request params containing taskId

    Returns:
        GetTaskResult: Task status response with spec-compliant fields
    """
    import fastmcp.server.context

    async with fastmcp.server.context.Context(fastmcp=server) as ctx:
        client_task_id = params.get("taskId")
        if not client_task_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="Missing required parameter: taskId"
                )
            )

        # Get session ID from Context
        session_id = ctx.session_id

        # Get execution from Docket (use instance attribute for cross-task access)
        docket = server._docket
        if docket is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Background tasks require Docket",
                )
            )

        # Look up full task key and creation timestamp from Redis
        redis_key = f"fastmcp:task:{session_id}:{client_task_id}"
        created_at_key = f"fastmcp:task:{session_id}:{client_task_id}:created_at"
        async with docket.redis() as redis:
            task_key_bytes = await redis.get(redis_key)
            created_at_bytes = await redis.get(created_at_key)

        task_key = None if task_key_bytes is None else task_key_bytes.decode("utf-8")
        created_at = (
            None if created_at_bytes is None else created_at_bytes.decode("utf-8")
        )

        if task_key is None:
            # Task not found - raise error per MCP protocol
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message=f"Task {client_task_id} not found"
                )
            )

        execution = await docket.get_execution(task_key)
        if execution is None:
            # Task key exists but no execution - raise error
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Task {client_task_id} execution not found",
                )
            )

        # Sync state from Redis
        await execution.sync()

        # Map Docket state to MCP state
        mcp_state = DOCKET_TO_MCP_STATE.get(execution.state, "failed")

        # Build response (use default ttl since we don't track per-task values)
        # createdAt is REQUIRED per SEP-1686 final spec (line 430)
        # Per spec lines 447-448: SHOULD NOT include related-task metadata in tasks/get
        error_message = None
        status_message = None

        if execution.state == ExecutionState.FAILED:
            try:
                await execution.get_result(timeout=timedelta(seconds=0))
            except Exception as error:
                error_message = str(error)
                status_message = f"Task failed: {error_message}"
        elif execution.progress and execution.progress.message:
            # Extract progress message from Docket if available (spec line 403)
            status_message = execution.progress.message

        return GetTaskResult(
            taskId=client_task_id,
            status=mcp_state,  # type: ignore[arg-type]
            createdAt=created_at,  # type: ignore[arg-type]
            lastUpdatedAt=datetime.now(timezone.utc),
            ttl=60000,
            pollInterval=1000,
            statusMessage=status_message,
        )


async def tasks_result_handler(server: FastMCP, params: dict[str, Any]) -> Any:
    """Handle MCP 'tasks/result' request (SEP-1686).

    Converts raw task return values to MCP types based on task type.

    Args:
        server: FastMCP server instance
        params: Request params containing taskId

    Returns:
        MCP result (CallToolResult, GetPromptResult, or ReadResourceResult)
    """
    import fastmcp.server.context

    async with fastmcp.server.context.Context(fastmcp=server) as ctx:
        client_task_id = params.get("taskId")
        if not client_task_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="Missing required parameter: taskId"
                )
            )

        # Get session ID from Context
        session_id = ctx.session_id

        # Get execution from Docket (use instance attribute for cross-task access)
        docket = server._docket
        if docket is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Background tasks require Docket",
                )
            )

        # Look up full task key from Redis
        redis_key = f"fastmcp:task:{session_id}:{client_task_id}"
        async with docket.redis() as redis:
            task_key_bytes = await redis.get(redis_key)

        task_key = None if task_key_bytes is None else task_key_bytes.decode("utf-8")

        if task_key is None:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Invalid taskId: {client_task_id} not found",
                )
            )

        execution = await docket.get_execution(task_key)
        if execution is None:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Invalid taskId: {client_task_id} not found",
                )
            )

        # Sync state from Redis
        await execution.sync()

        # Check if completed
        if execution.state not in (ExecutionState.COMPLETED, ExecutionState.FAILED):
            mcp_state = DOCKET_TO_MCP_STATE.get(execution.state, "failed")
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Task not completed yet (current state: {mcp_state})",
                )
            )

        # Get result from Docket
        try:
            raw_value = await execution.get_result(timeout=timedelta(seconds=0))
        except Exception as error:
            # Task failed - return error result
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=str(error))],
                isError=True,
                _meta={
                    "modelcontextprotocol.io/related-task": {
                        "taskId": client_task_id,
                    }
                },
            )

        # Parse task key to get type and component info
        key_parts = parse_task_key(task_key)
        task_type = key_parts["task_type"]

        # Convert based on task type (pass client_task_id for metadata)
        if task_type == "tool":
            return await convert_tool_result(
                server, raw_value, key_parts["component_identifier"], client_task_id
            )
        elif task_type == "prompt":
            return await convert_prompt_result(
                server, raw_value, key_parts["component_identifier"], client_task_id
            )
        elif task_type == "resource":
            return await convert_resource_result(
                server, raw_value, key_parts["component_identifier"], client_task_id
            )
        else:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Internal error: Unknown task type: {task_type}",
                )
            )


async def tasks_list_handler(
    server: FastMCP, params: dict[str, Any]
) -> ListTasksResult:
    """Handle MCP 'tasks/list' request (SEP-1686).

    Note: With client-side tracking, this returns minimal info.

    Args:
        server: FastMCP server instance
        params: Request params (cursor, limit)

    Returns:
        ListTasksResult: Response with tasks list and pagination
    """
    # Return empty list - client tracks tasks locally
    return ListTasksResult(tasks=[], nextCursor=None)


async def tasks_cancel_handler(
    server: FastMCP, params: dict[str, Any]
) -> CancelTaskResult:
    """Handle MCP 'tasks/cancel' request (SEP-1686).

    Cancels a running task, transitioning it to cancelled state.

    Args:
        server: FastMCP server instance
        params: Request params containing taskId

    Returns:
        CancelTaskResult: Task status response showing cancelled state
    """
    import fastmcp.server.context

    async with fastmcp.server.context.Context(fastmcp=server) as ctx:
        client_task_id = params.get("taskId")
        if not client_task_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="Missing required parameter: taskId"
                )
            )

        # Get session ID from Context
        session_id = ctx.session_id

        docket = server._docket
        if docket is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Background tasks require Docket",
                )
            )

        # Look up full task key and creation timestamp from Redis
        redis_key = f"fastmcp:task:{session_id}:{client_task_id}"
        created_at_key = f"fastmcp:task:{session_id}:{client_task_id}:created_at"
        async with docket.redis() as redis:
            task_key_bytes = await redis.get(redis_key)
            created_at_bytes = await redis.get(created_at_key)

        task_key = None if task_key_bytes is None else task_key_bytes.decode("utf-8")
        created_at = (
            None if created_at_bytes is None else created_at_bytes.decode("utf-8")
        )

        if task_key is None:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Invalid taskId: {client_task_id} not found",
                )
            )

        # Check if task exists
        execution = await docket.get_execution(task_key)
        if execution is None:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Invalid taskId: {client_task_id} not found",
                )
            )

        # Cancel via Docket (now sets CANCELLED state natively)
        await docket.cancel(task_key)

        # Return task status with cancelled state
        # createdAt is REQUIRED per SEP-1686 final spec (line 430)
        # Per spec lines 447-448: SHOULD NOT include related-task metadata in tasks/cancel
        return CancelTaskResult(
            taskId=client_task_id,
            status="cancelled",
            createdAt=created_at or datetime.now(timezone.utc).isoformat(),
            lastUpdatedAt=datetime.now(timezone.utc),
            ttl=60_000,
            pollInterval=1000,
            statusMessage="Task cancelled",
        )
