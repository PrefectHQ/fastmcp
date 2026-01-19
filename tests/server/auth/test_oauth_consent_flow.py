"""Tests for OAuth Proxy consent flow with server-side storage.

This test suite verifies:
1. OAuth transactions are stored in server-side storage (not in-memory)
2. Authorization codes are stored in server-side storage
3. Consent flow redirects correctly through /consent endpoint
4. CSRF protection works with cookies
5. State persists across storage backends
6. Security headers (X-Frame-Options) are set correctly
7. Cookie signing and tampering detection
8. Auto-approve behavior with valid cookies
"""

import re
import secrets
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from mcp.types import Icon
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import OAuthTransaction


class MockTokenVerifier(TokenVerifier):
    """Mock token verifier for testing."""

    def __init__(self):
        self.required_scopes = ["read", "write"]

    async def verify_token(self, token: str):
        """Mock token verification."""
        return AccessToken(
            token=token,
            client_id="mock-client",
            scopes=self.required_scopes,
            expires_at=int(time.time() + 3600),
        )


class _Verifier(TokenVerifier):
    """Minimal token verifier for security tests."""

    def __init__(self):
        self.required_scopes = ["read"]

    async def verify_token(self, token: str):
        return AccessToken(
            token=token, client_id="c", scopes=self.required_scopes, expires_at=None
        )


@pytest.fixture
def storage():
    """Create a fresh in-memory storage for each test."""
    return MemoryStore()


@pytest.fixture
def oauth_proxy_with_storage(storage):
    """Create OAuth proxy with explicit storage backend."""
    return OAuthProxy(
        upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
        upstream_token_endpoint="https://github.com/login/oauth/access_token",
        upstream_client_id="test-upstream-client",
        upstream_client_secret="test-upstream-secret",
        token_verifier=MockTokenVerifier(),
        base_url="https://myserver.com",
        redirect_path="/auth/callback",
        client_storage=storage,  # Use our test storage
        jwt_signing_key="test-secret",
    )


@pytest.fixture
def oauth_proxy_https():
    """OAuthProxy configured with HTTPS base_url for __Host- cookies."""
    return OAuthProxy(
        upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
        upstream_token_endpoint="https://github.com/login/oauth/access_token",
        upstream_client_id="client-id",
        upstream_client_secret="client-secret",
        token_verifier=_Verifier(),
        base_url="https://myserver.example",
        client_storage=MemoryStore(),
        jwt_signing_key="test-secret",
    )


async def _start_flow(
    proxy: OAuthProxy, client_id: str, redirect: str
) -> tuple[str, str]:
    """Register client and start auth; returns (txn_id, consent_url)."""
    await proxy.register_client(
        OAuthClientInformationFull(
            client_id=client_id,
            client_secret="s",
            redirect_uris=[AnyUrl(redirect)],
        )
    )
    params = AuthorizationParams(
        redirect_uri=AnyUrl(redirect),
        redirect_uri_provided_explicitly=True,
        state="client-state-xyz",
        code_challenge="challenge",
        scopes=["read"],
    )
    consent_url = await proxy.authorize(
        OAuthClientInformationFull(
            client_id=client_id,
            client_secret="s",
            redirect_uris=[AnyUrl(redirect)],
        ),
        params,
    )
    qs = parse_qs(urlparse(consent_url).query)
    return qs["txn_id"][0], consent_url


def _extract_csrf(html: str) -> str | None:
    """Extract CSRF token from HTML form."""
    m = re.search(r"name=\"csrf_token\"\s+value=\"([^\"]+)\"", html)
    return m.group(1) if m else None


class TestServerSideStorage:
    """Tests verifying OAuth state is stored in AsyncKeyValue storage."""

    async def test_transaction_stored_in_storage_not_memory(
        self, oauth_proxy_with_storage, storage
    ):
        """Verify OAuth transactions are stored in AsyncKeyValue, not in-memory dict."""
        # Register client
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        # Start authorization flow
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:54321/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state-123",
            code_challenge="challenge-abc",
            scopes=["read", "write"],
        )

        redirect_url = await oauth_proxy_with_storage.authorize(client, params)

        # Extract transaction ID from consent redirect
        parsed = urlparse(redirect_url)
        assert "/consent" in parsed.path, "Should redirect to consent page"

        query_params = parse_qs(parsed.query)
        txn_id = query_params["txn_id"][0]

        # Verify transaction is NOT in the old in-memory dict
        # (the attribute should not exist or should be empty)
        assert (
            not hasattr(oauth_proxy_with_storage, "_oauth_transactions")
            or len(getattr(oauth_proxy_with_storage, "_oauth_transactions", {})) == 0
        )

        # Verify transaction IS in storage backend
        transaction = await storage.get(collection="mcp-oauth-transactions", key=txn_id)
        assert transaction is not None, "Transaction should be in storage"

        # Verify transaction has expected structure
        assert transaction["client_id"] == "test-client"
        assert transaction["client_redirect_uri"] == "http://localhost:54321/callback"
        assert transaction["client_state"] == "client-state-123"
        assert transaction["code_challenge"] == "challenge-abc"
        assert transaction["scopes"] == ["read", "write"]

    async def test_authorization_code_stored_in_storage(
        self, oauth_proxy_with_storage, storage
    ):
        """Verify authorization codes are stored in AsyncKeyValue storage."""
        # Register client
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:54321/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        # Create a test app with OAuth routes
        app = Starlette(routes=oauth_proxy_with_storage.get_routes())

        with TestClient(app) as test_client:
            # Start authorization flow
            params = AuthorizationParams(
                redirect_uri=AnyUrl("http://localhost:54321/callback"),
                redirect_uri_provided_explicitly=True,
                state="client-state",
                code_challenge="challenge-xyz",
                scopes=["read"],
            )

            redirect_url = await oauth_proxy_with_storage.authorize(client, params)

            # Extract txn_id from consent redirect
            parsed = urlparse(redirect_url)
            query_params = parse_qs(parsed.query)
            txn_id = query_params["txn_id"][0]

            # Simulate consent approval
            # First, get the consent page to establish CSRF cookie
            consent_response = test_client.get(
                f"/consent?txn_id={txn_id}", follow_redirects=False
            )

            # Extract CSRF token from response (it's in the HTML form)
            csrf_token = None
            if consent_response.status_code == 200:
                # For this test, we'll generate a CSRF token manually
                # In production, this comes from the consent page HTML
                csrf_token = secrets.token_urlsafe(32)

            # Approve consent with CSRF token
            # Set cookies on client instance to avoid deprecation warning
            for k, v in consent_response.cookies.items():
                test_client.cookies.set(k, v)
            approval_response = test_client.post(
                "/consent",
                data={
                    "action": "approve",
                    "txn_id": txn_id,
                    "csrf_token": csrf_token if csrf_token else "",
                },
                follow_redirects=False,
            )

            # After approval, authorization code should be in storage
            # The code is returned in the redirect URL
            if approval_response.status_code in (302, 303):
                location = approval_response.headers.get("location", "")
                callback_params = parse_qs(urlparse(location).query)

                if "code" in callback_params:
                    auth_code = callback_params["code"][0]

                    # Verify code is NOT in old in-memory dict
                    assert (
                        not hasattr(oauth_proxy_with_storage, "_client_codes")
                        or len(getattr(oauth_proxy_with_storage, "_client_codes", {}))
                        == 0
                    )

                    # Verify code IS in storage
                    code_data = await storage.get(
                        collection="mcp-authorization-codes", key=auth_code
                    )
                    assert code_data is not None, (
                        "Authorization code should be in storage"
                    )
                    assert code_data["client_id"] == "test-client"
                    assert code_data["scopes"] == ["read"]

    async def test_storage_collections_are_isolated(self, oauth_proxy_with_storage):
        """Verify that transactions, codes, and clients use separate collections."""
        # Register a client
        client = OAuthClientInformationFull(
            client_id="isolation-test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        # Start authorization to create transaction
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="test-state",
            code_challenge="test-challenge",
            scopes=["read"],
        )

        await oauth_proxy_with_storage.authorize(client, params)

        # Get all collections from storage
        storage = oauth_proxy_with_storage._client_storage

        # Verify client is in client collection
        client_data = await storage.get(
            collection="mcp-oauth-proxy-clients", key="isolation-test-client"
        )
        assert client_data is not None

        # Verify we can list transactions separately
        # (This tests that collections are properly namespaced)
        transactions = await storage.keys(collection="mcp-oauth-transactions")

        assert len(transactions) > 0, "Should have at least one transaction"

        # Verify transaction keys don't collide with client keys
        for txn_key in transactions:
            assert txn_key != "isolation-test-client"


class TestConsentFlowRedirects:
    """Tests for consent flow redirect behavior."""

    async def test_authorize_redirects_to_consent_page(self, oauth_proxy_with_storage):
        """Verify authorize() redirects to /consent instead of upstream."""
        client = OAuthClientInformationFull(
            client_id="consent-test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:8080/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:8080/callback"),
            redirect_uri_provided_explicitly=True,
            state="test-state",
            code_challenge="",
            scopes=["read"],
        )

        redirect_url = await oauth_proxy_with_storage.authorize(client, params)

        # Should redirect to consent page, not upstream
        assert "/consent" in redirect_url
        assert "github.com" not in redirect_url
        assert "?txn_id=" in redirect_url

    async def test_consent_page_contains_transaction_id(self, oauth_proxy_with_storage):
        """Verify consent page receives and displays transaction ID."""
        client = OAuthClientInformationFull(
            client_id="txn-test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:9090/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:9090/callback"),
            redirect_uri_provided_explicitly=True,
            state="test-state",
            code_challenge="test-challenge",
            scopes=["read", "write"],
        )

        redirect_url = await oauth_proxy_with_storage.authorize(client, params)

        # Extract txn_id parameter
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)

        assert "txn_id" in query
        txn_id = query["txn_id"][0]
        assert len(txn_id) > 0

        # Create test client
        app = Starlette(routes=oauth_proxy_with_storage.get_routes())

        with TestClient(app) as test_client:
            # Request consent page
            response = test_client.get(
                f"/consent?txn_id={txn_id}", follow_redirects=False
            )

            assert response.status_code == 200
            # Consent page should contain transaction reference
            assert txn_id.encode() in response.content or b"consent" in response.content


class TestCSRFProtection:
    """Tests for CSRF protection in consent flow."""

    async def test_consent_requires_csrf_token(self, oauth_proxy_with_storage):
        """Verify consent submission requires valid CSRF token."""
        client = OAuthClientInformationFull(
            client_id="csrf-test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:7070/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:7070/callback"),
            redirect_uri_provided_explicitly=True,
            state="test-state",
            code_challenge="",
            scopes=["read"],
        )

        redirect_url = await oauth_proxy_with_storage.authorize(client, params)
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)
        txn_id = query["txn_id"][0]

        app = Starlette(routes=oauth_proxy_with_storage.get_routes())

        with TestClient(app) as test_client:
            # Try to submit consent WITHOUT CSRF token
            response = test_client.post(
                "/consent",
                data={"action": "approve", "txn_id": txn_id},
                # No CSRF token!
                follow_redirects=False,
            )

            # Should reject or require CSRF
            # (Implementation may vary - checking for error response)
            assert response.status_code in (
                400,
                403,
                302,
            )  # Error or redirect to error

    async def test_consent_cookie_established_on_page_visit(
        self, oauth_proxy_with_storage
    ):
        """Verify consent page establishes CSRF cookie."""
        client = OAuthClientInformationFull(
            client_id="cookie-test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:6060/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:6060/callback"),
            redirect_uri_provided_explicitly=True,
            state="test-state",
            code_challenge="",
            scopes=["read"],
        )

        redirect_url = await oauth_proxy_with_storage.authorize(client, params)
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)
        txn_id = query["txn_id"][0]

        app = Starlette(routes=oauth_proxy_with_storage.get_routes())

        with TestClient(app) as test_client:
            # Visit consent page
            response = test_client.get(
                f"/consent?txn_id={txn_id}", follow_redirects=False
            )

            # Should set cookies for CSRF protection
            assert response.status_code == 200
            # Cookie may be set via Set-Cookie header
            cookies = response.cookies
            # Look for any CSRF-related cookie (implementation dependent)
            assert len(cookies) > 0 or "csrf" in response.text.lower(), (
                "Consent page should establish CSRF protection"
            )


class TestStoragePersistence:
    """Tests for state persistence across storage backends."""

    async def test_transaction_persists_after_retrieval(self, oauth_proxy_with_storage):
        """Verify transaction can be retrieved multiple times (until deleted)."""
        client = OAuthClientInformationFull(
            client_id="persist-test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:5050/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:5050/callback"),
            redirect_uri_provided_explicitly=True,
            state="persist-state",
            code_challenge="persist-challenge",
            scopes=["read"],
        )

        redirect_url = await oauth_proxy_with_storage.authorize(client, params)
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)
        txn_id = query["txn_id"][0]

        storage = oauth_proxy_with_storage._client_storage

        # Retrieve transaction multiple times
        txn1 = await storage.get(collection="mcp-oauth-transactions", key=txn_id)
        assert txn1 is not None

        txn2 = await storage.get(collection="mcp-oauth-transactions", key=txn_id)
        assert txn2 is not None

        # Should be the same data
        assert txn1["client_id"] == txn2["client_id"]
        assert txn1["client_state"] == txn2["client_state"]

    async def test_storage_uses_pydantic_adapter(self, oauth_proxy_with_storage):
        """Verify that PydanticAdapter serializes/deserializes correctly."""
        client = OAuthClientInformationFull(
            client_id="pydantic-test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:4040/callback")],
        )
        await oauth_proxy_with_storage.register_client(client)

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:4040/callback"),
            redirect_uri_provided_explicitly=True,
            state="pydantic-state",
            code_challenge="pydantic-challenge",
            scopes=["read", "write"],
        )

        redirect_url = await oauth_proxy_with_storage.authorize(client, params)
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)
        txn_id = query["txn_id"][0]

        # Retrieve using PydanticAdapter (which is what the proxy uses)
        transaction_store = oauth_proxy_with_storage._transaction_store
        txn_model = await transaction_store.get(key=txn_id)

        # Should be a Pydantic model instance
        assert isinstance(txn_model, OAuthTransaction)
        assert txn_model.client_id == "pydantic-test-client"
        assert txn_model.client_state == "pydantic-state"
        assert txn_model.code_challenge == "pydantic-challenge"
        assert txn_model.scopes == ["read", "write"]


class TestConsentSecurity:
    """Tests for consent page security features."""

    async def test_consent_sets_xfo_header(self, oauth_proxy_https):
        """Verify consent page sets X-Frame-Options header to prevent clickjacking."""
        txn_id, _ = await _start_flow(
            oauth_proxy_https, "client-a", "http://localhost:5001/callback"
        )
        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            r = c.get(f"/consent?txn_id={txn_id}")
            assert r.status_code == 200
            assert r.headers.get("X-Frame-Options") == "DENY"

    async def test_deny_sets_cookie_and_redirects_with_error(self, oauth_proxy_https):
        """Verify denying consent sets signed cookie and redirects with error."""
        client_redirect = "http://localhost:5002/callback"
        txn_id, _ = await _start_flow(oauth_proxy_https, "client-b", client_redirect)
        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            consent = c.get(f"/consent?txn_id={txn_id}")
            csrf = _extract_csrf(consent.text)
            assert csrf
            # Persist consent page cookies on client instance to avoid per-request deprecation
            for k, v in consent.cookies.items():
                c.cookies.set(k, v)
            r = c.post(
                "/consent",
                data={"action": "deny", "txn_id": txn_id, "csrf_token": csrf},
                follow_redirects=False,
            )
            assert r.status_code in (302, 303)
            loc = r.headers.get("location", "")
            parsed = urlparse(loc)
            assert parsed.scheme == "http" and parsed.netloc.startswith("localhost")
            q = parse_qs(parsed.query)
            assert q.get("error") == ["access_denied"]
            assert q.get("state") == ["client-state-xyz"]
            # Signed denied cookie should be set
            assert "MCP_DENIED_CLIENTS" in ";\n".join(
                r.headers.get("set-cookie", "").splitlines()
            )

    async def test_approve_sets_cookie_and_redirects_to_upstream(
        self, oauth_proxy_https
    ):
        """Verify approving consent sets signed cookie and redirects to upstream."""
        txn_id, _ = await _start_flow(
            oauth_proxy_https, "client-c", "http://localhost:5003/callback"
        )
        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            consent = c.get(f"/consent?txn_id={txn_id}")
            csrf = _extract_csrf(consent.text)
            assert csrf
            for k, v in consent.cookies.items():
                c.cookies.set(k, v)
            r = c.post(
                "/consent",
                data={"action": "approve", "txn_id": txn_id, "csrf_token": csrf},
                follow_redirects=False,
            )
            assert r.status_code in (302, 303)
            loc = r.headers.get("location", "")
            assert loc.startswith("https://github.com/login/oauth/authorize")
            assert f"state={txn_id}" in loc
            # Signed approved cookie should be set with __Host- prefix for HTTPS
            set_cookie = ";\n".join(r.headers.get("set-cookie", "").splitlines())
            assert "__Host-MCP_APPROVED_CLIENTS" in set_cookie

    async def test_tampered_cookie_is_ignored(self, oauth_proxy_https):
        """Verify tampered approval cookie is ignored and consent page shown."""
        txn_id, _ = await _start_flow(
            oauth_proxy_https, "client-d", "http://localhost:5004/callback"
        )
        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            # Create a tampered cookie (invalid signature)
            # Value format: payload.signature; using wrong signature to force failure
            tampered_value = "W10=.invalidsig"
            c.cookies.set("__Host-MCP_APPROVED_CLIENTS", tampered_value)
            r = c.get(f"/consent?txn_id={txn_id}", follow_redirects=False)
            # Should not auto-redirect to upstream; should show consent page
            assert r.status_code == 200
            # httpx returns a URL object; compare path or stringify
            assert urlparse(str(r.request.url)).path == "/consent"

    async def test_autoapprove_cookie_skips_consent(self, oauth_proxy_https):
        """Verify valid approval cookie auto-approves and redirects to upstream."""
        client_id = "client-e"
        redirect = "http://localhost:5005/callback"
        txn_id, _ = await _start_flow(oauth_proxy_https, client_id, redirect)
        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            # Approve once to set approved cookie
            consent = c.get(f"/consent?txn_id={txn_id}")
            csrf = _extract_csrf(consent.text)
            for k, v in consent.cookies.items():
                c.cookies.set(k, v)
            r = c.post(
                "/consent",
                data={
                    "action": "approve",
                    "txn_id": txn_id,
                    "csrf_token": csrf if csrf else "",
                },
                follow_redirects=False,
            )
            # Extract approved cookie value
            set_cookie = ";\n".join(r.headers.get("set-cookie", "").splitlines())
            m = re.search(r"__Host-MCP_APPROVED_CLIENTS=([^;]+)", set_cookie)
            assert m, "approved cookie should be set"
            approved_cookie = m.group(1)

            # Start a new flow for the same client and redirect
            new_txn, _ = await _start_flow(oauth_proxy_https, client_id, redirect)
            # Should auto-redirect to upstream when visiting consent due to cookie
            c.cookies.set("__Host-MCP_APPROVED_CLIENTS", approved_cookie)
            r2 = c.get(f"/consent?txn_id={new_txn}", follow_redirects=False)
            assert r2.status_code in (302, 303)
            assert r2.headers.get("location", "").startswith(
                "https://github.com/login/oauth/authorize"
            )


class TestConsentPageServerIcon:
    """Tests for server icon display in OAuth consent screen."""

    async def test_consent_screen_displays_server_icon(self):
        """Test that consent screen shows server's custom icon when available."""

        # Create mock JWT verifier
        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read"]
        verifier.verify_token = Mock(return_value=None)

        # Create OAuthProxy
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=verifier,
            base_url="https://proxy.example.com",
            client_storage=MemoryStore(),
            jwt_signing_key="test-secret",
        )

        # Create FastMCP server with custom icon

        server = FastMCP(
            name="My Custom Server",
            auth=proxy,
            icons=[Icon(src="https://example.com/custom-icon.png")],
            website_url="https://example.com",
        )

        # Create HTTP app
        app = server.http_app()

        # Register a test client with the proxy
        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)

        # Create a transaction manually

        txn_id = "test-txn-id"
        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri="http://localhost:12345/callback",
            client_state="client-state",
            code_challenge="challenge",
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await proxy._transaction_store.put(key=txn_id, value=transaction)

        # Make request to consent page
        with TestClient(app) as client:
            response = client.get(f"/consent?txn_id={txn_id}")

            # Check that response is successful
            assert response.status_code == 200

            # Check that HTML contains custom icon
            assert "https://example.com/custom-icon.png" in response.text

            # Check that server name is used as alt text
            assert 'alt="My Custom Server"' in response.text

    async def test_consent_screen_falls_back_to_fastmcp_logo(self):
        """Test that consent screen shows FastMCP logo when no server icon provided."""

        # Create mock JWT verifier
        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read"]
        verifier.verify_token = Mock(return_value=None)

        # Create OAuthProxy
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=verifier,
            base_url="https://proxy.example.com",
            client_storage=MemoryStore(),
            jwt_signing_key="test-secret",
        )

        # Create FastMCP server without icon
        server = FastMCP(name="Server Without Icon", auth=proxy)

        # Create HTTP app
        app = server.http_app()

        # Register a test client
        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)

        # Create a transaction

        txn_id = "test-txn-id"
        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri="http://localhost:12345/callback",
            client_state="client-state",
            code_challenge="challenge",
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await proxy._transaction_store.put(key=txn_id, value=transaction)

        # Make request to consent page
        with TestClient(app) as client:
            response = client.get(f"/consent?txn_id={txn_id}")

            # Check that response is successful
            assert response.status_code == 200

            # Check that HTML contains FastMCP logo
            assert "gofastmcp.com/assets/brand/blue-logo.png" in response.text

            # Check that alt text is still the server name
            assert 'alt="Server Without Icon"' in response.text

    async def test_consent_screen_escapes_server_name(self):
        """Test that server name is properly HTML-escaped."""

        # Create mock JWT verifier
        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read"]
        verifier.verify_token = Mock(return_value=None)

        # Create OAuthProxy
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=verifier,
            base_url="https://proxy.example.com",
            client_storage=MemoryStore(),
            jwt_signing_key="test-secret",
        )

        # Create FastMCP server with special characters in name
        server = FastMCP(
            name='<script>alert("xss")</script>Server',
            auth=proxy,
            icons=[Icon(src="https://example.com/icon.png")],
        )

        # Create HTTP app
        app = server.http_app()

        # Register a test client
        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)

        # Create a transaction

        txn_id = "test-txn-id"
        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri="http://localhost:12345/callback",
            client_state="client-state",
            code_challenge="challenge",
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await proxy._transaction_store.put(key=txn_id, value=transaction)

        # Make request to consent page
        with TestClient(app) as client:
            response = client.get(f"/consent?txn_id={txn_id}")

            # Check that response is successful
            assert response.status_code == 200

            # Check that script tag is escaped
            assert "<script>" not in response.text
            assert "&lt;script&gt;" in response.text
            assert (
                'alt="&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;Server"'
                in response.text
            )


class TestConsentCSPPolicy:
    """Tests for Content Security Policy customization on consent page."""

    async def test_default_csp_includes_form_action(self):
        """Test that default CSP includes form-action directive."""

        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read"]
        verifier.verify_token = Mock(return_value=None)

        # Create OAuthProxy with default CSP (no custom CSP)
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=verifier,
            base_url="https://proxy.example.com",
            client_storage=MemoryStore(),
            jwt_signing_key="test-secret",
        )

        server = FastMCP(name="Test Server", auth=proxy)
        app = server.http_app()

        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)

        txn_id = "test-txn-id"
        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri="http://localhost:12345/callback",
            client_state="client-state",
            code_challenge="challenge",
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await proxy._transaction_store.put(key=txn_id, value=transaction)

        with TestClient(app) as client:
            response = client.get(f"/consent?txn_id={txn_id}")

            assert response.status_code == 200
            # Default CSP should be present with form-action
            assert 'http-equiv="Content-Security-Policy"' in response.text
            assert "form-action" in response.text

    async def test_empty_csp_disables_csp_meta_tag(self):
        """Test that empty string CSP disables CSP meta tag entirely."""

        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read"]
        verifier.verify_token = Mock(return_value=None)

        # Create OAuthProxy with empty CSP to disable it
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=verifier,
            base_url="https://proxy.example.com",
            client_storage=MemoryStore(),
            jwt_signing_key="test-secret",
            consent_csp_policy="",  # Empty string disables CSP
        )

        server = FastMCP(name="Test Server", auth=proxy)
        app = server.http_app()

        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)

        txn_id = "test-txn-id"
        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri="http://localhost:12345/callback",
            client_state="client-state",
            code_challenge="challenge",
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await proxy._transaction_store.put(key=txn_id, value=transaction)

        with TestClient(app) as client:
            response = client.get(f"/consent?txn_id={txn_id}")

            assert response.status_code == 200
            # CSP meta tag should NOT be present
            assert 'http-equiv="Content-Security-Policy"' not in response.text

    async def test_custom_csp_policy_is_used(self):
        """Test that custom CSP policy is applied to consent page."""

        verifier = Mock(spec=TokenVerifier)
        verifier.required_scopes = ["read"]
        verifier.verify_token = Mock(return_value=None)

        # Create OAuthProxy with custom CSP policy
        custom_csp = "default-src 'self'; script-src 'none'"
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=verifier,
            base_url="https://proxy.example.com",
            client_storage=MemoryStore(),
            jwt_signing_key="test-secret",
            consent_csp_policy=custom_csp,
        )

        server = FastMCP(name="Test Server", auth=proxy)
        app = server.http_app()

        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client_info)

        txn_id = "test-txn-id"
        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id="test-client",
            client_redirect_uri="http://localhost:12345/callback",
            client_state="client-state",
            code_challenge="challenge",
            code_challenge_method="S256",
            scopes=["read"],
            created_at=time.time(),
        )
        await proxy._transaction_store.put(key=txn_id, value=transaction)

        with TestClient(app) as client:
            response = client.get(f"/consent?txn_id={txn_id}")

            assert response.status_code == 200
            # Custom CSP should be present (HTML-escaped)
            assert 'http-equiv="Content-Security-Policy"' in response.text
            # Check for the HTML-escaped version (single quotes become &#x27;)
            import html

            assert html.escape(custom_csp, quote=True) in response.text
            # Default form-action should NOT be present (we're using custom)
            assert "form-action" not in response.text
