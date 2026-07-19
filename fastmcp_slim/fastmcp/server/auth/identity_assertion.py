"""Server-side identity assertion (ID-JAG) support for FastMCP (SEP-990).

.. warning::
    **Beta Feature**: Identity assertion support is currently in beta. The API
    may change in future releases. Please report any issues you encounter.

SEP-990 defines an enterprise "on-behalf-of" flow. A corporate identity provider
(Okta, Entra, etc.) issues an *ID-JAG* (Identity Assertion JWT Authorization
Grant) that asserts an employee's identity to a specific MCP authorization
server. The client presents that ID-JAG at the token endpoint using the RFC 7523
``urn:ietf:params:oauth:grant-type:jwt-bearer`` grant (the RFC 8693 token-exchange
profile). This module validates the assertion and lets the authorization server
mint a short-lived access token carrying the asserted subject, with no refresh
token — the client re-exchanges a fresh ID-JAG instead, and revocation lives at
the IdP.

This module provides:

- ``IdentityAssertion``: a small pydantic config model attached to ``OAuthProxy``
  via the ``identity_assertion`` parameter.
- ``IdentityAssertionValidator``: validates an ID-JAG per RFC 7523 §3 and the
  SEP-990 processing rules, reusing FastMCP's :class:`JWTVerifier` for signature,
  issuer, audience, and expiry checks, and enforcing ``typ``, ``sub`` presence,
  and ``jti`` replay protection on top.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

import httpx2
from pydantic import BaseModel, Field, field_validator

from fastmcp.utilities.auth import decode_jwt_header
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.auth.providers.jwt import JWTVerifier

logger = get_logger(__name__)

#: RFC 7523 §2.1 authorization grant used to present the ID-JAG.
JWT_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

#: SEP-990 grant profile advertised in authorization server metadata.
ID_JAG_GRANT_PROFILE = "urn:ietf:params:oauth:grant-profile:id-jag"

#: SEP-990 §5.1: the ID-JAG's JOSE header ``typ`` MUST be this media type.
ID_JAG_TYP = "oauth-id-jag+jwt"

#: Asymmetric JWS algorithms JWTVerifier supports for JWKS-based verification.
SUPPORTED_ASSERTION_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)


class IdentityAssertion(BaseModel):
    """Configuration for server-side identity assertion (ID-JAG) support.

    When attached to an :class:`~fastmcp.server.auth.oauth_proxy.OAuthProxy` via the
    ``identity_assertion`` parameter, the proxy's token endpoint accepts the RFC 7523
    ``jwt-bearer`` grant carrying an ID-JAG issued by one of the ``trusted_issuers``,
    and mints a short-lived FastMCP access token for the asserted subject.

    Example:
        ```python
        from fastmcp.server.auth import OAuthProxy, IdentityAssertion

        auth = OAuthProxy(
            ...,
            identity_assertion=IdentityAssertion(
                trusted_issuers=["https://login.acme-corp.com"],
            ),
        )
        ```
    """

    trusted_issuers: list[str] = Field(
        ...,
        description=(
            "Issuer (`iss`) values the authorization server accepts on an ID-JAG. "
            "Each must exactly match the assertion's `iss` claim. For each issuer, "
            "the JWKS used to verify the assertion signature is discovered via OIDC "
            "(`{issuer}/.well-known/openid-configuration`) unless overridden in "
            "`jwks_uris`."
        ),
    )
    jwks_uris: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional explicit JWKS URI per issuer, keyed by the issuer string. When "
            "an issuer is absent here, its JWKS URI is discovered via OIDC. Provide "
            "this for issuers that do not publish an OIDC discovery document."
        ),
    )
    audience: str | None = Field(
        default=None,
        description=(
            "Expected `aud` value on the ID-JAG. When omitted, the audience is the "
            "authorization server's own issuer URL (its base URL), which is where the "
            "ID-JAG's `aud` must point per SEP-990. Override only when the IdP mints "
            "assertions bound to a different audience identifier."
        ),
    )
    required_scopes: list[str] | None = Field(
        default=None,
        description="Scopes that must be present on the issued access token.",
    )
    algorithm: str | None = Field(
        default=None,
        description=(
            "JWS signing algorithm the trusted issuers use (e.g. `ES256`, "
            "`PS256`). When omitted, verification defaults to `RS256`; IdPs "
            "signing with another algorithm must set this explicitly. When "
            "issuers use different algorithms, override per issuer with "
            "`algorithms`."
        ),
    )
    algorithms: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional per-issuer signing-algorithm override, keyed by the "
            "issuer string (mirroring `jwks_uris`). Issuers absent here fall "
            "back to `algorithm`."
        ),
    )
    access_token_expiry_seconds: int = Field(
        default=300,
        gt=0,
        description=(
            "Lifetime, in seconds, of the short-lived access token minted from an "
            "ID-JAG. SEP-990 relies on the client re-exchanging a fresh assertion, so "
            "this is intentionally short and no refresh token is issued."
        ),
    )

    @field_validator("trusted_issuers")
    @classmethod
    def _validate_trusted_issuers(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("identity_assertion.trusted_issuers must not be empty")
        for issuer in v:
            if not issuer or not issuer.strip():
                raise ValueError("trusted_issuers entries must be non-empty strings")
        return v

    @field_validator("algorithm")
    @classmethod
    def _validate_algorithm(cls, v: str | None) -> str | None:
        # Trusted issuers are verified via JWKS (public keys only), so the
        # algorithm must be one of the asymmetric JWS algorithms JWTVerifier
        # actually supports — HS* (shared-secret) has no JWKS equivalent, and
        # anything else (EdDSA, or a typo like RS999) would otherwise surface
        # as a 500 on the first exchange instead of a clean config error now.
        if v is not None and v not in SUPPORTED_ASSERTION_ALGORITHMS:
            supported = ", ".join(sorted(SUPPORTED_ASSERTION_ALGORITHMS))
            raise ValueError(
                f"Unsupported algorithm {v!r} for identity assertion: trusted "
                f"issuers are verified via JWKS, so algorithm must be one of "
                f"{supported}"
            )
        return v

    @field_validator("algorithms")
    @classmethod
    def _validate_algorithms(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is not None:
            for issuer, algorithm in v.items():
                if algorithm not in SUPPORTED_ASSERTION_ALGORITHMS:
                    supported = ", ".join(sorted(SUPPORTED_ASSERTION_ALGORITHMS))
                    raise ValueError(
                        f"Unsupported algorithm {algorithm!r} for issuer "
                        f"{issuer!r}: must be one of {supported}"
                    )
        return v


class IdentityAssertionError(Exception):
    """Raised when an ID-JAG fails validation.

    The message is for server-side logging only; the token endpoint maps this to a
    generic OAuth error response and does not leak the detail to the client.
    """


class IdentityAssertionValidator:
    """Validates ID-JAG assertions for the SEP-990 jwt-bearer grant.

    Reuses :class:`JWTVerifier` for signature, issuer, audience, and expiry checks
    (with JWKS fetching and caching), and layers on the SEP-990 processing rules
    that the generic verifier does not cover: the ``typ`` JOSE header, a mandatory
    ``sub``, and ``jti`` replay rejection.

    JTI replay protection mirrors :class:`CIMDAssertionValidator`: seen ``jti``
    values are cached until the assertion would expire anyway, with periodic
    cleanup and an emergency size cap. Like CIMD, the cache is per-process, so
    replay protection is not shared across horizontally-scaled workers or
    replicas; see the identity-assertion docs for the deployment caveat.
    """

    #: RFC 7523 recommends short-lived assertions; reject anything longer.
    MAX_ASSERTION_LIFETIME = 300  # 5 minutes
    #: Clock-skew tolerance for exp/iat checks.
    CLOCK_SKEW_SECONDS = 30

    def __init__(self, config: IdentityAssertion, audience: str):
        """Initialize the validator.

        Args:
            config: The identity assertion configuration.
            audience: The authorization server's own issuer URL; the ID-JAG's `aud`
                must match this unless `config.audience` overrides it.
        """
        self.config = config
        # Accept the audience both with and without a trailing slash: metadata
        # advertises the issuer exactly as pydantic renders base_url (a bare
        # domain gains a trailing slash), so an IdP that sets `aud` to the
        # advertised value verbatim must match, and so must one that strips it.
        if config.audience:
            self.audience: str | list[str] = config.audience
        else:
            base = audience.rstrip("/")
            self.audience = [base, base + "/"]

        self._jti_cache: dict[str, float] = {}
        self._jti_cache_max_size = 10000
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 60
        # One JWTVerifier per issuer, created lazily once the JWKS URI is known.
        self._verifiers: dict[str, JWTVerifier] = {}
        # OIDC discovery hardening: discovery runs before signature verification,
        # so a malformed-but-trusted-iss assertion can trigger an outbound HTTP
        # call. Serialize per-issuer lookups and back off after a failure so
        # concurrent or repeated garbage cannot amplify into request floods.
        self._discovery_locks: dict[str, asyncio.Lock] = {}
        self._discovery_failures: dict[str, float] = {}
        self._discovery_failure_cooldown = 30.0

    def _cleanup_expired_jtis(self) -> None:
        now = time.time()
        expired = [jti for jti, exp in self._jti_cache.items() if exp < now]
        for jti in expired:
            del self._jti_cache[jti]
        if expired:
            logger.debug("Cleaned up %d expired ID-JAG jtis from cache", len(expired))

    def _maybe_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup_expired_jtis()
            self._last_cleanup = now

    async def _discover_jwks_uri(self, issuer: str) -> str:
        """Discover an issuer's JWKS URI via OIDC discovery.

        Fetches ``{issuer}/.well-known/openid-configuration`` and returns its
        ``jwks_uri``. Trusted issuers are operator-configured, so this uses a
        plain fetch (consistent with how operator-configured JWKS URIs are
        treated elsewhere, including localhost issuers in development).
        """
        lock = self._discovery_locks.setdefault(issuer, asyncio.Lock())
        async with lock:
            failed_at = self._discovery_failures.get(issuer)
            if (
                failed_at is not None
                and time.monotonic() - failed_at < self._discovery_failure_cooldown
            ):
                raise IdentityAssertionError(
                    f"OIDC discovery for issuer {issuer!r} recently failed; backing off"
                )
            return await self._fetch_discovery(issuer)

    async def _fetch_discovery(self, issuer: str) -> str:
        """Perform the actual discovery fetch; caller holds the issuer lock."""
        config_url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            async with httpx2.AsyncClient() as client:
                response = await client.get(config_url, timeout=10.0)
                response.raise_for_status()
                body = response.json()
        except (httpx2.HTTPError, ValueError) as e:
            self._discovery_failures[issuer] = time.monotonic()
            raise IdentityAssertionError(
                f"OIDC discovery for issuer {issuer!r} failed: {e}"
            ) from e
        if not isinstance(body, dict):
            # Valid JSON that isn't an object (e.g. `[]` or a bare string) —
            # guard before .get() so a misbehaving discovery endpoint maps to
            # invalid_grant, not a 500 on every subsequent exchange.
            raise IdentityAssertionError(
                f"OIDC discovery document for issuer {issuer!r} is not a JSON object"
            )

        jwks_uri = body.get("jwks_uri")
        if not jwks_uri or not isinstance(jwks_uri, str):
            raise IdentityAssertionError(
                f"OIDC discovery document for issuer {issuer!r} has no jwks_uri"
            )
        return jwks_uri

    async def _get_verifier(self, issuer: str) -> JWTVerifier:
        from fastmcp.server.auth.providers.jwt import JWTVerifier as _JWTVerifier

        verifier = self._verifiers.get(issuer)
        if verifier is not None:
            return verifier

        jwks_uri = (self.config.jwks_uris or {}).get(issuer)
        if not jwks_uri:
            jwks_uri = await self._discover_jwks_uri(issuer)

        algorithm = (self.config.algorithms or {}).get(issuer, self.config.algorithm)
        verifier = _JWTVerifier(
            jwks_uri=jwks_uri,
            issuer=issuer,
            audience=self.audience,
            algorithm=algorithm,
        )
        self._verifiers[issuer] = verifier
        return verifier

    async def validate(
        self, assertion: str, *, client_id: str, resource_url: str | None
    ) -> dict:
        """Validate an ID-JAG and return its claims.

        Args:
            assertion: The compact-serialized ID-JAG JWT.
            client_id: The authenticated client presenting the assertion. Must
                match the assertion's signed `client_id` claim — checked before
                the jti is recorded as consumed, so an assertion presented by
                the wrong client is rejected without burning it for the right
                one.
            resource_url: This server's resource URL, if configured. Must match
                the assertion's signed `resource` claim, for the same reason.

        Returns:
            The verified claims (including `sub`, `iss`, and any `resource`/`scope`).

        Raises:
            IdentityAssertionError: If the assertion is invalid for any reason.
        """
        self._maybe_cleanup()

        # 1. typ header MUST be oauth-id-jag+jwt (SEP-990 §5.1).
        try:
            header = decode_jwt_header(assertion)
        except (ValueError, KeyError, IndexError) as e:
            raise IdentityAssertionError(f"Malformed assertion header: {e}") from e
        if not isinstance(header, dict):
            # A JSON-array/scalar header is valid JSON but not a JOSE header;
            # guard before .get() so this maps to invalid_grant, not a 500.
            raise IdentityAssertionError("Assertion JOSE header must be a JSON object")
        if header.get("typ") != ID_JAG_TYP:
            raise IdentityAssertionError(
                f"Assertion typ must be {ID_JAG_TYP!r}, got {header.get('typ')!r}"
            )

        # 2. iss must be a trusted issuer before we fetch any keys for it.
        try:
            unverified_claims = _decode_unverified_claims(assertion)
        except (ValueError, KeyError, IndexError) as e:
            raise IdentityAssertionError(f"Malformed assertion payload: {e}") from e
        if not isinstance(unverified_claims, dict):
            raise IdentityAssertionError("Assertion payload is not a JSON object")
        iss = unverified_claims.get("iss")
        if not iss or iss not in self.config.trusted_issuers:
            raise IdentityAssertionError(f"Untrusted assertion issuer: {iss!r}")

        # 3. Verify signature, iss, aud, and exp via JWTVerifier.
        verifier = await self._get_verifier(iss)
        access_token = await verifier.load_access_token(assertion)
        if access_token is None:
            raise IdentityAssertionError(
                "Assertion failed signature/issuer/audience/expiry validation"
            )
        claims = access_token.claims

        now = time.time()
        exp = _numeric_date_claim(claims, "exp")
        iat = _numeric_date_claim(claims, "iat")
        nbf = _numeric_date_claim(claims, "nbf")
        if exp is None:
            raise IdentityAssertionError("Assertion must include exp claim")
        if nbf is not None and nbf > now + self.CLOCK_SKEW_SECONDS:
            raise IdentityAssertionError("Assertion is not yet valid (nbf in future)")
        if iat is not None:
            if iat > now + self.CLOCK_SKEW_SECONDS:
                raise IdentityAssertionError("Assertion iat is in the future")
            if exp - iat > self.MAX_ASSERTION_LIFETIME:
                raise IdentityAssertionError(
                    f"Assertion lifetime too long (max {self.MAX_ASSERTION_LIFETIME}s)"
                )
        elif exp > now + self.MAX_ASSERTION_LIFETIME:
            raise IdentityAssertionError(
                f"Assertion exp too far in future (max {self.MAX_ASSERTION_LIFETIME}s)"
            )

        # 4. sub is mandatory (RFC 7523 §3) — it identifies the end user.
        sub = claims.get("sub")
        if not sub:
            raise IdentityAssertionError("Assertion must include sub claim")

        # 5. Required scopes on the issued access token derive from the assertion.
        if self.config.required_scopes:
            granted = set(_assertion_scopes(claims))
            missing = set(self.config.required_scopes) - granted
            if missing:
                raise IdentityAssertionError(
                    f"Assertion missing required scopes: {sorted(missing)}"
                )

        # 6. The signed client_id and resource claims bind the assertion to the
        # presenting client and this server. Checked here — before jti is
        # recorded as consumed below — so an assertion presented with the
        # wrong binding is rejected without burning replay protection for
        # whichever client/server it actually belongs to.
        assertion_client_id = claims.get("client_id")
        if not assertion_client_id or assertion_client_id != client_id:
            raise IdentityAssertionError(
                f"Assertion client_id {assertion_client_id!r} does not match "
                f"authenticated client {client_id!r}"
            )
        if resource_url is not None:
            assertion_resource = claims.get("resource")
            if not isinstance(assertion_resource, str) or not assertion_resource:
                raise IdentityAssertionError("Assertion is missing resource claim")
            if server_url_has_query(resource_url):
                claim_matches = assertion_resource.rstrip("/") == resource_url.rstrip(
                    "/"
                )
            else:
                claim_matches = normalize_resource_url(
                    assertion_resource
                ) == normalize_resource_url(resource_url)
            if not claim_matches:
                raise IdentityAssertionError(
                    f"Assertion resource {assertion_resource!r} does not match "
                    f"this server {resource_url!r}"
                )

        # 7. jti replay rejection (RFC 7523 §3). Must be a non-empty string —
        # an array/object jti is unhashable and would raise TypeError on the
        # cache lookup (a 500) instead of a clean invalid_grant.
        jti = claims.get("jti")
        if not jti or not isinstance(jti, str):
            raise IdentityAssertionError("Assertion must include a string jti claim")
        cached_exp = self._jti_cache.get(jti)
        if cached_exp is not None and cached_exp > now:
            raise IdentityAssertionError(f"Assertion replay detected: jti {jti} reused")

        # Enforce the cap BEFORE inserting so a rejected assertion never grows the
        # cache. A fresh jti that would exceed capacity is rejected outright (after
        # a cleanup pass to reclaim any expired entries first).
        if (
            jti not in self._jti_cache
            and len(self._jti_cache) >= self._jti_cache_max_size
        ):
            self._cleanup_expired_jtis()
            if len(self._jti_cache) >= self._jti_cache_max_size:
                logger.warning("ID-JAG jti cache at capacity, possible attack")
                raise IdentityAssertionError("Server overloaded, please retry")
        self._jti_cache[jti] = exp

        logger.debug("ID-JAG validated for subject=%s issuer=%s", sub, iss)
        return claims


def normalize_resource_url(url: str) -> str:
    """Normalize a resource URL by removing query parameters and trailing slashes.

    RFC 8707 allows clients to include query parameters in resource URLs, but
    the server's configured resource URL typically doesn't include them. This
    normalizes both sides for comparison by stripping query and fragment.
    """
    parsed = urlparse(str(url))
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", "")
    )


def server_url_has_query(url: str) -> bool:
    """Check if a URL has query parameters."""
    return bool(urlparse(str(url)).query)


def _numeric_date_claim(claims: dict, name: str) -> float | None:
    """Read a NumericDate claim (RFC 7519 §2), rejecting non-numeric values.

    A validly-signed assertion could still carry a malformed `exp`/`iat`/`nbf`
    (e.g. a string, from a misbehaving IdP); comparing against it directly
    would raise `TypeError` outside the validation-error path. `bool` is
    excluded even though it subclasses `int` in Python — `true`/`false` are
    not timestamps.
    """
    value = claims.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IdentityAssertionError(f"Assertion {name} claim must be a number")
    return float(value)


def _assertion_scopes(claims: dict) -> list[str]:
    """Extract the scopes an ID-JAG grants, from `scope` or `scp`."""
    scope = claims.get("scope")
    if isinstance(scope, str):
        return scope.split()
    scp = claims.get("scp")
    if isinstance(scp, list):
        return [str(s) for s in scp]
    if isinstance(scp, str):
        return scp.split()
    return []


def _decode_unverified_claims(token: str) -> dict:
    """Decode a JWT payload without verifying the signature.

    Used only to read the `iss` claim so we can select the trusted issuer's key
    before performing the real, signature-verifying decode.
    """
    import base64
    import json

    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))
