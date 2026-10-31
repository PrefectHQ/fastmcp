"""OAuth credentials must belong to the server config, not to one connection.

A multi-server `MCPConfig` proxy rebuilds every backend transport for each
request, while a single-server config builds its transport once. Resolving
`auth="oauth"` inside the transport gave each rebuild a fresh `OAuth` with its
own in-memory token store, so a proxy in front of an OAuth backend sent the user
through the browser flow on every request. Single-server configs shared one
`OAuth` through the client copy and logged in once.
"""

from typing import Any

import pytest

import fastmcp.client.auth.oauth as oauth_module
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.group import ClientGroup
from fastmcp.client.transports import ClientTransport, StreamableHttpTransport
from fastmcp.mcp_config import MCPConfig, MCPServerTypes, RemoteMCPServer
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.server import FastMCP
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


def _oauth_config() -> MCPConfig:
    return MCPConfig.from_dict(
        {
            "mcpServers": {
                "plain": {"url": "http://127.0.0.1:1/mcp"},
                "secure": {"url": "http://127.0.0.1:2/mcp", "auth": "oauth"},
            }
        }
    )


def _http_auth(server: MCPServerTypes) -> Any:
    """Build the server's transport and return the auth it resolved to."""
    transport: ClientTransport = server.to_transport()
    assert isinstance(transport, StreamableHttpTransport)
    return transport.auth


def test_oauth_auth_is_shared_across_transport_rebuilds():
    server = _oauth_config().mcpServers["secure"]

    first = _http_auth(server)
    second = _http_auth(server)

    assert isinstance(first, OAuth)
    assert first is second


def test_oauth_token_store_survives_rebuilds():
    server = _oauth_config().mcpServers["secure"]

    first = _http_auth(server)
    assert isinstance(first, OAuth)
    sentinel = object()
    first.__dict__["_test_token_store"] = sentinel

    second = _http_auth(server)
    assert isinstance(second, OAuth)
    assert second.__dict__["_test_token_store"] is sentinel


def test_oauth_client_is_rebuilt_when_the_url_changes():
    server = _oauth_config().mcpServers["secure"]
    assert isinstance(server, RemoteMCPServer)

    first = _http_auth(server)
    server.url = "http://127.0.0.1:3/mcp"
    second = _http_auth(server)

    assert isinstance(first, OAuth)
    assert isinstance(second, OAuth)
    assert first is not second


def test_non_oauth_auth_is_untouched():
    server = _oauth_config().mcpServers["plain"]

    assert _http_auth(server) is None
    assert _http_auth(server) is None


def test_bearer_auth_strings_still_resolve_per_transport():
    server = MCPConfig.from_dict(
        {
            "mcpServers": {
                "secure": {"url": "http://127.0.0.1:2/mcp", "auth": "token"},
            }
        }
    ).mcpServers["secure"]

    first = _http_auth(server)
    second = _http_auth(server)

    assert first is not None
    assert second is not None
    assert first is not second


class _CountingOAuthProvider(InMemoryOAuthProvider):
    """An in-memory provider that counts completed code exchanges.

    One exchange is one trip through the browser flow, so the counter is the
    number of times a user would have been asked to authorise.
    """

    def __init__(self, base_url: str):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["read", "write"]
            ),
        )
        self.authorizations = 0

    async def exchange_authorization_code(self, client, authorization_code):
        self.authorizations += 1
        return await super().exchange_authorization_code(client, authorization_code)


@pytest.fixture(params=["http", "sse"])
async def oauth_backend(request: pytest.FixtureRequest):
    """A real OAuth-protected server on a real port, spoken to over `request.param`."""
    transport = request.param
    port = find_available_port()
    provider = _CountingOAuthProvider(f"http://127.0.0.1:{port}")
    server = FastMCP("OAuthBackend", auth=provider)

    @server.tool
    def add(a: int, b: int) -> int:
        return a + b

    async with run_server_async(server, port=port, transport=transport) as url:
        yield url, provider, transport


async def test_one_authorization_per_configured_server(oauth_backend, monkeypatch):
    """Two proxy rebuilds must not send the user through the flow twice.

    This is the behaviour the fix exists for: `auth="oauth"` is resolved inside
    the transport, so a proxy that rebuilds transports per request used to mint
    a new `OAuth` with an empty token store and re-authorise every time. The
    exchange count is the evidence — the token-store tests above only show the
    two transports share an object.
    """
    url, provider, transport = oauth_backend
    # `auth="oauth"` builds the client internally; make that build headless so
    # the flow is real and only the browser hop is replaced.
    monkeypatch.setattr(oauth_module, "OAuth", HeadlessOAuth)

    config = MCPConfig.from_dict(
        {
            "mcpServers": {
                "secure": {"url": url, "auth": "oauth", "transport": transport},
            }
        }
    )

    for _ in range(2):
        async with ClientGroup.from_config(config) as group:
            tools = await group.list_tools()
            assert [tool.name for tool in tools] == ["secure_add"]

    assert provider.authorizations == 1
