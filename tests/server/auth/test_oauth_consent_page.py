"""Tests for OAuth Proxy consent page display, CSP policy, and consent binding cookie."""

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


class _Verifier(TokenVerifier):
    """Minimal token verifier for security tests."""

    def __init__(self):
        self.required_scopes = ["read"]

    async def verify_token(self, token: str):
        return AccessToken(
            token=token, client_id="c", scopes=self.required_scopes, expires_at=None
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


class TestConsentBindingCookie:
    """Tests for consent binding cookie that prevents confused deputy attacks.

    GHSA-rww4-4w9c-7733: Without browser-binding between consent approval and
    the IdP callback, an attacker can intercept the upstream authorization URL
    and send it to a victim whose browser completes the flow.
    """

    async def test_approve_sets_consent_binding_cookie(self, oauth_proxy_https):
        """Approving consent must set a signed consent binding cookie."""
        txn_id, _ = await _start_flow(
            oauth_proxy_https, "client-binding", "http://localhost:6001/callback"
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
            set_cookie_header = r.headers.get("set-cookie", "")
            assert "__Host-MCP_CONSENT_BINDING" in set_cookie_header

    async def test_auto_approve_sets_consent_binding_cookie(self, oauth_proxy_https):
        """Auto-approve path (previously approved client) must also set the binding cookie."""
        client_id = "client-autobinding"
        redirect = "http://localhost:6002/callback"
        txn_id, _ = await _start_flow(oauth_proxy_https, client_id, redirect)
        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            # First: approve manually to get the approved cookie
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
            # Extract approved cookie
            m = re.search(
                r"__Host-MCP_APPROVED_CLIENTS=([^;]+)",
                r.headers.get("set-cookie", ""),
            )
            assert m
            approved_cookie = m.group(1)

            # Second: start new flow, auto-approve should set binding cookie
            new_txn, _ = await _start_flow(oauth_proxy_https, client_id, redirect)
            c.cookies.set("__Host-MCP_APPROVED_CLIENTS", approved_cookie)
            r2 = c.get(f"/consent?txn_id={new_txn}", follow_redirects=False)
            assert r2.status_code in (302, 303)
            set_cookie_header = r2.headers.get("set-cookie", "")
            assert "__Host-MCP_CONSENT_BINDING" in set_cookie_header

    async def test_parallel_flows_do_not_interfere(self, oauth_proxy_https):
        """Multiple concurrent consent flows in the same browser must not clobber each other.

        Uses two different clients so the second flow also shows a consent form
        (auto-approve only kicks in for the same client+redirect pair).
        """
        txn1, _ = await _start_flow(
            oauth_proxy_https, "client-par-a", "http://localhost:6010/callback"
        )
        txn2, _ = await _start_flow(
            oauth_proxy_https, "client-par-b", "http://localhost:6011/callback"
        )
        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            # Approve first flow
            consent1 = c.get(f"/consent?txn_id={txn1}")
            csrf1 = _extract_csrf(consent1.text)
            assert csrf1
            for k, v in consent1.cookies.items():
                c.cookies.set(k, v)
            r1 = c.post(
                "/consent",
                data={"action": "approve", "txn_id": txn1, "csrf_token": csrf1},
                follow_redirects=False,
            )
            assert r1.status_code in (302, 303)
            for k, v in r1.cookies.items():
                c.cookies.set(k, v)

            # Approve second flow (different client, so consent form is shown)
            consent2 = c.get(f"/consent?txn_id={txn2}")
            csrf2 = _extract_csrf(consent2.text)
            assert csrf2
            for k, v in consent2.cookies.items():
                c.cookies.set(k, v)
            r2 = c.post(
                "/consent",
                data={"action": "approve", "txn_id": txn2, "csrf_token": csrf2},
                follow_redirects=False,
            )
            assert r2.status_code in (302, 303)
            for k, v in r2.cookies.items():
                c.cookies.set(k, v)

            # Both transactions should have consent tokens
            txn1_model = await oauth_proxy_https._transaction_store.get(key=txn1)
            txn2_model = await oauth_proxy_https._transaction_store.get(key=txn2)
            assert txn1_model is not None and txn1_model.consent_token
            assert txn2_model is not None and txn2_model.consent_token

            # First flow's callback should still work (cookie has both bindings)
            r_cb1 = c.get(
                f"/auth/callback?code=fake&state={txn1}", follow_redirects=False
            )
            # Should NOT be 403 — the binding for txn1 should still be in the cookie.
            # It will fail at token exchange (500) but not at consent verification.
            assert r_cb1.status_code != 403

    async def test_idp_callback_rejects_missing_consent_cookie(self, oauth_proxy_https):
        """IdP callback must reject requests without the consent binding cookie.

        This is the core confused deputy scenario: a different browser (the victim)
        hits the callback without the cookie that was set on the attacker's browser.
        """
        txn_id, _ = await _start_flow(
            oauth_proxy_https, "client-nocd", "http://localhost:6003/callback"
        )
        # Manually set consent_token on transaction (simulating consent approval)
        txn_model = await oauth_proxy_https._transaction_store.get(key=txn_id)
        assert txn_model is not None
        txn_model.consent_token = secrets.token_urlsafe(32)
        await oauth_proxy_https._transaction_store.put(
            key=txn_id, value=txn_model, ttl=15 * 60
        )

        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            # Hit callback WITHOUT the consent binding cookie
            r = c.get(
                f"/auth/callback?code=fake-code&state={txn_id}",
                follow_redirects=False,
            )
            assert r.status_code == 403
            assert (
                "session mismatch" in r.text.lower() or "Authorization Error" in r.text
            )

    async def test_idp_callback_rejects_wrong_consent_cookie(self, oauth_proxy_https):
        """IdP callback must reject requests with a tampered consent binding cookie."""
        txn_id, _ = await _start_flow(
            oauth_proxy_https, "client-wrongcd", "http://localhost:6004/callback"
        )
        txn_model = await oauth_proxy_https._transaction_store.get(key=txn_id)
        assert txn_model is not None
        txn_model.consent_token = secrets.token_urlsafe(32)
        await oauth_proxy_https._transaction_store.put(
            key=txn_id, value=txn_model, ttl=15 * 60
        )

        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            # Set a wrong/tampered consent binding cookie
            c.cookies.set("__Host-MCP_CONSENT_BINDING", "wrong-token.invalidsig")
            r = c.get(
                f"/auth/callback?code=fake-code&state={txn_id}",
                follow_redirects=False,
            )
            assert r.status_code == 403

    async def test_idp_callback_rejects_missing_consent_token_on_transaction(
        self, oauth_proxy_https
    ):
        """IdP callback must reject when transaction has no consent_token set."""
        txn_id, _ = await _start_flow(
            oauth_proxy_https, "client-notxntoken", "http://localhost:6005/callback"
        )
        # Transaction exists but consent_token is None (consent was never completed)
        app = Starlette(routes=oauth_proxy_https.get_routes())
        with TestClient(app) as c:
            r = c.get(
                f"/auth/callback?code=fake-code&state={txn_id}",
                follow_redirects=False,
            )
            assert r.status_code == 403

    async def test_consent_disabled_skips_binding_check(self):
        """When require_authorization_consent=False, the binding check is skipped."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=_Verifier(),
            base_url="https://myserver.example",
            client_storage=MemoryStore(),
            jwt_signing_key="test-secret",
            require_authorization_consent=False,
        )
        client_id = "client-noconsent"
        redirect = "http://localhost:6006/callback"
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
            state="st",
            code_challenge="ch",
            scopes=["read"],
        )
        upstream_url = await proxy.authorize(
            OAuthClientInformationFull(
                client_id=client_id,
                client_secret="s",
                redirect_uris=[AnyUrl(redirect)],
            ),
            params,
        )
        # With consent disabled, authorize returns upstream URL directly
        assert upstream_url.startswith("https://github.com/login/oauth/authorize")
        qs = parse_qs(urlparse(upstream_url).query)
        txn_id = qs["state"][0]

        # The transaction should have no consent_token
        txn_model = await proxy._transaction_store.get(key=txn_id)
        assert txn_model is not None
        assert txn_model.consent_token is None

        # IdP callback should NOT reject due to missing consent cookie
        # (it will fail at token exchange, but not at the consent check)
        app = Starlette(routes=proxy.get_routes())
        with TestClient(app) as c:
            r = c.get(
                f"/auth/callback?code=fake-code&state={txn_id}",
                follow_redirects=False,
            )
            # Should NOT be 403 (consent binding rejection)
            # It will be 500 because the fake code can't be exchanged with GitHub,
            # but that's fine — we're verifying the consent check was skipped.
            assert r.status_code != 403
