"""Tests verifying that client transports do not leak auth credentials on redirects.

httpx automatically strips Authorization headers on cross-origin redirects via its
_redirect_headers mechanism. These tests verify that FastMCP's transports rely on
this behavior correctly and do not override it.
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport


class TestHttpxBuiltinRedirectProtection:
    """Verify httpx's built-in cross-origin redirect auth stripping."""

    async def test_httpx_strips_auth_on_cross_origin_redirect(self):
        """httpx strips Authorization headers when redirecting to a different origin."""
        received_headers: dict[str, str] = {}

        async def target_endpoint(request: Request) -> Response:
            received_headers.update(dict(request.headers))
            return JSONResponse({"status": "ok"})

        async def redirect_cross_origin(request: Request) -> Response:
            return RedirectResponse(
                url="http://other-host.example.com/target",
                status_code=302,
            )

        app = Starlette(
            routes=[
                Route("/redirect", redirect_cross_origin),
                Route("/target", target_endpoint),
            ]
        )

        # Use an httpx client with follow_redirects=True (as MCP does)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            follow_redirects=True,
        ) as client:
            response = await client.get(
                "http://origin-host.example.com/redirect",
                headers={"Authorization": "Bearer secret-token"},
            )

        # httpx followed the redirect but stripped Authorization because
        # the redirect target is a different origin
        assert response.status_code == 200
        assert "authorization" not in received_headers

    async def test_httpx_preserves_auth_on_same_origin_redirect(self):
        """httpx preserves Authorization headers when redirecting to the same origin."""
        received_headers: dict[str, str] = {}

        async def target_endpoint(request: Request) -> Response:
            received_headers.update(dict(request.headers))
            return JSONResponse({"status": "ok"})

        async def redirect_same_origin(request: Request) -> Response:
            return RedirectResponse(
                url="http://same-host.example.com/target",
                status_code=302,
            )

        app = Starlette(
            routes=[
                Route("/redirect", redirect_same_origin),
                Route("/target", target_endpoint),
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            follow_redirects=True,
        ) as client:
            response = await client.get(
                "http://same-host.example.com/redirect",
                headers={"Authorization": "Bearer secret-token"},
            )

        assert response.status_code == 200
        assert received_headers.get("authorization") == "Bearer secret-token"

    @pytest.mark.parametrize(
        "auth_header",
        [
            "Bearer my-secret-token",
            "Basic dXNlcjpwYXNz",
            "Token ghp_xxxxxxxxxxxx",
        ],
    )
    async def test_various_auth_headers_stripped_on_cross_origin(
        self, auth_header: str
    ):
        """Verify that different auth header formats are all stripped."""
        received_headers: dict[str, str] = {}

        async def target(request: Request) -> Response:
            received_headers.update(dict(request.headers))
            return JSONResponse({"status": "ok"})

        async def redirect(request: Request) -> Response:
            return RedirectResponse(
                url="http://evil.example.com/steal",
                status_code=307,
            )

        app = Starlette(
            routes=[
                Route("/api", redirect),
                Route("/steal", target),
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            follow_redirects=True,
        ) as client:
            response = await client.get(
                "http://legit.example.com/api",
                headers={"Authorization": auth_header},
            )

        assert response.status_code == 200
        assert "authorization" not in received_headers


class TestMcpHttpClientRedirectProtection:
    """Verify that MCP's default httpx client has redirect protection."""

    async def test_create_mcp_http_client_strips_auth_on_cross_origin(self):
        """create_mcp_http_client creates clients that strip auth on cross-origin redirects."""
        received_headers: dict[str, str] = {}

        async def target(request: Request) -> Response:
            received_headers.update(dict(request.headers))
            return JSONResponse({"status": "ok"})

        async def redirect(request: Request) -> Response:
            return RedirectResponse(
                url="http://evil.example.com/steal",
                status_code=302,
            )

        app = Starlette(
            routes=[
                Route("/api", redirect),
                Route("/steal", target),
            ]
        )

        # Use AsyncClient directly with ASGI transport rather than
        # monkey-patching _transport on create_mcp_http_client, which
        # breaks when proxy env vars are set.
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            headers={"Authorization": "Bearer secret"},
            follow_redirects=True,
        )

        async with client:
            response = await client.get("http://legit.example.com/api")

        assert response.status_code == 200
        assert "authorization" not in received_headers


class TestStreamableHttpTransportFactory:
    """Verify factory and verify-factory redirect behavior."""

    def test_verify_factory_still_enables_redirects(self):
        """The verify factory should still create clients with follow_redirects=True."""
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            verify=False,
        )
        factory = transport._make_verify_factory()
        assert factory is not None
        client = factory()
        assert client.follow_redirects is True

    def test_sse_verify_factory_still_enables_redirects(self):
        """The SSE verify factory should still create clients with follow_redirects=True."""
        transport = SSETransport(
            "https://example.com/sse",
            verify=False,
        )
        factory = transport._make_verify_factory()
        assert factory is not None
        client = factory()
        assert client.follow_redirects is True
