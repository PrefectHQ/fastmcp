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
from collections.abc import Callable
from contextlib import aclosing
from urllib.parse import urlparse

import httpx2
import jwt
import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.client.auth import OAuthTokenError
from mcp.shared.auth import OAuthToken

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
    prm_scopes_supported: list[str] | None = None,
) -> tuple[Callable[[httpx2.Request], httpx2.Response], dict[str, httpx2.Request]]:
    """Build a responder for the standard M2M discovery + token exchange flow.

    Returns the responder and a dict that captures the token request and the
    final (retried) resource request for assertions. When ``prm_scopes_supported``
    is set, the protected-resource metadata advertises those scopes, which the SDK
    flow would otherwise apply to the token request.
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
            prm: dict = {
                "resource": server_url,
                "authorization_servers": [auth_server_url],
            }
            if prm_scopes_supported is not None:
                prm["scopes_supported"] = prm_scopes_supported
            return httpx2.Response(200, json=prm)

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

    async def test_explicit_scopes_win_over_server_advertised(self):
        """A caller's explicit scopes reach the token request even when the
        server advertises a different set (the inherited flow would otherwise
        overwrite them during 401 handling)."""
        provider = ClientCredentialsOAuthProvider(
            SERVER_URL,
            client_id="cid",
            client_secret="secret",
            scopes=["read", "write"],
        )
        responder, captured = make_m2m_responder(
            token_response={"access_token": "T", "token_type": "Bearer"},
            prm_scopes_supported=["admin", "superuser"],
        )

        await drive_auth_flow(provider, responder)

        token_body = form_body(captured["token_request"])
        assert token_body["scope"] == "read write"


class TestTokenCacheIsolation:
    """Cached tokens are namespaced by client identity, not just server URL."""

    async def test_distinct_client_ids_do_not_share_cached_tokens(self):
        """Two providers with different client_ids sharing one store against the
        same endpoint each retain their own token instead of clobbering one
        another."""
        store = MemoryStore()

        provider_a = ClientCredentialsOAuthProvider(
            SERVER_URL,
            client_id="client-a",
            client_secret="secret-a",
            token_storage=store,
        )
        provider_b = ClientCredentialsOAuthProvider(
            SERVER_URL,
            client_id="client-b",
            client_secret="secret-b",
            token_storage=store,
        )

        responder_a, _ = make_m2m_responder(
            token_response={
                "access_token": "TOKEN-A",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )
        responder_b, _ = make_m2m_responder(
            token_response={
                "access_token": "TOKEN-B",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

        await drive_auth_flow(provider_a, responder_a)
        requests_b = await drive_auth_flow(provider_b, responder_b)

        # provider_b acquires and uses its own token rather than reloading the
        # token provider_a wrote to the shared store.
        assert requests_b[-1].headers["Authorization"] == "Bearer TOKEN-B"

        # Each client's token is preserved under its own namespace.
        tokens_a = await provider_a.context.storage.get_tokens()
        tokens_b = await provider_b.context.storage.get_tokens()
        assert tokens_a is not None and tokens_a.access_token == "TOKEN-A"
        assert tokens_b is not None and tokens_b.access_token == "TOKEN-B"

    async def test_distinct_scopes_do_not_share_cached_tokens(self):
        """Two providers with the same client_id but different requested scopes
        sharing one store each retain their own token: a token issued for one
        scope set must not be reused for another."""
        store = MemoryStore()

        provider_read = ClientCredentialsOAuthProvider(
            SERVER_URL,
            client_id="cid",
            client_secret="secret",
            scopes=["read"],
            token_storage=store,
        )
        provider_write = ClientCredentialsOAuthProvider(
            SERVER_URL,
            client_id="cid",
            client_secret="secret",
            scopes=["write"],
            token_storage=store,
        )

        responder_read, _ = make_m2m_responder(
            token_response={
                "access_token": "TOKEN-READ",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )
        responder_write, _ = make_m2m_responder(
            token_response={
                "access_token": "TOKEN-WRITE",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

        await drive_auth_flow(provider_read, responder_read)
        requests_write = await drive_auth_flow(provider_write, responder_write)

        # The write-scoped provider acquires its own token instead of reloading
        # the read-scoped token from the shared store.
        assert requests_write[-1].headers["Authorization"] == "Bearer TOKEN-WRITE"

        tokens_read = await provider_read.context.storage.get_tokens()
        tokens_write = await provider_write.context.storage.get_tokens()
        assert tokens_read is not None and tokens_read.access_token == "TOKEN-READ"
        assert tokens_write is not None and tokens_write.access_token == "TOKEN-WRITE"


class TestPersistentTokenExpiry:
    """A token reloaded from persistent storage honors its stored expiry."""

    @pytest.mark.parametrize("expires_in", [-100, 0])
    async def test_expired_stored_token_is_refetched(self, expires_in):
        """Recreating a provider against a store holding an already-expired token
        re-fetches instead of trusting the stale token as if it never expires.

        `expires_in=0` is the boundary: an immediately-expiring token still
        declares an expiry, so it must not be mistaken for a non-expiring one.
        """
        store = MemoryStore()

        def make_provider() -> ClientCredentialsOAuthProvider:
            return ClientCredentialsOAuthProvider(
                SERVER_URL,
                client_id="cid",
                client_secret="secret",
                scopes=["read"],
                token_storage=store,
            )

        # Seed the shared store with a token whose absolute expiry is in the past.
        seed_provider = make_provider()
        await seed_provider.context.storage.set_tokens(
            OAuthToken(
                access_token="STALE-TOKEN",
                token_type="Bearer",
                expires_in=expires_in,
            )
        )

        provider = make_provider()
        responder, _ = make_m2m_responder(
            token_response={
                "access_token": "FRESH-TOKEN",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

        requests = await drive_auth_flow(provider, responder)

        assert requests[-1].headers["Authorization"] == "Bearer FRESH-TOKEN"

    async def test_nonexpiring_token_ignores_stale_stored_expiry(self):
        """A reloaded token without `expires_in` is non-expiring and must not
        inherit a stale expiry left by a previous token it replaced."""
        store = MemoryStore()

        def make_provider() -> ClientCredentialsOAuthProvider:
            return ClientCredentialsOAuthProvider(
                SERVER_URL,
                client_id="cid",
                client_secret="secret",
                scopes=["read"],
                token_storage=store,
            )

        # Record a stale past expiry, then replace the token with a non-expiring
        # one — set_tokens leaves the earlier expiry record in place.
        seed = make_provider()
        await seed.context.storage.set_tokens(
            OAuthToken(access_token="OLD", token_type="Bearer", expires_in=-100)
        )
        await seed.context.storage.set_tokens(
            OAuthToken(access_token="NONEXPIRING", token_type="Bearer")
        )

        provider = make_provider()
        responder, _ = make_m2m_responder(
            token_response={
                "access_token": "FRESH-TOKEN",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

        requests = await drive_auth_flow(provider, responder)

        # The non-expiring token is used as-is; the stale expiry does not force
        # a needless re-exchange to FRESH-TOKEN.
        assert requests[-1].headers["Authorization"] == "Bearer NONEXPIRING"


class TestStepUpScopeAccumulation:
    """A 403 insufficient_scope step-up unions the challenged scope with the
    caller's scopes instead of dropping the accumulated grant."""

    async def test_step_up_requests_union_of_scopes(self):
        token_scopes: list[str] = []
        require_write = False

        def responder(request: httpx2.Request) -> httpx2.Response:
            url = str(request.url)
            path = urlparse(url).path

            if url.startswith(SERVER_URL):
                auth = request.headers.get("Authorization", "")
                if not auth:
                    return httpx2.Response(401, headers={"WWW-Authenticate": "Bearer"})
                granted = auth.removeprefix("Bearer ").split()
                # Once the server begins demanding "write", a token lacking it is
                # challenged for step-up rather than accepted.
                if require_write and "write" not in granted:
                    return httpx2.Response(
                        403,
                        headers={
                            "WWW-Authenticate": (
                                'Bearer error="insufficient_scope", scope="write"'
                            )
                        },
                    )
                return httpx2.Response(200, text="ok")

            if path.startswith("/.well-known/oauth-protected-resource"):
                return httpx2.Response(
                    200,
                    json={
                        "resource": SERVER_URL,
                        "authorization_servers": [AUTH_SERVER_URL],
                    },
                )

            if path.startswith(
                "/.well-known/oauth-authorization-server"
            ) or path.startswith("/.well-known/openid-configuration"):
                return httpx2.Response(
                    200,
                    json={
                        "issuer": AUTH_SERVER_URL,
                        "authorization_endpoint": f"{AUTH_SERVER_URL}/authorize",
                        "token_endpoint": f"{AUTH_SERVER_URL}/token",
                        "response_types_supported": ["code"],
                    },
                )

            if url == f"{AUTH_SERVER_URL}/token":
                scope = form_body(request).get("scope", "")
                token_scopes.append(scope)
                return httpx2.Response(
                    200,
                    json={
                        "access_token": scope or "noscope",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )

            raise AssertionError(f"unexpected request: {request.method} {url}")

        provider = ClientCredentialsOAuthProvider(
            SERVER_URL, client_id="cid", client_secret="secret", scopes=["read"]
        )

        # Initial acquisition requests exactly the caller's scopes.
        await drive_auth_flow(provider, responder)
        assert token_scopes[0] == "read"

        # The server now requires an additional scope for the operation.
        require_write = True
        await drive_auth_flow(provider, responder)

        # The step-up token request carries the union, not just the caller's scope.
        assert set(token_scopes[-1].split()) == {"read", "write"}


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
    # The assertion is a JWT whose audience is exactly the requested issuer.
    claims = jwt.decode(assertion, options={"verify_signature": False})
    assert claims["aud"] == "https://issuer.example.com"
