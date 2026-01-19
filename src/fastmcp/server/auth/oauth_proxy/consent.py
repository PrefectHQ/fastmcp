"""OAuth Proxy Consent Management.

This module contains consent management functionality for the OAuth proxy.
The ConsentMixin class provides methods for handling user consent flows,
cookie management, and consent page rendering.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64encode
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlparse

from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from fastmcp.server.auth.oauth_proxy.ui import create_consent_html
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.ui import create_secure_html_response

if TYPE_CHECKING:
    from fastmcp.server.auth.oauth_proxy.proxy import OAuthProxy

logger = get_logger(__name__)


class ConsentMixin:
    """Mixin class providing consent management functionality for OAuthProxy.

    This mixin contains all methods related to:
    - Cookie signing and verification
    - Consent page rendering
    - Consent approval/denial handling
    - URI normalization for consent tracking
    """

    def _normalize_uri(self, uri: str) -> str:
        """Normalize a URI to a canonical form for consent tracking."""
        parsed = urlparse(uri)
        path = parsed.path or ""
        normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
        if normalized.endswith("/") and len(path) > 1:
            normalized = normalized[:-1]
        return normalized

    def _make_client_key(self, client_id: str, redirect_uri: str | AnyUrl) -> str:
        """Create a stable key for consent tracking from client_id and redirect_uri."""
        normalized = self._normalize_uri(str(redirect_uri))
        return f"{client_id}:{normalized}"

    def _cookie_name(self: OAuthProxy, base_name: str) -> str:
        """Return secure cookie name for HTTPS, fallback for HTTP development."""
        if self._is_https:
            return f"__Host-{base_name}"
        return f"__{base_name}"

    def _sign_cookie(self: OAuthProxy, payload: str) -> str:
        """Sign a cookie payload with HMAC-SHA256.

        Returns: base64(payload).base64(signature)
        """
        # Use upstream client secret as signing key
        key = self._upstream_client_secret.get_secret_value().encode()
        signature = hmac.new(key, payload.encode(), hashlib.sha256).digest()
        signature_b64 = base64.b64encode(signature).decode()
        return f"{payload}.{signature_b64}"

    def _verify_cookie(self: OAuthProxy, signed_value: str) -> str | None:
        """Verify and extract payload from signed cookie.

        Returns: payload if signature valid, None otherwise
        """
        try:
            if "." not in signed_value:
                return None
            payload, signature_b64 = signed_value.rsplit(".", 1)

            # Verify signature
            key = self._upstream_client_secret.get_secret_value().encode()
            expected_sig = hmac.new(key, payload.encode(), hashlib.sha256).digest()
            provided_sig = base64.b64decode(signature_b64.encode())

            # Constant-time comparison
            if not hmac.compare_digest(expected_sig, provided_sig):
                return None

            return payload
        except Exception:
            return None

    def _decode_list_cookie(
        self: OAuthProxy, request: Request, base_name: str
    ) -> list[str]:
        """Decode and verify a signed base64-encoded JSON list from cookie. Returns [] if missing/invalid."""
        # Prefer secure name, but also check non-secure variant for dev
        secure_name = self._cookie_name(base_name)
        raw = request.cookies.get(secure_name) or request.cookies.get(f"__{base_name}")
        if not raw:
            return []
        try:
            # Verify signature
            payload = self._verify_cookie(raw)
            if not payload:
                logger.debug("Cookie signature verification failed for %s", secure_name)
                return []

            # Decode payload
            data = base64.b64decode(payload.encode())
            value = json.loads(data.decode())
            if isinstance(value, list):
                return [str(x) for x in value]
        except Exception:
            logger.debug("Failed to decode cookie %s; treating as empty", secure_name)
        return []

    def _encode_list_cookie(self: OAuthProxy, values: list[str]) -> str:
        """Encode values to base64 and sign with HMAC.

        Returns: signed cookie value (payload.signature)
        """
        payload = json.dumps(values, separators=(",", ":")).encode()
        payload_b64 = base64.b64encode(payload).decode()
        return self._sign_cookie(payload_b64)

    def _set_list_cookie(
        self: OAuthProxy,
        response: HTMLResponse | RedirectResponse,
        base_name: str,
        value_b64: str,
        max_age: int,
    ) -> None:
        name = self._cookie_name(base_name)
        response.set_cookie(
            name,
            value_b64,
            max_age=max_age,
            secure=self._is_https,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def _build_upstream_authorize_url(
        self: OAuthProxy, txn_id: str, transaction: dict[str, Any]
    ) -> str:
        """Construct the upstream IdP authorization URL using stored transaction data."""
        query_params: dict[str, Any] = {
            "response_type": "code",
            "client_id": self._upstream_client_id,
            "redirect_uri": f"{str(self.base_url).rstrip('/')}{self._redirect_path}",
            "state": txn_id,
        }

        scopes_to_use = transaction.get("scopes") or self.required_scopes or []
        if scopes_to_use:
            query_params["scope"] = " ".join(scopes_to_use)

        # If PKCE forwarding was enabled, include the proxy challenge
        proxy_code_verifier = transaction.get("proxy_code_verifier")
        if proxy_code_verifier:
            challenge_bytes = hashlib.sha256(proxy_code_verifier.encode()).digest()
            proxy_code_challenge = (
                urlsafe_b64encode(challenge_bytes).decode().rstrip("=")
            )
            query_params["code_challenge"] = proxy_code_challenge
            query_params["code_challenge_method"] = "S256"

        # Forward resource indicator if present in transaction
        if resource := transaction.get("resource"):
            query_params["resource"] = resource

        # Extra configured parameters
        if self._extra_authorize_params:
            query_params.update(self._extra_authorize_params)

        separator = "&" if "?" in self._upstream_authorization_endpoint else "?"
        return f"{self._upstream_authorization_endpoint}{separator}{urlencode(query_params)}"

    async def _handle_consent(
        self: OAuthProxy, request: Request
    ) -> HTMLResponse | RedirectResponse:
        """Handle consent page - dispatch to GET or POST handler based on method."""
        if request.method == "POST":
            return await self._submit_consent(request)
        return await self._show_consent_page(request)

    async def _show_consent_page(
        self: OAuthProxy, request: Request
    ) -> HTMLResponse | RedirectResponse:
        """Display consent page or auto-approve/deny based on cookies."""
        from fastmcp.server.server import FastMCP

        txn_id = request.query_params.get("txn_id")
        if not txn_id:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>", status_code=400
            )

        txn_model = await self._transaction_store.get(key=txn_id)
        if not txn_model:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>", status_code=400
            )

        txn = txn_model.model_dump()
        client_key = self._make_client_key(txn["client_id"], txn["client_redirect_uri"])

        approved = set(self._decode_list_cookie(request, "MCP_APPROVED_CLIENTS"))
        denied = set(self._decode_list_cookie(request, "MCP_DENIED_CLIENTS"))

        if client_key in approved:
            upstream_url = self._build_upstream_authorize_url(txn_id, txn)
            return RedirectResponse(url=upstream_url, status_code=302)

        if client_key in denied:
            callback_params = {
                "error": "access_denied",
                "state": txn.get("client_state") or "",
            }
            sep = "&" if "?" in txn["client_redirect_uri"] else "?"
            return RedirectResponse(
                url=f"{txn['client_redirect_uri']}{sep}{urlencode(callback_params)}",
                status_code=302,
            )

        # Need consent: issue CSRF token and show HTML
        csrf_token = secrets.token_urlsafe(32)
        csrf_expires_at = time.time() + 15 * 60

        # Update transaction with CSRF token
        txn_model.csrf_token = csrf_token
        txn_model.csrf_expires_at = csrf_expires_at
        await self._transaction_store.put(
            key=txn_id, value=txn_model, ttl=15 * 60
        )  # Auto-expire after 15 minutes

        # Update dict for use in HTML generation
        txn["csrf_token"] = csrf_token
        txn["csrf_expires_at"] = csrf_expires_at

        # Load client to get client_name if available
        client = await self.get_client(txn["client_id"])
        client_name = getattr(client, "client_name", None) if client else None

        # Extract server metadata from app state
        fastmcp = getattr(request.app.state, "fastmcp_server", None)

        if isinstance(fastmcp, FastMCP):
            server_name = fastmcp.name
            icons = fastmcp.icons
            server_icon_url = icons[0].src if icons else None
            server_website_url = fastmcp.website_url
        else:
            server_name = None
            server_icon_url = None
            server_website_url = None

        html = create_consent_html(
            client_id=txn["client_id"],
            redirect_uri=txn["client_redirect_uri"],
            scopes=txn.get("scopes") or [],
            txn_id=txn_id,
            csrf_token=csrf_token,
            client_name=client_name,
            server_name=server_name,
            server_icon_url=server_icon_url,
            server_website_url=server_website_url,
            csp_policy=self._consent_csp_policy,
        )
        response = create_secure_html_response(html)
        # Store CSRF in cookie with short lifetime
        self._set_list_cookie(
            response,
            "MCP_CONSENT_STATE",
            self._encode_list_cookie([csrf_token]),
            max_age=15 * 60,
        )
        return response

    async def _submit_consent(
        self: OAuthProxy, request: Request
    ) -> RedirectResponse | HTMLResponse:
        """Handle consent approval/denial, set cookies, and redirect appropriately."""
        form = await request.form()
        txn_id = str(form.get("txn_id", ""))
        action = str(form.get("action", ""))
        csrf_token = str(form.get("csrf_token", ""))

        if not txn_id:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>", status_code=400
            )

        txn_model = await self._transaction_store.get(key=txn_id)
        if not txn_model:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>", status_code=400
            )

        txn = txn_model.model_dump()
        expected_csrf = txn.get("csrf_token")
        expires_at = float(txn.get("csrf_expires_at") or 0)

        if not expected_csrf or csrf_token != expected_csrf or time.time() > expires_at:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired consent token</p>", status_code=400
            )

        client_key = self._make_client_key(txn["client_id"], txn["client_redirect_uri"])

        if action == "approve":
            approved = set(self._decode_list_cookie(request, "MCP_APPROVED_CLIENTS"))
            if client_key not in approved:
                approved.add(client_key)
            approved_b64 = self._encode_list_cookie(sorted(approved))

            upstream_url = self._build_upstream_authorize_url(txn_id, txn)
            response = RedirectResponse(url=upstream_url, status_code=302)
            self._set_list_cookie(
                response, "MCP_APPROVED_CLIENTS", approved_b64, max_age=365 * 24 * 3600
            )
            # Clear CSRF cookie by setting empty short-lived value
            self._set_list_cookie(
                response, "MCP_CONSENT_STATE", self._encode_list_cookie([]), max_age=60
            )
            return response

        elif action == "deny":
            denied = set(self._decode_list_cookie(request, "MCP_DENIED_CLIENTS"))
            if client_key not in denied:
                denied.add(client_key)
            denied_b64 = self._encode_list_cookie(sorted(denied))

            callback_params = {
                "error": "access_denied",
                "state": txn.get("client_state") or "",
            }
            sep = "&" if "?" in txn["client_redirect_uri"] else "?"
            client_callback_url = (
                f"{txn['client_redirect_uri']}{sep}{urlencode(callback_params)}"
            )
            response = RedirectResponse(url=client_callback_url, status_code=302)
            self._set_list_cookie(
                response, "MCP_DENIED_CLIENTS", denied_b64, max_age=365 * 24 * 3600
            )
            self._set_list_cookie(
                response, "MCP_CONSENT_STATE", self._encode_list_cookie([]), max_age=60
            )
            return response

        else:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid action</p>", status_code=400
            )
