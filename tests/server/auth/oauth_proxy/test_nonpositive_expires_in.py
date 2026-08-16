import time

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationCode, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import ClientCode


@pytest.mark.parametrize("expires_in", [0, -5])
async def test_exchange_rejects_nonpositive_expires_in(jwt_verifier, expires_in):
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

    redirect_uri = "http://localhost/callback"
    client = OAuthClientInformationFull(
        client_id="mcp-client",
        client_secret="mcp-secret",
        redirect_uris=[AnyUrl(redirect_uri)],
    )
    await proxy.register_client(client)

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
            idp_tokens={"access_token": "upstream-token", "expires_in": expires_in},
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

    with pytest.raises(TokenError):
        await proxy.exchange_authorization_code(client, authorization_code)

    # Validation happens before the one-time client code is consumed.
    assert await proxy._code_store.get(key=code) is not None
