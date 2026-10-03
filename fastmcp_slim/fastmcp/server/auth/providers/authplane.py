"""Authplane authentication provider for FastMCP."""

from __future__ import annotations

from typing import Literal

from pydantic import AnyHttpUrl

from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# The only algorithms Authplane signs access tokens with. The AS's
# `signing.algorithm` config accepts exactly these two — ES256 (default) or
# RS256 — and its validator rejects anything else. (PS256 appears elsewhere in
# Authplane, but only as an accepted *DPoP proof* algorithm — a different
# verification context, not access-token signing.) HS256 and `none` are never
# issued and are always rejected server-side, so we never accept them here
# either, even though FastMCP's JWTVerifier would allow HS256. Restricting the
# accepted set to what the AS actually signs with closes off algorithm confusion.
AuthplaneAlgorithm = Literal["ES256", "RS256"]
_SUPPORTED_ALGORITHMS: frozenset[str] = frozenset({"ES256", "RS256"})


class _BearerOnlyJWTVerifier(JWTVerifier):
    """A `JWTVerifier` that refuses DPoP-bound (sender-constrained) tokens.

    Authplane can issue DPoP-bound access tokens (RFC 9449); those carry a `cnf`
    claim and are only safe to accept alongside a verified DPoP proof. This
    provider validates the bearer JWT only — it never sees the DPoP proof header
    (FastMCP's `verify_token(token)` hook receives just the token string), so
    accepting a `cnf`-bound token as a plain bearer would silently defeat the
    sender-constraint: a stolen token would be replayable from any machine.

    So any token carrying `cnf` is rejected here. Deployments that issue
    DPoP-bound tokens should use Authplane's `authplane-fastmcp` package, which
    verifies the proof. Tokens without `cnf` are unaffected.
    """

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = await super().load_access_token(token)
        if access is not None and "cnf" in access.claims:
            logger.warning(
                "Authplane: rejecting DPoP-bound token (cnf present). This "
                "provider validates bearer tokens only; use authplane-fastmcp "
                "for DPoP proof verification."
            )
            return None
        return access


class AuthplaneAuthProvider(RemoteAuthProvider):
    """Authplane authentication provider.

    `Authplane <https://github.com/AuthPlane/authserver>`_ is a self-hosted
    OAuth 2.1 authorization server for the Model Context Protocol, shipped as a
    single Go binary. It implements the MCP Authorization specification
    (2025-11-25): Dynamic Client Registration (RFC 7591), Client ID Metadata
    Documents, Resource Indicators (RFC 8707), and JWT access tokens (RFC 9068).

    Because Authplane supports DCR, MCP clients can register themselves at
    runtime — no pre-provisioned `client_id` is needed for the FastMCP server
    operator to hand out.

    This provider makes the FastMCP server a resource server: it verifies
    incoming JWTs against the Authplane JWKS and serves Protected Resource
    Metadata (RFC 9728) pointing clients at the Authplane instance.

    Audience binding
        Authplane audience-binds every access token to the resource URI the
        client asked for (RFC 8707), so a token minted for one MCP server cannot
        be replayed against another. This provider enforces that binding
        automatically: once FastMCP reports the path the MCP endpoint is mounted
        at, the verifier's expected audience is set to the resulting resource
        URL. Pass ``audience`` explicitly to override.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.authplane import AuthplaneAuthProvider

        auth = AuthplaneAuthProvider(
            issuer="https://auth.example.com",
            base_url="https://my-mcp-server.example.com",
            required_scopes=["tools/read"],
        )

        mcp = FastMCP("My App", auth=auth)
        ```

    Note:
        This provider validates JWTs against Authplane's JWKS, enforces scopes,
        and binds token audience to the resource (RFC 8707) — the common case,
        with no dependency beyond FastMCP. It validates *bearer* tokens only:
        a DPoP-bound token (RFC 9449, carrying a `cnf` claim) is rejected rather
        than accepted as a plain bearer, since this provider does not verify the
        DPoP proof and accepting one would defeat the sender-constraint. For
        inbound DPoP proof-of-possession, token introspection (RFC 7662),
        revocation checking (RFC 7009), RFC 8693 token exchange, and RFC 8414
        metadata discovery with background JWKS/metadata refresh, use Authplane's
        first-party ``authplane-fastmcp`` package — a drop-in ``FastMCP(**...)``
        backed by the full Authplane SDK.
    """

    def __init__(
        self,
        *,
        issuer: AnyHttpUrl | str,
        base_url: AnyHttpUrl | str,
        required_scopes: list[str] | str | None = None,
        scopes_supported: list[str] | str | None = None,
        audience: str | list[str] | None = None,
        algorithm: AuthplaneAlgorithm = "ES256",
        resource_base_url: AnyHttpUrl | str | None = None,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize the Authplane auth provider.

        Args:
            issuer: Base URL of the Authplane authorization server (e.g.
                "https://auth.example.com"). This is the `iss` claim value and
                the root of the RFC 8414 discovery document.
            base_url: Public URL of this FastMCP server.
            required_scopes: Scopes to require on incoming tokens. Defaults to
                none, leaving per-tool enforcement to the server.
            scopes_supported: Scopes to advertise in Protected Resource
                Metadata so clients know what to request. Defaults to
                `required_scopes`.
            audience: Expected `aud` claim. Defaults to the resource URL, which
                is what Authplane audience-binds tokens to. Set explicitly only
                when the deployment overrides resource indicators.
            algorithm: JWT signing algorithm to accept. Authplane signs access
                tokens with ES256 (the AS's default) or RS256 — those are the
                only two its `signing.algorithm` config permits. Only those two
                are accepted here; HS256 and `none` are never issued by Authplane
                and are rejected regardless of what the caller passes. Ignored
                when `token_verifier` is supplied.
            resource_base_url: Optional public base URL for the protected
                resource when it differs from `base_url` (e.g. behind a proxy).
            resource_name: Optional human-readable name for the resource.
            resource_documentation: Optional documentation URL for the resource.
            token_verifier: Optional custom token verifier. Defaults to a
                `JWTVerifier` pointed at Authplane's JWKS endpoint.
        """
        self.issuer = str(issuer).rstrip("/")

        parsed_required_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else []
        )
        parsed_scopes_supported = (
            parse_scopes(scopes_supported)
            if scopes_supported is not None
            else parsed_required_scopes or None
        )

        # Only bind the audience automatically when we own the verifier and the
        # caller did not pin one. A caller-supplied verifier is theirs to
        # configure; silently rewriting its audience would be surprising.
        self._bind_audience_to_resource = audience is None and token_verifier is None

        if token_verifier is None:
            # Runtime guard, not just the Literal hint: a dynamically supplied
            # string (e.g. from config) must not widen the accepted set to an
            # algorithm Authplane never signs with. Only enforced when we build
            # the verifier — a caller-supplied verifier owns its own policy.
            if algorithm not in _SUPPORTED_ALGORITHMS:
                raise ValueError(
                    f"Unsupported signing algorithm {algorithm!r}. Authplane "
                    f"signs access tokens only with {sorted(_SUPPORTED_ALGORITHMS)}."
                )
            token_verifier = _BearerOnlyJWTVerifier(
                jwks_uri=f"{self.issuer}/.well-known/jwks.json",
                issuer=self.issuer,
                algorithm=algorithm,
                required_scopes=parsed_required_scopes,
                audience=audience,
            )

        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(self.issuer)],
            base_url=AnyHttpUrl(str(base_url).rstrip("/")),
            scopes_supported=parsed_scopes_supported,
            resource_base_url=resource_base_url,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
        )

    def set_mcp_path(self, mcp_path: str | None) -> None:
        """Bind the expected token audience to this server's resource URL.

        Authplane issues tokens whose `aud` is the RFC 8707 resource indicator
        the client requested — the full MCP endpoint URL. That URL is only known
        once FastMCP reports where the endpoint is mounted, which is what this
        hook is for.
        """
        super().set_mcp_path(mcp_path)

        if not self._bind_audience_to_resource:
            return
        if self._resource_url is None:
            return
        if not isinstance(self.token_verifier, JWTVerifier):
            return

        resource_url = str(self._resource_url)
        self.token_verifier.audience = resource_url
        logger.info(
            "Authplane: bound expected token audience to resource URL %s",
            resource_url,
        )
