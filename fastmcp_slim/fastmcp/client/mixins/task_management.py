"""Task management methods for FastMCP Client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import mcp_types
from mcp import MCPError
from mcp_types import Result
from pydantic import ConfigDict

if TYPE_CHECKING:
    from fastmcp.client.client import Client
from mcp_types import (
    CancelTaskRequest,
    CancelTaskRequestParams,
    GetTaskPayloadRequest,
    GetTaskPayloadRequestParams,
    GetTaskRequest,
    GetTaskRequestParams,
    GetTaskResult,
    ListTasksRequest,
    PaginatedRequestParams,
)

from fastmcp.client.telemetry import client_span
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class _RawTaskPayloadResult(Result):
    """Permissive result type for `tasks/result` responses.

    Per the v2 spec, a `tasks/result` payload arrives as extra wire fields whose
    shape matches the original request's result type (CallToolResult,
    GetPromptResult, ReadResourceResult, ...). `GetTaskPayloadResult` is a bare
    `Result` that drops those fields on validation, so this subclass retains them
    with `extra="allow"`; callers re-parse the resulting dict into the concrete
    result type.
    """

    model_config = ConfigDict(
        alias_generator=Result.model_config.get("alias_generator"),
        populate_by_name=True,
        extra="allow",
    )


class ClientTaskManagementMixin:
    """Mixin providing task management methods for Client."""

    async def get_task_status(self: Client, task_id: str) -> GetTaskResult:
        """Query the status of a background task.

        Sends a 'tasks/get' MCP protocol request over the existing transport.

        Args:
            task_id: The task ID returned from call_tool_as_task

        Returns:
            GetTaskResult: Status information including taskId, status, pollInterval, etc.

        Raises:
            RuntimeError: If client not connected
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            "tasks/get",
            "tasks/get",
            task_id,
            session_id=self.transport.get_session_id(),
        ):
            request_meta = cast(
                "mcp_types.RequestParamsMeta | None", inject_trace_context()
            )
            request = GetTaskRequest(
                params=GetTaskRequestParams(
                    task_id=task_id,
                    _meta=request_meta,  # type: ignore[unknown-argument]
                )
            )
            return await self._await_with_session_monitoring(
                self.session.send_request(
                    request=request,  # type: ignore[arg-type]
                    result_type=GetTaskResult,
                )
            )

    async def get_task_result(self: Client, task_id: str) -> Any:
        """Retrieve the raw result of a completed background task.

        Sends a 'tasks/result' MCP protocol request over the existing transport.
        Returns the raw result - callers should parse it appropriately.

        Args:
            task_id: The task ID returned from call_tool_as_task

        Returns:
            Any: The raw result (could be tool, prompt, or resource result)

        Raises:
            RuntimeError: If client not connected, task not found, or task failed
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            "tasks/result",
            "tasks/result",
            task_id,
            session_id=self.transport.get_session_id(),
        ):
            request_meta = cast(
                "mcp_types.RequestParamsMeta | None", inject_trace_context()
            )
            request = GetTaskPayloadRequest(
                params=GetTaskPayloadRequestParams(
                    task_id=task_id,
                    _meta=request_meta,  # type: ignore[unknown-argument]
                )
            )
            # Return raw result - Task classes handle type-specific parsing
            result = await self._await_with_session_monitoring(
                self.session.send_request(
                    request=request,  # type: ignore[arg-type]
                    result_type=_RawTaskPayloadResult,
                )
            )
            # Return as dict for compatibility with Task class parsing. The payload
            # fields (content, structuredContent, messages, contents, ...) survive
            # via the permissive result type's extra="allow".
            return result.model_dump(exclude_none=True, by_alias=True)

    async def list_tasks(
        self: Client,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List background tasks.

        Sends a 'tasks/list' MCP protocol request to the server. If the server
        returns an empty list (indicating client-side tracking), falls back to
        querying status for locally tracked task IDs.

        Args:
            cursor: Optional pagination cursor
            limit: Maximum number of tasks to return (default 50)

        Returns:
            dict: Response with structure:
                - tasks: List of task status dicts with taskId, status, etc.
                - nextCursor: Optional cursor for next page

        Raises:
            RuntimeError: If client not connected
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            "tasks/list",
            "tasks/list",
            "",
            session_id=self.transport.get_session_id(),
        ):
            request_meta = cast(
                "mcp_types.RequestParamsMeta | None", inject_trace_context()
            )

            # Send protocol request
            params = PaginatedRequestParams(
                cursor=cursor,
                limit=limit,  # type: ignore[call-arg]  # Optional field in MCP SDK  # ty:ignore[unknown-argument]
                _meta=request_meta,  # type: ignore[unknown-argument]
            )
            request = ListTasksRequest(params=params)
            server_response = await self._await_with_session_monitoring(
                self.session.send_request(
                    request=request,  # type: ignore[invalid-argument-type]
                    result_type=mcp_types.ListTasksResult,
                )
            )

            # If server returned tasks, use those
            if server_response.tasks:
                return server_response.model_dump(by_alias=True)

            # Server returned empty - fall back to client-side tracking
            tasks = []
            for task_id in list(self._submitted_task_ids)[:limit]:
                try:
                    status = await self.get_task_status(task_id)
                    tasks.append(status.model_dump(by_alias=True))
                except MCPError:
                    # Task may have expired or been deleted, skip it
                    continue

            return {"tasks": tasks, "nextCursor": None}

    async def cancel_task(self: Client, task_id: str) -> mcp_types.CancelTaskResult:
        """Cancel a task, transitioning it to cancelled state.

        Sends a 'tasks/cancel' MCP protocol request. Task will halt execution
        and transition to cancelled state.

        Args:
            task_id: The task ID to cancel

        Returns:
            CancelTaskResult: The task status showing cancelled state

        Raises:
            RuntimeError: If task doesn't exist
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            "tasks/cancel",
            "tasks/cancel",
            task_id,
            session_id=self.transport.get_session_id(),
        ):
            request_meta = cast(
                "mcp_types.RequestParamsMeta | None", inject_trace_context()
            )
            request = CancelTaskRequest(
                params=CancelTaskRequestParams(
                    task_id=task_id,
                    _meta=request_meta,  # type: ignore[unknown-argument]
                )
            )
            return await self._await_with_session_monitoring(
                self.session.send_request(
                    request=request,  # type: ignore[invalid-argument-type]
                    result_type=mcp_types.CancelTaskResult,
                )
            )
