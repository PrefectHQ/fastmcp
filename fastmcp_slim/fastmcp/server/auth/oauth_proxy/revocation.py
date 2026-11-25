"""Revocation-only view of an OAuth proxy's tokens for the SDK ownership check."""

from __future__ import annotations

from typing import TYPE_CHECKING

from joserfc.errors import JoseError
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    IdentityAssertionParams,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fastmcp.server.auth.auth import AccessToken as FastMCPAccessToken
from fastmcp.server.auth.auth import TokenVerificationError

if TYPE_CHECKING:
    from fastmcp.server.auth.oauth_proxy.proxy import OAuthProxy


class RevocableAccessToken(FastMCPAccessToken):
    """An access token loaded by the revocation route.

    `token` is the upstream token, which every FastMCP access token of one
    grant shares. `fastmcp_jti` names the FastMCP token the client presented,
    so revocation can retire that token itself. When the grant's upstream
    token no longer validates, `token` is the FastMCP token and
    `upstream_live` is False: there is nothing upstream left to revoke, but
    the mapping must still go, or the next refresh would revive the token.
    """

    fastmcp_jti: str
    upstream_live: bool = True


class RevocationProvider:
    """Adapt only the operations used by the SDK's revocation handler.

    Normal token loads preserve the upstream user identity. The revocation
    ownership gate instead needs the downstream client from our verified JWT.
    Keeping this adapter private to the route avoids changing authentication
    results or mutating tokens cached by upstream verifiers.
    """

    def __init__(self, proxy: OAuthProxy) -> None:
        self._proxy = proxy

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self._proxy.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._proxy.register_client(client_info)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        return await self._proxy.authorize(client, params)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return await self._proxy.load_authorization_code(client, authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        return await self._proxy.exchange_authorization_code(client, authorization_code)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        return await self._proxy.exchange_refresh_token(client, refresh_token, scopes)

    async def exchange_identity_assertion(
        self, client: OAuthClientInformationFull, params: IdentityAssertionParams
    ) -> OAuthToken:
        return await self._proxy.exchange_identity_assertion(client, params)

    async def load_access_token(self, token: str) -> AccessToken | None:
        try:
            payload = self._proxy.jwt_issuer.verify_token(token)
        except (JoseError, ValueError, KeyError):
            return None
        client_id = payload.get("client_id")
        jti = payload.get("jti")
        if not isinstance(client_id, str) or not client_id:
            return None
        if not isinstance(jti, str) or not jti:
            return None
        try:
            validated = await self._proxy.load_access_token(token)
        except TokenVerificationError:
            validated = None
        if validated is None:
            # A genuine FastMCP token whose upstream token no longer validates
            # (a sibling's revocation, say) still has a mapping to retire.
            scope = payload.get("scope")
            exp = payload.get("exp")
            return RevocableAccessToken(
                token=token,
                client_id=client_id,
                scopes=scope.split() if isinstance(scope, str) else [],
                expires_at=int(exp) if isinstance(exp, (int, float)) else None,
                claims=payload,
                fastmcp_jti=jti,
                upstream_live=False,
            )
        return RevocableAccessToken(
            **{
                **validated.model_dump(exclude_none=True),
                "client_id": client_id,
                "fastmcp_jti": jti,
            }
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return await self._proxy.load_refresh_token(client, refresh_token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._proxy.revoke_token(token)
