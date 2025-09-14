from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from mcp.server.lowlevel.server import LifespanResultT
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http import EventStore
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.types import Lifespan, Receive, Scope, Send

from fastmcp.server.auth import AuthProvider
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


class StreamableHTTPASGIApp:
    """ASGI application wrapper for Streamable HTTP server transport."""

    def __init__(self, session_manager):
        self.session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.session_manager.handle_request(scope, receive, send)
        except RuntimeError as e:
            if str(e) == "Task group is not initialized. Make sure to use run().":
                logger.error(
                    f"Original RuntimeError from mcp library: {e}", exc_info=True
                )
                new_error_message = (
                    "FastMCP's StreamableHTTPSessionManager task group was not initialized. "
                    "This commonly occurs when the FastMCP application's lifespan is not "
                    "passed to the parent ASGI application (e.g., FastAPI or Starlette). "
                    "Please ensure you are setting `lifespan=mcp_app.lifespan` in your "
                    "parent app's constructor, where `mcp_app` is the application instance "
                    "returned by `fastmcp_instance.http_app()`. \\n"
                    "For more details, see the FastMCP ASGI integration documentation: "
                    "https://gofastmcp.com/deployment/asgi"
                )
                # Raise a new RuntimeError that includes the original error's message
                # for full context, but leads with the more helpful guidance.
                raise RuntimeError(f"{new_error_message}\\nOriginal error: {e}") from e
            else:
                # Re-raise other RuntimeErrors if they don't match the specific message
                raise


_current_http_request: ContextVar[Request | None] = ContextVar(  # type: ignore[assignment]
    "http_request",
    default=None,
)


class StarletteWithLifespan(Starlette):
    @property
    def lifespan(self) -> Lifespan[Starlette]:
        return self.router.lifespan_context


@contextmanager
def set_http_request(request: Request) -> Generator[Request, None, None]:
    token = _current_http_request.set(request)
    try:
        yield request
    finally:
        _current_http_request.reset(token)


class RequestContextMiddleware:
    """
    Middleware that stores each request in a ContextVar
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            with set_http_request(Request(scope)):
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def create_base_app(
    routes: list[BaseRoute],
    middleware: list[Middleware],
    debug: bool = False,
    lifespan: Callable | None = None,
) -> StarletteWithLifespan:
    """Create a base Starlette app with common middleware and routes.

    Args:
        routes: List of routes to include in the app
        middleware: List of middleware to include in the app
        debug: Whether to enable debug mode
        lifespan: Optional lifespan manager for the app

    Returns:
        A Starlette application
    """
    # Always add RequestContextMiddleware as the outermost middleware
    middleware.insert(0, Middleware(RequestContextMiddleware))

    return StarletteWithLifespan(
        routes=routes,
        middleware=middleware,
        debug=debug,
        lifespan=lifespan,
    )


def create_sse_app(
    server: FastMCP[LifespanResultT],
    message_path: str,
    sse_path: str,
    auth: AuthProvider | None = None,
    debug: bool = False,
    routes: list[BaseRoute] | None = None,
    middleware: list[Middleware] | None = None,
) -> StarletteWithLifespan:
    """Return an instance of the SSE server app.

    Args:
        server: The FastMCP server instance
        message_path: Path for SSE messages
        sse_path: Path for SSE connections
        auth: Optional authentication provider (AuthProvider)
        debug: Whether to enable debug mode
        routes: Optional list of custom routes
        middleware: Optional list of middleware
    Returns:
        A Starlette application with RequestContextMiddleware
    """

    server_routes: list[BaseRoute] = []
    server_middleware: list[Middleware] = []

    # Set up SSE transport
    sse = SseServerTransport(message_path)

    # Create handler for SSE connections
    async def handle_sse(scope: Scope, receive: Receive, send: Send) -> Response:
        async with sse.connect_sse(scope, receive, send) as streams:
            await server._mcp_server.run(
                streams[0],
                streams[1],
                server._mcp_server.create_initialization_options(),
            )
        return Response()

    # Set up auth if enabled
    if auth:
        # Get auth middleware from the provider
        auth_middleware = auth.get_middleware()

        # Get auth routes including protected MCP endpoint
        auth_routes = auth.get_routes(
            mcp_path=sse_path,
            mcp_endpoint=handle_sse,
        )

        server_routes.extend(auth_routes)
        server_middleware.extend(auth_middleware)

        # Manually wrap the SSE message endpoint with RequireAuthMiddleware
        server_routes.append(
            Mount(
                message_path,
                app=RequireAuthMiddleware(
                    sse.handle_post_message,
                    auth.required_scopes,
                    auth._get_resource_url("/.well-known/oauth-protected-resource"),
                ),
            )
        )
    else:
        # No auth required
        async def sse_endpoint(request: Request) -> Response:
            return await handle_sse(request.scope, request.receive, request._send)  # type: ignore[reportPrivateUsage]

        server_routes.append(
            Route(
                sse_path,
                endpoint=sse_endpoint,
                methods=["GET"],
            )
        )
        server_routes.append(
            Mount(
                message_path,
                app=sse.handle_post_message,
            )
        )

    # Add custom routes with lowest precedence
    if routes:
        server_routes.extend(routes)
    server_routes.extend(server._get_additional_http_routes())

    # Add middleware
    if middleware:
        server_middleware.extend(middleware)

    # Create and return the app
    app = create_base_app(
        routes=server_routes,
        middleware=server_middleware,
        debug=debug,
    )
    # Store the FastMCP server instance on the Starlette app state
    app.state.fastmcp_server = server
    app.state.path = sse_path

    return app


def create_streamable_http_app(
    server: FastMCP[LifespanResultT],
    streamable_http_path: str,
    event_store: EventStore | None = None,
    auth: AuthProvider | None = None,
    json_response: bool = False,
    stateless_http: bool = False,
    debug: bool = False,
    routes: list[BaseRoute] | None = None,
    middleware: list[Middleware] | None = None,
) -> StarletteWithLifespan:
    """Return an instance of the StreamableHTTP server app.

    Args:
        server: The FastMCP server instance
        streamable_http_path: Path for StreamableHTTP connections
        event_store: Optional event store for session management
        auth: Optional authentication provider (AuthProvider)
        json_response: Whether to use JSON response format
        stateless_http: Whether to use stateless mode (new transport per request)
        debug: Whether to enable debug mode
        routes: Optional list of custom routes
        middleware: Optional list of middleware

    Returns:
        A Starlette application with StreamableHTTP support
    """
    server_routes: list[BaseRoute] = []
    server_middleware: list[Middleware] = []

    # Create session manager using the provided event store
    session_manager = StreamableHTTPSessionManager(
        app=server._mcp_server,
        event_store=event_store,
        json_response=json_response,
        stateless=stateless_http,
    )

    # Create the ASGI app wrapper
    streamable_http_app = StreamableHTTPASGIApp(session_manager)

    # Add StreamableHTTP routes with or without auth
    if auth:
        # Get auth middleware from the provider
        auth_middleware = auth.get_middleware()

        # Get auth routes including protected MCP endpoint
        auth_routes = auth.get_routes(
            mcp_path=streamable_http_path,
            mcp_endpoint=streamable_http_app,
        )

        server_routes.extend(auth_routes)
        server_middleware.extend(auth_middleware)
    else:
        # No auth required
        server_routes.append(
            Route(
                streamable_http_path,
                endpoint=streamable_http_app,
            )
        )

    # Add custom routes with lowest precedence
    if routes:
        server_routes.extend(routes)
    server_routes.extend(server._get_additional_http_routes())

    # Add middleware
    if middleware:
        server_middleware.extend(middleware)

    # Create a lifespan manager to start and stop the session manager
    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        async with session_manager.run():
            yield

    # Create and return the app with lifespan
    app = create_base_app(
        routes=server_routes,
        middleware=server_middleware,
        debug=debug,
        lifespan=lifespan,
    )
    # Store the FastMCP server instance on the Starlette app state
    app.state.fastmcp_server = server

    app.state.path = streamable_http_path

    return app
