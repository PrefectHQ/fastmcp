from __future__ import annotations

import weakref
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import anyio
import mcp.types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import McpError
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
from pydantic import AnyUrl

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


class MiddlewareServerSession(ServerSession):
    """ServerSession that routes initialization requests through FastMCP middleware."""

    def __init__(self, fastmcp: FastMCP, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fastmcp_ref: weakref.ref[FastMCP] = weakref.ref(fastmcp)
        # Task group for subscription tasks (set during session run)
        self._subscription_task_group: anyio.TaskGroup | None = None  # type: ignore[valid-type]

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
        Override the _received_request method to route special requests
        through FastMCP middleware.

        Handles initialization requests and SEP-1686 task methods.
        """
        import fastmcp.server.context
        from fastmcp.server.middleware.middleware import MiddlewareContext

        if isinstance(responder.request.root, mcp.types.InitializeRequest):
            # The MCP SDK's ServerSession._received_request() handles the
            # initialize request internally by calling responder.respond()
            # to send the InitializeResult directly to the write stream, then
            # returning None. This bypasses the middleware return path entirely,
            # so middleware would only see the request, never the response.
            #
            # To expose the response to middleware (e.g., for logging server
            # capabilities), we wrap responder.respond() to capture the
            # InitializeResult before it's sent, then return it from
            # call_original_handler so it flows back through the middleware chain.
            captured_response: mcp.types.ServerResult | None = None
            original_respond = responder.respond

            async def capturing_respond(
                response: mcp.types.ServerResult,
            ) -> None:
                nonlocal captured_response
                captured_response = response
                return await original_respond(response)

            responder.respond = capturing_respond  # type: ignore[method-assign]

            async def call_original_handler(
                ctx: MiddlewareContext,
            ) -> mcp.types.InitializeResult | None:
                await super(MiddlewareServerSession, self)._received_request(responder)
                if captured_response is not None and isinstance(
                    captured_response.root, mcp.types.InitializeResult
                ):
                    return captured_response.root
                return None

            async with fastmcp.server.context.Context(
                fastmcp=self.fastmcp, session=self
            ) as fastmcp_ctx:
                # Create the middleware context.
                mw_context = MiddlewareContext(
                    message=responder.request.root,
                    source="client",
                    type="request",
                    method="initialize",
                    fastmcp_context=fastmcp_ctx,
                )

                try:
                    return await self.fastmcp._run_middleware(
                        mw_context, call_original_handler
                    )
                except McpError as e:
                    # McpError can be thrown from middleware in `on_initialize`
                    # send the error to responder.
                    if not responder._completed:
                        with responder:
                            await responder.respond(e.error)
                    else:
                        # Don't re-raise: prevents responding to initialize request twice
                        logger.warning(
                            "Received McpError but responder is already completed. "
                            "Cannot send error response as response was already sent.",
                            exc_info=e,
                        )

        # Fall through to default handling (task methods now handled via registered handlers)
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

    def get_capabilities(
        self,
        notification_options: NotificationOptions,
        experimental_capabilities: dict[str, dict[str, Any]],
    ) -> mcp.types.ServerCapabilities:
        """Override to set capabilities.tasks as a first-class field per SEP-1686.

        This ensures task capabilities appear in capabilities.tasks instead of
        capabilities.experimental.tasks, which is required by the MCP spec and
        enables proper task detection by clients like VS Code Copilot 1.107+.
        """
        from fastmcp.server.tasks.capabilities import get_task_capabilities

        # Get base capabilities from SDK (pass empty dict for experimental)
        # since we'll set tasks as a first-class field instead
        capabilities = super().get_capabilities(
            notification_options,
            experimental_capabilities or {},
        )

        # Set tasks as a first-class field (not experimental) per SEP-1686
        capabilities.tasks = get_task_capabilities()

        return capabilities

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
                # Store task group on session for subscription tasks (SEP-1686)
                session._subscription_task_group = tg

                async for message in session.incoming_messages:
                    tg.start_soon(
                        self._handle_message,
                        message,
                        session,
                        lifespan_context,
                        raise_exceptions,
                    )

    def read_resource(
        self,
    ) -> Callable[
        [
            Callable[
                [AnyUrl],
                Awaitable[mcp.types.ReadResourceResult | mcp.types.CreateTaskResult],
            ]
        ],
        Callable[
            [AnyUrl],
            Awaitable[mcp.types.ReadResourceResult | mcp.types.CreateTaskResult],
        ],
    ]:
        """
        Decorator for registering a read_resource handler with CreateTaskResult support.

        The MCP SDK's read_resource decorator does not support returning CreateTaskResult
        for background task execution. This decorator wraps the result in ServerResult.

        This decorator can be removed once the MCP SDK adds native CreateTaskResult support
        for resources.
        """

        def decorator(
            func: Callable[
                [AnyUrl],
                Awaitable[mcp.types.ReadResourceResult | mcp.types.CreateTaskResult],
            ],
        ) -> Callable[
            [AnyUrl],
            Awaitable[mcp.types.ReadResourceResult | mcp.types.CreateTaskResult],
        ]:
            async def handler(
                req: mcp.types.ReadResourceRequest,
            ) -> mcp.types.ServerResult:
                result = await func(req.params.uri)
                return mcp.types.ServerResult(result)

            self.request_handlers[mcp.types.ReadResourceRequest] = handler
            return func

        return decorator

    def get_prompt(
        self,
    ) -> Callable[
        [
            Callable[
                [str, dict[str, Any] | None],
                Awaitable[mcp.types.GetPromptResult | mcp.types.CreateTaskResult],
            ]
        ],
        Callable[
            [str, dict[str, Any] | None],
            Awaitable[mcp.types.GetPromptResult | mcp.types.CreateTaskResult],
        ],
    ]:
        """
        Decorator for registering a get_prompt handler with CreateTaskResult support.

        The MCP SDK's get_prompt decorator does not support returning CreateTaskResult
        for background task execution. This decorator wraps the result in ServerResult.

        This decorator can be removed once the MCP SDK adds native CreateTaskResult support
        for prompts.
        """

        def decorator(
            func: Callable[
                [str, dict[str, Any] | None],
                Awaitable[mcp.types.GetPromptResult | mcp.types.CreateTaskResult],
            ],
        ) -> Callable[
            [str, dict[str, Any] | None],
            Awaitable[mcp.types.GetPromptResult | mcp.types.CreateTaskResult],
        ]:
            async def handler(
                req: mcp.types.GetPromptRequest,
            ) -> mcp.types.ServerResult:
                result = await func(req.params.name, req.params.arguments)
                return mcp.types.ServerResult(result)

            self.request_handlers[mcp.types.GetPromptRequest] = handler
            return func

        return decorator
