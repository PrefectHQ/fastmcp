"""Sign in to a FastMCP server with an AT Protocol account.

MCP clients see the `OAuthProxy` authorization server. Its upstream step is AT
Protocol OAuth, run by `atproto-oauth` against the account's own authorization
server. The provider keeps only the verified DID, revokes the account grant, and
issues identity tokens that it signs itself and re-checks against the DID
allowlist on every request.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.experimental.auth.atproto import ATProtoProvider

    auth = ATProtoProvider(
        base_url="https://my-server.example.com",
        jwt_signing_key="...",
        allowed_dids=["did:plc:abc123..."],
    )
    mcp = FastMCP("My server", auth=auth)
    ```
"""

from __future__ import annotations

import base64
import re
import secrets
import time
from collections.abc import Iterable
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

from joserfc.errors import JoseError
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from typing_extensions import override

from fastmcp.experimental.auth.atproto.login import create_login_html
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.jwt_issuer import JWTIssuer, derive_jwt_key
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.ui import create_error_html
from fastmcp.server.auth.oauth_proxy.upstream import AsyncOAuth2Client, OAuthError
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.ui import create_secure_html_response

try:
    from atproto_identity.resolver import AsyncIdResolver
    from atproto_oauth import OAuthClient
    from atproto_oauth.stores import MemorySessionStore, MemoryStateStore
except ImportError as e:
    raise ImportError(
        "ATProtoProvider requires the `atproto` extra. "
        "Install with: pip install 'fastmcp[atproto]'"
    ) from e

logger = get_logger(__name__)

ATPROTO_SCOPE = "atproto"
DEFAULT_TYPEAHEAD_URL = (
    "https://typeahead.waow.tech/xrpc/app.bsky.actor.searchActorsTypeahead"
)
LOGIN_PATH = "/atproto/login"
CLIENT_METADATA_PATH = "/oauth-client-metadata.json"
PENDING_TTL_SECONDS = 15 * 60

_DID = re.compile(r"^did:[a-z]+:[a-zA-Z0-9._:%-]*[a-zA-Z0-9._-]$")


class _SignInError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _normalize_identifier(raw: str) -> str:
    value = raw.strip().removeprefix("at://").lstrip("@").strip().rstrip("/")
    return value if value.startswith("did:") else value.lower()


class _AllowedDIDs:
    def __init__(self, dids: Iterable[str] | None) -> None:
        if dids is None:
            self._dids: frozenset[str] | None = None
            return
        dids = frozenset(dids)
        invalid = sorted(did for did in dids if not _DID.fullmatch(did))
        if invalid:
            raise ValueError(f"allowed_dids must contain DIDs, got: {invalid}")
        self._dids = dids

    def __contains__(self, did: object) -> bool:
        return self._dids is None or did in self._dids

    @property
    def only(self) -> str | None:
        if self._dids is not None and len(self._dids) == 1:
            return next(iter(self._dids))
        return None


class ATProtoIdentityVerifier(TokenVerifier):
    """Verifies the identity tokens `ATProtoProvider` signs after sign-in."""

    def __init__(
        self,
        *,
        issuer: JWTIssuer,
        allowed_dids: _AllowedDIDs,
        required_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(required_scopes=required_scopes)
        self._issuer = issuer
        self._allowed_dids = allowed_dids

    async def verify_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        try:
            payload = self._issuer.verify_token(token)
        except JoseError:
            return None
        did = payload.get("sub")
        if not isinstance(did, str) or did not in self._allowed_dids:
            return None
        scope = str(payload.get("scope") or "")
        return AccessToken(
            token=token,
            client_id=str(payload.get("client_id") or did),
            scopes=scope.split(),
            expires_at=int(payload["exp"]),
            subject=did,
            claims={"sub": did, "did": did, "handle": payload.get("handle")},
        )


class _IdentityRefreshClient(AsyncOAuth2Client):
    """Refreshes identity tokens where `OAuthProxy` would refresh upstream ones."""

    def __init__(self, provider: ATProtoProvider) -> None:
        self.client_id = provider._upstream_client_id
        self.client_secret = None
        self.token_endpoint_auth_method = "none"
        self._provider = provider

    async def aclose(self) -> None:
        return None

    async def fetch_token(
        self, url: str, *, grant_type: str = "authorization_code", **params: Any
    ) -> dict[str, Any]:
        raise OAuthError(error="unsupported_grant_type")

    async def refresh_token(
        self, url: str, *, refresh_token: str | None = None, **params: Any
    ) -> dict[str, Any]:
        return self._provider._refresh_identity(refresh_token or "")


class ATProtoProvider(OAuthProxy):
    """Sign in to a FastMCP server with an AT Protocol account.

    Args:
        base_url: Public URL of this server. For local development use
            ``http://127.0.0.1:<port>``, which AT Protocol treats as a loopback
            client with no hosted metadata.
        jwt_signing_key: Secret for the tokens this server issues. Keep it
            stable, or every session ends on restart.
        allowed_dids: DIDs allowed to sign in, or ``None`` for any account.
            With exactly one DID, the handle page is skipped.
        client_name: Name shown on the account's authorization screen.
            Defaults to the FastMCP server's name.
        identity_token_expiry_seconds: Lifetime of each access token.
        session_expiry_seconds: How long a sign-in lasts.
        typeahead_url: ``app.bsky.actor.searchActorsTypeahead`` endpoint for
            the login page's suggestions, or ``None`` to disable them.

    The remaining arguments match `OAuthProxy`. Pending sign-ins are kept in
    memory, so run one server process per provider.
    """

    def __init__(
        self,
        *,
        base_url: AnyHttpUrl | str,
        jwt_signing_key: str | bytes,
        allowed_dids: Iterable[str] | None = None,
        client_name: str | None = None,
        resource_base_url: AnyHttpUrl | str | None = None,
        redirect_path: str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        service_documentation_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        require_authorization_consent: bool | Literal["remember", "external"] = True,
        consent_csp_policy: str | None = None,
        identity_token_expiry_seconds: int = 60 * 60,
        session_expiry_seconds: int = 30 * 24 * 60 * 60,
        typeahead_url: str | None = DEFAULT_TYPEAHEAD_URL,
        enable_cimd: bool = True,
    ) -> None:
        base = str(base_url).rstrip("/")
        parsed = urlparse(base)
        if parsed.hostname == "localhost":
            raise ValueError(
                "AT Protocol authorization servers reject 'localhost' redirects; "
                "use http://127.0.0.1:<port> as base_url for local development."
            )
        loopback = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1")
        if parsed.scheme != "https" and not loopback:
            raise ValueError("base_url must use HTTPS outside of 127.0.0.1")

        callback_path = redirect_path or "/auth/callback"
        if not callback_path.startswith("/"):
            callback_path = f"/{callback_path}"
        redirect_uri = f"{base}{callback_path}"
        client_id = (
            "http://localhost?"
            + urlencode({"redirect_uri": redirect_uri, "scope": ATPROTO_SCOPE})
            if loopback
            else f"{base}{CLIENT_METADATA_PATH}"
        )

        if isinstance(jwt_signing_key, str):
            jwt_signing_key = derive_jwt_key(
                low_entropy_material=jwt_signing_key, salt="fastmcp-jwt-signing-key"
            )
        self._identity_issuer = JWTIssuer(
            issuer=base,
            audience=f"{base}{LOGIN_PATH}#identity",
            signing_key=derive_jwt_key(
                high_entropy_material=base64.urlsafe_b64encode(
                    jwt_signing_key
                ).decode(),
                salt="fastmcp-atproto-identity-key",
            ),
        )
        self._allowed_dids = _AllowedDIDs(allowed_dids)

        super().__init__(
            upstream_authorization_endpoint=f"{base}{LOGIN_PATH}",
            upstream_token_endpoint=f"{base}{LOGIN_PATH}",
            upstream_client_id=client_id,
            token_verifier=ATProtoIdentityVerifier(
                issuer=self._identity_issuer,
                allowed_dids=self._allowed_dids,
                required_scopes=required_scopes,
            ),
            base_url=base_url,
            resource_base_url=resource_base_url,
            redirect_path=callback_path,
            issuer_url=issuer_url,
            service_documentation_url=service_documentation_url,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            forward_pkce=False,
            forward_resource=False,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            consent_csp_policy=consent_csp_policy,
            enable_cimd=enable_cimd,
        )

        self._atproto_base = base
        self._atproto_loopback = loopback
        self._atproto_redirect_uri = redirect_uri
        self._atproto_client_name = client_name
        self._identity_token_expiry_seconds = identity_token_expiry_seconds
        self._session_expiry_seconds = session_expiry_seconds
        self._typeahead_url = typeahead_url
        self._resolver = AsyncIdResolver()
        self._atproto = OAuthClient(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=ATPROTO_SCOPE,
            state_store=MemoryStateStore(),
            session_store=MemorySessionStore(),
        )
        self._pending: dict[str, tuple[str, float]] = {}

    # Identity tokens

    def _mint_identity_tokens(
        self, *, did: str, handle: str | None, client_id: str, scopes: list[str]
    ) -> dict[str, Any]:
        access_token = self._identity_issuer.issue_access_token(
            client_id=client_id,
            scopes=scopes,
            jti=secrets.token_urlsafe(24),
            expires_in=self._identity_token_expiry_seconds,
            subject=did,
            extra_claims={"handle": handle},
        )
        refresh_token = self._identity_issuer.issue_access_token(
            client_id=client_id,
            scopes=scopes,
            jti=secrets.token_urlsafe(24),
            expires_in=self._session_expiry_seconds,
            subject=did,
            extra_claims={"handle": handle, "token_use": "refresh"},
        )
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": self._identity_token_expiry_seconds,
            "refresh_expires_in": self._session_expiry_seconds,
            "did": did,
            "handle": handle,
        }

    def _refresh_identity(self, refresh_token: str) -> dict[str, Any]:
        try:
            payload = self._identity_issuer.verify_token(
                refresh_token, expected_token_use="refresh"
            )
        except JoseError as e:
            raise OAuthError(error="invalid_grant", description=str(e)) from e
        did = payload.get("sub")
        if not isinstance(did, str) or did not in self._allowed_dids:
            raise OAuthError(error="invalid_grant", description="Account not allowed")
        access_token = self._identity_issuer.issue_access_token(
            client_id=str(payload.get("client_id") or did),
            scopes=str(payload.get("scope") or "").split(),
            jti=secrets.token_urlsafe(24),
            expires_in=self._identity_token_expiry_seconds,
            subject=did,
            extra_claims={"handle": payload.get("handle")},
        )
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": self._identity_token_expiry_seconds,
            "refresh_token": refresh_token,
        }

    # OAuthProxy hooks

    @override
    def _create_upstream_oauth_client(self) -> AsyncOAuth2Client:
        return _IdentityRefreshClient(self)

    @override
    async def _extract_upstream_claims(
        self, idp_tokens: dict[str, Any]
    ) -> dict[str, Any] | None:
        return {"did": idp_tokens.get("did"), "handle": idp_tokens.get("handle")}

    @override
    def _build_upstream_authorize_url(
        self, txn_id: str, transaction: dict[str, Any]
    ) -> str:
        return f"{self._atproto_base}{LOGIN_PATH}?{urlencode({'txn_id': txn_id})}"

    @override
    def _callback_transaction_id(self, request: Request) -> str | None:
        pending = self._pending.get(request.query_params.get("state", ""))
        if pending is None or pending[1] < time.time():
            return None
        return pending[0]

    @override
    async def _exchange_upstream_code(
        self, request: Request, transaction: dict[str, Any]
    ) -> dict[str, Any]:
        params = request.query_params
        state = params.get("state", "")
        self._pending.pop(state, None)
        session = await self._atproto.handle_callback(
            code=params.get("code", ""), state=state, iss=params.get("iss", "")
        )
        try:
            await self._atproto.revoke_session(session)
        except Exception as e:
            logger.debug("AT Protocol grant revocation failed: %s", e)
        if session.did not in self._allowed_dids:
            raise PermissionError("That account isn't allowed to use this server.")
        handle = session.handle or None
        if handle and await self._resolver.handle.resolve(handle) != session.did:
            handle = None
        return self._mint_identity_tokens(
            did=session.did,
            handle=handle,
            client_id=transaction["client_id"],
            scopes=transaction["scopes"],
        )

    # Routes

    @override
    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        routes.append(
            Route(LOGIN_PATH, endpoint=self._handle_login, methods=["GET", "POST"])
        )
        if not self._atproto_loopback:
            routes.append(
                Route(
                    CLIENT_METADATA_PATH,
                    endpoint=self._handle_client_metadata,
                    methods=["GET"],
                )
            )
        return routes

    def _server_display(self, request: Request) -> tuple[str | None, str | None]:
        server = getattr(request.app.state, "fastmcp_server", None)
        name = getattr(server, "name", None)
        icons = getattr(server, "icons", None) or []
        icon_url = getattr(icons[0], "src", None) if icons else None
        return (name if isinstance(name, str) else None), icon_url

    async def _handle_client_metadata(self, request: Request) -> Response:
        server_name, _ = self._server_display(request)
        return JSONResponse(
            {
                "client_id": self._upstream_client_id,
                "client_name": self._atproto_client_name or server_name or "FastMCP",
                "client_uri": self._atproto_base,
                "redirect_uris": [self._atproto_redirect_uri],
                "scope": ATPROTO_SCOPE,
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "application_type": "web",
                "dpop_bound_access_tokens": True,
            },
            headers={"Cache-Control": "public, max-age=600"},
        )

    async def _render_login(
        self,
        request: Request,
        txn_id: str,
        client_id: str,
        *,
        identifier: str = "",
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        client = await self.get_client(client_id)
        server_name, server_icon_url = self._server_display(request)
        return create_secure_html_response(
            create_login_html(
                txn_id=txn_id,
                action_url=f"{self._atproto_base}{LOGIN_PATH}",
                client_name=getattr(client, "client_name", None) if client else None,
                server_name=server_name,
                server_icon_url=server_icon_url,
                identifier=identifier,
                error=error,
                typeahead_url=self._typeahead_url,
            ),
            status_code=status_code,
        )

    async def _handle_login(self, request: Request) -> Response:
        if request.method == "GET":
            txn_id = request.query_params.get("txn_id", "")
            identifier = ""
        else:
            form = await request.form()
            txn_id = str(form.get("txn_id", ""))
            identifier = _normalize_identifier(str(form.get("identifier", "")))

        transaction = await self._transaction_store.get(key=txn_id) if txn_id else None
        if transaction is None:
            return create_secure_html_response(
                create_error_html(
                    error_title="Sign-in expired",
                    error_message="This sign-in expired. Start again from your app.",
                ),
                status_code=400,
            )

        if request.method == "GET":
            identifier = self._allowed_dids.only or ""
            if not identifier:
                return await self._render_login(request, txn_id, transaction.client_id)

        try:
            authorization_url = await self._start_authorization(txn_id, identifier)
        except _SignInError as e:
            return await self._render_login(
                request,
                txn_id,
                transaction.client_id,
                identifier=identifier,
                error=e.reason,
                status_code=400,
            )
        return RedirectResponse(authorization_url, status_code=303)

    async def _start_authorization(self, txn_id: str, identifier: str) -> str:
        if identifier.startswith("did:"):
            did = identifier
        elif identifier:
            try:
                did = await self._resolver.handle.resolve(identifier)
            except Exception as e:
                logger.debug("Handle resolution failed for %s: %s", identifier, e)
                did = None
            if not did:
                raise _SignInError("handle_not_found")
        else:
            raise _SignInError("invalid_identifier")

        if did not in self._allowed_dids:
            raise _SignInError("not_allowed")

        try:
            authorization_url, state = await self._atproto.start_authorization(did)
        except ValueError as e:
            logger.info("AT Protocol identity for %s could not be resolved: %s", did, e)
            raise _SignInError("identity_unavailable") from e
        except Exception as e:
            logger.info("AT Protocol sign-in for %s could not start: %s", did, e)
            raise _SignInError("server_unavailable") from e

        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v[1] > now}
        self._pending[state] = (txn_id, now + PENDING_TTL_SECONDS)
        return authorization_url
