from __future__ import annotations

import asyncio
import copy
import datetime
import secrets
import weakref
from collections.abc import Coroutine
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar, cast, overload

import anyio
import httpx
import mcp.types
from exceptiongroup import catch
from mcp import ClientSession, McpError
from mcp.types import GetTaskResult, TaskStatusNotification
from pydantic import AnyUrl

import fastmcp
from fastmcp.client.elicitation import ElicitationHandler, create_elicitation_callback
from fastmcp.client.logging import (
    LogHandler,
    create_log_callback,
    default_log_handler,
)
from fastmcp.client.messages import MessageHandler, MessageHandlerT
from fastmcp.client.mixins import (
    ClientPromptsMixin,
    ClientResourcesMixin,
    ClientTaskManagementMixin,
    ClientToolsMixin,
)
from fastmcp.client.progress import ProgressHandler, default_progress_handler
from fastmcp.client.roots import (
    RootsHandler,
    RootsList,
    create_roots_callback,
)
from fastmcp.client.sampling import (
    SamplingHandler,
    create_sampling_callback,
)
from fastmcp.client.tasks import (
    PromptTask,
    ResourceTask,
    TaskNotificationHandler,
    ToolTask,
)
from fastmcp.mcp_config import MCPConfig
from fastmcp.server import FastMCP
from fastmcp.utilities.exceptions import get_catch_handlers
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.timeout import (
    normalize_timeout_to_seconds,
    normalize_timeout_to_timedelta,
)

from .transports import (
    ClientTransport,
    ClientTransportT,
    FastMCP1Server,
    FastMCPTransport,
    MCPConfigTransport,
    NodeStdioTransport,
    PythonStdioTransport,
    SessionKwargs,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
    infer_transport,
)

__all__ = [
    "Client",
    "ElicitationHandler",
    "LogHandler",
    "MessageHandler",
    "ProgressHandler",
    "RootsHandler",
    "RootsList",
    "SamplingHandler",
    "SessionKwargs",
]

logger = get_logger(__name__)

T = TypeVar("T", bound="ClientTransport")
ResultT = TypeVar("ResultT")


@dataclass
class ClientSessionState:
    """Holds all session-related state for a Client instance.

    This allows clean separation of configuration (which is copied) from
    session state (which should be fresh for each new client instance).
    """

    session: ClientSession | None = None
    nesting_counter: int = 0
    lock: anyio.Lock = field(default_factory=anyio.Lock)
    session_task: asyncio.Task | None = None
    ready_event: anyio.Event = field(default_factory=anyio.Event)
    stop_event: anyio.Event = field(default_factory=anyio.Event)
    initialize_result: mcp.types.InitializeResult | None = None


@dataclass
class CallToolResult:
    """Parsed result from a tool call."""

    content: list[mcp.types.ContentBlock]
    structured_content: dict[str, Any] | None
    meta: dict[str, Any] | None
    data: Any = None
    is_error: bool = False


class Client(
    Generic[ClientTransportT],
    ClientResourcesMixin,
    ClientPromptsMixin,
    ClientToolsMixin,
    ClientTaskManagementMixin,
):
    """
    MCP client that delegates connection management to a Transport instance.

    The Client class is responsible for MCP protocol logic, while the Transport
    handles connection establishment and management. Client provides methods for
    working with resources, prompts, tools and other MCP capabilities.

    This client supports reentrant context managers (multiple concurrent
    `async with client:` blocks) using reference counting and background session
    management. This allows efficient session reuse in any scenario with
    nested or concurrent client usage.

    MCP SDK 1.10 introduced automatic list_tools() calls during call_tool()
    execution. This created a race condition where events could be reset while
    other tasks were waiting on them, causing deadlocks. The issue was exposed
    in proxy scenarios but affects any reentrant usage.

    The solution uses reference counting to track active context managers,
    a background task to manage the session lifecycle, events to coordinate
    between tasks, and ensures all session state changes happen within a lock.
    Events are only created when needed, never reset outside locks.

    This design prevents race conditions where tasks wait on events that get
    replaced by other tasks, ensuring reliable coordination in concurrent scenarios.

    Args:
        transport:
            Connection source specification, which can be:

                - ClientTransport: Direct transport instance
                - FastMCP: In-process FastMCP server
                - AnyUrl or str: URL to connect to
                - Path: File path for local socket
                - MCPConfig: MCP server configuration
                - dict: Transport configuration

        roots: Optional RootsList or RootsHandler for filesystem access
        sampling_handler: Optional handler for sampling requests
        log_handler: Optional handler for log messages
        message_handler: Optional handler for protocol messages
        progress_handler: Optional handler for progress notifications
        timeout: Optional timeout for requests (seconds or timedelta)
        init_timeout: Optional timeout for initial connection (seconds or timedelta).
            Set to 0 to disable. If None, uses the value in the FastMCP global settings.

    Examples:
        ```python
        # Connect to FastMCP server
        client = Client("http://localhost:8080")

        async with client:
            # List available resources
            resources = await client.list_resources()

            # Call a tool
            result = await client.call_tool("my_tool", {"param": "value"})
        ```
    """

    @overload
    def __init__(self: Client[T], transport: T, *args: Any, **kwargs: Any) -> None: ...

    @overload
    def __init__(
        self: Client[SSETransport | StreamableHttpTransport],
        transport: AnyUrl,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: Client[FastMCPTransport],
        transport: FastMCP | FastMCP1Server,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: Client[PythonStdioTransport | NodeStdioTransport],
        transport: Path,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: Client[MCPConfigTransport],
        transport: MCPConfig | dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: Client[
            PythonStdioTransport
            | NodeStdioTransport
            | SSETransport
            | StreamableHttpTransport
        ],
        transport: str,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    def __init__(
        self,
        transport: (
            ClientTransportT
            | FastMCP
            | FastMCP1Server
            | AnyUrl
            | Path
            | MCPConfig
            | dict[str, Any]
            | str
        ),
        name: str | None = None,
        roots: RootsList | RootsHandler | None = None,
        sampling_handler: SamplingHandler | None = None,
        sampling_capabilities: mcp.types.SamplingCapability | None = None,
        elicitation_handler: ElicitationHandler | None = None,
        log_handler: LogHandler | None = None,
        message_handler: MessageHandlerT | MessageHandler | None = None,
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        auto_initialize: bool = True,
        init_timeout: datetime.timedelta | float | int | None = None,
        client_info: mcp.types.Implementation | None = None,
        auth: httpx.Auth | Literal["oauth"] | str | None = None,
    ) -> None:
        self.name = name or self.generate_name()

        self.transport = cast(ClientTransportT, infer_transport(transport))
        if auth is not None:
            self.transport._set_auth(auth)

        if log_handler is None:
            log_handler = default_log_handler

        if progress_handler is None:
            progress_handler = default_progress_handler

        self._progress_handler = progress_handler

        # Convert timeout to timedelta if needed
        timeout = normalize_timeout_to_timedelta(timeout)

        # handle init handshake timeout (0 means disabled)
        if init_timeout is None:
            init_timeout = fastmcp.settings.client_init_timeout
        self._init_timeout = normalize_timeout_to_seconds(init_timeout)

        self.auto_initialize = auto_initialize

        self._session_kwargs: SessionKwargs = {
            "sampling_callback": None,
            "list_roots_callback": None,
            "logging_callback": create_log_callback(log_handler),
            "message_handler": message_handler or TaskNotificationHandler(self),
            "read_timeout_seconds": timeout,
            "client_info": client_info,
        }

        if roots is not None:
            self.set_roots(roots)

        if sampling_handler is not None:
            self._session_kwargs["sampling_callback"] = create_sampling_callback(
                sampling_handler
            )
            # Default to tools-enabled capabilities unless explicitly overridden
            self._session_kwargs["sampling_capabilities"] = (
                sampling_capabilities
                if sampling_capabilities is not None
                else mcp.types.SamplingCapability(
                    tools=mcp.types.SamplingToolsCapability()
                )
            )

        if elicitation_handler is not None:
            self._session_kwargs["elicitation_callback"] = create_elicitation_callback(
                elicitation_handler
            )

        # Session context management - see class docstring for detailed explanation
        self._session_state = ClientSessionState()

        # Track task IDs submitted by this client (for list_tasks support)
        self._submitted_task_ids: set[str] = set()

        # Registry for routing notifications/tasks/status to Task objects

        self._task_registry: dict[
            str, weakref.ref[ToolTask | PromptTask | ResourceTask]
        ] = {}

    def _reset_session_state(self, full: bool = False) -> None:
        """Reset session state after disconnect or cancellation.

        Args:
            full: If True, also resets session_task and nesting_counter.
                  Use full=True for cancellation cleanup where the session
                  task was started but never completed normally.
        """
        self._session_state.session = None
        self._session_state.initialize_result = None
        if full:
            self._session_state.session_task = None
            self._session_state.nesting_counter = 0

    @property
    def session(self) -> ClientSession:
        """Get the current active session. Raises RuntimeError if not connected."""
        if self._session_state.session is None:
            raise RuntimeError(
                "Client is not connected. Use the 'async with client:' context manager first."
            )

        return self._session_state.session

    @property
    def initialize_result(self) -> mcp.types.InitializeResult | None:
        """Get the result of the initialization request."""
        return self._session_state.initialize_result

    def set_roots(self, roots: RootsList | RootsHandler) -> None:
        """Set the roots for the client. This does not automatically call `send_roots_list_changed`."""
        self._session_kwargs["list_roots_callback"] = create_roots_callback(roots)

    def set_sampling_callback(
        self,
        sampling_callback: SamplingHandler,
        sampling_capabilities: mcp.types.SamplingCapability | None = None,
    ) -> None:
        """Set the sampling callback for the client."""
        self._session_kwargs["sampling_callback"] = create_sampling_callback(
            sampling_callback
        )
        # Default to tools-enabled capabilities unless explicitly overridden
        self._session_kwargs["sampling_capabilities"] = (
            sampling_capabilities
            if sampling_capabilities is not None
            else mcp.types.SamplingCapability(tools=mcp.types.SamplingToolsCapability())
        )

    def set_elicitation_callback(
        self, elicitation_callback: ElicitationHandler
    ) -> None:
        """Set the elicitation callback for the client."""
        self._session_kwargs["elicitation_callback"] = create_elicitation_callback(
            elicitation_callback
        )

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self._session_state.session is not None

    def new(self) -> Client[ClientTransportT]:
        """Create a new client instance with the same configuration but fresh session state.

        This creates a new client with the same transport, handlers, and configuration,
        but with no active session. Useful for creating independent sessions that don't
        share state with the original client.

        Returns:
            A new Client instance with the same configuration but disconnected state.

        Example:
            ```python
            # Create a fresh client for each concurrent operation
            fresh_client = client.new()
            async with fresh_client:
                await fresh_client.call_tool("some_tool", {})
            ```
        """
        new_client = copy.copy(self)

        if not isinstance(self.transport, StdioTransport):
            # Reset session state to fresh state
            new_client._session_state = ClientSessionState()

        new_client.name += f":{secrets.token_hex(2)}"

        return new_client

    @asynccontextmanager
    async def _context_manager(self):
        with catch(get_catch_handlers()):
            async with self.transport.connect_session(
                **self._session_kwargs
            ) as session:
                self._session_state.session = session
                # Initialize the session if auto_initialize is enabled
                try:
                    if self.auto_initialize:
                        await self.initialize()
                    yield
                except anyio.ClosedResourceError as e:
                    raise RuntimeError("Server session was closed unexpectedly") from e
                finally:
                    self._reset_session_state()

    async def initialize(
        self,
        timeout: datetime.timedelta | float | int | None = None,
    ) -> mcp.types.InitializeResult:
        """Send an initialize request to the server.

        This method performs the MCP initialization handshake with the server,
        exchanging capabilities and server information. It is idempotent - calling
        it multiple times returns the cached result from the first call.

        The initialization happens automatically when entering the client context
        manager unless `auto_initialize=False` was set during client construction.
        Manual calls to this method are only needed when auto-initialization is disabled.

        Args:
            timeout: Optional timeout for the initialization request (seconds or timedelta).
                If None, uses the client's init_timeout setting.

        Returns:
            InitializeResult: The server's initialization response containing server info,
                capabilities, protocol version, and optional instructions.

        Raises:
            RuntimeError: If the client is not connected or initialization times out.

        Example:
            ```python
            # With auto-initialization disabled
            client = Client(server, auto_initialize=False)
            async with client:
                result = await client.initialize()
                print(f"Server: {result.serverInfo.name}")
                print(f"Instructions: {result.instructions}")
            ```
        """

        if self.initialize_result is not None:
            return self.initialize_result

        if timeout is None:
            timeout = self._init_timeout
        else:
            timeout = normalize_timeout_to_seconds(timeout)

        try:
            with anyio.fail_after(timeout):
                self._session_state.initialize_result = await self.session.initialize()
                return self._session_state.initialize_result
        except TimeoutError as e:
            raise RuntimeError("Failed to initialize server session") from e

    async def __aenter__(self):
        return await self._connect()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Use a timeout to prevent hanging during cleanup if the connection is in a bad
        # state (e.g., rate-limited). The MCP SDK's transport may try to terminate the
        # session which can hang if the server is unresponsive.
        with anyio.move_on_after(5):
            await self._disconnect()

    async def _connect(self):
        """
        Establish or reuse a session connection.

        This method implements the reentrant context manager pattern:
        - First call: Creates background session task and waits for it to be ready
        - Subsequent calls: Increments reference counter and reuses existing session
        - All operations protected by _context_lock to prevent race conditions

        The critical fix: Events are only created when starting a new session,
        never reset outside the lock, preventing the deadlock scenario where
        tasks wait on events that get replaced by other tasks.
        """
        # ensure only one session is running at a time to avoid race conditions
        async with self._session_state.lock:
            need_to_start = (
                self._session_state.session_task is None
                or self._session_state.session_task.done()
            )

            if need_to_start:
                if self._session_state.nesting_counter != 0:
                    raise RuntimeError(
                        f"Internal error: nesting counter should be 0 when starting new session, got {self._session_state.nesting_counter}"
                    )
                self._session_state.stop_event = anyio.Event()
                self._session_state.ready_event = anyio.Event()
                self._session_state.session_task = asyncio.create_task(
                    self._session_runner()
                )
                try:
                    await self._session_state.ready_event.wait()
                except asyncio.CancelledError:
                    # Cancellation during initial connection startup can leave the
                    # background session task running because __aexit__ is never invoked
                    # when __aenter__ is cancelled. Since we hold the session lock here
                    # and we know we started the session task, it's safe to tear it down
                    # without impacting other active contexts.
                    #
                    # Note: session_task is an asyncio.Task (not anyio) because it needs
                    # to outlive individual context manager scopes - anyio's structured
                    # concurrency doesn't allow tasks to escape their task group.
                    session_task = self._session_state.session_task
                    if session_task is not None:
                        # Request a graceful stop if the runner has already reached
                        # its stop_event wait.
                        self._session_state.stop_event.set()
                        session_task.cancel()
                        with anyio.CancelScope(shield=True):
                            with anyio.move_on_after(3):
                                try:
                                    await session_task
                                except asyncio.CancelledError:
                                    pass
                                except Exception as e:
                                    logger.debug(
                                        f"Error during cancelled session cleanup: {e}"
                                    )

                    # Reset session state so future callers can reconnect cleanly.
                    self._reset_session_state(full=True)

                    with anyio.CancelScope(shield=True):
                        with anyio.move_on_after(3):
                            try:
                                await self.transport.close()
                            except Exception as e:
                                logger.debug(
                                    f"Error closing transport after cancellation: {e}"
                                )

                    raise

                if self._session_state.session_task.done():
                    exception = self._session_state.session_task.exception()
                    if exception is None:
                        raise RuntimeError(
                            "Session task completed without exception but connection failed"
                        )
                    # Preserve specific exception types that clients may want to handle
                    if isinstance(exception, httpx.HTTPStatusError | McpError):
                        raise exception
                    raise RuntimeError(
                        f"Client failed to connect: {exception}"
                    ) from exception

            self._session_state.nesting_counter += 1

        return self

    async def _disconnect(self, force: bool = False):
        """
        Disconnect from session using reference counting.

        This method implements proper cleanup for reentrant context managers:
        - Decrements reference counter for normal exits
        - Only stops session when counter reaches 0 (no more active contexts)
        - Force flag bypasses reference counting for immediate shutdown
        - Session cleanup happens inside the lock to ensure atomicity

        Key fix: Removed the problematic "Reset for future reconnects" logic
        that was resetting events outside the lock, causing race conditions.
        Event recreation now happens only in _connect() when actually needed.
        """
        # ensure only one session is running at a time to avoid race conditions
        async with self._session_state.lock:
            # if we are forcing a disconnect, reset the nesting counter
            if force:
                self._session_state.nesting_counter = 0

            # otherwise decrement to check if we are done nesting
            else:
                self._session_state.nesting_counter = max(
                    0, self._session_state.nesting_counter - 1
                )

            # if we are still nested, return
            if self._session_state.nesting_counter > 0:
                return

            # stop the active session
            if self._session_state.session_task is None:
                return
            self._session_state.stop_event.set()
            # wait for session to finish to ensure state has been reset
            await self._session_state.session_task
            self._session_state.session_task = None

    async def _session_runner(self):
        """
        Background task that manages the actual session lifecycle.

        This task runs in the background and:
        1. Establishes the transport connection via _context_manager()
        2. Signals that the session is ready via _ready_event.set()
        3. Waits for disconnect signal via _stop_event.wait()
        4. Ensures _ready_event is always set, even on failures

        The simplified error handling (compared to the original) removes
        redundant exception re-raising while ensuring waiting tasks are
        always unblocked via the finally block.
        """
        try:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(self._context_manager())
                # Session/context is now ready
                self._session_state.ready_event.set()
                # Wait until disconnect/stop is requested
                await self._session_state.stop_event.wait()
        finally:
            # Ensure ready event is set even if context manager entry fails
            self._session_state.ready_event.set()

    async def _await_with_session_monitoring(
        self, coro: Coroutine[Any, Any, ResultT]
    ) -> ResultT:
        """Await a coroutine while monitoring the session task for errors.

        When using HTTP transports, server errors (4xx/5xx) are raised in the
        background session task, not in the coroutine waiting for a response.
        This causes the client to hang indefinitely since the response never
        arrives. This method monitors the session task and propagates any
        exceptions that occur, preventing the client from hanging.

        Args:
            coro: The coroutine to await (typically a session method call)

        Returns:
            The result of the coroutine

        Raises:
            The exception from the session task if it fails, or RuntimeError
            if the session task completes unexpectedly without an exception.
        """
        session_task = self._session_state.session_task

        # If no session task, just await directly
        if session_task is None:
            return await coro

        # If session task already failed, raise immediately
        if session_task.done():
            # Close the coroutine to avoid "was never awaited" warning
            coro.close()
            exc = session_task.exception()
            if exc:
                raise exc
            raise RuntimeError("Session task completed unexpectedly")

        # Create task for our call
        call_task = asyncio.create_task(coro)

        try:
            done, _ = await asyncio.wait(
                {call_task, session_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if session_task in done:
                # Session task completed (likely errored) before our call finished
                call_task.cancel()
                with anyio.CancelScope(shield=True), suppress(asyncio.CancelledError):
                    await call_task

                # Raise the session task exception
                exc = session_task.exception()
                if exc:
                    raise exc
                raise RuntimeError("Session task completed unexpectedly")

            # Our call completed first - get the result
            return call_task.result()
        except asyncio.CancelledError:
            call_task.cancel()
            with anyio.CancelScope(shield=True), suppress(asyncio.CancelledError):
                await call_task
            raise

    def _handle_task_status_notification(
        self, notification: TaskStatusNotification
    ) -> None:
        """Route task status notification to appropriate Task object.

        Called when notifications/tasks/status is received from server.
        Updates Task object's cache and triggers events/callbacks.
        """
        # Extract task ID from notification params
        task_id = notification.params.taskId
        if not task_id:
            return

        # Look up task in registry (weakref)
        task_ref = self._task_registry.get(task_id)
        if task_ref:
            task = task_ref()  # Dereference weakref
            if task:
                # Convert notification params to GetTaskResult (they share the same fields via Task)
                status = GetTaskResult.model_validate(notification.params.model_dump())
                task._handle_status_notification(status)

    async def close(self):
        await self._disconnect(force=True)
        await self.transport.close()

    # --- MCP Client Methods ---

    async def ping(self) -> bool:
        """Send a ping request."""
        result = await self._await_with_session_monitoring(self.session.send_ping())
        return isinstance(result, mcp.types.EmptyResult)

    async def cancel(
        self,
        request_id: str | int,
        reason: str | None = None,
    ) -> None:
        """Send a cancellation notification for an in-progress request."""
        notification = mcp.types.ClientNotification(
            root=mcp.types.CancelledNotification(
                method="notifications/cancelled",
                params=mcp.types.CancelledNotificationParams(
                    requestId=request_id,
                    reason=reason,
                ),
            )
        )
        await self.session.send_notification(notification)

    async def progress(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        """Send a progress notification."""
        await self.session.send_progress_notification(
            progress_token, progress, total, message
        )

    async def set_logging_level(self, level: mcp.types.LoggingLevel) -> None:
        """Send a logging/setLevel request."""
        await self._await_with_session_monitoring(self.session.set_logging_level(level))

    async def send_roots_list_changed(self) -> None:
        """Send a roots/list_changed notification."""
        await self.session.send_roots_list_changed()

    # --- Completion ---

    async def complete_mcp(
        self,
        ref: mcp.types.ResourceTemplateReference | mcp.types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp.types.CompleteResult:
        """Send a completion request and return the complete MCP protocol result.

        Args:
            ref (mcp.types.ResourceTemplateReference | mcp.types.PromptReference): The reference to complete.
            argument (dict[str, str]): Arguments to pass to the completion request.
            context_arguments (dict[str, Any] | None, optional): Optional context arguments to
                include with the completion request. Defaults to None.

        Returns:
            mcp.types.CompleteResult: The complete response object from the protocol,
                containing the completion and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        logger.debug(f"[{self.name}] called complete: {ref}")

        result = await self._await_with_session_monitoring(
            self.session.complete(
                ref=ref, argument=argument, context_arguments=context_arguments
            )
        )
        return result

    async def complete(
        self,
        ref: mcp.types.ResourceTemplateReference | mcp.types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp.types.Completion:
        """Send a completion request to the server.

        Args:
            ref (mcp.types.ResourceTemplateReference | mcp.types.PromptReference): The reference to complete.
            argument (dict[str, str]): Arguments to pass to the completion request.
            context_arguments (dict[str, Any] | None, optional): Optional context arguments to
                include with the completion request. Defaults to None.

        Returns:
            mcp.types.Completion: The completion object.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        result = await self.complete_mcp(
            ref=ref, argument=argument, context_arguments=context_arguments
        )
        return result.completion

    @classmethod
    def generate_name(cls, name: str | None = None) -> str:
        class_name = cls.__name__
        if name is None:
            return f"{class_name}-{secrets.token_hex(2)}"
        else:
            return f"{class_name}-{name}-{secrets.token_hex(2)}"
