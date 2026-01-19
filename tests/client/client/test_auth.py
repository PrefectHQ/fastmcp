"""Client authentication tests."""

import pytest
from mcp.client.auth import OAuthClientProvider

from fastmcp.client import Client
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)


class TestAuth:
    def test_default_auth_is_none(self):
        client = Client(transport=StreamableHttpTransport("http://localhost:8000"))
        assert client.transport.auth is None

    def test_stdio_doesnt_support_auth(self):
        with pytest.raises(ValueError, match="This transport does not support auth"):
            Client(transport=StdioTransport("echo", ["hello"]), auth="oauth")

    def test_oauth_literal_sets_up_oauth_shttp(self):
        client = Client(
            transport=StreamableHttpTransport("http://localhost:8000"), auth="oauth"
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, OAuthClientProvider)

    def test_oauth_literal_pass_direct_to_transport(self):
        client = Client(
            transport=StreamableHttpTransport("http://localhost:8000", auth="oauth"),
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, OAuthClientProvider)

    def test_oauth_literal_sets_up_oauth_sse(self):
        client = Client(transport=SSETransport("http://localhost:8000"), auth="oauth")
        assert isinstance(client.transport, SSETransport)
        assert isinstance(client.transport.auth, OAuthClientProvider)

    def test_oauth_literal_pass_direct_to_transport_sse(self):
        client = Client(transport=SSETransport("http://localhost:8000", auth="oauth"))
        assert isinstance(client.transport, SSETransport)
        assert isinstance(client.transport.auth, OAuthClientProvider)

    def test_auth_string_sets_up_bearer_auth_shttp(self):
        client = Client(
            transport=StreamableHttpTransport("http://localhost:8000"),
            auth="test_token",
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, BearerAuth)
        assert client.transport.auth.token.get_secret_value() == "test_token"

    def test_auth_string_pass_direct_to_transport_shttp(self):
        client = Client(
            transport=StreamableHttpTransport(
                "http://localhost:8000", auth="test_token"
            ),
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, BearerAuth)
        assert client.transport.auth.token.get_secret_value() == "test_token"

    def test_auth_string_sets_up_bearer_auth_sse(self):
        client = Client(
            transport=SSETransport("http://localhost:8000"),
            auth="test_token",
        )
        assert isinstance(client.transport, SSETransport)
        assert isinstance(client.transport.auth, BearerAuth)
        assert client.transport.auth.token.get_secret_value() == "test_token"

    def test_auth_string_pass_direct_to_transport_sse(self):
        client = Client(
            transport=SSETransport("http://localhost:8000", auth="test_token"),
        )
        assert isinstance(client.transport, SSETransport)
        assert isinstance(client.transport.auth, BearerAuth)
        assert client.transport.auth.token.get_secret_value() == "test_token"
