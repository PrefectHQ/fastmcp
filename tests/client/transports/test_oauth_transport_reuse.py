"""OAuth credentials must belong to the server config, not to one connection.

A multi-server `MCPConfig` proxy rebuilds every backend transport for each
request, while a single-server config builds its transport once. Resolving
`auth="oauth"` inside the transport gave each rebuild a fresh `OAuth` with its
own in-memory token store, so a proxy in front of an OAuth backend sent the user
through the browser flow on every request. Single-server configs shared one
`OAuth` through the client copy and logged in once.
"""

from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.mcp_config import MCPConfig


def _oauth_config() -> MCPConfig:
    return MCPConfig.from_dict(
        {
            "mcpServers": {
                "plain": {"url": "http://127.0.0.1:1/mcp"},
                "secure": {"url": "http://127.0.0.1:2/mcp", "auth": "oauth"},
            }
        }
    )


def test_oauth_auth_is_shared_across_transport_rebuilds():
    config = _oauth_config()

    first = config.mcpServers["secure"].to_transport()
    second = config.mcpServers["secure"].to_transport()

    assert isinstance(first, StreamableHttpTransport)
    assert isinstance(first.auth, OAuth)
    assert first.auth is second.auth


def test_oauth_token_store_survives_rebuilds():
    config = _oauth_config()

    first = config.mcpServers["secure"].to_transport()
    assert isinstance(first.auth, OAuth)
    sentinel = object()
    first.auth.__dict__["_test_token_store"] = sentinel

    second = config.mcpServers["secure"].to_transport()
    assert isinstance(second.auth, OAuth)
    assert second.auth.__dict__["_test_token_store"] is sentinel


def test_oauth_client_is_rebuilt_when_the_url_changes():
    config = _oauth_config()
    server = config.mcpServers["secure"]

    first = server.to_transport()
    server.url = "http://127.0.0.1:3/mcp"
    second = server.to_transport()

    assert isinstance(first.auth, OAuth)
    assert isinstance(second.auth, OAuth)
    assert first.auth is not second.auth


def test_non_oauth_auth_is_untouched():
    config = _oauth_config()

    first = config.mcpServers["plain"].to_transport()
    second = config.mcpServers["plain"].to_transport()

    assert first.auth is None
    assert second.auth is None


def test_bearer_auth_strings_still_resolve_per_transport():
    config = MCPConfig.from_dict(
        {
            "mcpServers": {
                "secure": {"url": "http://127.0.0.1:2/mcp", "auth": "token"},
            }
        }
    )

    first = config.mcpServers["secure"].to_transport()
    second = config.mcpServers["secure"].to_transport()

    assert first.auth is not None
    assert second.auth is not None
    assert first.auth is not second.auth
