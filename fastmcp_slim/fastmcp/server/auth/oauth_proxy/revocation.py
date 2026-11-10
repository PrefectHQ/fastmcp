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

if TYPE_CHECKING:
    from fastmcp.server.auth.oauth_proxy.proxy import OAuthProxy


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
        validated = await self._proxy.load_access_token(token)
        if validated is None:
            return None
        try:
            payload = self._proxy.jwt_issuer.verify_token(token)
        except (JoseError, ValueError, KeyError):
            return None
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            return None
        return validated.model_copy(update={"client_id": client_id})

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return await self._proxy.load_refresh_token(client, refresh_token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._proxy.revoke_token(token)
