"""SEP-1686 task request handlers.

Handles MCP task protocol requests: tasks/get, tasks/result, tasks/list, tasks/cancel.
These handlers query and manage existing tasks (contrast with handlers.py which creates tasks).

This module requires fastmcp[tasks] (pydocket). It is only imported when docket is available.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal

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

import fastmcp.server.context
from fastmcp.exceptions import NotFoundError
from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.tasks.config import DEFAULT_POLL_INTERVAL_MS, DEFAULT_TTL_MS
from fastmcp.server.tasks.keys import parse_task_key
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec

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


def _parse_key_version(key_suffix: str) -> tuple[str, str | None]:
    """Parse a key suffix into (name_or_uri, version).

    Keys always contain @ as a version delimiter (sentinel pattern):
    - "add@1.0" → ("add", "1.0")  # versioned
    - "add@" → ("add", None)      # unversioned
    - "user@example.com@1.0" → ("user@example.com", "1.0")  # @ in URI

    Uses rsplit to split on the LAST @ which is always the version delimiter.
    Falls back to treating the whole string as the name if @ is not present
    (for backwards compatibility with legacy task keys).
    """
    if "@" not in key_suffix:
        # Legacy key without version sentinel - treat as unversioned
        return key_suffix, None
    name_or_uri, version = key_suffix.rsplit("@", 1)
    return name_or_uri, version if version else None


async def _lookup_task_execution(
    docket: Any,
    session_id: str,
    client_task_id: str,
) -> tuple[Any, str | None, int]:
    """Look up task execution and metadata from Redis.

    Consolidates the common pattern of fetching task metadata from Redis,
    validating it exists, and retrieving the Docket execution.

    Args:
        docket: Docket instance
        session_id: Session ID
        client_task_id: Client-provided task ID

    Returns:
        Tuple of (execution, created_at, poll_interval_ms)

    Raises:
        McpError: If task not found or execution not found
    """
    task_meta_key = docket.key(f"fastmcp:task:{session_id}:{client_task_id}")
    created_at_key = docket.key(
        f"fastmcp:task:{session_id}:{client_task_id}:created_at"
    )
    poll_interval_key = docket.key(
        f"fastmcp:task:{session_id}:{client_task_id}:poll_interval"
    )

    # Fetch metadata (single round-trip with mget)
    async with docket.redis() as redis:
        task_key_bytes, created_at_bytes, poll_interval_bytes = await redis.mget(
            task_meta_key, created_at_key, poll_interval_key
        )

    # Decode and validate task_key
    task_key = task_key_bytes.decode("utf-8") if task_key_bytes else None
    if not task_key:
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message=f"Task {client_task_id} not found")
        )

    # Get execution
    execution = await docket.get_execution(task_key)
    if not execution:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=f"Task {client_task_id} execution not found",
            )
        )

    # Parse metadata with defaults
    created_at = created_at_bytes.decode("utf-8") if created_at_bytes else None
    try:
        poll_interval_ms = (
            int(poll_interval_bytes.decode("utf-8"))
            if poll_interval_bytes
            else DEFAULT_POLL_INTERVAL_MS
        )
    except (ValueError, UnicodeDecodeError):
        poll_interval_ms = DEFAULT_POLL_INTERVAL_MS

    return execution, created_at, poll_interval_ms


async def tasks_get_handler(server: FastMCP, params: dict[str, Any]) -> GetTaskResult:
    """Handle MCP 'tasks/get' request (SEP-1686).

    Args:
        server: FastMCP server instance
        params: Request params containing taskId

    Returns:
        GetTaskResult: Task status response with spec-compliant fields
    """
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

        # Get Docket instance
        docket = server._docket
        if docket is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Background tasks require Docket",
                )
            )

        # Look up task execution and metadata
        execution, created_at, poll_interval_ms = await _lookup_task_execution(
            docket, session_id, client_task_id
        )

        # Sync state from Redis
        await execution.sync()

        # Map Docket state to MCP state
        state_map = DOCKET_TO_MCP_STATE
        mcp_state: Literal[
            "working", "input_required", "completed", "failed", "cancelled"
        ] = state_map.get(execution.state, "failed")  # type: ignore[assignment]

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

        # createdAt is required per spec, but can be None from Redis
        # Parse ISO string to datetime, or use current time as fallback
        if created_at:
            try:
                created_at_dt = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                created_at_dt = datetime.now(timezone.utc)
        else:
            created_at_dt = datetime.now(timezone.utc)

        return GetTaskResult(
            taskId=client_task_id,
            status=mcp_state,
            createdAt=created_at_dt,
            lastUpdatedAt=datetime.now(timezone.utc),
            ttl=DEFAULT_TTL_MS,
            pollInterval=poll_interval_ms,
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
        task_meta_key = docket.key(f"fastmcp:task:{session_id}:{client_task_id}")
        async with docket.redis() as redis:
            task_key_bytes = await redis.get(task_meta_key)

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
        state_map = DOCKET_TO_MCP_STATE
        if execution.state not in (ExecutionState.COMPLETED, ExecutionState.FAILED):
            mcp_state = state_map.get(execution.state, "failed")
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
                _meta={  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                    "modelcontextprotocol.io/related-task": {
                        "taskId": client_task_id,
                    }
                },
            )

        # Parse task key to get component key
        key_parts = parse_task_key(task_key)
        component_key = key_parts["component_identifier"]

        # Look up component by its prefixed key (inlined from deleted get_component)
        component: Tool | Resource | ResourceTemplate | Prompt | None = None
        try:
            if component_key.startswith("tool:"):
                name, version_str = _parse_key_version(component_key[5:])
                version = VersionSpec(eq=version_str) if version_str else None
                component = await server.get_tool(name, version)
            elif component_key.startswith("resource:"):
                uri, version_str = _parse_key_version(component_key[9:])
                version = VersionSpec(eq=version_str) if version_str else None
                component = await server.get_resource(uri, version)
            elif component_key.startswith("template:"):
                uri, version_str = _parse_key_version(component_key[9:])
                version = VersionSpec(eq=version_str) if version_str else None
                component = await server.get_resource_template(uri, version)
            elif component_key.startswith("prompt:"):
                name, version_str = _parse_key_version(component_key[7:])
                version = VersionSpec(eq=version_str) if version_str else None
                component = await server.get_prompt(name, version)
        except NotFoundError:
            component = None

        if component is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Component not found for task: {component_key}",
                )
            )

        # Build related-task metadata
        related_task_meta = {
            "modelcontextprotocol.io/related-task": {
                "taskId": client_task_id,
            }
        }

        # Convert based on component type
        if isinstance(component, Tool):
            fastmcp_result = component.convert_result(raw_value)
            mcp_result = fastmcp_result.to_mcp_result()
            # Ensure we have a CallToolResult and add metadata
            if isinstance(mcp_result, mcp.types.CallToolResult):
                mcp_result._meta = related_task_meta  # type: ignore[attr-defined]
            elif isinstance(mcp_result, tuple):
                content, structured_content = mcp_result
                mcp_result = mcp.types.CallToolResult(
                    content=content,
                    structuredContent=structured_content,
                    _meta=related_task_meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                )
            else:
                mcp_result = mcp.types.CallToolResult(
                    content=mcp_result,
                    _meta=related_task_meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                )
            return mcp_result

        elif isinstance(component, Prompt):
            fastmcp_result = component.convert_result(raw_value)
            mcp_result = fastmcp_result.to_mcp_prompt_result()
            mcp_result._meta = related_task_meta  # type: ignore[attr-defined]
            return mcp_result

        elif isinstance(component, ResourceTemplate):
            fastmcp_result = component.convert_result(raw_value)
            mcp_result = fastmcp_result.to_mcp_result(component.uri_template)
            mcp_result._meta = related_task_meta  # type: ignore[attr-defined]
            return mcp_result

        elif isinstance(component, Resource):
            fastmcp_result = component.convert_result(raw_value)
            mcp_result = fastmcp_result.to_mcp_result(str(component.uri))
            mcp_result._meta = related_task_meta  # type: ignore[attr-defined]
            return mcp_result

        else:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Internal error: Unknown component type: {type(component).__name__}",
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

        # Get Docket instance
        docket = server._docket
        if docket is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Background tasks require Docket",
                )
            )

        # Look up task execution and metadata
        execution, created_at, poll_interval_ms = await _lookup_task_execution(
            docket, session_id, client_task_id
        )

        # Cancel via Docket (now sets CANCELLED state natively)
        # Note: We need to get task_key from execution.key for cancellation
        await docket.cancel(execution.key)

        # Return task status with cancelled state
        # createdAt is REQUIRED per SEP-1686 final spec (line 430)
        # Per spec lines 447-448: SHOULD NOT include related-task metadata in tasks/cancel
        return CancelTaskResult(
            taskId=client_task_id,
            status="cancelled",
            createdAt=datetime.fromisoformat(created_at)
            if created_at
            else datetime.now(timezone.utc),
            lastUpdatedAt=datetime.now(timezone.utc),
            ttl=DEFAULT_TTL_MS,
            pollInterval=poll_interval_ms,
            statusMessage="Task cancelled",
        )
