"""DPoP-bound requests to an AT Protocol authorization server.

The provider acts as a public OAuth client (``token_endpoint_auth_method:
none``) that only needs the account's identity: it pushes an authorization
request, exchanges the code, reads ``sub``, and revokes the grant.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from joserfc import jwt
from joserfc.jwk import ECKey

from fastmcp.experimental.auth.atproto.identity import (
    MAX_DOCUMENT_BYTES,
    ATProtoError,
    AuthorizationServer,
)
from fastmcp.server.auth.ssrf import (
    SSRFError,
    SSRFFetchError,
    ssrf_safe_fetch_response,
)
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

ATPROTO_SCOPE = "atproto"


class DPoPSession:
    """An ES256 DPoP key and the latest nonce the server handed out."""

    def __init__(
        self, private_jwk: dict[str, Any] | None = None, nonce: str | None = None
    ) -> None:
        self._key = (
            ECKey.import_key(private_jwk)
            if private_jwk is not None
            else ECKey.generate_key("P-256", private=True)
        )
        self.nonce = nonce

    @property
    def private_jwk(self) -> dict[str, Any]:
        return dict(self._key.as_dict(private=True))

    def proof(self, method: str, url: str) -> str:
        scheme, netloc, path, _, _ = urlsplit(url)
        claims: dict[str, Any] = {
            "jti": secrets.token_urlsafe(24),
            "htm": method,
            "htu": urlunsplit((scheme, netloc, path, "", "")),
            "iat": int(time.time()),
        }
        if self.nonce:
            claims["nonce"] = self.nonce
        header = {
            "typ": "dpop+jwt",
            "alg": "ES256",
            "jwk": dict(self._key.as_dict(private=False)),
        }
        return jwt.encode(header, claims, self._key, algorithms=["ES256"])

    async def post_form(
        self, url: str, data: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        """POST a form with a DPoP proof, retrying once on ``use_dpop_nonce``."""
        body: dict[str, Any] = {}
        status = 0
        for attempt in range(2):
            response = await ssrf_safe_fetch_response(
                url,
                method="POST",
                content=urlencode(data).encode(),
                request_headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "DPoP": self.proof("POST", url),
                },
                allowed_status_codes={200, 201, 400, 401},
                max_size=MAX_DOCUMENT_BYTES,
            )
            headers = {key.lower(): value for key, value in response.headers.items()}
            new_nonce = headers.get("dpop-nonce")
            if new_nonce:
                self.nonce = new_nonce
            status = response.status_code
            try:
                parsed = json.loads(response.content or b"{}")
            except ValueError:
                parsed = {}
            body = parsed if isinstance(parsed, dict) else {}
            if (
                attempt == 0
                and status in (400, 401)
                and body.get("error") == "use_dpop_nonce"
                and new_nonce
            ):
                continue
            break
        return status, body


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def push_authorization_request(
    server: AuthorizationServer,
    dpop: DPoPSession,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_verifier: str,
    login_hint: str,
) -> str:
    """Send a PAR and return the authorization URL to redirect the browser to."""
    try:
        status, body = await dpop.post_form(
            server.pushed_authorization_request_endpoint,
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": ATPROTO_SCOPE,
                "state": state,
                "code_challenge": pkce_challenge(code_verifier),
                "code_challenge_method": "S256",
                "login_hint": login_hint,
            },
        )
    except (SSRFError, SSRFFetchError) as e:
        raise ATProtoError("server_unavailable", f"PAR request failed: {e}") from e

    request_uri = body.get("request_uri")
    if status not in (200, 201) or not isinstance(request_uri, str):
        raise ATProtoError(
            "server_unavailable",
            f"PAR rejected ({status}): {body.get('error')} {body.get('error_description')}",
        )
    query = urlencode({"client_id": client_id, "request_uri": request_uri})
    separator = "&" if "?" in server.authorization_endpoint else "?"
    return f"{server.authorization_endpoint}{separator}{query}"


async def exchange_code(
    server: AuthorizationServer,
    dpop: DPoPSession,
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    """Exchange an authorization code; returns the token response."""
    try:
        status, body = await dpop.post_form(
            server.token_endpoint,
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
        )
    except (SSRFError, SSRFFetchError) as e:
        raise ATProtoError("server_unavailable", f"Token request failed: {e}") from e

    if status != 200:
        reason = (
            "invalid_grant"
            if body.get("error") == "invalid_grant"
            else "server_unavailable"
        )
        raise ATProtoError(
            reason,
            f"Token request rejected ({status}): {body.get('error')} {body.get('error_description')}",
        )
    if not isinstance(body.get("access_token"), str) or not isinstance(
        body.get("sub"), str
    ):
        raise ATProtoError(
            "server_unavailable", "Token response lacks access_token or sub"
        )
    if str(body.get("token_type", "")).lower() != "dpop":
        raise ATProtoError("server_unavailable", "Token response is not DPoP-bound")
    if ATPROTO_SCOPE not in str(body.get("scope", "")).split():
        raise ATProtoError(
            "server_unavailable", "Token response lacks the atproto scope"
        )
    return body


async def revoke_grant(
    server: AuthorizationServer,
    dpop: DPoPSession,
    *,
    client_id: str,
    token_response: dict[str, Any],
) -> None:
    """Best-effort revocation: the provider keeps no PDS tokens after sign-in."""
    if server.revocation_endpoint is None:
        return
    token = token_response.get("refresh_token") or token_response.get("access_token")
    if not isinstance(token, str):
        return
    try:
        status, body = await dpop.post_form(
            server.revocation_endpoint, {"client_id": client_id, "token": token}
        )
        if status != 200:
            logger.debug(
                "PDS token revocation returned %s: %s", status, body.get("error")
            )
    except (SSRFError, SSRFFetchError) as e:
        logger.debug("PDS token revocation failed: %s", e)
