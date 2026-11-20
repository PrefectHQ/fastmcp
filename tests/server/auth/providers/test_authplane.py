"""Tests for the Authplane auth provider."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import AnyHttpUrl

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.authplane import (
    AuthplaneAuthProvider,
    _BearerOnlyJWTVerifier,
)
from fastmcp.server.auth.providers.jwt import JWTVerifier

ISSUER = "https://auth.example.com"
BASE_URL = "https://mcp.example.com"


def make_provider(**kwargs: Any) -> AuthplaneAuthProvider:
    params: dict[str, Any] = {"issuer": ISSUER, "base_url": BASE_URL}
    params.update(kwargs)
    return AuthplaneAuthProvider(**params)


def jwt_verifier(provider: AuthplaneAuthProvider) -> JWTVerifier:
    """Narrow the provider's verifier to the concrete `JWTVerifier` it builds."""
    verifier = provider.token_verifier
    assert isinstance(verifier, JWTVerifier)
    return verifier


def bearer_verifier(provider: AuthplaneAuthProvider) -> _BearerOnlyJWTVerifier:
    """Narrow the provider's verifier to the `_BearerOnlyJWTVerifier` it builds."""
    verifier = provider.token_verifier
    assert isinstance(verifier, _BearerOnlyJWTVerifier)
    return verifier


class TestDefaults:
    def test_builds_jwt_verifier_against_authplane_jwks(self):
        provider = make_provider()

        verifier = provider.token_verifier
        assert isinstance(verifier, JWTVerifier)
        assert verifier.jwks_uri == f"{ISSUER}/.well-known/jwks.json"
        assert verifier.issuer == ISSUER
        assert verifier.algorithm == "ES256"

    def test_advertises_issuer_as_authorization_server(self):
        provider = make_provider()

        assert provider.authorization_servers == [AnyHttpUrl(ISSUER)]

    def test_no_scopes_are_required_by_default(self):
        provider = make_provider()

        assert provider.required_scopes == []

    def test_default_algorithm_matches_the_as_default(self):
        # Authplane's own signing default is ES256; the provider must default to
        # the same, or every token fails validation on a default install.
        provider = make_provider()

        assert jwt_verifier(provider).algorithm == "ES256"

    @pytest.mark.parametrize("alg", ["ES256", "RS256"])
    def test_supported_algorithms_are_accepted(self, alg):
        provider = make_provider(algorithm=alg)

        assert jwt_verifier(provider).algorithm == alg


class TestAlgorithmRestriction:
    """Only the two algorithms Authplane signs access tokens with are accepted."""

    @pytest.mark.parametrize("alg", ["HS256", "HS384", "HS512", "none", "PS256"])
    def test_unsupported_algorithms_are_rejected(self, alg):
        # HS256 is accepted by FastMCP's own JWTVerifier but is never issued by
        # Authplane; accepting it would open algorithm confusion. PS256 is a
        # valid DPoP-proof algorithm but Authplane never *signs access tokens*
        # with it, so it must not be accepted here either. Rejected before the
        # verifier is built.
        with pytest.raises(ValueError, match="Unsupported signing algorithm"):
            make_provider(algorithm=alg)

    def test_a_supplied_verifier_owns_its_algorithm_policy(self):
        # A caller who brings their own verifier is not subject to the guard —
        # the `algorithm` argument is ignored entirely, even if unsupported.
        custom = JWTVerifier(
            jwks_uri="https://elsewhere.example.com/keys",
            issuer=ISSUER,
            algorithm="ES256",
        )
        provider = make_provider(token_verifier=custom, algorithm="PS256")

        assert provider.token_verifier is custom
        assert custom.algorithm == "ES256"


def _fake_access_token(**claims: Any) -> AccessToken:
    return AccessToken(
        token="tok",
        client_id="client",
        scopes=list(claims.get("scope", "").split()),
        expires_at=None,
        claims=claims,
    )


class TestDpopBoundTokenRejection:
    """The default verifier accepts bearer tokens only; DPoP-bound (cnf) tokens
    are rejected rather than silently accepted as plain bearer."""

    def test_default_verifier_is_bearer_only(self):
        provider = make_provider()

        assert isinstance(provider.token_verifier, _BearerOnlyJWTVerifier)

    async def test_cnf_bound_token_is_rejected(self, monkeypatch):
        provider = make_provider()
        verifier = bearer_verifier(provider)
        bound = _fake_access_token(sub="u", scope="tools/read", cnf={"jkt": "abc"})

        async def fake_super(self, token):  # noqa: ANN001
            return bound

        # The parent JWTVerifier does all the real validation and returns a
        # valid token; our subclass must still reject it for carrying `cnf`.
        monkeypatch.setattr(JWTVerifier, "load_access_token", fake_super)

        assert await verifier.load_access_token("tok") is None

    async def test_plain_bearer_token_passes_through(self, monkeypatch):
        provider = make_provider()
        verifier = bearer_verifier(provider)
        plain = _fake_access_token(sub="u", scope="tools/read")

        async def fake_super(self, token):  # noqa: ANN001
            return plain

        monkeypatch.setattr(JWTVerifier, "load_access_token", fake_super)

        assert await verifier.load_access_token("tok") is plain

    async def test_invalid_token_stays_rejected(self, monkeypatch):
        # A token the parent rejects (None) must remain rejected.
        provider = make_provider()
        verifier = bearer_verifier(provider)

        async def fake_super(self, token):  # noqa: ANN001
            return None

        monkeypatch.setattr(JWTVerifier, "load_access_token", fake_super)

        assert await verifier.load_access_token("tok") is None

    async def test_supplied_verifier_is_not_wrapped(self, monkeypatch):
        # A caller-supplied verifier owns its own DPoP policy — we don't wrap it.
        custom = JWTVerifier(
            jwks_uri="https://elsewhere.example.com/keys",
            issuer=ISSUER,
        )
        provider = make_provider(token_verifier=custom)

        assert provider.token_verifier is custom
        assert not isinstance(provider.token_verifier, _BearerOnlyJWTVerifier)


class TestUrlNormalization:
    @pytest.mark.parametrize("issuer", [ISSUER, f"{ISSUER}/"])
    def test_trailing_slash_is_stripped_from_issuer(self, issuer):
        provider = make_provider(issuer=issuer)

        assert provider.issuer == ISSUER
        assert jwt_verifier(provider).jwks_uri == f"{ISSUER}/.well-known/jwks.json"

    def test_trailing_slash_is_stripped_from_base_url(self):
        provider = make_provider(base_url=f"{BASE_URL}/")

        assert str(provider.base_url).rstrip("/") == BASE_URL


class TestScopes:
    def test_space_delimited_required_scopes_are_parsed(self):
        provider = make_provider(required_scopes="tools/read tools/write")

        assert provider.required_scopes == ["tools/read", "tools/write"]

    def test_scopes_supported_defaults_to_required_scopes(self):
        provider = make_provider(required_scopes=["tools/read"])

        assert jwt_verifier(provider).required_scopes == ["tools/read"]
        assert provider._scopes_supported == ["tools/read"]

    def test_scopes_supported_can_exceed_required_scopes(self):
        provider = make_provider(
            required_scopes=["tools/read"],
            scopes_supported=["tools/read", "tools/write"],
        )

        assert provider.required_scopes == ["tools/read"]
        assert provider._scopes_supported == ["tools/read", "tools/write"]


class TestAudienceBinding:
    def test_audience_is_bound_to_the_resource_url(self):
        provider = make_provider()

        provider.get_routes(mcp_path="/mcp")

        assert jwt_verifier(provider).audience == f"{BASE_URL}/mcp"

    def test_explicit_audience_is_not_overwritten(self):
        provider = make_provider(audience="https://pinned.example.com/mcp")

        provider.get_routes(mcp_path="/mcp")

        assert jwt_verifier(provider).audience == "https://pinned.example.com/mcp"

    def test_resource_base_url_drives_the_bound_audience(self):
        provider = make_provider(resource_base_url="https://public.example.com")

        provider.get_routes(mcp_path="/mcp")

        assert jwt_verifier(provider).audience == "https://public.example.com/mcp"

    def test_audience_falls_back_to_base_url_when_no_mcp_path_is_known(self):
        provider = make_provider()

        provider.get_routes(mcp_path=None)

        # The invariant is that the expected audience always equals the
        # resource URL advertised in Protected Resource Metadata; with no
        # mounted path, that resource URL is base_url itself.
        assert jwt_verifier(provider).audience == str(provider._resource_url)
        assert str(jwt_verifier(provider).audience).rstrip("/") == BASE_URL


class TestCustomVerifier:
    def test_custom_verifier_is_used_as_is(self):
        custom = JWTVerifier(
            jwks_uri="https://elsewhere.example.com/keys",
            issuer=ISSUER,
            audience="https://pinned.example.com/mcp",
        )

        provider = make_provider(token_verifier=custom)

        assert provider.token_verifier is custom

    def test_custom_verifier_audience_is_never_rewritten(self):
        custom = JWTVerifier(
            jwks_uri="https://elsewhere.example.com/keys",
            issuer=ISSUER,
            audience="https://pinned.example.com/mcp",
        )
        provider = make_provider(token_verifier=custom)

        provider.get_routes(mcp_path="/mcp")

        assert custom.audience == "https://pinned.example.com/mcp"


class TestProtectedResourceMetadata:
    def test_prm_route_is_registered_for_the_mcp_path(self):
        provider = make_provider()

        paths = [route.path for route in provider.get_routes(mcp_path="/mcp")]

        assert "/.well-known/oauth-protected-resource/mcp" in paths

    def test_well_known_routes_are_a_subset_of_all_routes(self):
        provider = make_provider()

        well_known = provider.get_well_known_routes(mcp_path="/mcp")

        assert well_known
        assert all(route.path.startswith("/.well-known/") for route in well_known)
