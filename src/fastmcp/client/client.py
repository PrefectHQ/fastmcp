import datetime
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, cast

import anyio
import mcp.types
from exceptiongroup import catch
from mcp import ClientSession
from pydantic import AnyUrl

from fastmcp.client.logging import (
    LogHandler,
    MessageHandler,
    create_log_callback,
    default_log_handler,
)
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

from .transports import ClientTransport, SessionKwargs, infer_transport

__all__ = [
    "Client",
    "RootsHandler",
    "RootsList",
    "LogHandler",
    "MessageHandler",
    "SamplingHandler",
    "ProgressHandler",
]


class Client:
    """
    MCP client that delegates connection management to a Transport instance.

    The Client class is responsible for MCP protocol logic, while the Transport
    handles connection establishment and management. Client provides methods
    for working with resources, prompts, tools and other MCP capabilities.

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

    def __init__(
        self,
        transport: ClientTransport
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
        message_handler: MessageHandler | None = None,
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,
    ):
        self.transport = infer_transport(transport)
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._nesting_counter: int = 0
        self._initialize_result: mcp.types.InitializeResult | None = None

        if log_handler is None:
            log_handler = default_log_handler

        if progress_handler is None:
            progress_handler = default_progress_handler

        self._progress_handler = progress_handler

        if isinstance(timeout, int | float):
            timeout = datetime.timedelta(seconds=timeout)

        self._session_kwargs: SessionKwargs = {
            "sampling_callback": None,
            "list_roots_callback": None,
            "logging_callback": create_log_callback(log_handler),
            "message_handler": message_handler,
            "read_timeout_seconds": timeout,
        }

        if roots is not None:
            self.set_roots(roots)

        if sampling_handler is not None:
            self._session_kwargs["sampling_callback"] = create_sampling_callback(
                sampling_handler
            )

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
                    with anyio.fail_after(1):
                        self._initialize_result = await self._session.initialize()
                    yield
                except TimeoutError:
                    raise RuntimeError("Failed to initialize server session")
                finally:
                    self._exit_stack = None
                    self._session = None
                    self._initialize_result = None

    async def __aenter__(self):
        if self._nesting_counter == 0:
            # Create exit stack to manage both context managers
            stack = AsyncExitStack()
            await stack.__aenter__()

            await stack.enter_async_context(self._context_manager())

            self._exit_stack = stack

        self._nesting_counter += 1

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._nesting_counter -= 1

        if self._nesting_counter == 0:
            # Exit the stack which will handle cleaning up the session
            if self._exit_stack is not None:
                try:
                    await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
                finally:
                    self._exit_stack = None

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
        self, name: str, arguments: dict[str, str] | None = None
    ) -> mcp.types.GetPromptResult:
        """Send a prompts/get request and return the complete MCP protocol result.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, str] | None, optional): Arguments to pass to the prompt. Defaults to None.

        Returns:
            mcp.types.GetPromptResult: The complete response object from the protocol,
                containing the prompt messages and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
        """
        result = await self.session.get_prompt(name=name, arguments=arguments)
        return result

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> mcp.types.GetPromptResult:
        """Retrieve a rendered prompt message list from the server.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, str] | None, optional): Arguments to pass to the prompt. Defaults to None.

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
    ) -> list[
        mcp.types.TextContent | mcp.types.ImageContent | mcp.types.EmbeddedResource
    ]:
        """Call a tool on the server.

        Unlike call_tool_mcp, this method raises a ToolError if the tool call results in an error.

        Args:
            name (str): The name of the tool to call.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the tool. Defaults to None.
            timeout (datetime.timedelta | float | int | None, optional): The timeout for the tool call. Defaults to None.
            progress_handler (ProgressHandler | None, optional): The progress handler to use for the tool call. Defaults to None.

        Returns:
            list[mcp.types.TextContent | mcp.types.ImageContent | mcp.types.EmbeddedResource]:
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
