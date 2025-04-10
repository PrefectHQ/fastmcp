import abc
import contextlib
import datetime
from typing import Any, AsyncContextManager, TypedDict

import mcp.types
from mcp import ClientSession
from mcp.client.session import ListRootsFnT, LoggingFnT, MessageHandlerFnT, SamplingFnT
from mcp.shared.context import LifespanContextT, RequestContext
from pydantic import AnyUrl


def _get_roots_callback(roots: list[mcp.types.Root]) -> ListRootsFnT | None:
    async def _roots_callback(
        context: RequestContext[ClientSession, LifespanContextT],
    ) -> mcp.types.ListRootsResult:
        return mcp.types.ListRootsResult(roots=roots)

    return _roots_callback


class ClientKwargs(TypedDict, total=False):
    roots: list[mcp.types.Root] | None
    sampling_callback: SamplingFnT | None
    list_roots_callback: ListRootsFnT | None
    logging_callback: LoggingFnT | None
    message_handler: MessageHandlerFnT | None
    read_timeout_seconds: datetime.timedelta | None


class SessionKwargs(TypedDict, total=False):
    sampling_callback: SamplingFnT | None
    list_roots_callback: ListRootsFnT | None
    logging_callback: LoggingFnT | None
    message_handler: MessageHandlerFnT | None
    read_timeout_seconds: datetime.timedelta | None


class BaseClient(abc.ABC):
    def __init__(
        self,
        roots: list[mcp.types.Root] | None = None,
        sampling_callback: SamplingFnT | None = None,
        list_roots_callback: ListRootsFnT | None = None,
        logging_callback: LoggingFnT | None = None,
        message_handler: MessageHandlerFnT | None = None,
        read_timeout_seconds: datetime.timedelta | None = None,
    ):
        self._transport: Any | None = None
        self._session: ClientSession | None = None
        self._cm: AsyncContextManager | None = None

        if roots is not None:
            if list_roots_callback is not None:
                raise ValueError(
                    "Cannot provide both `roots` and `list_roots_callback`. "
                    "Either provide a list of roots or a callback to list roots."
                )
            else:
                list_roots_callback = _get_roots_callback(roots)

        self._sampling_callback = sampling_callback
        self._list_roots_callback = list_roots_callback
        self._logging_callback = logging_callback
        self._message_handler = message_handler
        self._read_timeout_seconds = read_timeout_seconds

    def _session_kwargs(self) -> SessionKwargs:
        return SessionKwargs(
            sampling_callback=self._sampling_callback,
            list_roots_callback=self._list_roots_callback,
            logging_callback=self._logging_callback,
            message_handler=self._message_handler,
            read_timeout_seconds=self._read_timeout_seconds,
        )

    @property
    def transport(self):
        """Get the current transport connection"""
        if self._transport is None:
            raise RuntimeError(
                "Client is not connected. Use 'async with client:' context manager first."
            )
        return self._transport

    @property
    def session(self):
        """Get the current session"""
        if self._session is None:
            raise RuntimeError(
                "Client is not connected. Use 'async with client:' context manager first."
            )
        return self._session

    def is_connected(self):
        """Check if the client is currently connected"""
        return self._session is not None

    @abc.abstractmethod
    def _connect(self) -> AsyncContextManager:
        """Return an async context manager that handles connection lifecycle.
        This will be called by __aenter__ to establish the connection."""
        raise NotImplementedError("Subclasses must implement this method")

    @contextlib.asynccontextmanager
    async def _create_connection_context(self):
        """Create and manage the connection context if not already connected.
        This handles both creating a new connection or reusing an existing one."""
        created_connection = False
        try:
            if not self.is_connected():
                # Only create a new connection if not already connected
                self._cm = self._connect()
                await self._cm.__aenter__()
                created_connection = True
            yield
        finally:
            if created_connection and self._cm is not None:
                # Only close if we created the connection in this context
                await self._cm.__aexit__(None, None, None)
                self._transport = None
                self._session = None
                self._cm = None

    @contextlib.asynccontextmanager
    async def _set_session(self, transport: Any, session: ClientSession):
        self._transport = transport
        self._session = session
        try:
            await self._session.initialize()
            yield
        finally:
            self._transport = None
            self._session = None

    async def __aenter__(self):
        self._connection_ctx = self._create_connection_context()
        await self._connection_ctx.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._connection_ctx.__aexit__(exc_type, exc_val, exc_tb)

    # --- MCP Client Methods ---

    async def ping(self) -> None:
        """Send a ping request."""
        await self.session.send_ping()

    async def progress(
        self, progress_token: str | int, progress: float, total: float | None = None
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
            uri = AnyUrl(uri)
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
