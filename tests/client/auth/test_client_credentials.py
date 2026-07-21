"""Tests for machine-to-machine (M2M) client authentication.

These cover the ``client_credentials`` grant (client_id + client_secret) and the
RFC 7523 ``private_key_jwt`` variant. Rather than standing up a real
authorization server (the in-memory server does not implement the
client_credentials grant), the tests drive the provider's ``async_auth_flow``
directly with a mock responder that answers OAuth discovery and the token
endpoint, exactly as httpx would while running the auth flow.
"""

import base64
import warnings
from base64 import urlsafe_b64decode
from collections.abc import Callable
from contextlib import aclosing
from urllib.parse import urlparse

import httpx2
import jwt
import pytest
from mcp.client.auth import OAuthTokenError

from fastmcp.client import Client
from fastmcp.client.auth import (
    ClientCredentialsOAuthProvider,
    PrivateKeyJWTOAuthProvider,
    SignedJWTParameters,
    static_assertion_provider,
)
from fastmcp.client.transports import SSETransport, StreamableHttpTransport

SERVER_URL = "https://mcp.example.com/mcp"
AUTH_SERVER_URL = "https://auth.example.com"
# 32+ bytes so PyJWT does not warn about weak HMAC keys under -W error.
SIGNING_KEY = "unit-test-signing-key-padded-to-32b"


def make_m2m_responder(
    *,
    token_response: dict,
    token_status: int = 200,
    server_url: str = SERVER_URL,
    auth_server_url: str = AUTH_SERVER_URL,
) -> tuple[Callable[[httpx2.Request], httpx2.Response], dict[str, httpx2.Request]]:
    """Build a responder for the standard M2M discovery + token exchange flow.

    Returns the responder and a dict that captures the token request and the
    final (retried) resource request for assertions.
    """
    captured: dict[str, httpx2.Request] = {}

    def responder(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        path = urlparse(url).path
        header_keys = {key.lower() for key in request.headers}

        if url.startswith(server_url):
            if "authorization" in header_keys:
                captured["final_request"] = request
                return httpx2.Response(200, text="ok")
            return httpx2.Response(401, headers={"WWW-Authenticate": "Bearer"})

        if path.startswith("/.well-known/oauth-protected-resource"):
            return httpx2.Response(
                200,
                json={
                    "resource": server_url,
                    "authorization_servers": [auth_server_url],
                },
            )

        if path.startswith(
            "/.well-known/oauth-authorization-server"
        ) or path.startswith("/.well-known/openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "issuer": auth_server_url,
                    "authorization_endpoint": f"{auth_server_url}/authorize",
                    "token_endpoint": f"{auth_server_url}/token",
                    "response_types_supported": ["code"],
                },
            )

        if url == f"{auth_server_url}/token":
            captured["token_request"] = request
            return httpx2.Response(token_status, json=token_response)

        raise AssertionError(f"unexpected request: {request.method} {url}")

    return responder, captured


async def drive_auth_flow(
    provider: httpx2.Auth,
    responder: Callable[[httpx2.Request], httpx2.Response],
    *,
    server_url: str = SERVER_URL,
) -> list[httpx2.Request]:
    """Drive an httpx auth flow to completion, feeding each yield to responder."""
    requests: list[httpx2.Request] = []
    async with aclosing(
        provider.async_auth_flow(httpx2.Request("POST", server_url))
    ) as flow:
        sent: httpx2.Response | None = None
        while True:
            try:
                request = await flow.asend(sent)  # ty: ignore[invalid-argument-type]
            except StopAsyncIteration:
                break
            requests.append(request)
            sent = responder(request)
    return requests


def form_body(request: httpx2.Request) -> dict[str, str]:
    """Parse an x-www-form-urlencoded request body into a dict."""
    return dict(httpx2.QueryParams(request.content.decode()))


class TestClientCredentialsConstruction:
    """Constructor ergonomics and deferred binding."""

    def test_deferred_binding(self):
        provider = ClientCredentialsOAuthProvider(
            client_id="cid", client_secret="secret"
        )
        assert provider._bound is False

        provider._bind(f"{SERVER_URL}/")
        assert provider._bound is True
        # Trailing slash is normalized away.
        assert provider.context.server_url == SERVER_URL

    def test_binding_at_construction(self):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL, client_id="cid", client_secret="secret"
        )
        assert provider._bound is True
        assert provider.context.server_url == SERVER_URL

    def test_bind_is_idempotent(self):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL, client_id="cid", client_secret="secret"
        )
        provider._bind("https://other.example.com/mcp")
        assert provider.context.server_url == SERVER_URL

    @pytest.mark.parametrize(
        "scopes, expected",
        [
            (["read", "write"], "read write"),
            ("read write", "read write"),
            (None, None),
        ],
    )
    def test_scope_normalization(self, scopes, expected):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL, client_id="cid", client_secret="secret", scopes=scopes
        )
        assert provider.context.client_metadata.scope == expected

    def test_default_token_endpoint_auth_method(self):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL, client_id="cid", client_secret="secret"
        )
        assert (
            provider._fixed_client_info.token_endpoint_auth_method
            == "client_secret_basic"
        )

    def test_token_endpoint_auth_method_override(self):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL,
            client_id="cid",
            client_secret="secret",
            token_endpoint_auth_method="client_secret_post",
        )
        assert (
            provider._fixed_client_info.token_endpoint_auth_method
            == "client_secret_post"
        )

    def test_in_memory_storage_does_not_warn(self):
        """M2M re-acquires tokens cheaply, so no in-memory storage warning."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            ClientCredentialsOAuthProvider(
                SERVER_URL, client_id="cid", client_secret="secret"
            )

    async def test_unbound_provider_raises(self):
        provider = ClientCredentialsOAuthProvider(
            client_id="cid", client_secret="secret"
        )
        with pytest.raises(RuntimeError, match="has no server URL"):
            provider.async_auth_flow(httpx2.Request("POST", SERVER_URL))


class TestClientCredentialsFlow:
    """The provider discovers the token endpoint, acquires and attaches a token."""

    async def test_acquires_and_attaches_token(self):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL, client_id="cid", client_secret="secret"
        )
        responder, captured = make_m2m_responder(
            token_response={
                "access_token": "ACCESS123",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

        requests = await drive_auth_flow(provider, responder)

        # The token exchange used the client_credentials grant.
        token_body = form_body(captured["token_request"])
        assert token_body["grant_type"] == "client_credentials"

        # The retried request carries the acquired bearer token.
        assert requests[-1].headers["Authorization"] == "Bearer ACCESS123"
        assert captured["final_request"].headers["Authorization"] == "Bearer ACCESS123"

    async def test_client_secret_basic_uses_authorization_header(self):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL,
            client_id="cid",
            client_secret="secret",
            token_endpoint_auth_method="client_secret_basic",
        )
        responder, captured = make_m2m_responder(
            token_response={"access_token": "T", "token_type": "Bearer"}
        )

        await drive_auth_flow(provider, responder)

        token_request = captured["token_request"]
        expected = base64.b64encode(b"cid:secret").decode()
        assert token_request.headers["Authorization"] == f"Basic {expected}"
        # Credentials are not duplicated in the body.
        assert "client_secret" not in form_body(token_request)

    async def test_client_secret_post_uses_body(self):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL,
            client_id="cid",
            client_secret="secret",
            token_endpoint_auth_method="client_secret_post",
        )
        responder, captured = make_m2m_responder(
            token_response={"access_token": "T", "token_type": "Bearer"}
        )

        await drive_auth_flow(provider, responder)

        token_body = form_body(captured["token_request"])
        assert token_body["client_id"] == "cid"
        assert token_body["client_secret"] == "secret"
        assert "Authorization" not in captured["token_request"].headers

    async def test_token_error_surfaces(self):
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL, client_id="cid", client_secret="wrong"
        )
        responder, _ = make_m2m_responder(
            token_response={"error": "invalid_client"},
            token_status=401,
        )

        with pytest.raises(OAuthTokenError, match="Token exchange failed"):
            await drive_auth_flow(provider, responder)


class TestPrivateKeyJWTFlow:
    """private_key_jwt builds a client assertion and attaches the token."""

    async def test_signed_assertion_flow(self):
        jwt_params = SignedJWTParameters(
            issuer="cid",
            subject="cid",
            signing_key=SIGNING_KEY,
            signing_algorithm="HS256",
        )
        provider = PrivateKeyJWTOAuthProvider(
            SERVER_URL,
            client_id="cid",
            assertion_provider=jwt_params.create_assertion_provider(),
        )
        responder, captured = make_m2m_responder(
            token_response={
                "access_token": "JWT-ACCESS",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

        requests = await drive_auth_flow(provider, responder)

        token_body = form_body(captured["token_request"])
        assert token_body["grant_type"] == "client_credentials"
        assert (
            token_body["client_assertion_type"]
            == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        )

        # The assertion is a JWT whose audience is the authorization server issuer.
        claims = jwt.decode(
            token_body["client_assertion"], options={"verify_signature": False}
        )
        assert claims["iss"] == "cid"
        assert claims["sub"] == "cid"
        # RFC 7523bis: the assertion audience is the auth server's issuer.
        assert claims["aud"] == AUTH_SERVER_URL

        assert requests[-1].headers["Authorization"] == "Bearer JWT-ACCESS"

    async def test_static_assertion_flow(self):
        prebuilt = jwt.encode(
            {"iss": "cid", "sub": "cid", "aud": "anything"},
            SIGNING_KEY,
            algorithm="HS256",
        )
        provider = PrivateKeyJWTOAuthProvider(
            SERVER_URL,
            client_id="cid",
            assertion_provider=static_assertion_provider(prebuilt),
        )
        responder, captured = make_m2m_responder(
            token_response={"access_token": "S", "token_type": "Bearer"}
        )

        await drive_auth_flow(provider, responder)

        token_body = form_body(captured["token_request"])
        assert token_body["client_assertion"] == prebuilt

    async def test_unbound_provider_raises(self):
        provider = PrivateKeyJWTOAuthProvider(
            client_id="cid",
            assertion_provider=static_assertion_provider("token"),
        )
        with pytest.raises(RuntimeError, match="has no server URL"):
            provider.async_auth_flow(httpx2.Request("POST", SERVER_URL))


class TestTransportIntegration:
    """Providers slot into a transport's ``auth=`` and bind to the URL."""

    def test_streamable_http_transport_binds_client_credentials(self):
        provider = ClientCredentialsOAuthProvider(
            client_id="cid", client_secret="secret"
        )
        transport = StreamableHttpTransport(SERVER_URL, auth=provider)
        assert transport.auth is provider
        assert provider._bound is True
        assert provider.context.server_url == SERVER_URL

    def test_sse_transport_binds_private_key_jwt(self):
        provider = PrivateKeyJWTOAuthProvider(
            client_id="cid",
            assertion_provider=static_assertion_provider("token"),
        )
        transport = SSETransport(SERVER_URL, auth=provider)
        assert transport.auth is provider
        assert provider._bound is True

    def test_client_binds_provider_from_url(self):
        provider = ClientCredentialsOAuthProvider(
            client_id="cid", client_secret="secret"
        )
        Client(SERVER_URL, auth=provider)
        assert provider._bound is True
        assert provider.context.server_url == SERVER_URL


def test_assertion_provider_signs_expected_audience():
    """SignedJWTParameters produces an assertion bound to the given audience."""
    jwt_params = SignedJWTParameters(
        issuer="cid",
        subject="cid",
        signing_key=SIGNING_KEY,
        signing_algorithm="HS256",
    )
    provider = jwt_params.create_assertion_provider()

    async def _run():
        return await provider("https://issuer.example.com")

    import anyio

    assertion = anyio.run(_run)
    # Sanity-check it is a JWT with three segments.
    header, payload, _ = assertion.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    claims = urlsafe_b64decode(padded)
    assert b"issuer.example.com" in claims
