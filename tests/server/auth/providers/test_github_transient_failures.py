"""Regression coverage for operational GitHub token verification failures."""

import time
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore

from fastmcp.server.auth import TokenVerificationError, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet
from fastmcp.server.auth.providers.github import GitHubProvider, GitHubTokenVerifier


def _response(
    status_code: int,
    text: str = "simulated",
    *,
    headers: dict[str, str] | None = None,
    json_data: dict | None = None,
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = headers or {}
    response.json.return_value = json_data or {}
    return response


def _github_user_response() -> MagicMock:
    return _response(
        200,
        json_data={
            "id": 12345,
            "login": "testuser",
            "name": "Test User",
            "email": "test@example.com",
            "avatar_url": "https://github.com/testuser.png",
        },
    )


async def _store_proxy_access_token(
    proxy: OAuthProxy,
    *,
    upstream_scope: str,
) -> str:
    proxy.set_mcp_path("/mcp")
    now = time.time()
    upstream_token_id = "upstream-token-id"
    jti = "access-jti"
    await proxy._upstream_token_store.put(
        key=upstream_token_id,
        value=UpstreamTokenSet(
            upstream_token_id=upstream_token_id,
            access_token="upstream-access-token",
            refresh_token=None,
            refresh_token_expires_at=None,
            expires_at=now + 3600,
            token_type="Bearer",
            scope=upstream_scope,
            client_id="mcp-client",
            created_at=now,
            raw_token_data={"access_token": "upstream-access-token"},
        ),
        ttl=3600,
    )
    await proxy._jti_mapping_store.put(
        key=jti,
        value=JTIMapping(
            jti=jti,
            upstream_token_id=upstream_token_id,
            created_at=now,
        ),
        ttl=3600,
    )
    return proxy.jwt_issuer.issue_access_token(
        client_id="mcp-client",
        scopes=upstream_scope.split(),
        jti=jti,
        expires_in=3600,
    )


async def test_github_401_is_still_an_invalid_token():
    client = AsyncMock()
    client.get.return_value = _response(401, "Bad credentials")
    verifier = GitHubTokenVerifier(http_client=client)

    assert await verifier.verify_token("bad-token") is None


@pytest.mark.parametrize("status_code", [403, 429, 500, 503])
async def test_github_operational_http_failure_raises_typed_error(status_code):
    client = AsyncMock()
    client.get.return_value = _response(status_code)
    verifier = GitHubTokenVerifier(http_client=client)

    with pytest.raises(TokenVerificationError, match=str(status_code)):
        await verifier.verify_token("still-valid-token")


async def test_github_transport_failure_raises_typed_error():
    client = AsyncMock()
    client.get.side_effect = httpx2.ConnectError(
        "simulated transport failure",
        request=httpx2.Request("GET", "https://api.github.com/user"),
    )
    verifier = GitHubTokenVerifier(http_client=client)

    with pytest.raises(TokenVerificationError, match="transport"):
        await verifier.verify_token("still-valid-token")


@pytest.mark.parametrize("status_code", [403, 429, 500, 503])
async def test_scope_operational_http_failure_raises_typed_error(status_code):
    client = AsyncMock()
    client.get.side_effect = [_github_user_response(), _response(status_code)]
    verifier = GitHubTokenVerifier(required_scopes=["user"], http_client=client)

    with pytest.raises(TokenVerificationError, match=str(status_code)):
        await verifier.verify_token("still-valid-token")


async def test_scope_transport_failure_raises_typed_error():
    client = AsyncMock()
    client.get.side_effect = [
        _github_user_response(),
        httpx2.ConnectError(
            "simulated scope transport failure",
            request=httpx2.Request("GET", "https://api.github.com/user/repos"),
        ),
    ]
    verifier = GitHubTokenVerifier(required_scopes=["user"], http_client=client)

    with pytest.raises(TokenVerificationError, match="transport"):
        await verifier.verify_token("still-valid-token")


async def test_scope_401_is_still_an_invalid_token():
    client = AsyncMock()
    client.get.side_effect = [
        _github_user_response(),
        _response(401, "Bad credentials"),
    ]
    verifier = GitHubTokenVerifier(required_scopes=["user"], http_client=client)

    assert await verifier.verify_token("revoked-token") is None


async def test_missing_scope_header_does_not_synthesize_user_scope():
    client = AsyncMock()
    client.get.side_effect = [_github_user_response(), _response(200, headers={})]
    verifier = GitHubTokenVerifier(required_scopes=["user"], http_client=client)

    assert await verifier.verify_token("standalone-token") is None


async def test_successful_scope_verification_is_cached():
    client = AsyncMock()
    client.get.side_effect = [
        _github_user_response(),
        _response(200, headers={"x-oauth-scopes": "user"}),
    ]
    verifier = GitHubTokenVerifier(
        required_scopes=["user"],
        cache_ttl_seconds=300,
        http_client=client,
    )

    first = await verifier.verify_token("valid-token")
    second = await verifier.verify_token("valid-token")

    assert first is not None
    assert first.scopes == ["user"]
    assert second is not None
    assert second.scopes == ["user"]
    assert second.client_id == first.client_id
    assert second is not first
    assert client.get.call_count == 2


class UnavailableVerifier(TokenVerifier):
    async def verify_token(self, token: str):
        raise TokenVerificationError("upstream verifier unavailable")


async def test_oauth_proxy_propagates_operational_verification_error():
    proxy = OAuthProxy(
        upstream_authorization_endpoint="https://idp.example.com/authorize",
        upstream_token_endpoint="https://idp.example.com/token",
        upstream_client_id="upstream-client",
        upstream_client_secret="upstream-secret",
        token_verifier=UnavailableVerifier(),
        base_url="https://proxy.example.com",
        jwt_signing_key="test-signing-key",
        client_storage=MemoryStore(),
    )
    fastmcp_token = await _store_proxy_access_token(proxy, upstream_scope="user")

    with pytest.raises(TokenVerificationError, match="upstream verifier unavailable"):
        await proxy.load_access_token(fastmcp_token)


async def test_github_provider_scope_outage_propagates_operational_error():
    client = AsyncMock()
    client.get.side_effect = [_github_user_response(), _response(503)]
    provider = GitHubProvider(
        client_id="github-client",
        client_secret="github-secret",
        base_url="https://proxy.example.com",
        required_scopes=["repo"],
        http_client=client,
        jwt_signing_key="test-signing-key",
        client_storage=MemoryStore(),
    )
    fastmcp_token = await _store_proxy_access_token(provider, upstream_scope="repo")

    with pytest.raises(TokenVerificationError, match="503"):
        await provider.load_access_token(fastmcp_token)


async def test_github_provider_scope_401_stays_invalid():
    client = AsyncMock()
    client.get.side_effect = [
        _github_user_response(),
        _response(401, "Bad credentials"),
    ]
    provider = GitHubProvider(
        client_id="github-client",
        client_secret="github-secret",
        base_url="https://proxy.example.com",
        required_scopes=["repo"],
        http_client=client,
        jwt_signing_key="test-signing-key",
        client_storage=MemoryStore(),
    )
    fastmcp_token = await _store_proxy_access_token(provider, upstream_scope="repo")

    assert await provider.load_access_token(fastmcp_token) is None
