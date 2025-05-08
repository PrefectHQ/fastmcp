"""StreamableHTTP Session Manager for MCP servers."""

# follows https://github.com/modelcontextprotocol/python-sdk/blob/ihrpr/shttp/src/mcp/server/streamable_http_manager.py
# and can be removed once that spec is finalized

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Any
from uuid import uuid4

import anyio
from anyio.abc import TaskStatus
from mcp.server.lowlevel.server import Server as MCPServer
from mcp.server.streamable_http import (
    MCP_SESSION_ID_HEADER,
    EventStore,
    StreamableHTTPServerTransport,
)
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)


class StreamableHTTPSessionManager:
    """
    Manages StreamableHTTP sessions with optional resumability via event store.

    This class abstracts away the complexity of session management, event storage,
    and request handling for StreamableHTTP transports. It handles:

    1. Session tracking for clients
    2. Resumability via an optional event store
    3. Connection management and lifecycle
    4. Request handling and transport setup

    Args:
        app: The MCP server instance
        event_store: Optional event store for resumability support.
                     If provided, enables resumable connections where clients
                     can reconnect and receive missed events.
                     If None, sessions are still tracked but not resumable.
        json_response: Whether to use JSON responses instead of SSE streams
        stateless: If True, creates a completely fresh transport for each request
                   with no session tracking or state persistence between requests.

    """

    def __init__(
        self,
        app: MCPServer[Any],
        event_store: EventStore | None = None,
        json_response: bool = False,
        stateless: bool = False,
    ):
        self.app = app
        self.event_store = event_store
        self.json_response = json_response
        self.stateless = stateless

        # Session tracking (only used if not stateless)
        self._session_creation_lock = anyio.Lock()
        self._server_instances: dict[str, StreamableHTTPServerTransport] = {}

        # The task group will be set during lifespan
        self._task_group = None

    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """
        Run the session manager with proper lifecycle management.

        This creates and manages the task group for all session operations.

        Use this in the lifespan context manager of your Starlette app:

        @contextlib.asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncIterator[None]:
            async with session_manager.run():
                yield
        """
        async with anyio.create_task_group() as tg:
            # Store the task group for later use
            self._task_group = tg
            logger.info("StreamableHTTP session manager started")
            try:
                yield  # Let the application run
            finally:
                logger.info("StreamableHTTP session manager shutting down")
                # Cancel task group to stop all spawned tasks
                tg.cancel_scope.cancel()
                self._task_group = None
                # Clear any remaining server instances
                self._server_instances.clear()

    async def handle_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """
        Process ASGI request with proper session handling and transport setup.

        Dispatches to the appropriate handler based on stateless mode.

        Args:
            scope: ASGI scope
            receive: ASGI receive function
            send: ASGI send function
        """
        if self._task_group is None:
            raise RuntimeError(
                "Task group is not initialized. Make sure to use the run()."
            )

        # Dispatch to the appropriate handler
        if self.stateless:
            await self._handle_stateless_request(scope, receive, send)
        else:
            await self._handle_stateful_request(scope, receive, send)

    async def _handle_stateless_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """
        Process request in stateless mode - creating a new transport for each request.

        Args:
            scope: ASGI scope
            receive: ASGI receive function
            send: ASGI send function
        """
        logger.debug("Stateless mode: Creating new transport for this request")
        # No session ID needed in stateless mode
        http_transport = StreamableHTTPServerTransport(
            mcp_session_id=None,  # No session tracking in stateless mode
            is_json_response_enabled=self.json_response,
            event_store=None,  # No event store in stateless mode
        )

        # Start server in a new task
        async def run_stateless_server(
            *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED
        ):
            async with http_transport.connect() as streams:
                read_stream, write_stream = streams
                task_status.started()
                await self.app.run(
                    read_stream,
                    write_stream,
                    self.app.create_initialization_options(),
                    stateless=True,
                )

        # Assert task group is not None for type checking
        assert self._task_group is not None
        # Start the server task
        await self._task_group.start(run_stateless_server)

        # Handle the HTTP request and return the response
        await http_transport.handle_request(scope, receive, send)

    async def _handle_stateful_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """
        Process request in stateful mode - maintaining session state between requests.

        Args:
            scope: ASGI scope
            receive: ASGI receive function
            send: ASGI send function
        """
        request = Request(scope, receive)
        request_mcp_session_id = request.headers.get(MCP_SESSION_ID_HEADER)

        # Existing session case
        if (
            request_mcp_session_id is not None
            and request_mcp_session_id in self._server_instances
        ):
            transport = self._server_instances[request_mcp_session_id]
            logger.debug("Session already exists, handling request directly")
            await transport.handle_request(scope, receive, send)
            return

        if request_mcp_session_id is None:
            # New session case
            logger.debug("Creating new transport")
            async with self._session_creation_lock:
                new_session_id = uuid4().hex
                http_transport = StreamableHTTPServerTransport(
                    mcp_session_id=new_session_id,
                    is_json_response_enabled=self.json_response,
                    event_store=self.event_store,  # May be None (no resumability)
                )

                assert http_transport.mcp_session_id is not None
                self._server_instances[http_transport.mcp_session_id] = http_transport
                logger.info(f"Created new transport with session ID: {new_session_id}")

                # Define the server runner
                async def run_server(
                    *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED
                ) -> None:
                    async with http_transport.connect() as streams:
                        read_stream, write_stream = streams
                        task_status.started()
                        await self.app.run(
                            read_stream,
                            write_stream,
                            self.app.create_initialization_options(),
                            stateless=False,  # Stateful mode
                        )

                # Assert task group is not None for type checking
                assert self._task_group is not None
                # Start the server task
                await self._task_group.start(run_server)

                # Handle the HTTP request and return the response
                await http_transport.handle_request(scope, receive, send)
        else:
            # Invalid session ID
            response = Response(
                "Bad Request: No valid session ID provided",
                status_code=HTTPStatus.BAD_REQUEST,
            )
            await response(scope, receive, send)
