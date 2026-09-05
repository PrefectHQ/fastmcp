"""Regression coverage for operational GitHub token verification failures."""

import time
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore

from fastmcp.server.auth import TokenVerificationError, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet
from fastmcp.server.auth.providers.github import GitHubTokenVerifier


def _response(status_code: int, text: str = "simulated") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    return response


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
            scope="user",
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
    fastmcp_token = proxy.jwt_issuer.issue_access_token(
        client_id="mcp-client",
        scopes=["user"],
        jti=jti,
        expires_in=3600,
    )

    with pytest.raises(TokenVerificationError, match="upstream verifier unavailable"):
        await proxy.load_access_token(fastmcp_token)
