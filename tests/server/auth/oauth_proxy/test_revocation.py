"""Revocation ownership must not replace the identity used for authentication."""

import time
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AccessToken
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette

from fastmcp.server.auth.identity_assertion import IdentityAssertion
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import (
    JTIMapping,
    RefreshTokenMetadata,
    UpstreamTokenSet,
    _hash_token,
)
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from tests.utilities.httpx2_mock import HTTPXMock


@pytest.fixture(params=["oauth", "oidc"])
async def proxy(jwt_verifier, request: pytest.FixtureRequest) -> OAuthProxy:
    if request.param == "oidc":
        configuration = OIDCConfiguration(
            issuer="https://idp.example.com",
            authorization_endpoint="https://idp.example.com/authorize",
            token_endpoint="https://idp.example.com/token",
            revocation_endpoint="https://idp.example.com/revoke",
            jwks_uri="https://idp.example.com/jwks",
            response_types_supported=["code"],
            subject_types_supported=["public"],
            id_token_signing_alg_values_supported=["RS256"],
        )
        with patch.object(
            OIDCConfiguration, "get_oidc_configuration", return_value=configuration
        ):
            proxy = OIDCProxy(
                config_url="https://idp.example.com/.well-known/openid-configuration",
                client_id="upstream-app",
                client_secret="test-secret",
                token_verifier=jwt_verifier,
                verify_id_token=True,
                base_url="https://proxy.example.com",
                jwt_signing_key="test-signing-key",
                client_storage=MemoryStore(),
            )
    else:
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_revocation_endpoint="https://idp.example.com/revoke",
            upstream_client_id="upstream-app",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-signing-key",
            client_storage=MemoryStore(),
        )
    proxy.set_mcp_path("/mcp")
    for client_id in ("dcr-client", "other-client", "alice"):
        await proxy.register_client(
            OAuthClientInformationFull(
                client_id=client_id,
                redirect_uris=[AnyUrl("https://client.example.com/callback")],
                token_endpoint_auth_method="none",
            )
        )
    return proxy


@pytest.fixture
async def issue_token(
    proxy: OAuthProxy, jwt_verifier
) -> Callable[[str], Awaitable[str]]:
    validated_tokens: dict[str, AccessToken] = {}
    jwt_verifier.verify_token = AsyncMock(side_effect=validated_tokens.get)

    async def issue(user: str) -> str:
        now = time.time()
        upstream_token = f"upstream-{user}"
        verification_token = (
            f"id-{user}" if isinstance(proxy, OIDCProxy) else upstream_token
        )
        validated_tokens[verification_token] = AccessToken(
            token=verification_token,
            client_id=user,
            scopes=["read"],
            expires_at=int(now + 3600),
            subject=user,
            claims={"sub": user},
        )
        await proxy._upstream_token_store.put(
            key=user,
            value=UpstreamTokenSet(
                upstream_token_id=user,
                access_token=upstream_token,
                refresh_token=None,
                refresh_token_expires_at=None,
                expires_at=now + 3600,
                token_type="Bearer",
                scope="read",
                client_id="dcr-client",
                created_at=now,
                raw_token_data={"id_token": verification_token},
            ),
            ttl=3600,
        )
        await proxy._jti_mapping_store.put(
            key=user,
            value=JTIMapping(jti=user, upstream_token_id=user, created_at=now),
            ttl=3600,
        )
        return proxy.jwt_issuer.issue_access_token(
            client_id="dcr-client", scopes=["read"], jti=user
        )

    return issue


async def test_authentication_preserves_two_users_with_one_dcr_client(
    proxy: OAuthProxy, issue_token: Callable[[str], Awaitable[str]]
) -> None:
    """The bearer verifier must preserve user separation with revocation enabled."""
    for user in ("alice", "bob"):
        token = await issue_token(user)
        for load in (proxy.verify_token, proxy.load_access_token):
            verified = await load(token)
            assert verified is not None
            assert verified.client_id == user
            assert verified.subject == user
            assert verified.claims == {"sub": user}


@pytest.mark.parametrize("hint", [None, "access_token", "refresh_token"])
async def test_owner_revokes_upstream_access_token(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
    hint: str | None,
) -> None:
    token = await issue_token("alice")
    before = await proxy.verify_token(token)
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    data = {"token": token, "client_id": "dcr-client", "client_secret": ""}
    if hint:
        data["token_type_hint"] = hint
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post("/revoke", data=data)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    request = httpx_mock.get_request()
    assert request is not None
    assert parse_qs(request.content.decode()) == {"token": ["upstream-alice"]}
    # Verifiers may cache their result; a revocation-only copy must not mutate it.
    assert before is not None
    assert before.client_id == "alice"
    after = await proxy.verify_token(token)
    assert after is not None
    assert after.client_id == "alice"


@pytest.mark.parametrize("client_id", ["other-client", "alice"])
async def test_non_owner_cannot_revoke_access_token(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
    client_id: str,
) -> None:
    token = await issue_token("alice")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": token, "client_id": client_id, "client_secret": ""},
        )
    assert response.status_code == 200
    assert httpx_mock.get_requests() == []


async def test_invalid_access_token_is_not_revoked(
    proxy: OAuthProxy, jwt_verifier, httpx_mock: HTTPXMock
) -> None:
    jwt_verifier.verify_token = AsyncMock()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={
                "token": "invalid-jwt",
                "client_id": "dcr-client",
                "client_secret": "",
            },
        )
    assert response.status_code == 200
    assert httpx_mock.get_requests() == []
    jwt_verifier.verify_token.assert_not_awaited()


async def test_revocation_keeps_body_limit_and_cors(
    proxy: OAuthProxy, jwt_verifier, httpx_mock: HTTPXMock
) -> None:
    jwt_verifier.verify_token = AsyncMock()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        preflight = await client.options(
            "/revoke",
            headers={
                "origin": "https://client.example.com",
                "access-control-request-method": "POST",
            },
        )
        response = await client.post(
            "/revoke",
            content=b"token=oversized",
            headers={
                "origin": "https://client.example.com",
                "content-type": "application/x-www-form-urlencoded",
                "content-length": str(DEFAULT_MAX_REQUEST_BODY_SIZE + 1),
            },
        )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "*"
    assert httpx_mock.get_requests() == []
    jwt_verifier.verify_token.assert_not_awaited()


@pytest.mark.parametrize("hint", [None, "access_token", "refresh_token"])
async def test_owner_revokes_upstream_refresh_token(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
    hint: str | None,
) -> None:
    await issue_token("alice")
    upstream = await proxy._upstream_token_store.get(key="alice")
    assert upstream is not None
    await proxy._upstream_token_store.put(
        key="alice",
        value=upstream.model_copy(update={"refresh_token": "upstream-refresh"}),
        ttl=3600,
    )
    token = proxy.jwt_issuer.issue_refresh_token(
        client_id="dcr-client", scopes=["read"], jti="alice", expires_in=3600
    )
    await proxy._refresh_token_store.put(
        key=_hash_token(token),
        value=RefreshTokenMetadata(
            client_id="dcr-client",
            scopes=["read"],
            expires_at=int(time.time()) + 3600,
            created_at=time.time(),
        ),
        ttl=3600,
    )
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    data = {"token": token, "client_id": "dcr-client", "client_secret": ""}
    if hint:
        data["token_type_hint"] = hint
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post("/revoke", data=data)
    assert response.status_code == 200
    request = httpx_mock.get_request()
    assert request is not None
    assert parse_qs(request.content.decode()) == {"token": ["upstream-refresh"]}
    assert await proxy._refresh_token_store.get(key=_hash_token(token)) is None


async def test_id_jag_revocation_remains_local(
    proxy: OAuthProxy, httpx_mock: HTTPXMock
) -> None:
    proxy._identity_assertion = IdentityAssertion(
        trusted_issuers=["https://idp.example.com"]
    )
    token = proxy.jwt_issuer.issue_access_token(
        client_id="dcr-client",
        scopes=["read"],
        jti="id-jag",
        subject="alice",
        extra_claims={"fastmcp_grant": "id_jag"},
    )
    assert await proxy.verify_token(token) is not None
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": token, "client_id": "dcr-client", "client_secret": ""},
        )
    assert response.status_code == 200
    assert await proxy.verify_token(token) is None
    assert httpx_mock.get_requests() == []
