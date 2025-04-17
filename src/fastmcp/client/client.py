import datetime
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Literal, cast, overload

import mcp.types
from mcp import ClientSession
from mcp.client.session import (
    LoggingFnT,
    MessageHandlerFnT,
)
from pydantic import AnyUrl

from fastmcp.client.roots import (
    RootsHandler,
    RootsList,
    create_roots_callback,
)
from fastmcp.client.sampling import SamplingHandler, create_sampling_callback
from fastmcp.exceptions import ClientError
from fastmcp.server import FastMCP

from .transports import ClientTransport, SessionKwargs, infer_transport

__all__ = ["Client", "RootsHandler", "RootsList"]


class Client:
    """
    MCP client that delegates connection management to a Transport instance.

    The Client class is primarily concerned with MCP protocol logic,
    while the Transport handles connection establishment and management.
    """

    def __init__(
        self,
        transport: ClientTransport | FastMCP | AnyUrl | Path | str,
        # Common args
        roots: RootsList | RootsHandler | None = None,
        sampling_handler: SamplingHandler | None = None,
        log_handler: LoggingFnT | None = None,
        message_handler: MessageHandlerFnT | None = None,
        read_timeout_seconds: datetime.timedelta | None = None,
    ):
        self.transport = infer_transport(transport)
        self._session: ClientSession | None = None
        self._session_cms: list[AbstractAsyncContextManager[ClientSession]] = []

        self._session_kwargs: SessionKwargs = {
            "sampling_callback": None,
            "list_roots_callback": None,
            "logging_callback": log_handler,
            "message_handler": message_handler,
            "read_timeout_seconds": read_timeout_seconds,
        }

        if roots is not None:
            self.set_roots(roots)

        if sampling_handler is not None:
            self.set_sampling_callback(sampling_handler)

    @property
    def session(self) -> ClientSession:
        """Get the current active session. Raises RuntimeError if not connected."""
        if self._session is None:
            raise RuntimeError(
                "Client is not connected. Use 'async with client:' context manager first."
            )
        return self._session

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

    async def __aenter__(self):
        if self.is_connected():
            # We're already connected, no need to add None to the session_cms list
            return self

        try:
            session_cm = self.transport.connect_session(**self._session_kwargs)
            self._session_cms.append(session_cm)
            self._session = await self._session_cms[-1].__aenter__()
            return self
        except Exception as e:
            # Ensure cleanup if __aenter__ fails partially
            self._session = None
            if self._session_cms:
                self._session_cms.pop()
            raise ConnectionError(
                f"Failed to connect using {self.transport}: {e}"
            ) from e

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session_cms:
            await self._session_cms[-1].__aexit__(exc_type, exc_val, exc_tb)
            self._session = None
            self._session_cms.pop()

    # --- MCP Client Methods ---
    async def ping(self) -> None:
        """Send a ping request."""
        await self.session.send_ping()

    async def progress(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
    ) -> None:
        """Send a progress notification."""
        await self.session.send_progress_notification(progress_token, progress, total)

    async def set_logging_level(self, level: mcp.types.LoggingLevel) -> None:
        """Send a logging/setLevel request."""
        await self.session.set_logging_level(level)

    async def send_roots_list_changed(self) -> None:
        """Send a roots/list_changed notification."""
        await self.session.send_roots_list_changed()

    async def list_resources(self) -> list[mcp.types.Resource]:
        """Send a resources/list request."""
        result = await self.session.list_resources()
        return result.resources

    async def list_resource_templates(self) -> list[mcp.types.ResourceTemplate]:
        """Send a resources/listResourceTemplates request."""
        result = await self.session.list_resource_templates()
        return result.resourceTemplates

    async def read_resource(
        self, uri: AnyUrl | str
    ) -> list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]:
        """Send a resources/read request."""
        if isinstance(uri, str):
            uri = AnyUrl(uri)  # Ensure AnyUrl
        result = await self.session.read_resource(uri)
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

    async def list_prompts(self) -> list[mcp.types.Prompt]:
        """Send a prompts/list request."""
        result = await self.session.list_prompts()
        return result.prompts

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> list[mcp.types.PromptMessage]:
        """Send a prompts/get request."""
        result = await self.session.get_prompt(name, arguments)
        return result.messages

    async def complete(
        self,
        ref: mcp.types.ResourceReference | mcp.types.PromptReference,
        argument: dict[str, str],
    ) -> mcp.types.Completion:
        """Send a completion request."""
        result = await self.session.complete(ref, argument)
        return result.completion

    async def list_tools(self) -> list[mcp.types.Tool]:
        """Send a tools/list request."""
        result = await self.session.list_tools()
        return result.tools

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        _return_raw_result: Literal[False] = False,
    ) -> list[
        mcp.types.TextContent | mcp.types.ImageContent | mcp.types.EmbeddedResource
    ]: ...

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        _return_raw_result: Literal[True] = True,
    ) -> mcp.types.CallToolResult: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        _return_raw_result: bool = False,
    ) -> (
        list[
            mcp.types.TextContent | mcp.types.ImageContent | mcp.types.EmbeddedResource
        ]
        | mcp.types.CallToolResult
    ):
        """Send a tools/call request."""
        result = await self.session.call_tool(name, arguments)
        if _return_raw_result:
            return result
        elif result.isError:
            msg = cast(mcp.types.TextContent, result.content[0]).text
            raise ClientError(msg)
        return result.content
