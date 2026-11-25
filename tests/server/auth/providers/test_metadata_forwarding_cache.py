"""Tests for the cached forwarding of upstream authorization server metadata.

Providers that forward their identity provider's
``/.well-known/oauth-authorization-server`` document used to fetch it from the
upstream on every unauthenticated request. The route serves one cached copy per
provider instance and refreshes it at most once per interval, with a single
refresh in flight at a time.
"""

import asyncio

import httpx2
import pytest

from fastmcp import FastMCP
from fastmcp.server.auth.providers.workos import AuthKitProvider

AUTHKIT_DOMAIN = "https://test.authkit.app"
BASE_URL = "http://127.0.0.1:8000"
METADATA_ROUTE = "/.well-known/oauth-authorization-server"
METADATA = {
    "issuer": AUTHKIT_DOMAIN,
    "authorization_endpoint": f"{AUTHKIT_DOMAIN}/oauth2/authorize",
    "token_endpoint": f"{AUTHKIT_DOMAIN}/oauth2/token",
}


class CountingUpstream:
    """Stands in for ``httpx2.AsyncClient`` and counts the metadata fetches made."""

    calls: int = 0
    failing: bool = False

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str) -> httpx2.Response:
        type(self).calls += 1
        # Yield so that concurrent handlers interleave at the fetch.
        await asyncio.sleep(0)
        if type(self).failing:
            raise httpx2.ConnectError("upstream down")
        return httpx2.Response(200, json=METADATA, request=httpx2.Request("GET", url))


@pytest.fixture
def provider() -> AuthKitProvider:
    return AuthKitProvider(authkit_domain=AUTHKIT_DOMAIN, base_url=BASE_URL)


def _fresh_upstream(*, failing: bool) -> type[CountingUpstream]:
    """A CountingUpstream subclass with its own call counter."""

    class Upstream(CountingUpstream):
        pass

    Upstream.calls = 0
    Upstream.failing = failing
    return Upstream


def _client(provider: AuthKitProvider, real_client) -> httpx2.AsyncClient:
    app = FastMCP("test", auth=provider).http_app()
    return real_client(transport=httpx2.ASGITransport(app=app), base_url=BASE_URL)


class TestForwardedMetadataCache:
    async def test_burst_of_requests_fetches_upstream_once(self, provider):
        real_client = httpx2.AsyncClient
        with pytest.MonkeyPatch.context() as mp:
            counting = _fresh_upstream(failing=False)
            mp.setattr(httpx2, "AsyncClient", counting)
            async with _client(provider, real_client) as client:
                responses = await asyncio.gather(
                    *(client.get(METADATA_ROUTE) for _ in range(20))
                )
                later = await client.get(METADATA_ROUTE)

        assert [r.status_code for r in responses] == [200] * 20
        assert all(r.json() == METADATA for r in responses)
        assert later.json() == METADATA
        assert counting.calls == 1

    async def test_failed_refresh_serves_cached_document(self, provider):
        real_client = httpx2.AsyncClient
        provider.FORWARDED_METADATA_TTL_SECONDS = 0.0  # every request is a refresh
        with pytest.MonkeyPatch.context() as mp:
            counting = _fresh_upstream(failing=False)
            mp.setattr(httpx2, "AsyncClient", counting)
            async with _client(provider, real_client) as client:
                first = await client.get(METADATA_ROUTE)
                counting.failing = True
                second = await client.get(METADATA_ROUTE)
                third = await client.get(METADATA_ROUTE)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json() == METADATA
        assert third.status_code == 200
        # One successful fetch, one failed refresh, then the back-off interval.
        assert counting.calls == 2

    async def test_failure_without_cached_document_is_a_server_error(self, provider):
        real_client = httpx2.AsyncClient
        with pytest.MonkeyPatch.context() as mp:
            counting = _fresh_upstream(failing=True)
            mp.setattr(httpx2, "AsyncClient", counting)
            async with _client(provider, real_client) as client:
                first = await client.get(METADATA_ROUTE)
                second = await client.get(METADATA_ROUTE)

        for response in (first, second):
            assert response.status_code == 500
            body = response.json()
            assert body["error"] == "server_error"
            assert body["error_description"].startswith(
                "Failed to fetch AuthKit metadata: "
            )
        # The second request arrived inside the back-off interval.
        assert counting.calls == 1
