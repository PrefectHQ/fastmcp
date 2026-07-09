"""Tests for server-side SEP-990 identity assertion (ID-JAG) support.

These exercise the OAuthProxy token endpoint end-to-end using a locally-minted
fake IdP JWT (a keypair is generated per test). The proxy's JWKS lookup is
served via httpx_mock, so no real network calls are made.
"""

import time

import httpx
import pytest
from joserfc import jwk, jwt
from key_value.aio.stores.memory import MemoryStore
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from pytest_httpx import HTTPXMock

from fastmcp import FastMCP
from fastmcp.server.auth import IdentityAssertion
from fastmcp.server.auth.identity_assertion import (
    ID_JAG_GRANT_PROFILE,
    ID_JAG_TYP,
    JWT_BEARER_GRANT_TYPE,
)
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from tests.server.auth.oauth_proxy.conftest import MockTokenVerifier

BASE_URL = "https://myserver.com"
ISSUER = "https://login.acme-corp.com"
JWKS_URI = "https://login.acme-corp.com/jwks"


def _idp_jwks(key_pair: RSAKeyPair) -> dict:
    """Build a JWKS document from an RSA key pair's public key."""
    public_key = jwk.import_key(key_pair.public_key, "RSA")
    data = public_key.as_dict()
    data["kid"] = "idp-key-1"
    data["alg"] = "RS256"
    return {"keys": [data]}


def _mint_id_jag(
    key_pair: RSAKeyPair,
    *,
    issuer: str = ISSUER,
    audience: str = BASE_URL,
    subject: str = "employee@acme-corp.com",
    typ: str = ID_JAG_TYP,
    jti: str = "jti-1",
    expires_in: int = 120,
    scope: str | None = None,
    include_iat: bool = True,
) -> str:
    """Mint a fake ID-JAG JWT with full control over header and claims."""
    now = int(time.time())
    header = {"alg": "RS256", "typ": typ, "kid": "idp-key-1"}
    payload: dict = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "exp": now + expires_in,
        "jti": jti,
    }
    if include_iat:
        payload["iat"] = now
    if scope is not None:
        payload["scope"] = scope
    signing_key = jwk.import_key(key_pair.private_key.get_secret_value(), "RSA")
    return jwt.encode(header, payload, signing_key, algorithms=["RS256"])


def _make_proxy(identity_assertion: IdentityAssertion | None) -> OAuthProxy:
    return OAuthProxy(
        upstream_authorization_endpoint="https://login.acme-corp.com/authorize",
        upstream_token_endpoint="https://login.acme-corp.com/token",
        upstream_client_id="upstream-client",
        upstream_client_secret="upstream-secret",
        token_verifier=MockTokenVerifier(),
        base_url=BASE_URL,
        jwt_signing_key="test-signing-key",
        client_storage=MemoryStore(),
        identity_assertion=identity_assertion,
    )


@pytest.fixture
def idp_key() -> RSAKeyPair:
    return RSAKeyPair.generate()


@pytest.fixture
def config() -> IdentityAssertion:
    # Explicit jwks_uris avoids OIDC discovery so only the JWKS fetch is mocked.
    return IdentityAssertion(
        trusted_issuers=[ISSUER],
        jwks_uris={ISSUER: JWKS_URI},
    )


async def _register_client(proxy: OAuthProxy) -> None:
    """Register the MCP client so the token endpoint can authenticate it."""
    await proxy.register_client(
        OAuthClientInformationFull(
            client_id="mcp-client",
            client_secret="mcp-secret",
            redirect_uris=[AnyUrl("http://localhost/callback")],
            grant_types=[JWT_BEARER_GRANT_TYPE],
        )
    )


async def _post_token(proxy: OAuthProxy, assertion: str) -> httpx.Response:
    """POST a jwt-bearer grant to the proxy's /token endpoint via an ASGI app."""
    await _register_client(proxy)
    app = FastMCP("ID-JAG Server", auth=proxy).http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return await client.post(
            "/token",
            data={
                "grant_type": JWT_BEARER_GRANT_TYPE,
                "assertion": assertion,
                "client_id": "mcp-client",
                "client_secret": "mcp-secret",
            },
        )


class TestIdentityAssertionConfig:
    def test_requires_trusted_issuers(self):
        with pytest.raises(ValueError):
            IdentityAssertion(trusted_issuers=[])

    def test_rejects_blank_issuer(self):
        with pytest.raises(ValueError):
            IdentityAssertion(trusted_issuers=["  "])

    def test_defaults(self):
        cfg = IdentityAssertion(trusted_issuers=[ISSUER])
        assert cfg.access_token_expiry_seconds == 300
        assert cfg.audience is None
        assert cfg.jwks_uris is None


class TestMetadataAdvertisement:
    async def _metadata(self, proxy: OAuthProxy) -> dict:
        app = FastMCP("ID-JAG Server", auth=proxy).http_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.get("/.well-known/oauth-authorization-server")
            return resp.json()

    async def test_advertises_grant_when_enabled(self, config: IdentityAssertion):
        proxy = _make_proxy(config)
        metadata = await self._metadata(proxy)
        assert JWT_BEARER_GRANT_TYPE in metadata["grant_types_supported"]
        assert (
            ID_JAG_GRANT_PROFILE in metadata["authorization_grant_profiles_supported"]
        )

    async def test_not_advertised_when_disabled(self):
        proxy = _make_proxy(None)
        metadata = await self._metadata(proxy)
        assert JWT_BEARER_GRANT_TYPE not in metadata["grant_types_supported"]
        assert metadata.get("authorization_grant_profiles_supported") is None


class TestTokenEndpoint:
    async def test_happy_path_issues_token(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, scope="read write")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"]
        # SEP-990: no refresh token is issued.
        assert body.get("refresh_token") is None
        assert body["expires_in"] == config.access_token_expiry_seconds

    async def test_issued_token_carries_subject(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        proxy.set_mcp_path("/mcp")
        assertion = _mint_id_jag(idp_key, subject="alice@acme-corp.com")

        resp = await _post_token(proxy, assertion)
        access_token = resp.json()["access_token"]

        # The FastMCP-issued token validates via the proxy and exposes the subject.
        loaded = await proxy.load_access_token(access_token)
        assert loaded is not None
        assert loaded.subject == "alice@acme-corp.com"

    async def test_asserted_subject_flows_into_auth_context(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        """The issued token, verified via the same path the bearer-auth middleware
        uses (`verify_token` -> `load_access_token`), exposes the asserted subject —
        which is exactly what `get_access_token()` returns to a tool."""
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        proxy.set_mcp_path("/mcp")
        assertion = _mint_id_jag(
            idp_key, subject="carol@acme-corp.com", scope="read write"
        )

        resp = await _post_token(proxy, assertion)
        access_token = resp.json()["access_token"]

        verified = await proxy.verify_token(access_token)
        assert verified is not None
        assert verified.subject == "carol@acme-corp.com"
        assert "read" in verified.scopes and "write" in verified.scopes

    async def test_grant_rejected_when_not_configured(
        self,
        idp_key: RSAKeyPair,
    ):
        proxy = _make_proxy(None)
        assertion = _mint_id_jag(idp_key)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_grant_type"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
class TestValidationMatrix:
    @pytest.fixture(autouse=True)
    def _mock_jwks(self, idp_key: RSAKeyPair, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))

    async def test_untrusted_issuer_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, issuer="https://evil.example.com")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_wrong_audience_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, audience="https://other-server.com")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_wrong_typ_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, typ="JWT")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_expired_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, expires_in=-10)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_replayed_jti_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, jti="replay-me")

        first = await _post_token(proxy, assertion)
        second = await _post_token(proxy, assertion)

        assert first.status_code == 200
        assert second.status_code == 401
        assert second.json()["error"] == "invalid_grant"

    async def test_wrong_signature_rejected(self, config: IdentityAssertion):
        # Sign with a different key than the one served in the JWKS.
        other_key = RSAKeyPair.generate()
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(other_key)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_missing_required_scope_rejected(self, idp_key: RSAKeyPair):
        config = IdentityAssertion(
            trusted_issuers=[ISSUER],
            jwks_uris={ISSUER: JWKS_URI},
            required_scopes=["admin"],
        )
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, scope="read")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"


class TestIssuerKeyDiscovery:
    async def test_jwks_discovered_via_oidc(
        self, idp_key: RSAKeyPair, httpx_mock: HTTPXMock
    ):
        # No explicit jwks_uris: the validator must discover the JWKS URI from
        # the issuer's OIDC configuration document.
        oidc_config_url = ISSUER.rstrip("/") + "/.well-known/openid-configuration"
        httpx_mock.add_response(url=oidc_config_url, json={"jwks_uri": JWKS_URI})
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))

        proxy = _make_proxy(IdentityAssertion(trusted_issuers=[ISSUER]))
        assertion = _mint_id_jag(idp_key)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 200
        assert resp.json()["access_token"]
