"""Transport-related methods for FastMCP Server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

import anyio
import uvicorn
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

import fastmcp
from fastmcp.server.event_store import EventStore
from fastmcp.server.http import (
    StarletteWithLifespan,
    create_sse_app,
    create_streamable_http_app,
)
from fastmcp.utilities.cli import log_server_banner
from fastmcp.utilities.logging import get_logger, temporary_log_level

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP, Transport

logger = get_logger(__name__)


class TransportMixin:
    """Mixin providing transport-related methods for FastMCP.

    Includes HTTP/stdio/SSE transport handling and custom HTTP routes.
    """

    async def run_async(
        self: FastMCP,
        transport: Transport | None = None,
        show_banner: bool | None = None,
        **transport_kwargs: Any,
    ) -> None:
        """Run the FastMCP server asynchronously.

        Args:
            transport: Transport protocol to use ("stdio", "http", "sse", or "streamable-http")
            show_banner: Whether to display the server banner. If None, uses the
                FASTMCP_SHOW_SERVER_BANNER setting (default: True).
        """
        if show_banner is None:
            show_banner = fastmcp.settings.show_server_banner
        if transport is None:
            transport = "stdio"
        if transport not in {"stdio", "http", "sse", "streamable-http"}:
            raise ValueError(f"Unknown transport: {transport}")

        if transport == "stdio":
            await self.run_stdio_async(
                show_banner=show_banner,
                **transport_kwargs,
            )
        elif transport in {"http", "sse", "streamable-http"}:
            await self.run_http_async(
                transport=transport,
                show_banner=show_banner,
                **transport_kwargs,
            )
        else:
            raise ValueError(f"Unknown transport: {transport}")

    def run(
        self: FastMCP,
        transport: Transport | None = None,
        show_banner: bool | None = None,
        **transport_kwargs: Any,
    ) -> None:
        """Run the FastMCP server. Note this is a synchronous function.

        Args:
            transport: Transport protocol to use ("http", "stdio", "sse", or "streamable-http")
            show_banner: Whether to display the server banner. If None, uses the
                FASTMCP_SHOW_SERVER_BANNER setting (default: True).
        """

        anyio.run(
            partial(
                self.run_async,
                transport,
                show_banner=show_banner,
                **transport_kwargs,
            )
        )

    def custom_route(
        self: FastMCP,
        path: str,
        methods: list[str],
        name: str | None = None,
        include_in_schema: bool = True,
    ) -> Callable[
        [Callable[[Request], Awaitable[Response]]],
        Callable[[Request], Awaitable[Response]],
    ]:
        """
        Decorator to register a custom HTTP route on the FastMCP server.

        Allows adding arbitrary HTTP endpoints outside the standard MCP protocol,
        which can be useful for OAuth callbacks, health checks, or admin APIs.
        The handler function must be an async function that accepts a Starlette
        Request and returns a Response.

        Args:
            path: URL path for the route (e.g., "/auth/callback")
            methods: List of HTTP methods to support (e.g., ["GET", "POST"])
            name: Optional name for the route (to reference this route with
                Starlette's reverse URL lookup feature)
            include_in_schema: Whether to include in OpenAPI schema, defaults to True

        Example:
            Register a custom HTTP route for a health check endpoint:
            ```python
            @server.custom_route("/health", methods=["GET"])
            async def health_check(request: Request) -> Response:
                return JSONResponse({"status": "ok"})
            ```
        """

        def decorator(
            fn: Callable[[Request], Awaitable[Response]],
        ) -> Callable[[Request], Awaitable[Response]]:
            self._additional_http_routes.append(
                Route(
                    path,
                    endpoint=fn,
                    methods=methods,
                    name=name,
                    include_in_schema=include_in_schema,
                )
            )
            return fn

        return decorator

    def _get_additional_http_routes(self: FastMCP) -> list[BaseRoute]:
        """Get all additional HTTP routes including from providers.

        Returns a list of all custom HTTP routes from this server and
        from all providers that have HTTP routes (e.g., FastMCPProvider).

        Returns:
            List of Starlette BaseRoute objects
        """
        return list(self._additional_http_routes)

    async def run_stdio_async(
        self: FastMCP,
        show_banner: bool = True,
        log_level: str | None = None,
        stateless: bool = False,
    ) -> None:
        """Run the server using stdio transport.

        Args:
            show_banner: Whether to display the server banner
            log_level: Log level for the server
            stateless: Whether to run in stateless mode (no session initialization)
        """
        from fastmcp.server.context import reset_transport, set_transport

        # Display server banner
        if show_banner:
            log_server_banner(server=self)

        token = set_transport("stdio")
        try:
            with temporary_log_level(log_level):
                async with self._lifespan_manager():
                    async with stdio_server() as (read_stream, write_stream):
                        mode = " (stateless)" if stateless else ""
                        logger.info(
                            f"Starting MCP server {self.name!r} with transport 'stdio'{mode}"
                        )

                        await self._mcp_server.run(
                            read_stream,
                            write_stream,
                            self._mcp_server.create_initialization_options(
                                notification_options=NotificationOptions(
                                    tools_changed=True
                                ),
                            ),
                            stateless=stateless,
                        )
        finally:
            reset_transport(token)

    async def run_http_async(
        self: FastMCP,
        show_banner: bool = True,
        transport: Literal["http", "streamable-http", "sse"] = "http",
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
        path: str | None = None,
        uvicorn_config: dict[str, Any] | None = None,
        middleware: list[ASGIMiddleware] | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        stateless: bool | None = None,
    ) -> None:
        """Run the server using HTTP transport.

        Args:
            transport: Transport protocol to use - "http" (default), "streamable-http", or "sse"
            host: Host address to bind to (defaults to settings.host)
            port: Port to bind to (defaults to settings.port)
            log_level: Log level for the server (defaults to settings.log_level)
            path: Path for the endpoint (defaults to settings.streamable_http_path or settings.sse_path)
            uvicorn_config: Additional configuration for the Uvicorn server
            middleware: A list of middleware to apply to the app
            json_response: Whether to use JSON response format (defaults to settings.json_response)
            stateless_http: Whether to use stateless HTTP (defaults to settings.stateless_http)
            stateless: Alias for stateless_http for CLI consistency
        """
        # Allow stateless as alias for stateless_http
        if stateless is not None and stateless_http is None:
            stateless_http = stateless

        # Resolve from settings/env var if not explicitly set
        if stateless_http is None:
            stateless_http = self._deprecated_settings.stateless_http

        # SSE doesn't support stateless mode
        if stateless_http and transport == "sse":
            raise ValueError("SSE transport does not support stateless mode")

        host = host or self._deprecated_settings.host
        port = port or self._deprecated_settings.port
        default_log_level_to_use = (
            log_level or self._deprecated_settings.log_level
        ).lower()

        app = self.http_app(
            path=path,
            transport=transport,
            middleware=middleware,
            json_response=json_response,
            stateless_http=stateless_http,
        )

        # Display server banner
        if show_banner:
            log_server_banner(server=self)
        uvicorn_config_from_user = uvicorn_config or {}

        config_kwargs: dict[str, Any] = {
            "timeout_graceful_shutdown": 0,
            "lifespan": "on",
            "ws": "websockets-sansio",
        }
        config_kwargs.update(uvicorn_config_from_user)

        if "log_config" not in config_kwargs and "log_level" not in config_kwargs:
            config_kwargs["log_level"] = default_log_level_to_use

        with temporary_log_level(log_level):
            async with self._lifespan_manager():
                config = uvicorn.Config(app, host=host, port=port, **config_kwargs)
                server = uvicorn.Server(config)
                path = getattr(app.state, "path", "").lstrip("/")
                mode = " (stateless)" if stateless_http else ""
                logger.info(
                    f"Starting MCP server {self.name!r} with transport {transport!r}{mode} on http://{host}:{port}/{path}"
                )

                await server.serve()

    def http_app(
        self: FastMCP,
        path: str | None = None,
        middleware: list[ASGIMiddleware] | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        transport: Literal["http", "streamable-http", "sse"] = "http",
        event_store: EventStore | None = None,
        retry_interval: int | None = None,
    ) -> StarletteWithLifespan:
        """Create a Starlette app using the specified HTTP transport.

        Args:
            path: The path for the HTTP endpoint
            middleware: A list of middleware to apply to the app
            json_response: Whether to use JSON response format
            stateless_http: Whether to use stateless mode (new transport per request)
            transport: Transport protocol to use - "http", "streamable-http", or "sse"
            event_store: Optional event store for SSE polling/resumability. When set,
                enables clients to reconnect and resume receiving events after
                server-initiated disconnections. Only used with streamable-http transport.
            retry_interval: Optional retry interval in milliseconds for SSE polling.
                Controls how quickly clients should reconnect after server-initiated
                disconnections. Requires event_store to be set. Only used with
                streamable-http transport.

        Returns:
            A Starlette application configured with the specified transport
        """

        if transport in ("streamable-http", "http"):
            return create_streamable_http_app(
                server=self,
                streamable_http_path=path
                or self._deprecated_settings.streamable_http_path,
                event_store=event_store,
                retry_interval=retry_interval,
                auth=self.auth,
                json_response=(
                    json_response
                    if json_response is not None
                    else self._deprecated_settings.json_response
                ),
                stateless_http=(
                    stateless_http
                    if stateless_http is not None
                    else self._deprecated_settings.stateless_http
                ),
                debug=self._deprecated_settings.debug,
                middleware=middleware,
            )
        elif transport == "sse":
            return create_sse_app(
                server=self,
                message_path=self._deprecated_settings.message_path,
                sse_path=path or self._deprecated_settings.sse_path,
                auth=self.auth,
                debug=self._deprecated_settings.debug,
                middleware=middleware,
            )
        else:
            raise ValueError(f"Unknown transport: {transport}")
