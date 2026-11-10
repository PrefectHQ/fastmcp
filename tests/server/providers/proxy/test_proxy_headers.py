"""Header forwarding across ProxyProvider HTTP hops."""

import json
from typing import Any

import httpx2
from mcp import MCPError
from mcp_types import METHOD_NOT_FOUND
from mcp_types.version import LATEST_HANDSHAKE_VERSION, LATEST_MODERN_VERSION

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from fastmcp.utilities.tests import asgi_server


async def test_proxy_does_not_forward_frontend_mcp_headers_to_legacy_backend():
    """A modern frontend's transport state does not contaminate a legacy backend."""
    captured_requests: list[httpx2.Request] = []

    class RejectDiscovery(Middleware):
        async def on_discover(self, context, call_next):
            raise MCPError(code=METHOD_NOT_FOUND, message="Method not found")

    backend = FastMCP("Legacy Backend", middleware=[RejectDiscovery()])

    @backend.tool
    def legacy_ping() -> str:
        return "pong"

    async with asgi_server(backend) as running_backend:

        async def capture_request(request: httpx2.Request) -> None:
            captured_requests.append(request)

        def backend_http_client(
            headers: dict[str, str] | None = None,
            timeout: httpx2.Timeout | None = None,
            auth: httpx2.Auth | None = None,
            **kwargs: Any,
        ) -> httpx2.AsyncClient:
            return running_backend.http_client(
                headers=headers,
                timeout=timeout,
                auth=auth,
                event_hooks={"request": [capture_request]},
                **kwargs,
            )

        backend_transport = running_backend.transport(
            httpx_client_factory=backend_http_client
        )
        proxy = FastMCP(
            "Proxy",
            providers=[
                ProxyProvider(lambda: ProxyClient(backend_transport, mode="auto"))
            ],
        )

        async with asgi_server(proxy) as running_proxy:
            async with running_proxy.client(
                mode="auto",
                headers={
                    "Authorization": "Bearer frontend-token",
                    "X-Proxy-Custom": "preserved",
                    "Mcp-Name": "frontend-name",
                    "Mcp-Param-Tenant": "frontend-tenant",
                    "Mcp-Session-Id": "frontend-session",
                    "Last-Event-ID": "frontend-event",
                },
            ) as client:
                assert client.protocol_version == LATEST_MODERN_VERSION
                tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["legacy_ping"]

    def request_for(method: str) -> httpx2.Request:
        return next(
            request
            for request in captured_requests
            if request.method == "POST"
            and json.loads(request.content).get("method") == method
        )

    discover = request_for("server/discover")
    initialize = request_for("initialize")
    list_tools = request_for("tools/list")

    assert discover.headers["mcp-protocol-version"] == LATEST_MODERN_VERSION
    assert discover.headers["mcp-method"] == "server/discover"

    assert "mcp-protocol-version" not in initialize.headers
    assert "mcp-method" not in initialize.headers
    initialize_body = json.loads(initialize.content)
    assert initialize_body["params"]["protocolVersion"] == LATEST_HANDSHAKE_VERSION

    assert list_tools.headers["mcp-protocol-version"] == LATEST_HANDSHAKE_VERSION
    assert "mcp-method" not in list_tools.headers
    assert list_tools.headers["mcp-session-id"] != "frontend-session"

    for request in (discover, initialize, list_tools):
        assert request.headers["authorization"] == "Bearer frontend-token"
        assert request.headers["x-proxy-custom"] == "preserved"
        assert "mcp-name" not in request.headers
        assert "mcp-param-tenant" not in request.headers
        assert request.headers.get("mcp-session-id") != "frontend-session"
        assert "last-event-id" not in request.headers
