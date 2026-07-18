"""Hugging Face OAuth provider for FastMCP."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from json import JSONDecodeError
from typing import Any, Literal

import httpx2
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

HUGGINGFACE_AUTHORIZATION_ENDPOINT = "https://huggingface.co/oauth/authorize"
HUGGINGFACE_TOKEN_ENDPOINT = "https://huggingface.co/oauth/token"
HUGGINGFACE_USERINFO_ENDPOINT = "https://huggingface.co/oauth/userinfo"
HUGGINGFACE_WHOAMI_ENDPOINT = "https://huggingface.co/api/whoami-v2"

DEFAULT_HUGGINGFACE_SCOPES = ["openid", "profile"]


def _extract_scopes(data: Mapping[str, Any]) -> list[str]:
    scope_value = data.get("scope") or data.get("scopes")
    if isinstance(scope_value, str):
        return parse_scopes(scope_value) or []
    if isinstance(scope_value, list):
        return [str(scope).strip() for scope in scope_value if str(scope).strip()]

    auth = data.get("auth")
    if not isinstance(auth, Mapping):
        return []
    access_token = auth.get("accessToken")
    if not isinstance(access_token, Mapping):
        return []

    nested_scopes = access_token.get("scopes") or access_token.get("scope")
    if isinstance(nested_scopes, str):
        return parse_scopes(nested_scopes) or []
    if isinstance(nested_scopes, list):
        return [
            str(scope.get("name") if isinstance(scope, Mapping) else scope).strip()
            for scope in nested_scopes
            if str(scope.get("name") if isinstance(scope, Mapping) else scope).strip()
        ]
    return []


class HuggingFaceTokenVerifier(TokenVerifier):
    """Token verifier for Hugging Face OAuth access tokens.

    Hugging Face OAuth access tokens are opaque, so validation is performed by
    calling Hugging Face's userinfo endpoint.
    """

    def __init__(
        self,
        *,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        http_client: httpx2.AsyncClient | None = None,
    ):
        super().__init__(required_scopes=required_scopes)
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a Hugging Face OAuth token using the userinfo endpoint."""
        try:
            async with (
                contextlib.nullcontext(self._http_client)
                if self._http_client is not None
                else httpx2.AsyncClient(timeout=self.timeout_seconds)
            ) as client:
                userinfo_response = await client.get(
                    HUGGINGFACE_USERINFO_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "FastMCP-HuggingFace-OAuth",
                    },
                )
                if userinfo_response.status_code != 200:
                    logger.debug(
                        "Hugging Face token verification failed: %d",
                        userinfo_response.status_code,
                    )
                    return None

                userinfo = userinfo_response.json()
                sub = userinfo.get("sub")
                if not sub:
                    logger.debug("Hugging Face userinfo missing 'sub' claim")
                    return None

                token_scopes = _extract_scopes(userinfo)
                whoami: dict[str, Any] | None = None
                if not token_scopes or (
                    self.required_scopes
                    and not set(self.required_scopes).issubset(set(token_scopes))
                ):
                    whoami = await self._fetch_whoami(client, token)
                    if whoami:
                        token_scopes = list(
                            dict.fromkeys([*token_scopes, *_extract_scopes(whoami)])
                        )

                if not token_scopes:
                    token_scopes = list(DEFAULT_HUGGINGFACE_SCOPES)

                if self.required_scopes and not set(self.required_scopes).issubset(
                    set(token_scopes)
                ):
                    logger.debug(
                        "Hugging Face token missing required scopes. Has %d, needs %d",
                        len(token_scopes),
                        len(self.required_scopes),
                    )
                    return None

                username = (
                    userinfo.get("preferred_username")
                    or userinfo.get("nickname")
                    or userinfo.get("name")
                )
                return AccessToken(
                    token=token,
                    client_id=str(sub),
                    scopes=token_scopes,
                    expires_at=None,
                    claims={
                        "sub": str(sub),
                        "name": userinfo.get("name"),
                        "preferred_username": username,
                        "email": userinfo.get("email"),
                        "email_verified": userinfo.get("email_verified"),
                        "profile": userinfo.get("profile"),
                        "picture": userinfo.get("picture"),
                        "organizations": userinfo.get("organizations"),
                        "huggingface_userinfo": userinfo,
                        "huggingface_whoami": whoami,
                    },
                )

        except httpx2.RequestError as e:
            logger.debug("Failed to verify Hugging Face token: %s", e)
            return None
        except JSONDecodeError as e:
            logger.debug("Hugging Face token verification error: %s", e)
            return None

    async def _fetch_whoami(
        self, client: httpx2.AsyncClient, token: str
    ) -> dict[str, Any] | None:
        response = await client.get(
            HUGGINGFACE_WHOAMI_ENDPOINT,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "FastMCP-HuggingFace-OAuth",
            },
        )
        if response.status_code != 200:
            logger.debug("Hugging Face whoami lookup failed: %d", response.status_code)
            return None
        return response.json()


class HuggingFaceProvider(OAuthProxy):
    """Complete Hugging Face OAuth provider for FastMCP."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str | None = None,
        base_url: AnyHttpUrl | str,
        resource_base_url: AnyHttpUrl | str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        redirect_path: str | None = None,
        required_scopes: list[str] | None = None,
        valid_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool | Literal["remember", "external"] = True,
        consent_csp_policy: str | None = None,
        forward_resource: bool = True,
        fallback_refresh_token_expiry_seconds: int | None = None,
        fastmcp_access_token_expiry_seconds: int | None = None,
        token_expiry_threshold_seconds: int = 0,
        extra_authorize_params: dict[str, str] | None = None,
        extra_token_params: dict[str, str] | None = None,
        http_client: httpx2.AsyncClient | None = None,
        enable_cimd: bool = True,
    ):
        """Initialize Hugging Face OAuth provider.

        Args:
            client_id: Hugging Face OAuth app client ID. Public apps and CIMD
                client IDs are supported.
            client_secret: Hugging Face OAuth app client secret. Optional for
                public PKCE apps; when omitted, ``jwt_signing_key`` is required.
            base_url: Public URL where OAuth endpoints will be accessible.
            required_scopes: Required Hugging Face scopes. Defaults to
                ``["openid", "profile"]``.
            valid_scopes: Scopes clients may request. Defaults to required scopes.
            extra_authorize_params: Extra authorization parameters, such as
                ``{"orgIds": "your-org-id"}`` for organization grants.
        """
        required_scopes_final = (
            parse_scopes(required_scopes)
            if required_scopes is not None
            else list(DEFAULT_HUGGINGFACE_SCOPES)
        ) or []
        valid_scopes_final = parse_scopes(valid_scopes)

        # Do not pass provider-level required_scopes into the verifier here.
        # Hugging Face's userinfo endpoint validates opaque access tokens and
        # returns identity claims, but granted scopes are carried reliably in
        # the upstream token response. OAuthProxy stores those scopes, enforces
        # provider.required_scopes against FastMCP-issued tokens, and
        # _uses_alternate_verification() patches the stored upstream scopes
        # onto the returned AccessToken.
        token_verifier = HuggingFaceTokenVerifier(
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )

        super().__init__(
            upstream_authorization_endpoint=HUGGINGFACE_AUTHORIZATION_ENDPOINT,
            upstream_token_endpoint=HUGGINGFACE_TOKEN_ENDPOINT,
            upstream_client_id=client_id,
            upstream_client_secret=client_secret,
            token_verifier=token_verifier,
            base_url=base_url,
            resource_base_url=resource_base_url,
            redirect_path=redirect_path,
            issuer_url=issuer_url or base_url,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            consent_csp_policy=consent_csp_policy,
            forward_resource=forward_resource,
            fallback_refresh_token_expiry_seconds=fallback_refresh_token_expiry_seconds,
            fastmcp_access_token_expiry_seconds=fastmcp_access_token_expiry_seconds,
            token_expiry_threshold_seconds=token_expiry_threshold_seconds,
            extra_authorize_params=extra_authorize_params,
            extra_token_params=extra_token_params,
            token_endpoint_auth_method="client_secret_basic"
            if client_secret
            else "none",
            valid_scopes=valid_scopes_final,
            enable_cimd=enable_cimd,
        )

        logger.debug(
            "Initialized Hugging Face OAuth provider for client %s with scopes: %s",
            client_id,
            required_scopes_final,
        )

        self.required_scopes = required_scopes_final
        self.update_default_scopes(valid_scopes_final or required_scopes_final)

    def _uses_alternate_verification(self) -> bool:
        """Patch returned token scopes from the upstream token response.

        Hugging Face OAuth access tokens are opaque. The userinfo endpoint
        validates the token and returns identity claims, but scope information is
        carried by the token response stored in OAuthProxy's upstream token set.
        """
        return True
