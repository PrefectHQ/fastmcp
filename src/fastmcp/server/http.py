from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import (
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.auth.provider import OAuthAuthorizationServerProvider
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_current_http_request: ContextVar[Request | None] = ContextVar(
    "http_request",
    default=None,
)


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
        with set_http_request(Request(scope)):
            await self.app(scope, receive, send)


def create_sse_app(
    server: FastMCP,
    message_path: str,
    sse_path: str,
    auth_server_provider: OAuthAuthorizationServerProvider | None = None,
    auth_settings: AuthSettings | None = None,
    debug: bool = False,
    additional_routes: list[Route] | list[Mount] | list[Route | Mount] | None = None,
) -> Starlette:
    """Return an instance of the SSE server app.

    Args:
        server: The FastMCP server instance
        message_path: Path for SSE messages
        sse_path: Path for SSE connections
        auth_server_provider: Optional auth provider
        auth_settings: Optional auth settings
        debug: Whether to enable debug mode
        additional_routes: Optional list of custom routes

    Returns:
        A Starlette application with RequestContextMiddleware
    """
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

    # Configure routes and middleware
    routes: list[Route | Mount] = []
    middleware: list[Middleware] = []

    # Handle authentication configuration
    if auth_server_provider:
        # Ensure auth settings are provided when auth provider is present
        if not auth_settings:
            raise ValueError(
                "auth_settings must be provided when auth_server_provider is specified"
            )

        # Configure auth middleware
        middleware = [
            Middleware(
                AuthenticationMiddleware,
                backend=BearerAuthBackend(provider=auth_server_provider),
            ),
            Middleware(AuthContextMiddleware),
        ]

        # Get required scopes for authentication
        required_scopes = auth_settings.required_scopes or []

        # Add auth routes
        routes.extend(
            create_auth_routes(
                provider=auth_server_provider,
                issuer_url=auth_settings.issuer_url,
                service_documentation_url=auth_settings.service_documentation_url,
                client_registration_options=auth_settings.client_registration_options,
                revocation_options=auth_settings.revocation_options,
            )
        )

        # Add authenticated routes
        routes.append(
            Route(
                sse_path,
                endpoint=RequireAuthMiddleware(handle_sse, required_scopes),
                methods=["GET"],
            )
        )
        routes.append(
            Mount(
                message_path,
                app=RequireAuthMiddleware(sse.handle_post_message, required_scopes),
            )
        )
    else:
        # No authentication required
        async def sse_endpoint(request: Request) -> Response:
            return await handle_sse(request.scope, request.receive, request._send)  # type: ignore[reportPrivateUsage]

        routes.append(
            Route(
                sse_path,
                endpoint=sse_endpoint,
                methods=["GET"],
            )
        )
        routes.append(
            Mount(
                message_path,
                app=sse.handle_post_message,
            )
        )

    # Add custom routes with lowest precedence
    if additional_routes:
        routes.extend(additional_routes)

    # Add RequestContextMiddleware as the outermost middleware
    middleware.append(Middleware(RequestContextMiddleware))

    # Create and return the Starlette app with middleware
    return Starlette(debug=debug, routes=routes, middleware=middleware)
