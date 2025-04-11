import datetime
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import mcp.types
from mcp import ClientSession
from mcp.client.session import (
    ListRootsFnT,
    LoggingFnT,
    MessageHandlerFnT,
    SamplingFnT,
)
from mcp.shared.context import LifespanContextT, RequestContext
from pydantic import AnyUrl

from fastmcp.server import FastMCP

from .transports import ClientTransport, SessionKwargs, infer_transport


def _get_roots_callback(roots: list[mcp.types.Root]) -> ListRootsFnT | None:
    async def _roots_callback(
        context: RequestContext[ClientSession, LifespanContextT],
    ) -> mcp.types.ListRootsResult:
        return mcp.types.ListRootsResult(roots=roots)

    return _roots_callback


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
        roots: list[mcp.types.Root] | None = None,
        sampling_callback: SamplingFnT | None = None,
        list_roots_callback: ListRootsFnT | None = None,
        logging_callback: LoggingFnT | None = None,
        message_handler: MessageHandlerFnT | None = None,
        read_timeout_seconds: datetime.timedelta | None = None,
    ):
        self.transport = infer_transport(transport)
        self._session: ClientSession | None = None
        self._session_cm: AbstractAsyncContextManager[ClientSession] | None = None

        # Store common kwargs to pass to transport.connect_session
        if roots is not None and list_roots_callback is not None:
            raise ValueError("Cannot provide both `roots` and `list_roots_callback`.")
        resolved_list_roots_callback = list_roots_callback or (
            _get_roots_callback(roots) if roots else None
        )

        self._session_kwargs: SessionKwargs = {
            "sampling_callback": sampling_callback,
            "list_roots_callback": resolved_list_roots_callback,
            "logging_callback": logging_callback,
            "message_handler": message_handler,
            "read_timeout_seconds": read_timeout_seconds,
        }

    @property
    def session(self) -> ClientSession:
        """Get the current active session. Raises RuntimeError if not connected."""
        if self._session is None:
            raise RuntimeError(
                "Client is not connected. Use 'async with client:' context manager first."
            )
        return self._session

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self._session is not None

    async def __aenter__(self):
        if self.is_connected():
            raise RuntimeError("Client is already connected in an async context.")
        try:
            self._session_cm = self.transport.connect_session(**self._session_kwargs)
            self._session = await self._session_cm.__aenter__()
            return self
        except Exception as e:
            # Ensure cleanup if __aenter__ fails partially
            self._session = None
            self._session_cm = None
            raise ConnectionError(
                f"Failed to connect using {self.transport}: {e}"
            ) from e

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session_cm:
            await self._session_cm.__aexit__(exc_type, exc_val, exc_tb)
        self._session = None
        self._session_cm = None

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

    async def list_resources(self) -> mcp.types.ListResourcesResult:
        """Send a resources/list request."""
        return await self.session.list_resources()

    async def list_resource_templates(self) -> mcp.types.ListResourceTemplatesResult:
        """Send a resources/listResourceTemplates request."""
        return await self.session.list_resource_templates()

    async def read_resource(self, uri: AnyUrl | str) -> mcp.types.ReadResourceResult:
        """Send a resources/read request."""
        if isinstance(uri, str):
            uri = AnyUrl(uri)  # Ensure AnyUrl
        return await self.session.read_resource(uri)

    async def subscribe_resource(self, uri: AnyUrl | str) -> None:
        """Send a resources/subscribe request."""
        if isinstance(uri, str):
            uri = AnyUrl(uri)
        await self.session.subscribe_resource(uri)

    async def unsubscribe_resource(self, uri: AnyUrl | str) -> None:
        """Send a resources/unsubscribe request."""
        if isinstance(uri, str):
            uri = AnyUrl(uri)
        await self.session.unsubscribe_resource(uri)

    async def list_prompts(self) -> mcp.types.ListPromptsResult:
        """Send a prompts/list request."""
        return await self.session.list_prompts()

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> mcp.types.GetPromptResult:
        """Send a prompts/get request."""
        return await self.session.get_prompt(name, arguments)

    async def complete(
        self,
        ref: mcp.types.ResourceReference | mcp.types.PromptReference,
        argument: dict[str, str],
    ) -> mcp.types.CompleteResult:
        """Send a completion/complete request."""
        return await self.session.complete(ref, argument)

    async def list_tools(self) -> mcp.types.ListToolsResult:
        """Send a tools/list request."""
        return await self.session.list_tools()

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp.types.CallToolResult:
        """Send a tools/call request."""
        return await self.session.call_tool(name, arguments)

    async def send_roots_list_changed(self) -> None:
        """Send a roots/list_changed notification."""
        await self.session.send_roots_list_changed()
