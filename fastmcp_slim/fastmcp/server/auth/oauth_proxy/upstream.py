"""httpx2-based upstream OAuth2 token client.

Replaces `authlib.integrations.httpx_client.AsyncOAuth2Client` for the OAuth
proxy's upstream token-endpoint calls. authlib's httpx integration imports the
legacy `httpx` package — which authlib does not declare as a dependency and
FastMCP no longer ships — so importing it on a clean install fails.

This module reimplements the narrow surface the proxy uses (`fetch_token`,
`refresh_token`, `client_secret`, `aclose`) on `httpx2.AsyncClient`, preserving
authlib's wire behavior exactly:

- form-encoded POST token requests with authlib's default headers
- `client_secret_basic` (latin-1 basic auth, authlib-style), `client_secret_post`,
  and `none` client authentication methods
- falsy parameters dropped from the request body
- `expires_at` computed onto the returned token dict
- the previous refresh token injected into the response when the server does
  not rotate it
- `OAuthError` (authlib's httpx-free core error class) raised for RFC 6749
  error responses, and 5xx responses raised as HTTP status errors
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx2
from authlib.integrations.base_client import OAuthError

__all__ = ["AsyncOAuth2Client", "OAuthError"]

_DEFAULT_TOKEN_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
}


class AsyncOAuth2Client:
    """Minimal async OAuth2 client for upstream token-endpoint interactions.

    Drop-in replacement for the slice of authlib's `AsyncOAuth2Client` that
    `OAuthProxy` uses. Subclasses of `OAuthProxy` that override
    `_create_upstream_oauth_client` may return any object with the same
    `fetch_token`/`refresh_token`/`client_secret`/`aclose` surface (including
    an authlib client, if legacy httpx is installed in their environment).
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str | None = None,
        token_endpoint_auth_method: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_endpoint_auth_method = (
            token_endpoint_auth_method or "client_secret_basic"
        )
        self._client = httpx2.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _apply_client_auth(self, data: dict[str, Any], headers: dict[str, str]) -> None:
        """Attach client credentials per the configured auth method (RFC 6749 §2.3)."""
        method = self.token_endpoint_auth_method
        if method == "client_secret_basic":
            text = f"{self.client_id}:{self.client_secret}"
            credential = base64.b64encode(text.encode("latin1")).decode("ascii")
            headers["Authorization"] = f"Basic {credential}"
        elif method == "client_secret_post":
            data["client_id"] = self.client_id
            data["client_secret"] = self.client_secret or ""
        elif method == "none":
            data["client_id"] = self.client_id
        else:
            raise ValueError(
                f"Unsupported token_endpoint_auth_method: {method!r}. "
                "Supported methods: client_secret_basic, client_secret_post, none."
            )

    async def _request_token(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        headers = dict(_DEFAULT_TOKEN_HEADERS)
        self._apply_client_auth(data, headers)

        response = await self._client.post(url, data=data, headers=headers)
        if response.status_code >= 500:
            response.raise_for_status()

        token: dict[str, Any] = response.json()
        if "error" in token:
            raise OAuthError(
                error=token["error"], description=token.get("error_description")
            )

        # Mirror authlib's OAuth2Token: derive expires_at from expires_in so
        # the stored raw token data keeps the same shape as before.
        if token.get("expires_at") is not None:
            try:
                token["expires_at"] = int(token["expires_at"])
            except ValueError:
                if token.get("expires_in"):
                    token["expires_at"] = int(time.time()) + int(token["expires_in"])
        elif token.get("expires_in"):
            token["expires_at"] = int(time.time()) + int(token["expires_in"])

        return token

    async def fetch_token(
        self,
        url: str,
        *,
        grant_type: str = "authorization_code",
        **params: Any,
    ) -> dict[str, Any]:
        """Exchange an authorization grant for tokens at the token endpoint.

        Falsy parameters are dropped from the request body, matching authlib.
        """
        data: dict[str, Any] = {"grant_type": grant_type}
        data.update({key: value for key, value in params.items() if value})
        return await self._request_token(url, data)

    async def refresh_token(
        self,
        url: str,
        *,
        refresh_token: str | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """Fetch a new access token using a refresh token.

        If the server does not rotate the refresh token, the previous one is
        injected into the returned dict, matching authlib.
        """
        data: dict[str, Any] = {"grant_type": "refresh_token"}
        if refresh_token:
            data["refresh_token"] = refresh_token
        data.update({key: value for key, value in params.items() if value})

        token = await self._request_token(url, data)
        if "refresh_token" not in token:
            token["refresh_token"] = refresh_token
        return token
