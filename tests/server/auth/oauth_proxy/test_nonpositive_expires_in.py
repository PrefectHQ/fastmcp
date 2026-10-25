import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationCode, RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import (
    ClientCode,
    JTIMapping,
    UpstreamTokenSet,
)


def make_proxy(jwt_verifier):
    proxy = OAuthProxy(
        upstream_authorization_endpoint="https://idp.example.com/authorize",
        upstream_token_endpoint="https://idp.example.com/token",
        upstream_client_id="upstream-client",
        upstream_client_secret="upstream-secret",
        token_verifier=jwt_verifier,
        base_url="https://proxy.example.com",
        jwt_signing_key="test-signing-key",
        client_storage=MemoryStore(),
    )
    proxy.set_mcp_path("/mcp")
    return proxy


async def make_client(proxy):
    redirect_uri = "http://localhost/callback"
    client = OAuthClientInformationFull(
        client_id="mcp-client",
        client_secret="mcp-secret",
        redirect_uris=[AnyUrl(redirect_uri)],
    )
    await proxy.register_client(client)
    return client, redirect_uri


@pytest.mark.parametrize("expires_in", [0, -5])
async def test_exchange_rejects_nonpositive_expires_in(jwt_verifier, expires_in):
    proxy = make_proxy(jwt_verifier)
    client, redirect_uri = await make_client(proxy)

    now = time.time()
    code = f"test-code-{expires_in}"
    await proxy._code_store.put(
        key=code,
        value=ClientCode(
            code=code,
            client_id="mcp-client",
            redirect_uri=redirect_uri,
            code_challenge=None,
            code_challenge_method="S256",
            scopes=["read"],
            idp_tokens={
                "access_token": "upstream-token",
                "expires_in": expires_in,
            },
            expires_at=now + 300,
            created_at=now,
        ),
        ttl=300,
    )

    authorization_code = AuthorizationCode(
        code=code,
        client_id="mcp-client",
        redirect_uri=AnyUrl(redirect_uri),
        redirect_uri_provided_explicitly=True,
        scopes=["read"],
        expires_at=now + 300,
        code_challenge="",
    )

    with pytest.raises(TokenError) as exc_info:
        await proxy.exchange_authorization_code(client, authorization_code)

    assert exc_info.value.error == "invalid_grant"
    assert await proxy._code_store.get(key=code) is not None


@pytest.mark.parametrize("expires_in", [0, -5])
async def test_refresh_rejects_nonpositive_expires_in_before_state_mutation(
    jwt_verifier, expires_in
):
    proxy = make_proxy(jwt_verifier)
    client, _ = await make_client(proxy)

    now = time.time()
    upstream_token_id = "upstream-token-id"
    refresh_jti = "refresh-jti"
    upstream_token_set = UpstreamTokenSet(
        upstream_token_id=upstream_token_id,
        access_token="old-upstream-access",
        refresh_token="upstream-refresh",
        refresh_token_expires_at=now + 3600,
        expires_at=now + 60,
        token_type="Bearer",
        scope="read",
        client_id="mcp-client",
        created_at=now,
        raw_token_data={"access_token": "old-upstream-access"},
    )
    await proxy._upstream_token_store.put(
        key=upstream_token_id,
        value=upstream_token_set,
        ttl=3600,
    )
    await proxy._jti_mapping_store.put(
        key=refresh_jti,
        value=JTIMapping(
            jti=refresh_jti,
            upstream_token_id=upstream_token_id,
            created_at=now,
        ),
        ttl=3600,
    )
    refresh_jwt = proxy.jwt_issuer.issue_refresh_token(
        client_id="mcp-client",
        scopes=["read"],
        jti=refresh_jti,
        expires_in=3600,
    )

    oauth_client = Mock()
    oauth_client.refresh_token = AsyncMock(
        return_value={
            "access_token": "new-upstream-access",
            "expires_in": expires_in,
            "token_type": "Bearer",
        }
    )
    oauth_client.aclose = AsyncMock()

    with patch.object(
        proxy,
        "_create_upstream_oauth_client",
        return_value=oauth_client,
    ):
        with pytest.raises(TokenError) as exc_info:
            await proxy.exchange_refresh_token(
                client=client,
                refresh_token=RefreshToken(
                    token=refresh_jwt,
                    client_id="mcp-client",
                    scopes=["read"],
                    expires_at=int(now) + 3600,
                ),
                scopes=["read"],
            )

    assert exc_info.value.error == "invalid_grant"
    persisted = await proxy._upstream_token_store.get(key=upstream_token_id)
    assert persisted is not None
    assert persisted.access_token == "old-upstream-access"
    assert await proxy._jti_mapping_store.get(key=refresh_jti) is not None
