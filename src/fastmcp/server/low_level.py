from __future__ import annotations

import importlib.metadata
import logging
import weakref
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import anyio
import mcp.types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import LoggingLevel, McpError
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
from mcp.types import ErrorData, JSONRPCError, JSONRPCMessage, JSONRPCRequest, JSONRPCResponse
from pydantic import AnyUrl

from fastmcp.apps.config import UI_EXTENSION_ID
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)

# Guard: _receive_loop override was written for mcp SDK 1.x
_MCP_VERSION = importlib.metadata.version("mcp")
if not _MCP_VERSION.startswith("1."):
    import warnings

    warnings.warn(
        f"Event protocol _receive_loop override was written for mcp 1.x, current: {_MCP_VERSION}"
    )


@dataclass
class _EventRequestContext:
    """Lightweight context for event request handlers.

    Provides the session reference that ``_handle_subscribe_events`` and
    siblings expect, without pulling in the full ``RequestContext``
    machinery (which is unavailable for event requests since they are
    not in the SDK's ``ClientRequest`` union).
    """

    session: ServerSession


class MiddlewareServerSession(ServerSession):
    """ServerSession that routes initialization requests through FastMCP middleware."""

    def __init__(self, fastmcp: FastMCP, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fastmcp_ref: weakref.ref[FastMCP] = weakref.ref(fastmcp)
        # Task group for subscription tasks (set during session run)
        self._subscription_task_group: anyio.TaskGroup | None = None  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
        # Minimum logging level requested by the client via logging/setLevel
        self._minimum_logging_level: LoggingLevel | None = None

    @property
    def fastmcp(self) -> FastMCP:
        """Get the FastMCP instance."""
        fastmcp = self._fastmcp_ref()
        if fastmcp is None:
            raise RuntimeError("FastMCP instance is no longer available")
        return fastmcp

    def client_supports_extension(self, extension_id: str) -> bool:
        """Check if the connected client supports a given MCP extension.

        Inspects the ``extensions`` extra field on ``ClientCapabilities``
        sent by the client during initialization.
        """
        client_params = self._client_params
        if client_params is None:
            return False
        caps = client_params.capabilities
        if caps is None:
            return False
        # ClientCapabilities uses extra="allow" — extensions is an extra field
        extras = caps.model_extra or {}
        extensions: dict[str, Any] | None = extras.get("extensions")
        if not extensions:
            return False
        return extension_id in extensions

    # Methods handled directly at the JSON-RPC level because their request
    # types are not in the published SDK's ClientRequest union.
    _EVENT_METHODS = frozenset({"events/subscribe", "events/unsubscribe", "events/list"})

    async def _receive_loop(self) -> None:
        """Override to intercept events/* methods before SDK validation.

        **Why this exists**: The mcp SDK's ``ClientRequest`` union does not
        include event request types (``events/subscribe``,
        ``events/unsubscribe``, ``events/list``).  Sending these through the
        default ``_receive_loop`` causes a Pydantic validation error.  We
        intercept them at the raw JSON-RPC level and handle them directly.

        **SDK version**: Written against ``mcp 1.x``
        (``ServerSession._receive_loop``).  A module-level version guard emits
        a warning if the installed mcp package moves to 2.x+.

        **When to remove**: Once the upstream mcp SDK adds event types to the
        ``ClientRequest`` union, this override (and the version guard) can be
        deleted, and event handling can use standard SDK request handlers.
        """
        async with (
            self._read_stream,
            self._write_stream,
        ):
            try:
                async for message in self._read_stream:
                    if isinstance(message, Exception):
                        await self._handle_incoming(message)
                    elif isinstance(message.message.root, JSONRPCRequest):
                        method = message.message.root.method
                        if method in self._EVENT_METHODS:
                            # Only handle events/* if the server has events capability
                            if self.fastmcp._event_topics:
                                await self._handle_event_request(
                                    method=method,
                                    params=message.message.root.params or {},
                                    request_id=message.message.root.id,
                                )
                            else:
                                # No events capability: return -32601 Method not found
                                error_response = JSONRPCError(
                                    jsonrpc="2.0",
                                    id=message.message.root.id,
                                    error=ErrorData(
                                        code=-32601,
                                        message=f"Method not found: {method}",
                                    ),
                                )
                                session_message = SessionMessage(
                                    message=JSONRPCMessage(error_response)
                                )
                                await self._write_stream.send(session_message)
                        else:
                            # Delegate to parent for standard SDK validation
                            await self._handle_sdk_request(message)
                    elif isinstance(message.message.root, mcp.types.JSONRPCNotification):
                        await self._handle_sdk_notification(message)
                    else:
                        await self._handle_response(message)
            except anyio.ClosedResourceError:
                logging.debug("Read stream closed by client")
            except Exception as e:
                logging.exception(f"Unhandled exception in receive loop: {e}")
            finally:
                for id, stream in self._response_streams.items():
                    error = ErrorData(code=-32000, message="Connection closed")
                    try:
                        await stream.send(JSONRPCError(jsonrpc="2.0", id=id, error=error))
                        await stream.aclose()
                    except Exception:
                        pass
                self._response_streams.clear()

    async def _handle_sdk_request(self, message: SessionMessage) -> None:
        """Process a request through the standard SDK validation path."""
        from mcp.types import INVALID_PARAMS

        try:
            validated_request = self._receive_request_type.model_validate(
                message.message.root.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                )
            )
            responder = RequestResponder(
                request_id=message.message.root.id,
                request_meta=validated_request.root.params.meta
                if validated_request.root.params
                else None,
                request=validated_request,
                session=self,
                on_complete=lambda r: self._in_flight.pop(r.request_id, None),
                message_metadata=message.metadata,
            )
            self._in_flight[responder.request_id] = responder
            await self._received_request(responder)

            if not responder._completed:
                await self._handle_incoming(responder)
        except Exception as e:
            logging.warning(f"Failed to validate request: {e}")
            error_response = JSONRPCError(
                jsonrpc="2.0",
                id=message.message.root.id,
                error=ErrorData(code=INVALID_PARAMS, message="Invalid request parameters", data=""),
            )
            session_message = SessionMessage(message=JSONRPCMessage(error_response))
            await self._write_stream.send(session_message)

    async def _handle_sdk_notification(self, message: SessionMessage) -> None:
        """Process a notification through the standard SDK path."""
        from mcp.types import CancelledNotification, ProgressNotification

        try:
            notification = self._receive_notification_type.model_validate(
                message.message.root.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                )
            )
            if isinstance(notification.root, CancelledNotification):
                cancelled_id = notification.root.params.requestId
                if cancelled_id in self._in_flight:
                    await self._in_flight[cancelled_id].cancel()
            else:
                if isinstance(notification.root, ProgressNotification):
                    progress_token = notification.root.params.progressToken
                    if progress_token in self._progress_callbacks:
                        callback = self._progress_callbacks[progress_token]
                        try:
                            await callback(
                                notification.root.params.progress,
                                notification.root.params.total,
                                notification.root.params.message,
                            )
                        except Exception as e:
                            logging.error("Progress callback raised an exception: %s", e)
                await self._received_notification(notification)
                await self._handle_incoming(notification)
        except Exception as e:
            logging.warning(
                f"Failed to validate notification: {e}. Message was: {message.message.root}"
            )

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

            responder.respond = capturing_respond  # type: ignore[method-assign]  # ty:ignore[invalid-assignment]

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
                    return None

        # Fall through to default handling (task methods now handled via registered handlers)
        return await super()._received_request(responder)

    async def _handle_event_request(
        self,
        method: str,
        params: dict[str, Any],
        request_id: Any,
    ) -> None:
        """Handle events/* requests before SDK validation.

        Since event request types are not in the published SDK's ClientRequest
        union, we intercept them at the raw JSON-RPC level and handle them
        directly, sending the response back through the write stream.
        """
        fastmcp = self.fastmcp

        ctx = _EventRequestContext(self)

        try:
            if method == "events/subscribe":
                from fastmcp.server.events import EventSubscribeParams

                typed_params = EventSubscribeParams(**params)
                result_dict = await fastmcp._handle_subscribe_events(ctx, typed_params)
            elif method == "events/unsubscribe":
                from fastmcp.server.events import EventUnsubscribeParams

                typed_params = EventUnsubscribeParams(**params)
                result_dict = await fastmcp._handle_unsubscribe_events(
                    ctx, typed_params
                )
            elif method == "events/list":
                result_dict = await fastmcp._handle_list_events(ctx, None)
            else:
                return  # Should not happen

            response = JSONRPCResponse(
                jsonrpc="2.0",
                id=request_id,
                result=result_dict,
            )
        except McpError as e:
            response = JSONRPCError(
                jsonrpc="2.0",
                id=request_id,
                error=e.error,
            )
        except Exception as e:
            logging.warning(f"Error handling {method}: {e}")
            response = JSONRPCError(
                jsonrpc="2.0",
                id=request_id,
                error=ErrorData(
                    code=-32603,
                    message=f"Internal error: {e}",
                ),
            )

        session_message = SessionMessage(message=JSONRPCMessage(response))
        await self._write_stream.send(session_message)


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

        # Set events capability with declared topic descriptors
        if self.fastmcp._event_topics:
            from fastmcp.server.events import EventsCapability

            # ServerCapabilities has extra="allow", so this extra field
            # survives serialization just like tasks and extensions do.
            capabilities.events = EventsCapability(  # type: ignore[attr-defined]
                topics=list(self.fastmcp._event_topics.values()),
            )

        # Advertise MCP Apps extension support (io.modelcontextprotocol/ui)
        # Uses the same extra-field pattern as tasks above — ServerCapabilities
        # has extra="allow" so this survives serialization.
        # Merge with any existing extensions to avoid clobbering other features.
        existing_extensions: dict[str, Any] = (
            getattr(capabilities, "extensions", None) or {}
        )
        capabilities.extensions = {**existing_extensions, UI_EXTENSION_ID: {}}

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

            # Register session for event broadcasting
            from uuid import uuid4

            session_id = str(uuid4())
            session._fastmcp_event_session_id = session_id  # type: ignore[attr-defined]
            self.fastmcp._active_sessions.add(session)

            try:
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
            finally:
                # Cleanup: remove session and its subscriptions
                self.fastmcp._active_sessions.discard(session)
                await self.fastmcp._subscription_registry.remove_all(session_id)

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
