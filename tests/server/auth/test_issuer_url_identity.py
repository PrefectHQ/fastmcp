"""Tests that `issuer_url` is authoritative for authorization server identity.

Regression tests for #4610. `issuer_url` lets the OAuth issuer identity differ
from `base_url`, which is where the OAuth endpoints are actually mounted. The
issuer identity — the `issuer` field of the authorization server metadata, the
`iss` claim of minted tokens, and the RFC 9207 `iss` authorization response
parameter — must come from `issuer_url`, while every endpoint URL must keep
coming from `base_url`.

RFC 8414 §3.3 is the reason this matters: the protected resource metadata points
clients at `issuer_url`, the client performs discovery there, and the `issuer`
in the returned metadata must match the identifier used for discovery.
"""

import re
import time
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.identity_assertion import IdentityAssertion
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

# The server is mounted under /api, so its endpoints live at BASE_URL while its
# issuer identity is the root of the same host.
BASE_URL = "https://api.example.com/api"
ISSUER_URL = "https://api.example.com"

# Pydantic renders a bare-authority AnyHttpUrl with a trailing slash.
ISSUER = "https://api.example.com/"
BASE_URL_ISSUER = "https://api.example.com/api"


class _Verifier(TokenVerifier):
    """Minimal token verifier."""

    def __init__(self):
        self.required_scopes = ["read"]

    async def verify_token(self, token: str) -> AccessToken:
        return AccessToken(
            token=token,
            client_id="client-id",
            scopes=self.required_scopes,
            expires_at=int(time.time() + 3600),
        )


def build_proxy(issuer_url: str | None) -> OAuthProxy:
    """Build an OAuth proxy mounted at BASE_URL, optionally with a distinct issuer."""
    return OAuthProxy(
        upstream_authorization_endpoint="https://upstream.example.com/authorize",
        upstream_token_endpoint="https://upstream.example.com/token",
        upstream_revocation_endpoint="https://upstream.example.com/revoke",
        upstream_client_id="client-id",
        upstream_client_secret="client-secret",
        token_verifier=_Verifier(),
        base_url=BASE_URL,
        issuer_url=issuer_url,
        client_storage=MemoryStore(),
        jwt_signing_key="test-secret",
    )


def build_provider(issuer_url: str | None) -> InMemoryOAuthProvider:
    """Build a plain OAuth provider mounted at BASE_URL, optionally with a distinct issuer."""
    return InMemoryOAuthProvider(
        base_url=BASE_URL,
        issuer_url=issuer_url,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )


def build_mounted_app(auth_provider) -> Starlette:
    """Mount an authenticated FastMCP server under /api with well-known routes at root."""
    mcp = FastMCP("test-server", auth=auth_provider)
    mcp_app = mcp.http_app(path="/mcp")
    return Starlette(
        routes=[
            *auth_provider.get_well_known_routes(mcp_path="/mcp"),
            Mount("/api", app=mcp_app),
        ],
        lifespan=mcp_app.lifespan,
    )


async def fetch_json(auth_provider, path: str) -> dict:
    """Fetch a well-known document from a mounted authenticated server."""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=build_mounted_app(auth_provider)),
        base_url=ISSUER_URL,
    ) as client:
        response = await client.get(path)
        assert response.status_code == 200
        return response.json()


class TestOAuthProxyIssuerIdentity:
    """`OAuthProxy` (and therefore `OIDCProxy`) identity comes from `issuer_url`."""

    async def test_protected_resource_metadata_points_at_issuer_url(self):
        metadata = await fetch_json(
            build_proxy(ISSUER_URL), "/.well-known/oauth-protected-resource/api/mcp"
        )
        assert metadata["authorization_servers"] == [ISSUER]

    async def test_authorization_server_metadata_issuer_is_issuer_url(self):
        metadata = await fetch_json(
            build_proxy(ISSUER_URL), "/.well-known/oauth-authorization-server"
        )
        assert metadata["issuer"] == ISSUER

    @pytest.mark.parametrize(
        "field, expected",
        [
            ("authorization_endpoint", f"{BASE_URL}/authorize"),
            ("token_endpoint", f"{BASE_URL}/token"),
            ("registration_endpoint", f"{BASE_URL}/register"),
            ("revocation_endpoint", f"{BASE_URL}/revoke"),
        ],
    )
    async def test_endpoints_stay_on_base_url(self, field: str, expected: str):
        metadata = await fetch_json(
            build_proxy(ISSUER_URL), "/.well-known/oauth-authorization-server"
        )
        assert metadata[field] == expected

    async def test_minted_token_iss_claim_is_issuer_url(self):
        proxy = build_proxy(ISSUER_URL)
        # get_routes() configures the MCP path, which creates the JWT issuer.
        proxy.get_routes(mcp_path="/mcp")

        token = proxy.jwt_issuer.issue_access_token(
            client_id="client-id", scopes=["read"], jti="test-jti"
        )

        assert proxy.jwt_issuer.verify_token(token)["iss"] == ISSUER

    async def test_authorization_response_iss_matches_metadata_issuer(self):
        """RFC 9207: the `iss` on a client-facing response matches the metadata."""
        proxy = build_proxy(ISSUER_URL)
        redirect = "http://localhost:5100/callback"
        client = OAuthClientInformationFull(
            client_id="rfc9207-client",
            client_secret="s",
            redirect_uris=[AnyUrl(redirect)],
        )
        await proxy.register_client(client)
        consent_url = await proxy.authorize(
            client,
            AuthorizationParams(
                redirect_uri=AnyUrl(redirect),
                redirect_uri_provided_explicitly=True,
                state="client-state",
                code_challenge="challenge",
                scopes=["read"],
            ),
        )
        txn_id = parse_qs(urlparse(consent_url).query)["txn_id"][0]

        app = Starlette(routes=proxy.get_routes())
        with TestClient(app) as test_client:
            metadata = test_client.get("/.well-known/oauth-authorization-server").json()

            consent = test_client.get(f"/consent?txn_id={txn_id}")
            csrf_match = re.search(
                r"name=\"csrf_token\"\s+value=\"([^\"]+)\"", consent.text
            )
            assert csrf_match
            for name, value in consent.cookies.items():
                test_client.cookies.set(name, value)

            denial = test_client.post(
                "/consent",
                data={
                    "action": "deny",
                    "txn_id": txn_id,
                    "csrf_token": csrf_match.group(1),
                },
                follow_redirects=False,
            )

        assert denial.status_code in (302, 303)
        params = parse_qs(urlparse(denial.headers["location"]).query)
        assert params["iss"] == [ISSUER]
        assert params["iss"] == [metadata["issuer"]]

    @pytest.mark.parametrize(
        "issuer_url, expected",
        [(ISSUER_URL, ISSUER), (None, BASE_URL_ISSUER)],
    )
    def test_identity_assertion_audience_is_issuer_identifier(
        self, issuer_url: str | None, expected: str
    ):
        """SEP-990: an ID-JAG is bound to the server's advertised issuer.

        RFC 7523 §3 requires the `aud` to identify the authorization server,
        and an authorization server is identified by its issuer — the value
        published as `issuer` in the authorization server metadata.
        """
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://upstream.example.com/authorize",
            upstream_token_endpoint="https://upstream.example.com/token",
            upstream_client_id="client-id",
            upstream_client_secret="client-secret",
            token_verifier=_Verifier(),
            base_url=BASE_URL,
            issuer_url=issuer_url,
            client_storage=MemoryStore(),
            jwt_signing_key="test-secret",
            identity_assertion=IdentityAssertion(
                trusted_issuers=["https://login.example.com"]
            ),
        )

        validator = proxy._identity_assertion_validator
        assert validator is not None
        assert validator.audience == [expected.rstrip("/"), f"{expected.rstrip('/')}/"]


class TestOAuthProxyIssuerDefaults:
    """With `issuer_url` unset, identity falls back to `base_url` as before."""

    async def test_authorization_server_metadata_issuer_is_base_url(self):
        # base_url has a path, so RFC 8414 path-aware discovery applies.
        metadata = await fetch_json(
            build_proxy(None), "/.well-known/oauth-authorization-server/api"
        )
        assert metadata["issuer"] == BASE_URL_ISSUER

    async def test_protected_resource_metadata_points_at_base_url(self):
        metadata = await fetch_json(
            build_proxy(None), "/.well-known/oauth-protected-resource/api/mcp"
        )
        assert metadata["authorization_servers"] == [BASE_URL_ISSUER]

    async def test_minted_token_iss_claim_is_base_url(self):
        proxy = build_proxy(None)
        proxy.get_routes(mcp_path="/mcp")

        token = proxy.jwt_issuer.issue_access_token(
            client_id="client-id", scopes=["read"], jti="test-jti"
        )

        assert proxy.jwt_issuer.verify_token(token)["iss"] == BASE_URL_ISSUER


class TestOAuthProviderIssuerIdentity:
    """The plain `OAuthProvider` path behaves the same way."""

    async def test_authorization_server_metadata_issuer_is_issuer_url(self):
        metadata = await fetch_json(
            build_provider(ISSUER_URL), "/.well-known/oauth-authorization-server"
        )
        assert metadata["issuer"] == ISSUER

    async def test_protected_resource_metadata_points_at_issuer_url(self):
        metadata = await fetch_json(
            build_provider(ISSUER_URL),
            "/.well-known/oauth-protected-resource/api/mcp",
        )
        assert metadata["authorization_servers"] == [ISSUER]

    @pytest.mark.parametrize(
        "field, expected",
        [
            ("authorization_endpoint", f"{BASE_URL}/authorize"),
            ("token_endpoint", f"{BASE_URL}/token"),
            ("registration_endpoint", f"{BASE_URL}/register"),
            ("revocation_endpoint", f"{BASE_URL}/revoke"),
        ],
    )
    async def test_endpoints_stay_on_base_url(self, field: str, expected: str):
        metadata = await fetch_json(
            build_provider(ISSUER_URL), "/.well-known/oauth-authorization-server"
        )
        assert metadata[field] == expected

    async def test_issuer_defaults_to_base_url(self):
        # base_url has a path, so RFC 8414 path-aware discovery applies.
        metadata = await fetch_json(
            build_provider(None), "/.well-known/oauth-authorization-server/api"
        )
        assert metadata["issuer"] == BASE_URL_ISSUER
