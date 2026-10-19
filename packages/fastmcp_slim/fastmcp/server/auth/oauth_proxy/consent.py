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
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from fastmcp.server.auth.oauth_proxy.models import (
    ConsentCSRFToken,
    ProxyDCRClient,
    _hash_token,
)
from fastmcp.server.auth.oauth_proxy.ui import create_consent_html
from fastmcp.server.auth.redirect_validation import (
    build_client_redirect,
    validate_redirect_uri,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.ui import create_secure_html_response

if TYPE_CHECKING:
    from fastmcp.server.auth.oauth_proxy.proxy import OAuthProxy

# Maximum number of remembered client approvals/denials stored in cookies.
# Keeps the Cookie header bounded to avoid hitting reverse proxy header limits.
_MAX_REMEMBERED_CLIENTS = 25

# Maximum number of consent-state cookies the browser carries at once. Each
# render of a consent page adds one, and a handful of renders is normal (a
# reload, a preload, an extension re-fetching the URL); the bound keeps the
# Cookie header from growing without limit across many pending flows.
#
# The matching server-side state is bounded by its own 15-minute TTL rather
# than by a count, because counting entries would mean reading them back and
# rewriting them — the read-modify-write that concurrent renders race on.
_MAX_CSRF_TOKENS = 10

# Base name of the consent-state cookie. One cookie is set per issued CSRF
# token (`MCP_CONSENT_STATE_<digest>`) rather than one list shared by all of
# them: two renders in flight at once both build their Set-Cookie from the same
# inbound Cookie header, so a shared list silently drops whichever entry was
# written first. Separate names never collide.
#
# The unsuffixed name is the pre-upgrade flat list. It is read, never written,
# so a consent page rendered before an upgrade can still be submitted after it.
_CONSENT_STATE_COOKIE_BASE = "MCP_CONSENT_STATE"

logger = get_logger(__name__)


def _consent_state_base_name(csrf_token: str) -> str:
    """Base cookie name carrying a single issued CSRF token.

    The token is hashed rather than used directly so the raw token does not end
    up in a cookie name, which is far more likely to be logged than its value.
    """
    digest = hashlib.sha256(csrf_token.encode()).hexdigest()[:32]
    return f"{_CONSENT_STATE_COOKIE_BASE}_{digest}"


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

    def _validate_client_redirect_uri(
        self: OAuthProxy,
        redirect_uri: str,
    ) -> bool:
        """Validate a stored transaction redirect URI before sending a browser to it."""
        return validate_redirect_uri(
            redirect_uri=redirect_uri,
            allowed_patterns=self._allowed_client_redirect_uris,
        )

    def _cookie_name(self: OAuthProxy, base_name: str) -> str:
        """Return secure cookie name for HTTPS, fallback for HTTP development."""
        if self._is_https:
            return f"__Host-{base_name}"
        return f"__{base_name}"

    def _cookie_signing_key(self: OAuthProxy) -> bytes:
        """Return the key used for HMAC-signing consent cookies.

        Uses the upstream client secret when available, falling back to the
        JWT signing key (which is always present — OAuthProxy requires it
        when no client secret is provided).
        """
        if self._upstream_client_secret is not None:
            return self._upstream_client_secret.get_secret_value().encode()
        return self._jwt_signing_key

    def _sign_cookie(self: OAuthProxy, payload: str) -> str:
        """Sign a cookie payload with HMAC-SHA256.

        Returns: base64(payload).base64(signature)
        """
        key = self._cookie_signing_key()
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
            key = self._cookie_signing_key()
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
        secure_name = self._cookie_name(base_name)
        raw = request.cookies.get(secure_name)
        # Only fall back to the non-__Host- name over plain HTTP. On HTTPS,
        # __Host- enforces host-only scope; accepting the weaker name would
        # let a sibling-subdomain attacker inject a domain-scoped cookie.
        if not raw and not self._is_https:
            raw = request.cookies.get(f"__{base_name}")
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

    def _read_consent_state_cookies(
        self: OAuthProxy, request: Request
    ) -> dict[str, tuple[str, float]]:
        """Per-token consent-state cookies the browser sent, by cookie name.

        Returns {cookie_name: (txn_id, issued_at)} for every cookie whose
        signature verifies. Unsigned, tampered, or unparsable cookies are
        skipped rather than raising, the same way the other cookie readers here
        treat them.
        """
        prefix = self._cookie_name(f"{_CONSENT_STATE_COOKIE_BASE}_")
        found: dict[str, tuple[str, float]] = {}
        for name, raw in request.cookies.items():
            if not name.startswith(prefix):
                continue
            payload = self._verify_cookie(raw)
            if not payload:
                logger.debug("Cookie signature verification failed for %s", name)
                continue
            try:
                data = json.loads(base64.b64decode(payload.encode()).decode())
            except Exception:
                logger.debug("Failed to decode cookie %s; ignoring", name)
                continue
            if not isinstance(data, dict):
                continue
            txn_id = data.get("txn")
            issued_at = data.get("iat")
            if isinstance(txn_id, str) and isinstance(issued_at, int | float):
                found[name] = (txn_id, float(issued_at))
        return found

    def _set_consent_state_cookie(
        self: OAuthProxy,
        response: HTMLResponse | RedirectResponse,
        csrf_token: str,
        txn_id: str,
        issued_at: float,
    ) -> None:
        """Record that this browser received `csrf_token`, under its own name.

        The cookie is what makes the double-submit check meaningful: the token
        in the form has to match one this browser was actually handed. Writing
        it under a name derived from the token keeps that property while making
        the write independent of every other render's.
        """
        payload = base64.b64encode(
            json.dumps(
                {"txn": txn_id, "iat": issued_at}, separators=(",", ":")
            ).encode()
        ).decode()
        response.set_cookie(
            self._cookie_name(_consent_state_base_name(csrf_token)),
            self._sign_cookie(payload),
            max_age=15 * 60,
            secure=self._is_https,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def _clear_consent_state_for_transaction(
        self: OAuthProxy,
        request: Request,
        response: HTMLResponse | RedirectResponse,
        txn_id: str,
        *,
        include_legacy: bool,
    ) -> None:
        """Expire the consent state belonging to one completed transaction.

        Only this transaction's cookies are removed. Another consent flow the
        same browser has open keeps its own state, which a single shared list
        had no way to express — completing either flow wiped both.
        """
        for name, (cookie_txn, _issued_at) in self._read_consent_state_cookies(
            request
        ).items():
            if hmac.compare_digest(cookie_txn, txn_id):
                self._expire_cookie(response, name)

        if include_legacy:
            # The pre-upgrade cookie is a flat list with no transaction
            # attached, so it can only be cleared wholesale. Reached only when
            # the submitted token came from it, which means every flow sharing
            # it was rendered before the upgrade too.
            self._set_list_cookie(
                response,
                _CONSENT_STATE_COOKIE_BASE,
                self._encode_list_cookie([]),
                max_age=60,
            )

    def _expire_cookie(
        self: OAuthProxy,
        response: HTMLResponse | RedirectResponse,
        name: str,
    ) -> None:
        """Expire one cookie by name, matching the attributes it was set with."""
        response.set_cookie(
            name,
            "",
            max_age=0,
            secure=self._is_https,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def _read_consent_bindings(self: OAuthProxy, request: Request) -> dict[str, str]:
        """Read the consent binding map from the signed cookie.

        Returns a dict of {txn_id: consent_token} for all pending flows.
        """
        cookie_name = self._cookie_name("MCP_CONSENT_BINDING")
        raw = request.cookies.get(cookie_name)
        # Only fall back to the non-__Host- name over plain HTTP. On HTTPS,
        # __Host- enforces host-only scope; accepting the weaker name would
        # bypass that guarantee.
        if not raw and not self._is_https:
            raw = request.cookies.get("__MCP_CONSENT_BINDING")
        if not raw:
            return {}
        payload = self._verify_cookie(raw)
        if not payload:
            return {}
        try:
            data = json.loads(base64.b64decode(payload.encode()).decode())
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            logger.debug("Failed to decode consent binding cookie")
        return {}

    def _write_consent_bindings(
        self: OAuthProxy,
        response: HTMLResponse | RedirectResponse,
        bindings: dict[str, str],
    ) -> None:
        """Write the consent binding map to a signed cookie."""
        name = self._cookie_name("MCP_CONSENT_BINDING")
        if not bindings:
            response.set_cookie(
                name,
                "",
                max_age=0,
                secure=self._is_https,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return
        payload_bytes = json.dumps(bindings, separators=(",", ":")).encode()
        payload_b64 = base64.b64encode(payload_bytes).decode()
        signed_value = self._sign_cookie(payload_b64)
        response.set_cookie(
            name,
            signed_value,
            max_age=15 * 60,
            secure=self._is_https,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def _set_consent_binding_cookie(
        self: OAuthProxy,
        request: Request,
        response: HTMLResponse | RedirectResponse,
        txn_id: str,
        consent_token: str,
    ) -> None:
        """Add a consent binding entry for a transaction.

        This cookie binds the browser that approved consent to the IdP callback,
        ensuring a different browser cannot complete the OAuth flow. Multiple
        concurrent flows are supported by storing a map of txn_id → consent_token.
        """
        bindings = self._read_consent_bindings(request)
        bindings[txn_id] = consent_token
        self._write_consent_bindings(response, bindings)

    def _clear_consent_binding_cookie(
        self: OAuthProxy,
        request: Request,
        response: HTMLResponse | RedirectResponse,
        txn_id: str,
    ) -> None:
        """Remove a specific consent binding entry after successful callback."""
        bindings = self._read_consent_bindings(request)
        bindings.pop(txn_id, None)
        self._write_consent_bindings(response, bindings)

    def _verify_consent_binding_cookie(
        self: OAuthProxy,
        request: Request,
        txn_id: str,
        expected_token: str,
    ) -> bool:
        """Verify the consent binding for a specific transaction."""
        bindings = self._read_consent_bindings(request)
        actual = bindings.get(txn_id)
        if not actual:
            return False
        return hmac.compare_digest(actual, expected_token)

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

        # Silent consent only fires in "remember" mode, and only when the
        # request arrived via a safe navigation context. AS-in-the-middle
        # attacks surface as cross-site redirects from a third-party origin
        # into /authorize; forcing the HTML prompt in that case preserves
        # the consent-screen mitigation without blocking legitimate
        # client-initiated flows (Sec-Fetch-Site: none).
        if self._require_authorization_consent == "remember":
            sec_fetch_site = request.headers.get("Sec-Fetch-Site")
            # Fail closed on missing header: legacy clients degrade to the
            # explicit prompt rather than silent approval.
            silent_eligible = sec_fetch_site in ("same-origin", "same-site", "none")

            if silent_eligible:
                approved = set(
                    self._decode_list_cookie(request, "MCP_APPROVED_CLIENTS")
                )
                denied = set(self._decode_list_cookie(request, "MCP_DENIED_CLIENTS"))

                if client_key in approved:
                    consent_token = secrets.token_urlsafe(32)
                    txn_model.consent_token = consent_token
                    await self._transaction_store.put(
                        key=txn_id, value=txn_model, ttl=15 * 60
                    )
                    upstream_url = self._build_upstream_authorize_url(txn_id, txn)
                    response = RedirectResponse(url=upstream_url, status_code=302)
                    self._set_consent_binding_cookie(
                        request, response, txn_id, consent_token
                    )
                    return response

                if client_key in denied:
                    if not self._validate_client_redirect_uri(
                        txn["client_redirect_uri"]
                    ):
                        logger.warning(
                            "Blocked consent denial redirect to disallowed URI for transaction %s",
                            txn_id,
                        )
                        return create_secure_html_response(
                            "<h1>Error</h1><p>Invalid redirect URI</p>",
                            status_code=400,
                        )

                    callback_params = {
                        "error": "access_denied",
                        "state": txn.get("client_state") or "",
                    }
                    return RedirectResponse(
                        url=build_client_redirect(
                            txn["client_redirect_uri"],
                            callback_params,
                            iss=str(self.issuer_url),
                        ),
                        status_code=302,
                    )
            else:
                logger.info(
                    "Silent consent skipped for transaction %s: Sec-Fetch-Site=%r "
                    "(cross-site navigation; forcing explicit consent prompt)",
                    txn_id,
                    sec_fetch_site,
                )

        # Need consent: issue CSRF token and show HTML.
        #
        # A transaction can be rendered more than once before it is submitted —
        # a reload, a browser preload, an extension re-fetching the URL. Every
        # render issues its own token, so that a token stays unique to the
        # browser that received it and the double-submit cookie check keeps its
        # meaning, and every token issued for the transaction stays valid until
        # it expires. Dropping the earlier one kills the form the user is
        # already looking at: they click Approve and get "Invalid or expired
        # consent token" on a flow that never expired, with no way to recover.
        #
        # The token is stored under its own key rather than appended to a list
        # on the transaction. Two renders in flight at once would both read the
        # same transaction, each append their token, and the second write would
        # drop the first — `AsyncKeyValue` has no compare-and-swap to prevent
        # it. Independent keys make the writes commute, including across
        # processes sharing one storage backend.
        csrf_token = secrets.token_urlsafe(32)
        issued_at = time.time()
        csrf_expires_at = issued_at + 15 * 60
        await self._consent_csrf_store.put(
            key=_hash_token(csrf_token),
            value=ConsentCSRFToken(txn_id=txn_id, expires_at=csrf_expires_at),
            ttl=15 * 60,  # Auto-expire after 15 minutes
        )

        # Load client to get client_name and CIMD info if available
        client = await self.get_client(txn["client_id"])
        client_name = getattr(client, "client_name", None) if client else None

        # Detect CIMD clients for verified domain badge
        is_cimd_client = False
        cimd_domain: str | None = None
        if isinstance(client, ProxyDCRClient) and client.cimd_document is not None:
            is_cimd_client = True
            cimd_domain = urlparse(txn["client_id"]).hostname

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
            is_cimd_client=is_cimd_client,
            cimd_domain=cimd_domain,
        )
        response = create_secure_html_response(html)
        self._set_consent_state_cookie(response, csrf_token, txn_id, issued_at)

        # Keep the browser's consent state bounded. The cookie just set always
        # survives; the oldest of the rest are expired to make room. Eviction
        # is by issued-at from the cookie itself, so it does not depend on any
        # server-side bookkeeping that renders would have to share.
        others = self._read_consent_state_cookies(request)
        others.pop(self._cookie_name(_consent_state_base_name(csrf_token)), None)
        surplus = len(others) + 1 - _MAX_CSRF_TOKENS
        if surplus > 0:
            by_age = sorted(others.items(), key=lambda item: item[1][1])
            for name, _entry in by_age[:surplus]:
                self._expire_cookie(response, name)
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

        # Look the token up by its own key. A record proves the token was
        # issued by a render of THIS transaction; nothing else can have written
        # it, and a concurrent render cannot have removed it.
        csrf_record = (
            await self._consent_csrf_store.get(key=_hash_token(csrf_token))
            if csrf_token
            else None
        )
        if csrf_record is not None:
            legacy_csrf = False
            csrf_valid = (
                hmac.compare_digest(csrf_record.txn_id, txn_id)
                and time.time() <= csrf_record.expires_at
            )
        else:
            # No record: either the token is bogus, or the consent page was
            # rendered by a version that kept the token on the transaction.
            # Honouring the old location keeps a flow that was already open
            # during an upgrade submittable instead of failing at Approve.
            stored = txn_model.csrf_token
            csrf_valid = bool(csrf_token and stored) and (
                hmac.compare_digest(stored or "", csrf_token)
                and time.time() <= (txn_model.csrf_expires_at or 0)
            )
            legacy_csrf = csrf_valid

        if not csrf_valid:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired consent token</p>", status_code=400
            )

        # Double-submit CSRF check: verify the form token matches the cookie.
        # Without this, an attacker who knows their own tx_id/csrf_token can
        # CSRF the victim's browser into approving consent, bypassing the
        # consent binding cookie protection.
        if legacy_csrf:
            cookie_ok = csrf_token in self._decode_list_cookie(
                request, _CONSENT_STATE_COOKIE_BASE
            )
        else:
            entry = self._read_consent_state_cookies(request).get(
                self._cookie_name(_consent_state_base_name(csrf_token))
            )
            cookie_ok = entry is not None and hmac.compare_digest(entry[0], txn_id)
        if not cookie_ok:
            logger.warning(
                "CSRF double-submit check failed for transaction %s "
                "(possible cross-site consent forgery)",
                txn_id,
            )
            return create_secure_html_response(
                "<h1>Error</h1><p>Authorization session mismatch. "
                "Please try authenticating again.</p>",
                status_code=403,
            )

        client_key = self._make_client_key(txn["client_id"], txn["client_redirect_uri"])

        remember_mode = self._require_authorization_consent == "remember"

        if action == "approve":
            consent_token = secrets.token_urlsafe(32)
            txn_model.consent_token = consent_token
            await self._transaction_store.put(key=txn_id, value=txn_model, ttl=15 * 60)

            upstream_url = self._build_upstream_authorize_url(txn_id, txn)
            response = RedirectResponse(url=upstream_url, status_code=302)

            # Only persist the approval for future silent consent in "remember"
            # mode; in the default "always" mode the cookie would never be read.
            if remember_mode:
                approved = list(
                    self._decode_list_cookie(request, "MCP_APPROVED_CLIENTS")
                )
                if client_key in approved:
                    approved.remove(client_key)
                approved.append(client_key)
                approved = approved[-_MAX_REMEMBERED_CLIENTS:]
                self._set_list_cookie(
                    response,
                    "MCP_APPROVED_CLIENTS",
                    self._encode_list_cookie(approved),
                    max_age=365 * 24 * 3600,
                )

            # Retire this transaction's consent state, both halves of it: the
            # stored token so it cannot be replayed, and the cookies that
            # carried it. Other pending flows are left alone.
            await self._consent_csrf_store.delete(key=_hash_token(csrf_token))
            self._clear_consent_state_for_transaction(
                request, response, txn_id, include_legacy=legacy_csrf
            )
            self._set_consent_binding_cookie(request, response, txn_id, consent_token)
            return response

        elif action == "deny":
            if not self._validate_client_redirect_uri(txn["client_redirect_uri"]):
                logger.warning(
                    "Blocked consent denial redirect to disallowed URI for transaction %s",
                    txn_id,
                )
                return create_secure_html_response(
                    "<h1>Error</h1><p>Invalid redirect URI</p>",
                    status_code=400,
                )

            callback_params = {
                "error": "access_denied",
                "state": txn.get("client_state") or "",
            }
            client_callback_url = build_client_redirect(
                txn["client_redirect_uri"], callback_params, iss=str(self.issuer_url)
            )
            response = RedirectResponse(url=client_callback_url, status_code=302)

            if remember_mode:
                denied = list(self._decode_list_cookie(request, "MCP_DENIED_CLIENTS"))
                if client_key in denied:
                    denied.remove(client_key)
                denied.append(client_key)
                denied = denied[-_MAX_REMEMBERED_CLIENTS:]
                self._set_list_cookie(
                    response,
                    "MCP_DENIED_CLIENTS",
                    self._encode_list_cookie(denied),
                    max_age=365 * 24 * 3600,
                )

            await self._consent_csrf_store.delete(key=_hash_token(csrf_token))
            self._clear_consent_state_for_transaction(
                request, response, txn_id, include_legacy=legacy_csrf
            )
            return response

        else:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid action</p>", status_code=400
            )
