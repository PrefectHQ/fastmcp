"""Tests for enhanced OAuth error responses.

This test suite covers:
1. Enhanced authorization handler (HTML and JSON error pages)
2. Enhanced middleware (better error messages)
3. Content negotiation
4. Server branding in error pages
"""

import asyncio
from urllib.parse import parse_qs, quote, urlparse

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyHttpUrl, AnyUrl
from starlette.applications import Starlette
from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.http import create_streamable_http_app


class _UnderScopedTokenVerifier(TokenVerifier):
    def __init__(self, required_scopes: list[str]):
        super().__init__(required_scopes=required_scopes)

    async def verify_token(self, token: str) -> AccessToken:
        return AccessToken(token=token, client_id="test-client", scopes=["other"])


class _UnderScopedOAuthProxy(OAuthProxy):
    async def verify_token(self, token: str) -> AccessToken:
        return AccessToken(token=token, client_id="test-client", scopes=["other"])


class _DirectClientRedirectOAuthProxy(OAuthProxy):
    """Proxy whose `authorize()` bypasses consent/upstream entirely and
    redirects straight back to the client with a `code` — the pattern used
    by providers (or tests) that short-circuit the standard
    consent -> upstream IdP -> callback flow. OAuthProxy's own `authorize()`
    never does this itself, but a subclass legitimately can, and
    `AuthorizationHandler.handle()` must still attach `iss` to whatever
    redirect comes back.

    Appends its own `code`/`state` with `&` rather than an unconditional
    `?` so this still produces a well-formed URL when `redirect_uri` is a
    registered redirect that already carries its own query string (e.g. a
    client-supplied `iss`)."""

    async def authorize(self, client, params):  # type: ignore[override]
        separator = "&" if "?" in str(params.redirect_uri) else "?"
        return (
            f"{params.redirect_uri}{separator}code=test-auth-code&state={params.state}"
        )


class _DirectClientRedirectWithIssOAuthProxy(OAuthProxy):
    """Like `_DirectClientRedirectOAuthProxy`, but the provider's
    `authorize()` override already put its own `iss` on the redirect —
    simulating a provider that is itself RFC 9207-aware (or, when
    `redirect_iss` doesn't match this server's issuer, a provider bug).
    `response_kind` selects whether the redirect looks like a success
    (`code`) or error (`error`) response; `AuthorizationHandler.handle()`
    must not duplicate `iss` on either."""

    def __init__(
        self,
        *args,
        redirect_iss: str,
        response_kind: str = "code",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._redirect_iss = redirect_iss
        self._response_kind = response_kind

    async def authorize(self, client, params):  # type: ignore[override]
        payload = (
            f"code=test-auth-code&state={params.state}"
            if self._response_kind == "code"
            else f"error=access_denied&state={params.state}"
        )
        iss = quote(self._redirect_iss, safe="")
        return f"{params.redirect_uri}?{payload}&iss={iss}"


class TestEnhancedAuthorizationHandler:
    """Tests for enhanced authorization handler error responses."""

    @pytest.fixture
    def oauth_proxy(self, rsa_key_pair):
        """Create OAuth proxy for testing."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

    def test_unregistered_client_returns_html_for_browser(self, oauth_proxy):
        """Test that unregistered client returns styled HTML for browser requests."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                headers={"Accept": "text/html"},
            )

            # Should return 400 with HTML content
            assert response.status_code == 400
            assert "text/html" in response.headers["content-type"]

            # HTML should contain error message
            html = response.text
            assert "Client Not Registered" in html
            assert "unregistered-client-id" in html
            assert "To fix this" in html
            assert "Close this browser window" in html
            assert "Clear authentication tokens" in html

            # Should have Link header for registration endpoint
            assert "Link" in response.headers
            assert "/register" in response.headers["Link"]

    def test_unregistered_client_returns_json_for_api(self, oauth_proxy):
        """Test that unregistered client returns enhanced JSON for API clients."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                headers={"Accept": "application/json"},
            )

            # Should return 400 with JSON content
            assert response.status_code == 400
            assert "application/json" in response.headers["content-type"]

            # JSON should have enhanced error response
            data = response.json()
            assert data["error"] == "invalid_request"
            assert "unregistered-client-id" in data["error_description"]
            assert data["state"] == "test-state"

            # Should include registration endpoint hints
            assert "registration_endpoint" in data
            assert data["registration_endpoint"] == "https://myserver.com/register"
            assert "authorization_server_metadata" in data

            # Should have Link header
            assert "Link" in response.headers
            assert "/register" in response.headers["Link"]

    def test_successful_authorization_not_enhanced(self, oauth_proxy):
        """Test that successful authorizations are not modified by enhancement."""
        app = Starlette(routes=oauth_proxy.get_routes())

        # Register a valid client first
        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        # Need to register synchronously
        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                headers={"Accept": "text/html"},
                follow_redirects=False,
            )

            # Should redirect to consent page (302), not return error
            assert response.status_code == 302
            assert "/consent" in response.headers["location"]

    def test_redirect_error_includes_proxy_issuer(self, oauth_proxy):
        """Authorization error redirects should include RFC 9207 issuer."""
        app = Starlette(routes=oauth_proxy.get_routes())

        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            scope="read",
        )

        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                    "scope": "admin",
                },
                headers={"Accept": "text/html"},
                follow_redirects=False,
            )

            assert response.status_code == 302
            query_params = parse_qs(urlparse(response.headers["location"]).query)
            assert query_params["error"] == ["invalid_scope"]
            assert query_params["state"] == ["test-state"]
            assert query_params["iss"] == ["https://myserver.com/"]

    def test_redirect_error_matches_path_base_url_metadata_issuer(self, rsa_key_pair):
        """Authorization error redirects should match the metadata issuer exactly."""
        oauth_proxy = OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://proxy.example.com/oauth",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        app = Starlette(routes=oauth_proxy.get_routes())

        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            scope="read",
        )

        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            metadata_response = client.get("/.well-known/oauth-authorization-server")
            metadata = metadata_response.json()

            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                    "scope": "admin",
                },
                headers={"Accept": "text/html"},
                follow_redirects=False,
            )

            assert metadata["issuer"] == "https://proxy.example.com/oauth"
            assert response.status_code == 302
            query_params = parse_qs(urlparse(response.headers["location"]).query)
            assert query_params["error"] == ["invalid_scope"]
            assert query_params["state"] == ["test-state"]
            assert query_params["iss"] == [metadata["issuer"]]

    def test_success_redirect_from_authorize_override_includes_issuer(
        self, rsa_key_pair
    ):
        """RFC 9207 regression: a `code` redirect returned directly by
        `authorize()` (bypassing consent/upstream) must carry `iss` too, not
        just `error` redirects.

        `AuthorizationHandler.handle()` previously only attached `iss` when
        the SDK's redirect contained an `error` parameter. The base
        `OAuthProxy.authorize()` never redirects straight to the client, so
        this gap was invisible until a provider override (or a test mock,
        like the GitHub provider integration test) returned the client
        redirect directly — at which point the server was advertising
        `authorization_response_iss_parameter_supported: true` while
        silently breaking RFC 9207-aware clients on this path.
        """
        oauth_proxy = _DirectClientRedirectOAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        app = Starlette(routes=oauth_proxy.get_routes())

        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            scope="read",
        )
        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            metadata = client.get("/.well-known/oauth-authorization-server").json()
            assert metadata["authorization_response_iss_parameter_supported"] is True

            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                follow_redirects=False,
            )

        assert response.status_code == 302
        query_params = parse_qs(urlparse(response.headers["location"]).query)
        assert query_params["code"] == ["test-auth-code"]
        assert query_params["state"] == ["test-state"]
        assert query_params["iss"] == [metadata["issuer"]]

    def test_success_redirect_does_not_duplicate_iss_already_in_redirect_uri(
        self, rsa_key_pair
    ):
        """RFC 9207 P2 regression: a registered redirect_uri may already
        carry its own `iss` query parameter — distinct from the provider
        adding one itself (covered by
        `test_success_redirect_with_matching_iss_not_duplicated` below).
        `AuthorizationHandler.handle()` must still land on exactly one
        `iss` (the canonical value), with every other query byte on the
        registered URI preserved untouched.
        """
        oauth_proxy = _DirectClientRedirectOAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        app = Starlette(routes=oauth_proxy.get_routes())

        client_redirect_uri = (
            "http://localhost:12345/callback?iss=tenant&flag&sig=%FF%FE"
        )
        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl(client_redirect_uri)],
            scope="read",
        )
        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            metadata = client.get("/.well-known/oauth-authorization-server").json()

            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": client_redirect_uri,
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                follow_redirects=False,
            )

        assert response.status_code == 302
        location = response.headers["location"]
        query = urlparse(location).query
        query_params = parse_qs(query)
        assert query_params["code"] == ["test-auth-code"]
        assert query_params["state"] == ["test-state"]
        # Exactly one `iss`, corrected to the canonical value -- a
        # duplicate would make this list have length 2.
        assert query_params["iss"] == [metadata["issuer"]]
        # Other query bytes from the registered redirect_uri survive
        # byte-for-byte.
        assert "flag" in query
        assert "sig=%FF%FE" in query

    def test_success_redirect_with_matching_iss_not_duplicated(self, rsa_key_pair):
        """If a provider's `authorize()` override already stamped the
        correct `iss` on its redirect, `handle()` must not append a second
        one — RFC 6749 §3.1 forbids a response parameter appearing twice.
        """
        oauth_proxy = _DirectClientRedirectWithIssOAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            redirect_iss="https://myserver.com/",
        )
        app = Starlette(routes=oauth_proxy.get_routes())

        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            scope="read",
        )
        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            metadata = client.get("/.well-known/oauth-authorization-server").json()

            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                follow_redirects=False,
            )

        assert response.status_code == 302
        query_params = parse_qs(urlparse(response.headers["location"]).query)
        # Exactly one `iss` (a duplicate would make this list have length 2).
        assert query_params["iss"] == [metadata["issuer"]]

    def test_success_redirect_with_mismatched_iss_is_corrected(self, rsa_key_pair):
        """A provider's `authorize()` override can put an `iss` on its
        redirect that doesn't match what this server advertises in its own
        discovery document (`self._issuer`). An RFC 9207 client validates
        `iss` against that document, so the mismatched value is already
        unusable to a spec-compliant client. `handle()` corrects it to the
        canonical value rather than leaving the broken value in place or
        appending a second `iss` (which RFC 6749 §3.1 forbids outright).

        This is a deliberate policy choice, not the only defensible one —
        see the comment in `AuthorizationHandler.handle()` for the
        reasoning, and update this test if that policy changes.
        """
        oauth_proxy = _DirectClientRedirectWithIssOAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            redirect_iss="https://wrong-issuer.example.com/",
        )
        app = Starlette(routes=oauth_proxy.get_routes())

        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            scope="read",
        )
        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            metadata = client.get("/.well-known/oauth-authorization-server").json()

            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                follow_redirects=False,
            )

        assert response.status_code == 302
        query_params = parse_qs(urlparse(response.headers["location"]).query)
        # Exactly one `iss`, corrected to the canonical value rather than
        # left mismatched or duplicated.
        assert query_params["iss"] == [metadata["issuer"]]
        assert query_params["iss"] != ["https://wrong-issuer.example.com/"]

    def test_error_redirect_with_existing_iss_not_duplicated(self, rsa_key_pair):
        """The duplication guard applies to error redirects too, not just
        success ones — a provider override can construct an `error`
        redirect that already carries `iss`.
        """
        oauth_proxy = _DirectClientRedirectWithIssOAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
            redirect_iss="https://myserver.com/",
            response_kind="error",
        )
        app = Starlette(routes=oauth_proxy.get_routes())

        client_info = OAuthClientInformationFull(
            client_id="valid-client",
            client_secret="valid-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            scope="read",
        )
        asyncio.run(oauth_proxy.register_client(client_info))

        with TestClient(app) as client:
            metadata = client.get("/.well-known/oauth-authorization-server").json()

            response = client.get(
                "/authorize",
                params={
                    "client_id": "valid-client",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                    "state": "test-state",
                },
                follow_redirects=False,
            )

        assert response.status_code == 302
        query_params = parse_qs(urlparse(response.headers["location"]).query)
        assert query_params["error"] == ["access_denied"]
        assert query_params["iss"] == [metadata["issuer"]]

    def test_html_error_includes_server_branding(self, oauth_proxy):
        """Test that HTML error page includes server branding from FastMCP instance."""
        from mcp_types import Icon

        # Create FastMCP server with custom branding
        mcp = FastMCP(
            "My Custom Server",
            icons=[Icon(src="https://example.com/icon.png", mime_type="image/png")],
        )

        # Create app with OAuth routes
        app = Starlette(routes=oauth_proxy.get_routes())
        # Attach FastMCP instance to app state (same as done in http.py)
        app.state.fastmcp_server = mcp

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                },
                headers={"Accept": "text/html"},
            )

            assert response.status_code == 400
            html = response.text

            # Should include custom server icon
            assert "https://example.com/icon.png" in html


class TestEnhancedRequireAuthMiddleware:
    """Tests for enhanced authentication middleware error messages."""

    @staticmethod
    def create_scoped_app(
        required_scopes: list[str],
        scopes_supported: list[str],
        challenge_scopes: list[str] | None = None,
    ) -> Starlette:
        auth = RemoteAuthProvider(
            token_verifier=_UnderScopedTokenVerifier(required_scopes),
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="http://localhost:8000",
            scopes_supported=scopes_supported,
            challenge_scopes=challenge_scopes,
        )
        return FastMCP("Test Server", auth=auth).http_app()

    @staticmethod
    def create_oauth_app() -> Starlette:
        from key_value.aio.stores.memory import MemoryStore

        auth = _UnderScopedOAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=_UnderScopedTokenVerifier(["openid"]),
            base_url="http://localhost:8000",
            valid_scopes=["openid", "email", "calendar"],
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )
        return FastMCP("Test Server", auth=auth).http_app()

    @pytest.fixture
    def jwt_verifier(self, rsa_key_pair):
        """Create JWT verifier for testing."""
        return JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.com",
            audience="https://test.com",
            base_url="https://test.com",
        )

    def test_missing_auth_no_error_attribute(self, jwt_verifier):
        """Test that missing auth returns 401 without error attribute (RFC 6750 §3.1)."""
        server = FastMCP("Test Server")

        @server.tool
        def test_tool() -> str:
            return "test"

        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=jwt_verifier,
        )

        with TestClient(app) as client:
            # Request without Authorization header
            response = client.post("/mcp")

            assert response.status_code == 401
            assert "www-authenticate" in response.headers

            # Per RFC 6750 §3.1: no error attribute when auth is missing
            www_auth = response.headers["www-authenticate"]
            assert "error=" not in www_auth
            assert response.content == b""

    def test_missing_auth_challenge_includes_supported_scopes(self):
        app = self.create_scoped_app(
            required_scopes=["read"],
            scopes_supported=["api://client-id/read"],
            challenge_scopes=["api://client-id/read"],
        )

        with TestClient(app) as client:
            response = client.post("/mcp")

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == (
            'Bearer scope="api://client-id/read", '
            'resource_metadata="http://localhost:8000/'
            '.well-known/oauth-protected-resource/mcp"'
        )

    def test_insufficient_scope_challenge_includes_supported_scopes(self):
        app = self.create_scoped_app(
            required_scopes=["read"],
            scopes_supported=["api://client-id/read"],
            challenge_scopes=["api://client-id/read"],
        )

        with TestClient(app) as client:
            response = client.post("/mcp", headers={"Authorization": "Bearer narrow"})

        assert response.status_code == 403
        assert response.headers["www-authenticate"] == (
            'Bearer error="insufficient_scope", '
            'error_description="Required scope: read", '
            'scope="api://client-id/read", '
            'resource_metadata="http://localhost:8000/'
            '.well-known/oauth-protected-resource/mcp"'
        )

    def test_missing_auth_challenge_uses_required_scope_with_empty_catalog(self):
        app = self.create_scoped_app(required_scopes=["read"], scopes_supported=[])

        with TestClient(app) as client:
            response = client.post("/mcp")

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == (
            'Bearer scope="read", resource_metadata="http://localhost:8000/'
            '.well-known/oauth-protected-resource/mcp"'
        )

    def test_remote_missing_auth_challenge_excludes_optional_catalog_scopes(self):
        app = self.create_scoped_app(
            required_scopes=["read"],
            scopes_supported=["read", "admin"],
        )

        with TestClient(app) as client:
            response = client.post("/mcp")
            metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()

        assert response.status_code == 401
        assert 'scope="read"' in response.headers["www-authenticate"]
        assert "admin" not in response.headers["www-authenticate"]
        assert metadata["scopes_supported"] == ["read", "admin"]

    def test_remote_insufficient_scope_challenge_excludes_optional_catalog_scopes(
        self,
    ):
        app = self.create_scoped_app(
            required_scopes=["read"],
            scopes_supported=["read", "admin"],
        )

        with TestClient(app) as client:
            response = client.post("/mcp", headers={"Authorization": "Bearer narrow"})

        assert response.status_code == 403
        assert 'scope="read"' in response.headers["www-authenticate"]
        assert "admin" not in response.headers["www-authenticate"]

    def test_oauth_missing_auth_challenge_excludes_optional_scopes(self):
        app = self.create_oauth_app()

        with TestClient(app) as client:
            response = client.post("/mcp")
            metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()

        assert response.status_code == 401
        assert 'scope="openid"' in response.headers["www-authenticate"]
        assert "email" not in response.headers["www-authenticate"]
        assert "calendar" not in response.headers["www-authenticate"]
        assert metadata["scopes_supported"] == ["openid", "email", "calendar"]

    def test_oauth_insufficient_scope_challenge_excludes_optional_scopes(self):
        app = self.create_oauth_app()

        with TestClient(app) as client:
            response = client.post("/mcp", headers={"Authorization": "Bearer narrow"})

        assert response.status_code == 403
        assert 'scope="openid"' in response.headers["www-authenticate"]
        assert "email" not in response.headers["www-authenticate"]
        assert "calendar" not in response.headers["www-authenticate"]

    def test_invalid_token_enhanced_error_message(self, jwt_verifier):
        """Test that invalid_token errors have enhanced error messages."""
        server = FastMCP("Test Server")

        @server.tool
        def test_tool() -> str:
            return "test"

        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=jwt_verifier,
        )

        with TestClient(app) as client:
            # Request WITH an invalid Authorization header
            response = client.post(
                "/mcp", headers={"Authorization": "Bearer invalid-token"}
            )

            assert response.status_code == 401
            assert "www-authenticate" in response.headers

            # Check enhanced error message
            data = response.json()
            assert data["error"] == "invalid_token"
            # Should have enhanced description with resolution steps
            assert "clear authentication tokens" in data["error_description"]
            assert "automatically re-register" in data["error_description"]

    def test_invalid_token_www_authenticate_header_format(self, jwt_verifier):
        """Test that invalid token WWW-Authenticate header includes error attribute."""
        server = FastMCP("Test Server")
        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=jwt_verifier,
        )

        with TestClient(app) as client:
            # Request WITH an invalid token
            response = client.post(
                "/mcp", headers={"Authorization": "Bearer invalid-token"}
            )

            assert response.status_code == 401
            www_auth = response.headers["www-authenticate"]

            # Should follow Bearer challenge format with error
            assert www_auth.startswith("Bearer ")
            assert 'error="invalid_token"' in www_auth
            assert "error_description=" in www_auth

    def test_insufficient_scope_not_enhanced(self, rsa_key_pair):
        """Test that insufficient_scope errors are not modified."""
        # Create a valid token with wrong scopes
        jwt_verifier = JWTVerifier(
            public_key=rsa_key_pair.public_key,
            issuer="https://test.com",
            audience="https://test.com",
            base_url="https://test.com",
        )

        server = FastMCP("Test Server")

        @server.tool
        def test_tool() -> str:
            return "test"

        app = create_streamable_http_app(
            server=server,
            streamable_http_path="/mcp",
            auth=jwt_verifier,
        )

        # Note: Testing insufficient_scope would require mocking the verifier
        # to return a token with wrong scopes. For now, we verify the middleware
        # is properly in place by checking it rejects unauthenticated requests.
        with TestClient(app) as client:
            response = client.post("/mcp")
            # Without a valid token, we get invalid_token
            assert response.status_code == 401


class TestContentNegotiation:
    """Tests for content negotiation in error responses."""

    @pytest.fixture
    def oauth_proxy(self, rsa_key_pair):
        """Create OAuth proxy for testing."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=JWTVerifier(
                public_key=rsa_key_pair.public_key,
                issuer="https://test.com",
                audience="https://test.com",
                base_url="https://test.com",
            ),
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=MemoryStore(),
        )

    def test_html_preferred_when_both_accepted(self, oauth_proxy):
        """Test that HTML is preferred when both text/html and application/json are accepted."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                },
                headers={"Accept": "text/html,application/json"},
            )

            # Should prefer HTML
            assert response.status_code == 400
            assert "text/html" in response.headers["content-type"]

    def test_json_when_only_json_accepted(self, oauth_proxy):
        """Test that JSON is returned when only application/json is accepted."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                },
                headers={"Accept": "application/json"},
            )

            assert response.status_code == 400
            assert "application/json" in response.headers["content-type"]

    def test_json_when_no_accept_header(self, oauth_proxy):
        """Test that JSON is returned when no Accept header is provided."""
        app = Starlette(routes=oauth_proxy.get_routes())

        with TestClient(app) as client:
            response = client.get(
                "/authorize",
                params={
                    "client_id": "unregistered-client-id",
                    "redirect_uri": "http://localhost:12345/callback",
                    "response_type": "code",
                    "code_challenge": "test-challenge",
                },
            )

            # Without Accept header, should return JSON (API default)
            assert response.status_code == 400
            assert "application/json" in response.headers["content-type"]
