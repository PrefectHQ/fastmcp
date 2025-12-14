from __future__ import annotations

import asyncio
import copy
import datetime
import secrets
import uuid
import weakref
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar, cast, overload

import anyio
import httpx
import mcp.types
import pydantic_core
from exceptiongroup import catch
from mcp import ClientSession, McpError
from mcp.types import (
    CancelTaskRequest,
    CancelTaskRequestParams,
    GetTaskPayloadRequest,
    GetTaskPayloadRequestParams,
    GetTaskPayloadResult,
    GetTaskRequest,
    GetTaskRequestParams,
    GetTaskResult,
    ListTasksRequest,
    PaginatedRequestParams,
    TaskStatusNotification,
)
from pydantic import AnyUrl

import fastmcp
from fastmcp.client.elicitation import ElicitationHandler, create_elicitation_callback
from fastmcp.client.logging import (
    LogHandler,
    create_log_callback,
    default_log_handler,
)
from fastmcp.client.messages import MessageHandler, MessageHandlerT
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
from fastmcp.exceptions import ToolError
from fastmcp.mcp_config import MCPConfig
from fastmcp.server import FastMCP
from fastmcp.utilities.exceptions import get_catch_handlers
from fastmcp.utilities.json_schema_type import json_schema_to_type
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import get_cached_typeadapter

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


class Client(Generic[ClientTransportT]):
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
        if isinstance(timeout, int | float):
            timeout = datetime.timedelta(seconds=float(timeout))

        # handle init handshake timeout
        if init_timeout is None:
            init_timeout = fastmcp.settings.client_init_timeout
        if isinstance(init_timeout, datetime.timedelta):
            init_timeout = init_timeout.total_seconds()
        elif not init_timeout:
            init_timeout = None
        else:
            init_timeout = float(init_timeout)
        self._init_timeout = init_timeout

        self.auto_initialize = auto_initialize

        self._session_kwargs: SessionKwargs = {
            "sampling_callback": None,
            "list_roots_callback": None,
            "logging_callback": create_log_callback(log_handler),
            "message_handler": message_handler or TaskNotificationHandler(self),
            "read_timeout_seconds": timeout,  # ty: ignore[invalid-argument-type]
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

        # Convert timeout if needed
        if isinstance(timeout, datetime.timedelta):
            timeout = timeout.total_seconds()
        elif timeout is not None:
            timeout = float(timeout)

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
        result = await self.session.send_ping()
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
        await self.session.set_logging_level(level)

    async def send_roots_list_changed(self) -> None:
        """Send a roots/list_changed notification."""
        await self.session.send_roots_list_changed()

    # --- Resources ---

    async def list_resources_mcp(self) -> mcp.types.ListResourcesResult:
        """Send a resources/list request and return the complete MCP protocol result.

        Returns:
            mcp.types.ListResourcesResult: The complete response object from the protocol,
                containing the list of resources and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        logger.debug(f"[{self.name}] called list_resources")

        result = await self.session.list_resources()
        return result

    async def list_resources(self) -> list[mcp.types.Resource]:
        """Retrieve a list of resources available on the server.

        Returns:
            list[mcp.types.Resource]: A list of Resource objects.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        result = await self.list_resources_mcp()
        return result.resources

    async def list_resource_templates_mcp(
        self,
    ) -> mcp.types.ListResourceTemplatesResult:
        """Send a resources/listResourceTemplates request and return the complete MCP protocol result.

        Returns:
            mcp.types.ListResourceTemplatesResult: The complete response object from the protocol,
                containing the list of resource templates and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        logger.debug(f"[{self.name}] called list_resource_templates")

        result = await self.session.list_resource_templates()
        return result

    async def list_resource_templates(
        self,
    ) -> list[mcp.types.ResourceTemplate]:
        """Retrieve a list of resource templates available on the server.

        Returns:
            list[mcp.types.ResourceTemplate]: A list of ResourceTemplate objects.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        result = await self.list_resource_templates_mcp()
        return result.resourceTemplates

    async def read_resource_mcp(
        self, uri: AnyUrl | str, meta: dict[str, Any] | None = None
    ) -> mcp.types.ReadResourceResult:
        """Send a resources/read request and return the complete MCP protocol result.

        Args:
            uri (AnyUrl | str): The URI of the resource to read. Can be a string or an AnyUrl object.
            meta (dict[str, Any] | None, optional): Request metadata (e.g., for SEP-1686 tasks). Defaults to None.

        Returns:
            mcp.types.ReadResourceResult: The complete response object from the protocol,
                containing the resource contents and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        logger.debug(f"[{self.name}] called read_resource: {uri}")

        if isinstance(uri, str):
            uri = AnyUrl(uri)  # Ensure AnyUrl

        # If meta provided, use send_request for SEP-1686 task support
        if meta:
            task_dict = meta.get("modelcontextprotocol.io/task")
            request = mcp.types.ReadResourceRequest(
                params=mcp.types.ReadResourceRequestParams(
                    uri=uri,
                    task=mcp.types.TaskMetadata(**task_dict)
                    if task_dict
                    else None,  # SEP-1686: task as direct param (spec-compliant)
                )
            )
            result = await self.session.send_request(
                request=request,  # type: ignore[arg-type]
                result_type=mcp.types.ReadResourceResult,
            )
        else:
            result = await self.session.read_resource(uri)
        return result

    @overload
    async def read_resource(
        self,
        uri: AnyUrl | str,
        *,
        task: Literal[False] = False,
    ) -> list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]: ...

    @overload
    async def read_resource(
        self,
        uri: AnyUrl | str,
        *,
        task: Literal[True],
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> ResourceTask: ...

    async def read_resource(
        self,
        uri: AnyUrl | str,
        *,
        task: bool = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> (
        list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]
        | ResourceTask
    ):
        """Read the contents of a resource or resolved template.

        Args:
            uri (AnyUrl | str): The URI of the resource to read. Can be a string or an AnyUrl object.
            task (bool): If True, execute as background task (SEP-1686). Defaults to False.
            task_id (str | None): Optional client-provided task ID (auto-generated if not provided).
            ttl (int): Time to keep results available in milliseconds (default 60s).

        Returns:
            list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents] | ResourceTask:
                A list of content objects if task=False, or a ResourceTask object if task=True.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        if task:
            return await self._read_resource_as_task(uri, task_id, ttl)

        if isinstance(uri, str):
            try:
                uri = AnyUrl(uri)  # Ensure AnyUrl
            except Exception as e:
                raise ValueError(
                    f"Provided resource URI is invalid: {str(uri)!r}"
                ) from e
        result = await self.read_resource_mcp(uri)
        return result.contents

    async def _read_resource_as_task(
        self,
        uri: AnyUrl | str,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> ResourceTask:
        """Read a resource for background execution (SEP-1686).

        Returns a ResourceTask object that handles both background and immediate execution.

        Args:
            uri: Resource URI to read
            task_id: Optional client-provided task ID (ignored, for backward compatibility)
            ttl: Time to keep results available in milliseconds (default 60s)

        Returns:
            ResourceTask: Future-like object for accessing task status and results
        """
        # Per SEP-1686 final spec: client sends only ttl, server generates taskId
        # Read resource with task metadata (no taskId sent)
        result = await self.read_resource_mcp(
            uri=uri,
            meta={
                "modelcontextprotocol.io/task": {
                    "ttl": ttl,
                }
            },
        )

        # Check if server accepted background execution
        # If response includes task metadata with taskId, server accepted background mode
        # If response includes returned_immediately=True, server declined and executed sync
        task_meta = (result.meta or {}).get("modelcontextprotocol.io/task", {})
        if task_meta.get("taskId"):
            # Background execution accepted - extract server-generated taskId
            server_task_id = task_meta["taskId"]
            # Track this task ID for list_tasks()
            self._submitted_task_ids.add(server_task_id)

            # Create task object
            task_obj = ResourceTask(
                self, server_task_id, uri=str(uri), immediate_result=None
            )

            # Register for notification routing
            self._task_registry[server_task_id] = weakref.ref(task_obj)  # type: ignore[assignment]

            return task_obj
        else:
            # Server declined background execution (graceful degradation)
            # Use a synthetic task ID for the immediate result
            synthetic_task_id = task_id or str(uuid.uuid4())
            return ResourceTask(
                self, synthetic_task_id, uri=str(uri), immediate_result=result.contents
            )

    # async def subscribe_resource(self, uri: AnyUrl | str) -> None:
    #     """Send a resources/subscribe request."""
    #     if isinstance(uri, str):
    #         uri = AnyUrl(uri)
    #     await self.session.subscribe_resource(uri)

    # async def unsubscribe_resource(self, uri: AnyUrl | str) -> None:
    #     """Send a resources/unsubscribe request."""
    #     if isinstance(uri, str):
    #         uri = AnyUrl(uri)
    #     await self.session.unsubscribe_resource(uri)

    # --- Prompts ---

    async def list_prompts_mcp(self) -> mcp.types.ListPromptsResult:
        """Send a prompts/list request and return the complete MCP protocol result.

        Returns:
            mcp.types.ListPromptsResult: The complete response object from the protocol,
                containing the list of prompts and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        logger.debug(f"[{self.name}] called list_prompts")

        result = await self.session.list_prompts()
        return result

    async def list_prompts(self) -> list[mcp.types.Prompt]:
        """Retrieve a list of prompts available on the server.

        Returns:
            list[mcp.types.Prompt]: A list of Prompt objects.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        result = await self.list_prompts_mcp()
        return result.prompts

    # --- Prompt ---
    async def get_prompt_mcp(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> mcp.types.GetPromptResult:
        """Send a prompts/get request and return the complete MCP protocol result.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the prompt. Defaults to None.
            meta (dict[str, Any] | None, optional): Request metadata (e.g., for SEP-1686 tasks). Defaults to None.

        Returns:
            mcp.types.GetPromptResult: The complete response object from the protocol,
                containing the prompt messages and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        logger.debug(f"[{self.name}] called get_prompt: {name}")

        # Serialize arguments for MCP protocol - convert non-string values to JSON
        serialized_arguments: dict[str, str] | None = None
        if arguments:
            serialized_arguments = {}
            for key, value in arguments.items():
                if isinstance(value, str):
                    serialized_arguments[key] = value
                else:
                    # Use pydantic_core.to_json for consistent serialization
                    serialized_arguments[key] = pydantic_core.to_json(value).decode(
                        "utf-8"
                    )

        # If meta provided, use send_request for SEP-1686 task support
        if meta:
            task_dict = meta.get("modelcontextprotocol.io/task")
            request = mcp.types.GetPromptRequest(
                params=mcp.types.GetPromptRequestParams(
                    name=name,
                    arguments=serialized_arguments,
                    task=mcp.types.TaskMetadata(**task_dict)
                    if task_dict
                    else None,  # SEP-1686: task as direct param (spec-compliant)
                )
            )
            result = await self.session.send_request(
                request=request,  # type: ignore[arg-type]
                result_type=mcp.types.GetPromptResult,
            )
        else:
            result = await self.session.get_prompt(
                name=name, arguments=serialized_arguments
            )
        return result

    @overload
    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        task: Literal[False] = False,
    ) -> mcp.types.GetPromptResult: ...

    @overload
    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        task: Literal[True],
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> PromptTask: ...

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        task: bool = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> mcp.types.GetPromptResult | PromptTask:
        """Retrieve a rendered prompt message list from the server.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the prompt. Defaults to None.
            task (bool): If True, execute as background task (SEP-1686). Defaults to False.
            task_id (str | None): Optional client-provided task ID (auto-generated if not provided).
            ttl (int): Time to keep results available in milliseconds (default 60s).

        Returns:
            mcp.types.GetPromptResult | PromptTask: The complete response object if task=False,
                or a PromptTask object if task=True.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        if task:
            return await self._get_prompt_as_task(name, arguments, task_id, ttl)

        result = await self.get_prompt_mcp(name=name, arguments=arguments)
        return result

    async def _get_prompt_as_task(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> PromptTask:
        """Get a prompt for background execution (SEP-1686).

        Returns a PromptTask object that handles both background and immediate execution.

        Args:
            name: Prompt name to get
            arguments: Prompt arguments
            task_id: Optional client-provided task ID (ignored, for backward compatibility)
            ttl: Time to keep results available in milliseconds (default 60s)

        Returns:
            PromptTask: Future-like object for accessing task status and results
        """
        # Per SEP-1686 final spec: client sends only ttl, server generates taskId
        # Call prompt with task metadata (no taskId sent)
        result = await self.get_prompt_mcp(
            name=name,
            arguments=arguments or {},
            meta={
                "modelcontextprotocol.io/task": {
                    "ttl": ttl,
                }
            },
        )

        # Check if server accepted background execution
        # If response includes task metadata with taskId, server accepted background mode
        # If response includes returned_immediately=True, server declined and executed sync
        task_meta = (result.meta or {}).get("modelcontextprotocol.io/task", {})
        if task_meta.get("taskId"):
            # Background execution accepted - extract server-generated taskId
            server_task_id = task_meta["taskId"]
            # Track this task ID for list_tasks()
            self._submitted_task_ids.add(server_task_id)

            # Create task object
            task_obj = PromptTask(
                self, server_task_id, prompt_name=name, immediate_result=None
            )

            # Register for notification routing
            self._task_registry[server_task_id] = weakref.ref(task_obj)  # type: ignore[assignment]

            return task_obj
        else:
            # Server declined background execution (graceful degradation)
            # Use a synthetic task ID for the immediate result
            synthetic_task_id = task_id or str(uuid.uuid4())
            return PromptTask(
                self, synthetic_task_id, prompt_name=name, immediate_result=result
            )

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
        """
        logger.debug(f"[{self.name}] called complete: {ref}")

        result = await self.session.complete(
            ref=ref, argument=argument, context_arguments=context_arguments
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
        """
        result = await self.complete_mcp(
            ref=ref, argument=argument, context_arguments=context_arguments
        )
        return result.completion

    # --- Tools ---

    async def list_tools_mcp(self) -> mcp.types.ListToolsResult:
        """Send a tools/list request and return the complete MCP protocol result.

        Returns:
            mcp.types.ListToolsResult: The complete response object from the protocol,
                containing the list of tools and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        logger.debug(f"[{self.name}] called list_tools")

        result = await self.session.list_tools()
        return result

    async def list_tools(self) -> list[mcp.types.Tool]:
        """Retrieve a list of tools available on the server.

        Returns:
            list[mcp.types.Tool]: A list of Tool objects.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        result = await self.list_tools_mcp()
        return result.tools

    # --- Call Tool ---

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> mcp.types.CallToolResult:
        """Send a tools/call request and return the complete MCP protocol result.

        This method returns the raw CallToolResult object, which includes an isError flag
        and other metadata. It does not raise an exception if the tool call results in an error.

        Args:
            name (str): The name of the tool to call.
            arguments (dict[str, Any]): Arguments to pass to the tool.
            timeout (datetime.timedelta | float | int | None, optional): The timeout for the tool call. Defaults to None.
            progress_handler (ProgressHandler | None, optional): The progress handler to use for the tool call. Defaults to None.
            meta (dict[str, Any] | None, optional): Additional metadata to include with the request.
                This is useful for passing contextual information (like user IDs, trace IDs, or preferences)
                that shouldn't be tool arguments but may influence server-side processing. The server
                can access this via `context.request_context.meta`. Defaults to None.

        Returns:
            mcp.types.CallToolResult: The complete response object from the protocol,
                containing the tool result and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        logger.debug(f"[{self.name}] called call_tool: {name}")

        # Convert timeout to timedelta if needed
        if isinstance(timeout, int | float):
            timeout = datetime.timedelta(seconds=float(timeout))

        # For task submissions, use send_request to bypass SDK validation
        # Task acknowledgments don't have structured content, which would fail validation
        if meta and "modelcontextprotocol.io/task" in meta:
            task_dict = meta.get("modelcontextprotocol.io/task")
            request = mcp.types.CallToolRequest(
                params=mcp.types.CallToolRequestParams(
                    name=name,
                    arguments=arguments,
                    task=mcp.types.TaskMetadata(**task_dict)
                    if task_dict
                    else None,  # SEP-1686: task as direct param (spec-compliant)
                )
            )
            result = await self.session.send_request(
                request=request,  # type: ignore[arg-type]
                result_type=mcp.types.CallToolResult,
                request_read_timeout_seconds=timeout,  # type: ignore[arg-type]
                progress_callback=progress_handler or self._progress_handler,
            )
        else:
            result = await self.session.call_tool(
                name=name,
                arguments=arguments,
                read_timeout_seconds=timeout,  # ty: ignore[invalid-argument-type]
                progress_callback=progress_handler or self._progress_handler,
                meta=meta,
            )
        return result

    async def _parse_call_tool_result(
        self, name: str, result: mcp.types.CallToolResult, raise_on_error: bool = False
    ) -> CallToolResult:
        """Parse an mcp.types.CallToolResult into our CallToolResult dataclass.

        Args:
            name: Tool name (for schema lookup)
            result: Raw MCP protocol result
            raise_on_error: Whether to raise ToolError on errors

        Returns:
            CallToolResult: Parsed result with structured data
        """
        data = None
        if result.isError and raise_on_error:
            msg = cast(mcp.types.TextContent, result.content[0]).text
            raise ToolError(msg)
        elif result.structuredContent:
            try:
                if name not in self.session._tool_output_schemas:
                    await self.session.list_tools()
                if name in self.session._tool_output_schemas:
                    output_schema = self.session._tool_output_schemas.get(name)
                    if output_schema:
                        if output_schema.get("x-fastmcp-wrap-result"):
                            output_schema = output_schema.get("properties", {}).get(
                                "result"
                            )
                            structured_content = result.structuredContent.get("result")
                        else:
                            structured_content = result.structuredContent
                        output_type = json_schema_to_type(output_schema)
                        type_adapter = get_cached_typeadapter(output_type)
                        data = type_adapter.validate_python(structured_content)
                    else:
                        data = result.structuredContent
            except Exception as e:
                logger.error(f"[{self.name}] Error parsing structured content: {e}")

        return CallToolResult(
            content=result.content,
            structured_content=result.structuredContent,
            meta=result.meta,
            data=data,
            is_error=result.isError,
        )

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: datetime.timedelta | float | int | None = None,
        progress_handler: ProgressHandler | None = None,
        raise_on_error: bool = True,
        meta: dict[str, Any] | None = None,
        task: Literal[False] = False,
    ) -> CallToolResult: ...

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: datetime.timedelta | float | int | None = None,
        progress_handler: ProgressHandler | None = None,
        raise_on_error: bool = True,
        meta: dict[str, Any] | None = None,
        task: Literal[True],
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> ToolTask: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: datetime.timedelta | float | int | None = None,
        progress_handler: ProgressHandler | None = None,
        raise_on_error: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> CallToolResult | ToolTask:
        """Call a tool on the server.

        Unlike call_tool_mcp, this method raises a ToolError if the tool call results in an error.

        Args:
            name (str): The name of the tool to call.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the tool. Defaults to None.
            timeout (datetime.timedelta | float | int | None, optional): The timeout for the tool call. Defaults to None.
            progress_handler (ProgressHandler | None, optional): The progress handler to use for the tool call. Defaults to None.
            raise_on_error (bool, optional): Whether to raise an exception if the tool call results in an error. Defaults to True.
            meta (dict[str, Any] | None, optional): Additional metadata to include with the request.
                This is useful for passing contextual information (like user IDs, trace IDs, or preferences)
                that shouldn't be tool arguments but may influence server-side processing. The server
                can access this via `context.request_context.meta`. Defaults to None.
            task (bool): If True, execute as background task (SEP-1686). Defaults to False.
            task_id (str | None): Optional client-provided task ID (auto-generated if not provided).
            ttl (int): Time to keep results available in milliseconds (default 60s).

        Returns:
            CallToolResult | ToolTask: The content returned by the tool if task=False,
                or a ToolTask object if task=True. If the tool returns structured
                outputs, they are returned as a dataclass (if an output schema
                is available) or a dictionary; otherwise, a list of content
                blocks is returned. Note: to receive both structured and
                unstructured outputs, use call_tool_mcp instead and access the
                raw result object.

        Raises:
            ToolError: If the tool call results in an error.
            RuntimeError: If called while the client is not connected.
        """
        if task:
            return await self._call_tool_as_task(name, arguments, task_id, ttl)

        result = await self.call_tool_mcp(
            name=name,
            arguments=arguments or {},
            timeout=timeout,
            progress_handler=progress_handler,
            meta=meta,
        )
        return await self._parse_call_tool_result(
            name, result, raise_on_error=raise_on_error
        )

    async def _call_tool_as_task(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> ToolTask:
        """Call a tool for background execution (SEP-1686).

        Returns a ToolTask object that handles both background and immediate execution.
        If the server accepts background execution, ToolTask will poll for results.
        If the server declines (graceful degradation), ToolTask wraps the immediate result.

        Args:
            name: Tool name to call
            arguments: Tool arguments
            task_id: Optional client-provided task ID (ignored, for backward compatibility)
            ttl: Time to keep results available in milliseconds (default 60s)

        Returns:
            ToolTask: Future-like object for accessing task status and results
        """
        # Per SEP-1686 final spec: client sends only ttl, server generates taskId
        # Call tool with task metadata (no taskId sent)
        result = await self.call_tool_mcp(
            name=name,
            arguments=arguments or {},
            meta={
                "modelcontextprotocol.io/task": {
                    "ttl": ttl,
                }
            },
        )

        # Check if server accepted background execution
        # If response includes task metadata with taskId, server accepted background mode
        # If response includes returned_immediately=True, server declined and executed sync
        task_meta = (result.meta or {}).get("modelcontextprotocol.io/task", {})
        if task_meta.get("taskId"):
            # Background execution accepted - extract server-generated taskId
            server_task_id = task_meta["taskId"]
            # Track this task ID for list_tasks()
            self._submitted_task_ids.add(server_task_id)

            # Create task object
            task_obj = ToolTask(
                self, server_task_id, tool_name=name, immediate_result=None
            )

            # Register for notification routing
            self._task_registry[server_task_id] = weakref.ref(task_obj)  # type: ignore[assignment]

            return task_obj
        else:
            # Server declined background execution (graceful degradation)
            # or returned_immediately=True - executed synchronously
            # Wrap the immediate result
            parsed_result = await self._parse_call_tool_result(name, result)
            # Use a synthetic task ID for the immediate result
            synthetic_task_id = task_id or str(uuid.uuid4())
            return ToolTask(
                self, synthetic_task_id, tool_name=name, immediate_result=parsed_result
            )

    async def get_task_status(self, task_id: str) -> GetTaskResult:
        """Query the status of a background task.

        Sends a 'tasks/get' MCP protocol request over the existing transport.

        Args:
            task_id: The task ID returned from call_tool_as_task

        Returns:
            GetTaskResult: Status information including taskId, status, pollInterval, etc.

        Raises:
            RuntimeError: If client not connected
        """
        request = GetTaskRequest(params=GetTaskRequestParams(taskId=task_id))
        return await self.session.send_request(
            request=request,  # type: ignore[arg-type]
            result_type=GetTaskResult,  # type: ignore[arg-type]
        )

    async def get_task_result(self, task_id: str) -> Any:
        """Retrieve the raw result of a completed background task.

        Sends a 'tasks/result' MCP protocol request over the existing transport.
        Returns the raw result - callers should parse it appropriately.

        Args:
            task_id: The task ID returned from call_tool_as_task

        Returns:
            Any: The raw result (could be tool, prompt, or resource result)

        Raises:
            RuntimeError: If client not connected, task not found, or task failed
        """
        request = GetTaskPayloadRequest(
            params=GetTaskPayloadRequestParams(taskId=task_id)
        )
        # Return raw result - Task classes handle type-specific parsing
        result = await self.session.send_request(
            request=request,  # type: ignore[arg-type]
            result_type=GetTaskPayloadResult,  # type: ignore[arg-type]
        )
        # Return as dict for compatibility with Task class parsing
        return result.model_dump(exclude_none=True, by_alias=True)

    async def list_tasks(
        self,
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
        """
        # Send protocol request
        params = PaginatedRequestParams(cursor=cursor, limit=limit)
        request = ListTasksRequest(params=params)
        server_response = await self.session.send_request(
            request=request,  # type: ignore[invalid-argument-type]
            result_type=mcp.types.ListTasksResult,
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
            except Exception:
                # Task may have expired or been deleted, skip it
                continue

        return {"tasks": tasks, "nextCursor": None}

    async def cancel_task(self, task_id: str) -> mcp.types.CancelTaskResult:
        """Cancel a task, transitioning it to cancelled state.

        Sends a 'tasks/cancel' MCP protocol request. Task will halt execution
        and transition to cancelled state.

        Args:
            task_id: The task ID to cancel

        Returns:
            CancelTaskResult: The task status showing cancelled state

        Raises:
            RuntimeError: If task doesn't exist
        """
        request = CancelTaskRequest(params=CancelTaskRequestParams(taskId=task_id))
        return await self.session.send_request(
            request=request,  # type: ignore[invalid-argument-type]
            result_type=mcp.types.CancelTaskResult,
        )

    @classmethod
    def generate_name(cls, name: str | None = None) -> str:
        class_name = cls.__name__
        if name is None:
            return f"{class_name}-{secrets.token_hex(2)}"
        else:
            return f"{class_name}-{name}-{secrets.token_hex(2)}"
