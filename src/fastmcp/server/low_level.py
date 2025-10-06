from __future__ import annotations

import weakref
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import anyio
import mcp.types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.lowlevel.server import (
    LifespanResultT,
    NotificationOptions,
    RequestT,
)
from mcp.server.lowlevel.server import (
    Server as _Server,
)
from mcp.server.models import InitializationOptions
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server as stdio_server
from mcp.shared.message import SessionMessage
from mcp.shared.session import RequestResponder

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


class MiddlewareServerSession(ServerSession):
    """ServerSession that routes initialization requests through FastMCP middleware."""

    def __init__(self, fastmcp: FastMCP, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fastmcp_ref: weakref.ref[FastMCP] = weakref.ref(fastmcp)

    @property
    def fastmcp(self) -> FastMCP:
        """Get the FastMCP instance."""
        fastmcp = self._fastmcp_ref()
        if fastmcp is None:
            raise RuntimeError("FastMCP instance is no longer available")
        return fastmcp

    async def _received_request(
        self,
        responder: RequestResponder[mcp.types.ClientRequest, mcp.types.ServerResult],
    ):
        """
        Override the _received_request method to route initialization requests
        through FastMCP middleware.

        These are not handled by routes that FastMCP typically overrides and
        require special handling.
        """
        import fastmcp.server.context
        from fastmcp.server.middleware.middleware import MiddlewareContext

        if isinstance(responder.request.root, mcp.types.InitializeRequest):

            async def call_original_handler(
                ctx: MiddlewareContext,
            ) -> None:
                return await super(MiddlewareServerSession, self)._received_request(
                    responder
                )

            async with fastmcp.server.context.Context(
                fastmcp=self.fastmcp
            ) as fastmcp_ctx:
                # Create the middleware context.
                mw_context = MiddlewareContext(
                    message=responder.request.root,
                    source="client",
                    type="request",
                    method="initialize",
                    fastmcp_context=fastmcp_ctx,
                )

                return await self.fastmcp._apply_middleware(
                    mw_context, call_original_handler
                )
        else:
            return await super()._received_request(responder)


class LowLevelServer(_Server[LifespanResultT, RequestT]):
    def __init__(self, fastmcp: FastMCP, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Store a weak reference to FastMCP to avoid circular references
        self._fastmcp_ref: weakref.ref[FastMCP] = weakref.ref(fastmcp)

        # FastMCP servers support notifications for all components
        self.notification_options = NotificationOptions(
            prompts_changed=True,
            resources_changed=True,
            tools_changed=True,
        )

    @property
    def fastmcp(self) -> FastMCP:
        """Get the FastMCP instance."""
        fastmcp = self._fastmcp_ref()
        if fastmcp is None:
            raise RuntimeError("FastMCP instance is no longer available")
        return fastmcp

    def create_initialization_options(
        self,
        notification_options: NotificationOptions | None = None,
        experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> InitializationOptions:
        # ensure we use the FastMCP notification options
        if notification_options is None:
            notification_options = self.notification_options
        return super().create_initialization_options(
            notification_options=notification_options,
            experimental_capabilities=experimental_capabilities,
            **kwargs,
        )

    async def run(
        self,
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception],
        write_stream: MemoryObjectSendStream[SessionMessage],
        initialization_options: InitializationOptions,
        raise_exceptions: bool = False,
        stateless: bool = False,
    ):
        """
        Overrides the run method to use the MiddlewareServerSession.
        """
        async with AsyncExitStack() as stack:
            lifespan_context = await stack.enter_async_context(self.lifespan(self))
            session = await stack.enter_async_context(
                MiddlewareServerSession(
                    self.fastmcp,
                    read_stream,
                    write_stream,
                    initialization_options,
                    stateless=stateless,
                )
            )

            async with anyio.create_task_group() as tg:
                async for message in session.incoming_messages:
                    tg.start_soon(
                        self._handle_message,
                        message,
                        session,
                        lifespan_context,
                        raise_exceptions,
                    )
