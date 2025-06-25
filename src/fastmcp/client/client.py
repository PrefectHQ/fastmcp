import asyncio
import datetime
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Generic, Literal, cast, overload

import anyio
import httpx
import mcp.types
import pydantic_core
from exceptiongroup import catch
from mcp import ClientSession
from pydantic import AnyUrl

import fastmcp
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
from fastmcp.client.sampling import SamplingHandler, create_sampling_callback
from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.utilities.exceptions import get_catch_handlers
from fastmcp.utilities.mcp_config import MCPConfig
from fastmcp.utilities.types import MCPContent

from .transports import (
    ClientTransportT,
    FastMCP1Server,
    FastMCPTransport,
    MCPConfigTransport,
    NodeStdioTransport,
    PythonStdioTransport,
    SessionKwargs,
    SSETransport,
    StreamableHttpTransport,
    infer_transport,
)

__all__ = [
    "Client",
    "SessionKwargs",
    "RootsHandler",
    "RootsList",
    "LogHandler",
    "MessageHandler",
    "SamplingHandler",
    "ProgressHandler",
]


class Client(Generic[ClientTransportT]):
    """
    MCP client that delegates connection management to a Transport instance.

    The Client class is responsible for MCP protocol logic, while the Transport
    handles connection establishment and management. Client provides methods for
    working with resources, prompts, tools and other MCP capabilities.

    Args:
        transport: Connection source specification, which can be:
            - ClientTransport: Direct transport instance
            - FastMCP: In-process FastMCP server
            - AnyUrl | str: URL to connect to
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
        ```python # Connect to FastMCP server client =
        Client("http://localhost:8080")

        async with client:
            # List available resources resources = await client.list_resources()

            # Call a tool result = await client.call_tool("my_tool", {"param":
            "value"})
        ```
    """

    @overload
    def __new__(
        cls,
        transport: ClientTransportT,
        **kwargs: Any,
    ) -> "Client[ClientTransportT]": ...

    @overload
    def __new__(
        cls, transport: AnyUrl, **kwargs
    ) -> "Client[SSETransport|StreamableHttpTransport]": ...

    @overload
    def __new__(
        cls, transport: FastMCP | FastMCP1Server, **kwargs
    ) -> "Client[FastMCPTransport]": ...

    @overload
    def __new__(
        cls, transport: Path, **kwargs
    ) -> "Client[PythonStdioTransport|NodeStdioTransport]": ...

    @overload
    def __new__(
        cls, transport: MCPConfig | dict[str, Any], **kwargs
    ) -> "Client[MCPConfigTransport]": ...

    @overload
    def __new__(
        cls, transport: str, **kwargs
    ) -> "Client[PythonStdioTransport|NodeStdioTransport|SSETransport|StreamableHttpTransport]": ...

    def __new__(cls, transport, **kwargs) -> "Client":
        instance = super().__new__(cls)
        return instance

    def __init__(
        self,
        transport: ClientTransportT
        | FastMCP
        | AnyUrl
        | Path
        | MCPConfig
        | dict[str, Any]
        | str,
        # Common args
        roots: RootsList | RootsHandler | None = None,
        sampling_handler: SamplingHandler | None = None,
        log_handler: LogHandler | None = None,
        message_handler: MessageHandlerT | MessageHandler | None = None,
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        init_timeout: datetime.timedelta | float | int | None = None,
        client_info: mcp.types.Implementation | None = None,
        auth: httpx.Auth | Literal["oauth"] | str | None = None,
    ):
        self.transport = cast(ClientTransportT, infer_transport(transport))
        if auth is not None:
            self.transport._set_auth(auth)
        self._initialize_result: mcp.types.InitializeResult | None = None

        if log_handler is None:
            log_handler = default_log_handler

        if progress_handler is None:
            progress_handler = default_progress_handler

        self._progress_handler = progress_handler

        if isinstance(timeout, int | float):
            timeout = datetime.timedelta(seconds=timeout)

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

        self._session_kwargs: SessionKwargs = {
            "sampling_callback": None,
            "list_roots_callback": None,
            "logging_callback": create_log_callback(log_handler),
            "message_handler": message_handler,
            "read_timeout_seconds": timeout,
            "client_info": client_info,
        }

        if roots is not None:
            self.set_roots(roots)

        if sampling_handler is not None:
            self._session_kwargs["sampling_callback"] = create_sampling_callback(
                sampling_handler
            )

        # session context management
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._nesting_counter: int = 0
        self._context_lock = anyio.Lock()
        self._session_task: asyncio.Task | None = None
        self._ready_event = anyio.Event()
        self._stop_event = anyio.Event()

    @property
    def session(self) -> ClientSession:
        """Get the current active session. Raises RuntimeError if not connected."""
        if self._session is None:
            raise RuntimeError(
                "Client is not connected. Use the 'async with client:' context manager first."
            )

        return self._session

    @property
    def initialize_result(self) -> mcp.types.InitializeResult:
        """Get the result of the initialization request."""
        if self._initialize_result is None:
            raise RuntimeError(
                "Client is not connected. Use the 'async with client:' context manager first."
            )
        return self._initialize_result

    def set_roots(self, roots: RootsList | RootsHandler) -> None:
        """Set the roots for the client. This does not automatically call `send_roots_list_changed`."""
        self._session_kwargs["list_roots_callback"] = create_roots_callback(roots)

    def set_sampling_callback(self, sampling_callback: SamplingHandler) -> None:
        """Set the sampling callback for the client."""
        self._session_kwargs["sampling_callback"] = create_sampling_callback(
            sampling_callback
        )

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self._session is not None

    @asynccontextmanager
    async def _context_manager(self):
        with catch(get_catch_handlers()):
            async with self.transport.connect_session(
                **self._session_kwargs
            ) as session:
                self._session = session
                # Initialize the session
                try:
                    with anyio.fail_after(self._init_timeout):
                        self._initialize_result = await self._session.initialize()
                    yield
                except anyio.ClosedResourceError:
                    raise RuntimeError("Server session was closed unexpectedly")
                except TimeoutError:
                    raise RuntimeError("Failed to initialize server session")
                finally:
                    self._session = None
                    self._initialize_result = None

    async def __aenter__(self):
        await self._connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._disconnect()

    async def _connect(self):
        # ensure only one session is running at a time to avoid race conditions
        async with self._context_lock:
            need_to_start = self._session_task is None or self._session_task.done()
            if need_to_start:
                self._stop_event = anyio.Event()
                self._ready_event = anyio.Event()
                self._session_task = asyncio.create_task(self._session_runner())
            await self._ready_event.wait()
            self._nesting_counter += 1
        return self

    async def _disconnect(self, force: bool = False):
        # ensure only one session is running at a time to avoid race conditions
        async with self._context_lock:
            # if we are forcing a disconnect, reset the nesting counter
            if force:
                self._nesting_counter = 0

            # otherwise decrement to check if we are done nesting
            else:
                self._nesting_counter = max(0, self._nesting_counter - 1)

            # if we are still nested, return
            if self._nesting_counter > 0:
                return

            # stop the active seesion
            if self._session_task is None:
                return
            self._stop_event.set()
            runner_task = self._session_task
            self._session_task = None

        # wait for the session to finish
        if runner_task:
            await runner_task

        # Reset for future reconnects
        self._stop_event = anyio.Event()
        self._ready_event = anyio.Event()
        self._session = None
        self._initialize_result = None

    async def _session_runner(self):
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(self._context_manager())
                # Session/context is now ready
                self._ready_event.set()
                # Wait until disconnect/stop is requested
                await self._stop_event.wait()
            finally:
                # On exit, ensure ready event is set (idempotent)
                self._ready_event.set()

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
            mcp.types.CancelledNotification(
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
        self, uri: AnyUrl | str
    ) -> mcp.types.ReadResourceResult:
        """Send a resources/read request and return the complete MCP protocol result.

        Args:
            uri (AnyUrl | str): The URI of the resource to read. Can be a string or an AnyUrl object.

        Returns:
            mcp.types.ReadResourceResult: The complete response object from the protocol,
                containing the resource contents and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        if isinstance(uri, str):
            uri = AnyUrl(uri)  # Ensure AnyUrl
        result = await self.session.read_resource(uri)
        return result

    async def read_resource(
        self, uri: AnyUrl | str
    ) -> list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]:
        """Read the contents of a resource or resolved template.

        Args:
            uri (AnyUrl | str): The URI of the resource to read. Can be a string or an AnyUrl object.

        Returns:
            list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]: A list of content
                objects, typically containing either text or binary data.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        if isinstance(uri, str):
            try:
                uri = AnyUrl(uri)  # Ensure AnyUrl
            except Exception as e:
                raise ValueError(
                    f"Provided resource URI is invalid: {str(uri)!r}"
                ) from e
        result = await self.read_resource_mcp(uri)
        return result.contents

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
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp.types.GetPromptResult:
        """Send a prompts/get request and return the complete MCP protocol result.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the prompt. Defaults to None.

        Returns:
            mcp.types.GetPromptResult: The complete response object from the protocol,
                containing the prompt messages and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
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

        result = await self.session.get_prompt(
            name=name, arguments=serialized_arguments
        )
        return result

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp.types.GetPromptResult:
        """Retrieve a rendered prompt message list from the server.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the prompt. Defaults to None.

        Returns:
            mcp.types.GetPromptResult: The complete response object from the protocol,
                containing the prompt messages and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        result = await self.get_prompt_mcp(name=name, arguments=arguments)
        return result

    # --- Completion ---

    async def complete_mcp(
        self,
        ref: mcp.types.ResourceReference | mcp.types.PromptReference,
        argument: dict[str, str],
    ) -> mcp.types.CompleteResult:
        """Send a completion request and return the complete MCP protocol result.

        Args:
            ref (mcp.types.ResourceReference | mcp.types.PromptReference): The reference to complete.
            argument (dict[str, str]): Arguments to pass to the completion request.

        Returns:
            mcp.types.CompleteResult: The complete response object from the protocol,
                containing the completion and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        result = await self.session.complete(ref=ref, argument=argument)
        return result

    async def complete(
        self,
        ref: mcp.types.ResourceReference | mcp.types.PromptReference,
        argument: dict[str, str],
    ) -> mcp.types.Completion:
        """Send a completion request to the server.

        Args:
            ref (mcp.types.ResourceReference | mcp.types.PromptReference): The reference to complete.
            argument (dict[str, str]): Arguments to pass to the completion request.

        Returns:
            mcp.types.Completion: The completion object.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        result = await self.complete_mcp(ref=ref, argument=argument)
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
    ) -> mcp.types.CallToolResult:
        """Send a tools/call request and return the complete MCP protocol result.

        This method returns the raw CallToolResult object, which includes an isError flag
        and other metadata. It does not raise an exception if the tool call results in an error.

        Args:
            name (str): The name of the tool to call.
            arguments (dict[str, Any]): Arguments to pass to the tool.
            timeout (datetime.timedelta | float | int | None, optional): The timeout for the tool call. Defaults to None.
            progress_handler (ProgressHandler | None, optional): The progress handler to use for the tool call. Defaults to None.

        Returns:
            mcp.types.CallToolResult: The complete response object from the protocol,
                containing the tool result and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """

        if isinstance(timeout, int | float):
            timeout = datetime.timedelta(seconds=timeout)
        result = await self.session.call_tool(
            name=name,
            arguments=arguments,
            read_timeout_seconds=timeout,
            progress_callback=progress_handler or self._progress_handler,
        )
        return result

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        progress_handler: ProgressHandler | None = None,
    ) -> list[MCPContent]:
        """Call a tool on the server.

        Unlike call_tool_mcp, this method raises a ToolError if the tool call results in an error.

        Args:
            name (str): The name of the tool to call.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the tool. Defaults to None.
            timeout (datetime.timedelta | float | int | None, optional): The timeout for the tool call. Defaults to None.
            progress_handler (ProgressHandler | None, optional): The progress handler to use for the tool call. Defaults to None.

        Returns:
            list[mcp.types.TextContent | mcp.types.ImageContent | mcp.types.AudioContent | mcp.types.EmbeddedResource]:
                The content returned by the tool.

        Raises:
            ToolError: If the tool call results in an error.
            RuntimeError: If called while the client is not connected.
        """
        result = await self.call_tool_mcp(
            name=name,
            arguments=arguments or {},
            timeout=timeout,
            progress_handler=progress_handler,
        )
        if result.isError:
            msg = cast(mcp.types.TextContent, result.content[0]).text
            raise ToolError(msg)
        return result.content
