from ssl import VerifyMode

import httpx

from fastmcp.client.transports import SSETransport, StreamableHttpTransport


async def test_oauth_uses_same_client_as_transport_streamable_http():
    transport = StreamableHttpTransport(
        "https://some.fake.url/",
        httpx_client_factory=lambda *args, **kwargs: httpx.AsyncClient(
            verify=False, *args, **kwargs
        ),
        auth="oauth",
    )

    async with transport.auth.httpx_client_factory() as httpx_client:  # type: ignore[attr-defined]
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

    async with transport.auth.httpx_client_factory() as httpx_client:  # type: ignore[attr-defined]
        assert httpx_client._transport is not None
        assert (
            httpx_client._transport._pool._ssl_context.verify_mode  # type: ignore[attr-defined]
            == VerifyMode.CERT_NONE
        )
