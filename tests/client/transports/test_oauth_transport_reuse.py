"""OAuth credentials must belong to the server config, not to one connection.

A multi-server `MCPConfig` proxy rebuilds every backend transport for each
request, while a single-server config builds its transport once. Resolving
`auth="oauth"` inside the transport gave each rebuild a fresh `OAuth` with its
own in-memory token store, so a proxy in front of an OAuth backend sent the user
through the browser flow on every request. Single-server configs shared one
`OAuth` through the client copy and logged in once.
"""

from typing import Any

from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports import ClientTransport, StreamableHttpTransport
from fastmcp.mcp_config import MCPConfig, MCPServerTypes, RemoteMCPServer


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
