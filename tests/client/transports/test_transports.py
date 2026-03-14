import ssl
from ssl import VerifyMode
from typing import cast

import httpx
import pytest
from mcp.shared._httpx_utils import McpHttpClientFactory

from fastmcp import Client
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports import SSETransport, StreamableHttpTransport


async def test_oauth_uses_same_client_as_transport_streamable_http():
    transport = StreamableHttpTransport(
        "https://some.fake.url/",
        httpx_client_factory=lambda *args, **kwargs: httpx.AsyncClient(
            verify=False, *args, **kwargs
        ),
        auth="oauth",
    )

    assert isinstance(transport.auth, OAuth)
    async with transport.auth.httpx_client_factory() as httpx_client:
        assert httpx_client._transport is not None
        assert (
            httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
            == VerifyMode.CERT_NONE
        )


async def test_oauth_uses_same_client_as_transport_sse():
    transport = SSETransport(
        "https://some.fake.url/",
        httpx_client_factory=lambda *args, **kwargs: httpx.AsyncClient(
            verify=False, *args, **kwargs
        ),
        auth="oauth",
    )

    assert isinstance(transport.auth, OAuth)
    async with transport.auth.httpx_client_factory() as httpx_client:
        assert httpx_client._transport is not None
        assert (
            httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
            == VerifyMode.CERT_NONE
        )


class TestSSLVerify:
    def test_streamable_http_transport_stores_verify_false(self):
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            verify=False,
        )
        assert transport.verify is False

    def test_streamable_http_transport_stores_verify_ssl_context(self):
        ctx = ssl.create_default_context()
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            verify=ctx,
        )
        assert transport.verify is ctx

    def test_streamable_http_transport_stores_verify_cert_path(self):
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            verify="/path/to/cert.pem",
        )
        assert transport.verify == "/path/to/cert.pem"

    def test_streamable_http_transport_verify_default_is_none(self):
        transport = StreamableHttpTransport("https://example.com/mcp")
        assert transport.verify is None

    def test_sse_transport_stores_verify_false(self):
        transport = SSETransport(
            "https://example.com/sse",
            verify=False,
        )
        assert transport.verify is False

    def test_sse_transport_stores_verify_ssl_context(self):
        ctx = ssl.create_default_context()
        transport = SSETransport(
            "https://example.com/sse",
            verify=ctx,
        )
        assert transport.verify is ctx

    def test_sse_transport_verify_default_is_none(self):
        transport = SSETransport("https://example.com/sse")
        assert transport.verify is None

    def test_client_passes_verify_to_streamable_http_transport(self):
        client = Client("https://example.com/mcp", verify=False)
        assert isinstance(client.transport, StreamableHttpTransport)
        assert client.transport.verify is False

    def test_client_passes_verify_ssl_context_to_transport(self):
        ctx = ssl.create_default_context()
        client = Client("https://example.com/mcp", verify=ctx)
        assert isinstance(client.transport, StreamableHttpTransport)
        assert client.transport.verify is ctx

    def test_client_passes_verify_cert_path_to_transport(self):
        client = Client(
            "https://example.com/mcp",
            verify="/path/to/cert.pem",
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert client.transport.verify == "/path/to/cert.pem"

    def test_client_verify_none_leaves_transport_default(self):
        client = Client("https://example.com/mcp")
        assert isinstance(client.transport, StreamableHttpTransport)
        assert client.transport.verify is None

    def test_client_verify_raises_for_non_http_transport(self):
        from fastmcp import FastMCP

        server = FastMCP("test")
        with pytest.raises(
            ValueError,
            match="only supported for HTTP transports",
        ):
            Client(server, verify=False)

    def test_client_passes_verify_to_sse_transport(self):
        client = Client("https://example.com/sse", verify=False)
        assert isinstance(client.transport, SSETransport)
        assert client.transport.verify is False

    async def test_streamable_http_verify_propagates_to_oauth(self):
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            verify=False,
            auth="oauth",
        )
        assert isinstance(transport.auth, OAuth)
        async with transport.auth.httpx_client_factory() as httpx_client:
            assert (
                httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
                == VerifyMode.CERT_NONE
            )

    async def test_sse_verify_propagates_to_oauth(self):
        transport = SSETransport(
            "https://example.com/sse",
            verify=False,
            auth="oauth",
        )
        assert isinstance(transport.auth, OAuth)
        async with transport.auth.httpx_client_factory() as httpx_client:
            assert (
                httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
                == VerifyMode.CERT_NONE
            )

    async def test_client_verify_propagates_to_oauth(self):
        client = Client(
            "https://example.com/mcp",
            verify=False,
            auth="oauth",
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, OAuth)
        async with client.transport.auth.httpx_client_factory() as httpx_client:
            assert (
                httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
                == VerifyMode.CERT_NONE
            )

    async def test_verify_propagates_to_preconstructed_oauth_instance(self):
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            verify=False,
            auth=OAuth(),
        )
        assert isinstance(transport.auth, OAuth)
        async with transport.auth.httpx_client_factory() as httpx_client:
            assert (
                httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
                == VerifyMode.CERT_NONE
            )

    async def test_client_verify_resyncs_existing_oauth_on_transport(self):
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            auth="oauth",
        )
        assert isinstance(transport.auth, OAuth)
        # OAuth was created without verify — factory should be default
        async with transport.auth.httpx_client_factory() as httpx_client:
            assert (
                httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
                != VerifyMode.CERT_NONE
            )

        # Now wrap in Client with verify=False — should resync OAuth
        client = Client(transport, verify=False)
        assert isinstance(client.transport.auth, OAuth)
        async with client.transport.auth.httpx_client_factory() as httpx_client:
            assert (
                httpx_client._transport._pool._ssl_context.verify_mode
                == VerifyMode.CERT_NONE
            )

    async def test_client_verify_overrides_transport_verify_in_oauth(self):
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            verify=False,
            auth="oauth",
        )
        assert isinstance(transport.auth, OAuth)
        # OAuth should initially have verify=False
        async with transport.auth.httpx_client_factory() as httpx_client:
            assert (
                httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
                == VerifyMode.CERT_NONE
            )

        # Client overrides verify to True — OAuth should update
        client = Client(transport, verify=True)
        assert isinstance(client.transport.auth, OAuth)
        async with client.transport.auth.httpx_client_factory() as httpx_client:
            assert (
                httpx_client._transport._pool._ssl_context.verify_mode
                != VerifyMode.CERT_NONE
            )

    async def test_oauth_custom_factory_preserved_with_verify(self):
        custom_factory = cast(
            McpHttpClientFactory,
            lambda **kwargs: httpx.AsyncClient(verify=False, **kwargs),
        )
        auth = OAuth(httpx_client_factory=custom_factory)
        transport = StreamableHttpTransport(
            "https://example.com/mcp",
            verify=True,
            auth=auth,
        )
        assert isinstance(transport.auth, OAuth)
        assert transport.auth.httpx_client_factory is custom_factory

    def test_warns_when_both_factory_and_verify_provided_streamable(self):
        factory = cast(McpHttpClientFactory, httpx.AsyncClient)
        with pytest.warns(UserWarning, match="httpx_client_factory.*takes precedence"):
            StreamableHttpTransport(
                "https://example.com/mcp",
                httpx_client_factory=factory,
                verify=False,
            )

    def test_warns_when_both_factory_and_verify_provided_sse(self):
        factory = cast(McpHttpClientFactory, httpx.AsyncClient)
        with pytest.warns(UserWarning, match="httpx_client_factory.*takes precedence"):
            SSETransport(
                "https://example.com/sse",
                httpx_client_factory=factory,
                verify=False,
            )
