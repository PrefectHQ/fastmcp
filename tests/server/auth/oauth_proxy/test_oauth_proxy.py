"""Tests for OAuth proxy initialization and configuration."""

import time
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.testclient import TestClient

from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import OAuthTransaction
from fastmcp.server.auth.oauth_proxy.upstream import AsyncOAuth2Client


class TestOAuthProxyInitialization:
    """Tests for OAuth proxy initialization and configuration."""

    def test_basic_initialization(self, jwt_verifier):
        """Test basic proxy initialization with required parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        assert (
            proxy._upstream_authorization_endpoint
            == "https://auth.example.com/authorize"
        )
        assert proxy._upstream_token_endpoint == "https://auth.example.com/token"
        assert proxy._upstream_client_id == "client-123"
        assert proxy._upstream_client_secret is not None
        assert proxy._upstream_client_secret.get_secret_value() == "secret-456"
        assert str(proxy.base_url) == "https://api.example.com/"

    def test_all_optional_parameters(self, jwt_verifier):
        """Test initialization with all optional parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            upstream_revocation_endpoint="https://auth.example.com/revoke",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            redirect_path="/custom/callback",
            issuer_url="https://issuer.example.com",
            service_documentation_url="https://docs.example.com",
            allowed_client_redirect_uris=["http://localhost:*"],
            valid_scopes=["custom", "scopes"],
            forward_pkce=False,
            token_endpoint_auth_method="client_secret_post",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        assert proxy._upstream_revocation_endpoint == "https://auth.example.com/revoke"
        assert proxy._redirect_path == "/custom/callback"
        assert proxy._forward_pkce is False
        assert proxy._token_endpoint_auth_method == "client_secret_post"
        assert proxy.client_registration_options is not None
        assert proxy.client_registration_options.valid_scopes == ["custom", "scopes"]
        assert proxy.client_registration_options.default_scopes == ["custom", "scopes"]

    def test_default_scope_str_prefers_valid_scopes(self, jwt_verifier):
        """When valid_scopes is provided, _default_scope_str should use it
        instead of required_scopes. This ensures CIMD clients (which bypass
        RegistrationHandler) get registered with the full set of valid scopes."""
        jwt_verifier.required_scopes = ["openid"]
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            valid_scopes=["openid", "email", "calendar"],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        assert proxy._default_scope_str == "openid email calendar"

    def test_default_scope_str_falls_back_to_required_scopes(self, jwt_verifier):
        """Without valid_scopes, _default_scope_str falls back to required_scopes."""
        jwt_verifier.required_scopes = ["openid"]
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        assert proxy._default_scope_str == "openid"

    def test_update_default_scopes_updates_scope_str(self, jwt_verifier):
        """update_default_scopes should update the internal default scope string."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            valid_scopes=["openid"],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        assert proxy._default_scope_str == "openid"

        proxy.update_default_scopes(["openid", "email", "calendar"])
        assert proxy._default_scope_str == "openid email calendar"

    def test_update_default_scopes_updates_cimd_manager(self, jwt_verifier):
        """update_default_scopes should update CIMD manager's default_scope."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            valid_scopes=["openid"],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            enable_cimd=True,
        )
        assert proxy._cimd_manager is not None
        assert proxy._cimd_manager.default_scope == "openid"

        proxy.update_default_scopes(["openid", "email", "drive"])
        assert proxy._cimd_manager.default_scope == "openid email drive"

    def test_update_default_scopes_updates_registration_options(self, jwt_verifier):
        """update_default_scopes should update client registration scope options."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            valid_scopes=["openid"],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        assert proxy.client_registration_options is not None
        assert proxy.client_registration_options.valid_scopes == ["openid"]
        assert proxy.client_registration_options.default_scopes == ["openid"]

        scopes = ["openid", "email", "calendar"]
        proxy.update_default_scopes(scopes)
        scopes.append("drive")

        assert proxy.client_registration_options.valid_scopes == [
            "openid",
            "email",
            "calendar",
        ]
        assert proxy.client_registration_options.default_scopes == [
            "openid",
            "email",
            "calendar",
        ]

    def test_update_default_scopes_no_cimd_manager(self, jwt_verifier):
        """update_default_scopes should work when CIMD is disabled (no manager)."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            valid_scopes=["openid"],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            enable_cimd=False,
        )
        assert proxy._cimd_manager is None

        # Should not raise
        proxy.update_default_scopes(["openid", "email"])
        assert proxy._default_scope_str == "openid email"

    def test_redirect_path_normalization(self, jwt_verifier):
        """Test that redirect_path is normalized with leading slash."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://api.com",
            redirect_path="auth/callback",  # No leading slash
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        assert proxy._redirect_path == "/auth/callback"

    async def test_metadata_advertises_cimd_support(self, jwt_verifier):
        """OAuth metadata should advertise CIMD and public-client auth support."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            enable_cimd=True,
        )

        app = Starlette(routes=proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)

        async with httpx2.AsyncClient(
            transport=transport, base_url="https://api.example.com"
        ) as client:
            response = await client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        metadata = response.json()
        assert metadata.get("client_id_metadata_document_supported") is True
        assert set(metadata.get("token_endpoint_auth_methods_supported")) == {
            "private_key_jwt",
            "none",
        }

    async def test_metadata_advertises_only_public_client_auth(self, jwt_verifier):
        """The proxy authenticates every client as public, so metadata must
        advertise `none` and must not claim secret-based methods it never enforces.
        """
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            enable_cimd=False,
        )

        app = Starlette(routes=proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)

        async with httpx2.AsyncClient(
            transport=transport, base_url="https://api.example.com"
        ) as client:
            response = await client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        metadata = response.json()
        assert set(metadata.get("token_endpoint_auth_methods_supported")) == {"none"}

    async def test_metadata_advertises_authorization_response_issuer_parameter(
        self, jwt_verifier
    ):
        """OAuth metadata should advertise RFC 9207 authorization response issuers."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        app = Starlette(routes=proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)

        async with httpx2.AsyncClient(
            transport=transport, base_url="https://api.example.com"
        ) as client:
            response = await client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        metadata = response.json()
        assert metadata["issuer"] == "https://api.example.com/"
        assert metadata["authorization_response_iss_parameter_supported"] is True


class TestOptionalClientSecret:
    """Tests for OAuthProxy without upstream_client_secret."""

    def test_no_secret_requires_jwt_signing_key(self, jwt_verifier):
        """OAuthProxy requires jwt_signing_key when client_secret is omitted."""
        with pytest.raises(ValueError, match="jwt_signing_key is required"):
            OAuthProxy(
                upstream_authorization_endpoint="https://auth.example.com/authorize",
                upstream_token_endpoint="https://auth.example.com/token",
                upstream_client_id="client-123",
                token_verifier=jwt_verifier,
                base_url="https://api.example.com",
                client_storage=MemoryStore(),
            )

    def test_no_secret_with_jwt_key_succeeds(self, jwt_verifier):
        """OAuthProxy initializes successfully without client_secret when jwt_signing_key is given."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key=b"a" * 32,
            client_storage=MemoryStore(),
        )
        assert proxy._upstream_client_secret is None
        assert proxy._upstream_client_id == "client-123"

    def test_factory_method_without_secret(self, jwt_verifier):
        """_create_upstream_oauth_client works when no secret is configured."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key=b"a" * 32,
            client_storage=MemoryStore(),
        )
        client = proxy._create_upstream_oauth_client()
        assert isinstance(client, AsyncOAuth2Client)
        assert client.client_id == "client-123"

    def test_factory_method_with_secret(self, jwt_verifier):
        """_create_upstream_oauth_client includes the secret when configured."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        client = proxy._create_upstream_oauth_client()
        assert isinstance(client, AsyncOAuth2Client)
        assert client.client_secret == "secret-456"

    def test_consent_cookies_work_without_secret(self, jwt_verifier):
        """Cookie signing/verification works using JWT key when no secret is configured."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key=b"a" * 32,
            client_storage=MemoryStore(),
        )
        signed = proxy._sign_cookie("test-payload")
        assert proxy._verify_cookie(signed) == "test-payload"
        assert proxy._verify_cookie("tampered.payload") is None


class TestIdpCallbackErrorForwarding:
    """Tests for error forwarding in the IdP callback."""

    async def test_error_with_valid_transaction_redirects_to_client(self, oauth_proxy):
        """When the IdP returns an error and the transaction exists, the proxy
        must forward the error to the client's redirect_uri rather than showing
        an HTML error page."""
        txn_id = "test-txn-123"
        client_redirect_uri = "http://localhost:12345/callback"
        client_state = "client-state-abc"

        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri=client_redirect_uri,
            client_state=client_state,
            code_challenge=None,
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await oauth_proxy._transaction_store.put(key=txn_id, value=transaction)

        app = Starlette(routes=oauth_proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)

        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://myserver.com",
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"/auth/callback?error=access_denied&error_description=User+denied+access&state={txn_id}"
            )

        assert response.status_code == 302
        location = response.headers["location"]
        parsed = urlparse(location)
        assert (
            parsed.scheme + "://" + parsed.netloc + parsed.path == client_redirect_uri
        )
        params = parse_qs(parsed.query)
        assert params["error"] == ["access_denied"]
        assert params["error_description"] == ["User denied access"]
        assert params["state"] == [client_state]
        assert params["iss"] == ["https://myserver.com/"]

    async def test_error_redirect_does_not_duplicate_iss_already_in_redirect_uri(
        self, oauth_proxy
    ):
        """RFC 9207 P2 regression: a registered redirect_uri may already
        carry its own `iss` query parameter (e.g. a multi-tenant client
        encoding its tenant in the callback URL). Forwarding an IdP error
        must not append a second `iss` on top of it -- RFC 6749 §3.1
        forbids a response parameter appearing more than once -- and every
        other query byte on the registered URI (a valueless `flag` and a
        non-UTF-8 percent-encoded `sig`) must survive untouched.
        """
        txn_id = "test-txn-dup-iss"
        client_redirect_uri = (
            "http://localhost:12345/callback?iss=tenant&flag&sig=%FF%FE"
        )
        client_state = "client-state-abc"

        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri=client_redirect_uri,
            client_state=client_state,
            code_challenge=None,
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await oauth_proxy._transaction_store.put(key=txn_id, value=transaction)

        app = Starlette(routes=oauth_proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)

        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://myserver.com",
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"/auth/callback?error=access_denied&state={txn_id}"
            )

        assert response.status_code == 302
        location = response.headers["location"]
        query = urlparse(location).query
        params = parse_qs(query)

        # Exactly one `iss`, corrected to the canonical value -- a
        # duplicate would make this list have length 2.
        assert params["iss"] == ["https://myserver.com/"]
        # Other query bytes from the registered redirect_uri survive
        # byte-for-byte.
        assert "flag" in query
        assert "sig=%FF%FE" in query

    async def test_error_with_unsafe_transaction_redirect_returns_html_error(
        self, oauth_proxy
    ):
        """IdP errors must not redirect to unsafe stored callback URIs."""
        txn_id = "test-txn-unsafe"

        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri="javascript:alert(document.cookie)//",
            client_state="client-state-abc",
            code_challenge=None,
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await oauth_proxy._transaction_store.put(key=txn_id, value=transaction)

        app = Starlette(routes=oauth_proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)

        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://myserver.com",
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"/auth/callback?error=access_denied&state={txn_id}"
            )

        assert response.status_code == 400
        assert "location" not in response.headers
        assert "Invalid redirect URI" in response.text

    async def test_error_with_missing_transaction_returns_html_error(self, oauth_proxy):
        """When the IdP returns an error but the transaction is missing or
        expired, the proxy must return a local HTML error page — there is no
        trusted client redirect_uri to forward to."""
        app = Starlette(routes=oauth_proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)

        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://myserver.com",
            follow_redirects=False,
        ) as client:
            response = await client.get(
                "/auth/callback?error=access_denied&state=nonexistent-txn"
            )

        assert response.status_code == 400


class TestIdpCallbackSuccessForwarding:
    """Tests for the success (`code`) path in the IdP callback."""

    async def test_success_redirect_does_not_duplicate_iss_already_in_redirect_uri(
        self, jwt_verifier
    ):
        """RFC 9207 P2 regression at the success-redirect call site: a
        registered redirect_uri already carrying `iss` must end up with
        exactly one `iss` (the canonical value) after the proxy forwards
        the exchanged authorization code, and every other query byte on the
        registered URI must survive untouched.
        """
        # Consent is disabled here because this test exercises callback
        # forwarding, not the consent-binding-cookie check that the
        # standard consent flow additionally requires.
        oauth_proxy = OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            redirect_path="/auth/callback",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            require_authorization_consent=False,
        )

        client_id = "success-dup-iss-client"
        client_redirect_uri = (
            "http://localhost:12345/callback?iss=tenant&flag&sig=%FF%FE"
        )
        client_info = OAuthClientInformationFull(
            client_id=client_id,
            client_secret="test-secret",
            redirect_uris=[AnyUrl(client_redirect_uri)],
        )
        await oauth_proxy.register_client(client_info)

        txn_id = "test-txn-success-dup-iss"
        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id=client_id,
            client_redirect_uri=client_redirect_uri,
            client_state="client-state-success",
            code_challenge=None,
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await oauth_proxy._transaction_store.put(key=txn_id, value=transaction)

        app = Starlette(routes=oauth_proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)

        with patch(
            "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client"
        ) as MockClient:
            mock_client = AsyncMock()
            mock_client.fetch_token = AsyncMock(
                return_value={
                    "access_token": "upstream-access-token",
                    "refresh_token": "upstream-refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
            MockClient.return_value = mock_client

            async with httpx2.AsyncClient(
                transport=transport,
                base_url="https://myserver.com",
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"/auth/callback?code=idp-authorization-code&state={txn_id}"
                )

        assert response.status_code == 302
        location = response.headers["location"]
        query = urlparse(location).query
        params = parse_qs(query)

        assert "code" in params
        assert params["state"] == ["client-state-success"]
        # Exactly one `iss`, corrected to the canonical value -- a
        # duplicate would make this list have length 2.
        assert params["iss"] == ["https://myserver.com/"]
        # Other query bytes from the registered redirect_uri survive
        # byte-for-byte.
        assert "flag" in query
        assert "sig=%FF%FE" in query


class TestCIMDTokenEndpointAudience:
    """The `aud` expected on a CIMD assertion matches the advertised token endpoint."""

    @pytest.mark.parametrize(
        "base_url",
        ["https://api.example.com", "https://api.example.com/api"],
    )
    def test_token_endpoint_url_matches_advertised_metadata(
        self, jwt_verifier, base_url: str
    ):
        """A bare-authority base_url must not expect `aud` of `https://host//token`.

        CIMD is enabled by default, and a spec-following client binds its
        private_key_jwt assertion to the `token_endpoint` the metadata
        advertises. If the proxy expects a different string, every such client
        is rejected with invalid_client.
        """
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url=base_url,
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

        app = Starlette(routes=proxy.get_routes(mcp_path="/mcp"))
        with TestClient(app) as client:
            metadata = client.get("/.well-known/oauth-authorization-server").json()

        assert proxy.token_endpoint_url == metadata["token_endpoint"]


class ScopeSendingProxy(OAuthProxy):
    """A provider that opts back into sending scopes at the token endpoint."""

    def _prepare_scopes_for_token_exchange(self, scopes: list[str]) -> list[str]:
        return scopes


class TestUpstreamTokenExchangeParams:
    """The auth-code token request carries only RFC 6749 §4.1.3 parameters.

    `scope` is requested at the authorization endpoint; the token endpoint
    reports what was granted. Sending `scope` on the exchange is tolerated by
    many providers but rejected by spec-following ones, so the default omits it.
    """

    @staticmethod
    def _make_proxy(jwt_verifier, cls: type[OAuthProxy] = OAuthProxy) -> OAuthProxy:
        return cls(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            redirect_path="/auth/callback",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            require_authorization_consent=False,
        )

    @staticmethod
    async def _exchange_kwargs(
        proxy: OAuthProxy, txn_id: str, scopes: list[str]
    ) -> Mapping[str, Any]:
        """Drive the IdP callback and return the token-endpoint kwargs."""
        client_info = OAuthClientInformationFull(
            client_id="exchange-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)
        await proxy._transaction_store.put(
            key=txn_id,
            value=OAuthTransaction(
                txn_id=txn_id,
                client_id="exchange-client",
                client_redirect_uri="http://localhost:12345/callback",
                client_state="client-state",
                code_challenge=None,
                code_challenge_method="S256",
                scopes=scopes,
                created_at=time.time(),
            ),
        )

        app = Starlette(routes=proxy.get_routes())
        transport = httpx2.ASGITransport(app=app)
        with patch(
            "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client"
        ) as MockClient:
            mock_client = AsyncMock()
            mock_client.fetch_token = AsyncMock(
                return_value={
                    "access_token": "upstream-access-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": " ".join(scopes),
                }
            )
            MockClient.return_value = mock_client

            async with httpx2.AsyncClient(
                transport=transport,
                base_url="https://myserver.com",
                follow_redirects=False,
            ) as http_client:
                response = await http_client.get(
                    f"/auth/callback?code=idp-code&state={txn_id}"
                )

        assert response.status_code == 302
        call = mock_client.fetch_token.await_args
        assert call is not None
        return call.kwargs

    async def test_token_exchange_omits_scope(self, jwt_verifier):
        """The client's scopes are not echoed back at the token endpoint."""
        proxy = self._make_proxy(jwt_verifier)

        kwargs = await self._exchange_kwargs(
            proxy, "txn-no-scope", ["openid", "profile"]
        )

        assert "scope" not in kwargs
        # The parameters that *do* belong on an authorization-code exchange
        # are still sent.
        assert kwargs["code"] == "idp-code"
        assert kwargs["redirect_uri"] == "https://myserver.com/auth/callback"

    async def test_provider_override_can_still_send_scope(self, jwt_verifier):
        """Opting back in stays possible for providers that require it."""
        proxy = self._make_proxy(jwt_verifier, cls=ScopeSendingProxy)

        kwargs = await self._exchange_kwargs(
            proxy, "txn-sends-scope", ["openid", "profile"]
        )

        assert kwargs["scope"] == "openid profile"

    async def test_scope_is_still_requested_at_the_authorization_endpoint(
        self, jwt_verifier
    ):
        """Omitting scope on the exchange must not drop it from the authorize leg."""
        proxy = self._make_proxy(jwt_verifier)
        client_info = OAuthClientInformationFull(
            client_id="authorize-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)

        url = await proxy.authorize(
            client_info,
            AuthorizationParams(
                redirect_uri=AnyUrl("http://localhost:12345/callback"),
                redirect_uri_provided_explicitly=True,
                state="client-state",
                code_challenge="",
                scopes=["openid", "profile"],
            ),
        )

        assert parse_qs(urlparse(url).query)["scope"] == ["openid profile"]
