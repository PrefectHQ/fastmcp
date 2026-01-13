"""SEP-1686 client Task classes."""

from __future__ import annotations

import abc
import asyncio
import inspect
import time
import weakref
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Generic, TypeVar

import mcp.types
from mcp.types import GetTaskResult, TaskStatusNotification

from fastmcp.client.messages import Message, MessageHandler
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult, Client


class TaskNotificationHandler(MessageHandler):
    """MessageHandler that routes task status notifications to Task objects."""

    def __init__(self, client: Client):
        super().__init__()
        self._client_ref: weakref.ref[Client] = weakref.ref(client)

    async def dispatch(self, message: Message) -> None:
        """Dispatch messages, including task status notifications."""
        if isinstance(message, mcp.types.ServerNotification):
            if isinstance(message.root, TaskStatusNotification):
                client = self._client_ref()
                if client:
                    client._handle_task_status_notification(message.root)

        await super().dispatch(message)


TaskResultT = TypeVar("TaskResultT")


class Task(abc.ABC, Generic[TaskResultT]):
    """
    Abstract base class for MCP background tasks (SEP-1686).

    Provides a uniform API whether the server accepts background execution
    or executes synchronously (graceful degradation per SEP-1686).

    Subclasses:
        - ToolTask: For tool calls (result type: CallToolResult)
        - PromptTask: For prompts (future, result type: GetPromptResult)
        - ResourceTask: For resources (future, result type: ReadResourceResult)
    """

    def __init__(
        self,
        client: Client,
        task_id: str,
        immediate_result: TaskResultT | None = None,
    ):
        """
        Create a Task wrapper.

        Args:
            client: The FastMCP client
            task_id: The task identifier
            immediate_result: If server executed synchronously, the immediate result
        """
        self._client = client
        self._task_id = task_id
        self._immediate_result = immediate_result
        self._is_immediate = immediate_result is not None

        # Notification-based optimization (SEP-1686 notifications/tasks/status)
        self._status_cache: GetTaskResult | None = None
        self._status_event: asyncio.Event | None = None  # Lazy init
        self._status_callbacks: list[
            Callable[[GetTaskResult], None | Awaitable[None]]
        ] = []
        self._cached_result: TaskResultT | None = None

    def _check_client_connected(self) -> None:
        """Validate that client context is still active.

        Raises:
            RuntimeError: If accessed outside client context (unless immediate)
        """
        if self._is_immediate:
            return  # Already resolved, no client needed

        try:
            _ = self._client.session
        except RuntimeError as e:
            raise RuntimeError(
                "Cannot access task results outside client context. "
                "Task futures must be used within 'async with client:' block."
            ) from e

    @property
    def task_id(self) -> str:
        """Get the task ID."""
        return self._task_id

    @property
    def returned_immediately(self) -> bool:
        """Check if server executed the task immediately.

        Returns:
            True if server executed synchronously (graceful degradation or no task support)
            False if server accepted background execution
        """
        return self._is_immediate

    def _handle_status_notification(self, status: GetTaskResult) -> None:
        """Process incoming notifications/tasks/status (internal).

        Called by Client when a notification is received for this task.
        Updates cache, triggers events, and invokes user callbacks.

        Args:
            status: Task status from notification
        """
        # Update cache for next status() call
        self._status_cache = status

        # Wake up any wait() calls
        if self._status_event is not None:
            self._status_event.set()

        # Invoke user callbacks
        for callback in self._status_callbacks:
            try:
                result = callback(status)
                if inspect.isawaitable(result):
                    # Fire and forget async callbacks
                    asyncio.create_task(result)  # type: ignore[arg-type] # noqa: RUF006
            except Exception as e:
                logger.warning(f"Task callback error: {e}", exc_info=True)

    def on_status_change(
        self,
        callback: Callable[[GetTaskResult], None | Awaitable[None]],
    ) -> None:
        """Register callback for status change notifications.

        The callback will be invoked when a notifications/tasks/status is received
        for this task (optional server feature per SEP-1686 lines 436-444).

        Supports both sync and async callbacks (auto-detected).

        Args:
            callback: Function to call with GetTaskResult when status changes.
                     Can return None (sync) or Awaitable[None] (async).

        Example:
            >>> task = await client.call_tool("slow_operation", {}, task=True)
            >>>
            >>> def on_update(status: GetTaskResult):
            ...     print(f"Task {status.taskId} is now {status.status}")
            >>>
            >>> task.on_status_change(on_update)
            >>> result = await task  # Callback fires when status changes
        """
        self._status_callbacks.append(callback)

    async def status(self) -> GetTaskResult:
        """Get current task status.

        If server executed immediately, returns synthetic completed status.
        Otherwise queries the server for current status.
        """
        self._check_client_connected()

        if self._is_immediate:
            # Return synthetic completed status
            now = datetime.now(timezone.utc)
            return GetTaskResult(
                taskId=self._task_id,
                status="completed",
                createdAt=now,
                lastUpdatedAt=now,
                ttl=None,
                pollInterval=1000,
            )

        # Return cached status if available (from notification)
        if self._status_cache is not None:
            cached = self._status_cache
            # Don't clear cache - keep it for next call
            return cached

        # Query server and cache the result
        self._status_cache = await self._client.get_task_status(self._task_id)
        return self._status_cache

    @abc.abstractmethod
    async def result(self) -> TaskResultT:
        """Wait for and return the task result.

        Must be implemented by subclasses to return the appropriate result type.
        """
        ...

    async def wait(
        self, *, state: str | None = None, timeout: float = 300.0
    ) -> GetTaskResult:
        """Wait for task to reach a specific state or complete.

        Uses event-based waiting when notifications are available (fast),
        with fallback to polling (reliable). Optimally wakes up immediately
        on status changes when server sends notifications/tasks/status.

        Args:
            state: Desired state ('submitted', 'working', 'completed', 'failed').
                   If None, waits for any terminal state (completed/failed)
            timeout: Maximum time to wait in seconds

        Returns:
            GetTaskResult: Final task status

        Raises:
            TimeoutError: If desired state not reached within timeout
        """
        self._check_client_connected()

        if self._is_immediate:
            # Already done
            return await self.status()

        # Initialize event for notification wake-ups
        if self._status_event is None:
            self._status_event = asyncio.Event()

        start = time.time()
        terminal_states = {"completed", "failed", "cancelled"}
        poll_interval = 0.5  # Fallback polling interval (500ms)

        while True:
            # Check cached status first (updated by notifications)
            if self._status_cache:
                current = self._status_cache.status
                if state is None:
                    if current in terminal_states:
                        return self._status_cache
                elif current == state:
                    return self._status_cache

            # Check timeout
            elapsed = time.time() - start
            if elapsed >= timeout:
                raise TimeoutError(
                    f"Task {self._task_id} did not reach {state or 'terminal state'} within {timeout}s"
                )

            remaining = timeout - elapsed

            # Wait for notification event OR poll timeout
            try:
                await asyncio.wait_for(
                    self._status_event.wait(), timeout=min(poll_interval, remaining)
                )
                self._status_event.clear()
            except asyncio.TimeoutError:
                # Fallback: poll server (notification didn't arrive in time)
                self._status_cache = await self._client.get_task_status(self._task_id)

    async def cancel(self) -> None:
        """Cancel this task, transitioning it to cancelled state.

        Sends a tasks/cancel protocol request. The server will attempt to halt
        execution and move the task to cancelled state.

        Note: If server executed immediately (graceful degradation), this is a no-op
        as there's no server-side task to cancel.
        """
        if self._is_immediate:
            # No server-side task to cancel
            return
        self._check_client_connected()
        await self._client.cancel_task(self._task_id)
        # Invalidate cache to force fresh status fetch
        self._status_cache = None

    def __await__(self):
        """Allow 'await task' to get result."""
        return self.result().__await__()


class ToolTask(Task["CallToolResult"]):
    """
    Represents a tool call that may execute in background or immediately.

    Provides a uniform API whether the server accepts background execution
    or executes synchronously (graceful degradation per SEP-1686).

    Usage:
        task = await client.call_tool_as_task("analyze", args)

        # Check status
        status = await task.status()

        # Wait for completion
        await task.wait()

        # Get result (waits if needed)
        result = await task.result()  # Returns CallToolResult

        # Or just await the task directly
        result = await task
    """

    def __init__(
        self,
        client: Client,
        task_id: str,
        tool_name: str,
        immediate_result: CallToolResult | None = None,
    ):
        """
        Create a ToolTask wrapper.

        Args:
            client: The FastMCP client
            task_id: The task identifier
            tool_name: Name of the tool being executed
            immediate_result: If server executed synchronously, the immediate result
        """
        super().__init__(client, task_id, immediate_result)
        self._tool_name = tool_name

    async def result(self) -> CallToolResult:
        """Wait for and return the tool result.

        If server executed immediately, returns the immediate result.
        Otherwise waits for background task to complete and retrieves result.

        Returns:
            CallToolResult: The parsed tool result (same as call_tool returns)
        """
        # Check cache first
        if self._cached_result is not None:
            return self._cached_result

        if self._is_immediate:
            assert self._immediate_result is not None  # Type narrowing
            result = self._immediate_result
        else:
            # Check client connected
            self._check_client_connected()

            # Wait for completion using event-based wait (respects notifications)
            await self.wait()

            # Get the raw result (dict or CallToolResult)
            raw_result = await self._client.get_task_result(self._task_id)

            # Convert to CallToolResult if needed and parse
            if isinstance(raw_result, dict):
                # Raw dict from get_task_result - parse as CallToolResult
                mcp_result = mcp.types.CallToolResult.model_validate(raw_result)
                result = await self._client._parse_call_tool_result(
                    self._tool_name, mcp_result, raise_on_error=True
                )
            elif isinstance(raw_result, mcp.types.CallToolResult):
                # Already a CallToolResult from MCP protocol - parse it
                result = await self._client._parse_call_tool_result(
                    self._tool_name, raw_result, raise_on_error=True
                )
            else:
                # Legacy ToolResult format - convert to MCP type
                if hasattr(raw_result, "content") and hasattr(
                    raw_result, "structured_content"
                ):
                    mcp_result = mcp.types.CallToolResult(
                        content=raw_result.content,
                        structuredContent=raw_result.structured_content,
                        _meta=raw_result.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                    )
                    result = await self._client._parse_call_tool_result(
                        self._tool_name, mcp_result, raise_on_error=True
                    )
                else:
                    # Unknown type - just return it
                    result = raw_result

        # Cache before returning
        self._cached_result = result
        return result


class PromptTask(Task[mcp.types.GetPromptResult]):
    """
    Represents a prompt call that may execute in background or immediately.

    Provides a uniform API whether the server accepts background execution
    or executes synchronously (graceful degradation per SEP-1686).

    Usage:
        task = await client.get_prompt_as_task("analyze", args)
        result = await task  # Returns GetPromptResult
    """

    def __init__(
        self,
        client: Client,
        task_id: str,
        prompt_name: str,
        immediate_result: mcp.types.GetPromptResult | None = None,
    ):
        """
        Create a PromptTask wrapper.

        Args:
            client: The FastMCP client
            task_id: The task identifier
            prompt_name: Name of the prompt being executed
            immediate_result: If server executed synchronously, the immediate result
        """
        super().__init__(client, task_id, immediate_result)
        self._prompt_name = prompt_name

    async def result(self) -> mcp.types.GetPromptResult:
        """Wait for and return the prompt result.

        If server executed immediately, returns the immediate result.
        Otherwise waits for background task to complete and retrieves result.

        Returns:
            GetPromptResult: The prompt result with messages and description
        """
        # Check cache first
        if self._cached_result is not None:
            return self._cached_result

        if self._is_immediate:
            assert self._immediate_result is not None
            result = self._immediate_result
        else:
            # Check client connected
            self._check_client_connected()

            # Wait for completion using event-based wait (respects notifications)
            await self.wait()

            # Get the raw MCP result
            mcp_result = await self._client.get_task_result(self._task_id)

            # Parse as GetPromptResult
            result = mcp.types.GetPromptResult.model_validate(mcp_result)

        # Cache before returning
        self._cached_result = result
        return result


class ResourceTask(
    Task[list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]]
):
    """
    Represents a resource read that may execute in background or immediately.

    Provides a uniform API whether the server accepts background execution
    or executes synchronously (graceful degradation per SEP-1686).

    Usage:
        task = await client.read_resource_as_task("file://data.txt")
        contents = await task  # Returns list[ReadResourceContents]
    """

    def __init__(
        self,
        client: Client,
        task_id: str,
        uri: str,
        immediate_result: list[
            mcp.types.TextResourceContents | mcp.types.BlobResourceContents
        ]
        | None = None,
    ):
        """
        Create a ResourceTask wrapper.

        Args:
            client: The FastMCP client
            task_id: The task identifier
            uri: URI of the resource being read
            immediate_result: If server executed synchronously, the immediate result
        """
        super().__init__(client, task_id, immediate_result)
        self._uri = uri

    async def result(
        self,
    ) -> list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]:
        """Wait for and return the resource contents.

        If server executed immediately, returns the immediate result.
        Otherwise waits for background task to complete and retrieves result.

        Returns:
            list[ReadResourceContents]: The resource contents
        """
        # Check cache first
        if self._cached_result is not None:
            return self._cached_result

        if self._is_immediate:
            assert self._immediate_result is not None
            result = self._immediate_result
        else:
            # Check client connected
            self._check_client_connected()

            # Wait for completion using event-based wait (respects notifications)
            await self.wait()

            # Get the raw MCP result
            mcp_result = await self._client.get_task_result(self._task_id)

            # Parse as ReadResourceResult or extract contents
            if isinstance(mcp_result, mcp.types.ReadResourceResult):
                # Already parsed by TasksResponse - extract contents
                result = list(mcp_result.contents)
            elif isinstance(mcp_result, dict) and "contents" in mcp_result:
                # Dict format - parse each content item
                parsed_contents = []
                for item in mcp_result["contents"]:
                    if isinstance(item, dict):
                        if "blob" in item:
                            parsed_contents.append(
                                mcp.types.BlobResourceContents.model_validate(item)
                            )
                        else:
                            parsed_contents.append(
                                mcp.types.TextResourceContents.model_validate(item)
                            )
                    else:
                        parsed_contents.append(item)
                result = parsed_contents
            else:
                # Fallback - might be the list directly
                result = mcp_result if isinstance(mcp_result, list) else [mcp_result]

        # Cache before returning
        self._cached_result = result
        return result
