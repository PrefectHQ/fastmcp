"""Tests for middleware in HTTP apps."""

from collections.abc import Callable
from typing import Any

import httpx
from httpx import ASGITransport
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route
from starlette.types import ASGIApp

from fastmcp.server import FastMCP
from fastmcp.server.http import create_sse_app, create_streamable_http_app


class HeaderMiddleware(BaseHTTPMiddleware):
    """Simple middleware that adds a custom header to responses."""

    def __init__(self, app: ASGIApp, header_name: str, header_value: str):
        super().__init__(app)
        self.header_name = header_name
        self.header_value = header_value

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        response.headers[self.header_name] = self.header_value
        return response


class RequestModifierMiddleware(BaseHTTPMiddleware):
    """Middleware that adds a value to request state."""

    def __init__(self, app: ASGIApp, key: str, value: Any):
        super().__init__(app)
        self.key = key
        self.value = value

    async def dispatch(self, request: Request, call_next: Callable):
        request.state.custom_value = {self.key: self.value}
        return await call_next(request)


async def endpoint_handler(request: Request):
    """Endpoint that returns request state or headers."""
    if hasattr(request.state, "custom_value"):
        return JSONResponse({"state": request.state.custom_value})
    return JSONResponse({"message": "Hello, world!"})


async def test_sse_app_with_custom_middleware():
    """Test that custom middleware works with SSE app."""
    server = FastMCP(name="TestServer")

    # Create custom middleware
    # TODO(ty): remove when Starlette Middleware typing is supported
    custom_middleware = [
        Middleware(
            HeaderMiddleware,  # type: ignore[arg-type]
            header_name="X-Custom-Header",
            header_value="test-value",
        )
    ]

    # Add a test route to server's additional routes
    routes: list[BaseRoute] = [Route("/test", endpoint_handler)]
    server._additional_http_routes = routes

    # Create the app with custom middleware
    app = server.http_app(transport="sse", middleware=custom_middleware)

    # Create a test client
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/test")

        # Verify middleware was applied
        assert response.status_code == 200
        assert response.headers["X-Custom-Header"] == "test-value"


async def test_streamable_http_app_with_custom_middleware():
    """Test that custom middleware works with StreamableHTTP app."""
    server = FastMCP(name="TestServer")

    # Create custom middleware
    # TODO(ty): remove when Starlette Middleware typing is supported
    custom_middleware = [
        Middleware(
            HeaderMiddleware,  # type: ignore[arg-type]
            header_name="X-Custom-Header",
            header_value="test-value",
        )
    ]

    # Add a test route to server's additional routes
    routes: list[BaseRoute] = [Route("/test", endpoint_handler)]
    server._additional_http_routes = routes

    # Create the app with custom middleware
    app = server.http_app(transport="http", middleware=custom_middleware)

    # Create a test client
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/test")

        # Verify middleware was applied
        assert response.status_code == 200
        assert response.headers["X-Custom-Header"] == "test-value"


async def test_create_sse_app_with_custom_middleware():
    """Test that custom middleware works with create_sse_app function."""
    server = FastMCP(name="TestServer")

    # Create custom middleware
    # TODO(ty): remove when Starlette Middleware typing is supported
    custom_middleware = [
        Middleware(
            RequestModifierMiddleware,  # type: ignore[arg-type]
            key="modified_by",
            value="middleware",
        )
    ]

    # Add a test route
    additional_routes: list[BaseRoute] = [Route("/test", endpoint_handler)]

    # Create the app with custom middleware
    app = create_sse_app(
        server=server,
        message_path="/message",
        sse_path="/sse/",
        middleware=custom_middleware,
        routes=additional_routes,
    )

    # Create a test client
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/test")

        # Verify middleware was applied
        assert response.status_code == 200
        data = response.json()
        assert "state" in data
        assert data["state"]["modified_by"] == "middleware"


async def test_create_streamable_http_app_with_custom_middleware():
    """Test that custom middleware works with create_streamable_http_app function."""
    server = FastMCP(name="TestServer")

    # Create custom middleware
    # TODO(ty): remove when Starlette Middleware typing is supported
    custom_middleware = [
        Middleware(
            RequestModifierMiddleware,  # type: ignore[arg-type]
            key="modified_by",
            value="middleware",
        )
    ]

    # Add a test route
    additional_routes: list[BaseRoute] = [Route("/test", endpoint_handler)]

    # Create the app with custom middleware
    app = create_streamable_http_app(
        server=server,
        streamable_http_path="/streamable",
        middleware=custom_middleware,
        routes=additional_routes,
    )

    # Create a test client
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/test")

        # Verify middleware was applied
        assert response.status_code == 200
        data = response.json()
        assert "state" in data
        assert data["state"]["modified_by"] == "middleware"


async def test_multiple_middleware_ordering():
    """Test that multiple middleware are applied in the correct order."""
    server = FastMCP(name="TestServer")

    # Create multiple middleware
    # TODO(ty): remove when Starlette Middleware typing is supported
    custom_middleware = [
        Middleware(
            HeaderMiddleware,  # type: ignore[arg-type]
            header_name="X-First-Header",
            header_value="first",
        ),
        Middleware(
            HeaderMiddleware,  # type: ignore[arg-type]
            header_name="X-Second-Header",
            header_value="second",
        ),
    ]

    # Add a test route to server's additional routes
    routes: list[BaseRoute] = [Route("/test", endpoint_handler)]
    server._additional_http_routes = routes

    # Create the app with custom middleware
    app = server.http_app(transport="sse", middleware=custom_middleware)

    # Create a test client
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/test")

        # Verify both middleware were applied
        assert response.status_code == 200
        assert response.headers["X-First-Header"] == "first"
        assert response.headers["X-Second-Header"] == "second"
