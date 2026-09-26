"""AT Protocol OAuth provider for FastMCP.

MCP clients see an ordinary OAuth 2.1 authorization server with dynamic client
registration and client ID metadata documents, inherited from `OAuthProxy`.
Where `OAuthProxy` would redirect to a fixed upstream IdP, this provider shows
a handle sign-in page, runs AT Protocol OAuth against the account's own
authorization server (PAR, PKCE, DPoP, `atproto` scope), and keeps only the
verified DID. The PDS grant is revoked immediately; sessions are represented
by short-lived identity tokens this server signs and re-checks against the
DID allowlist on every request.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.atproto import ATProtoProvider

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
import secrets
import time
from collections.abc import Iterable
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

from joserfc.errors import JoseError
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl, BaseModel
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from typing_extensions import override

from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.jwt_issuer import JWTIssuer, derive_jwt_key
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import (
    DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
    ClientCode,
    OAuthTransaction,
)
from fastmcp.server.auth.oauth_proxy.ui import create_error_html
from fastmcp.server.auth.oauth_proxy.upstream import AsyncOAuth2Client, OAuthError
from fastmcp.server.auth.providers.atproto.identity import (
    ATProtoError,
    AuthorizationServer,
    discover_authorization_server,
    is_did,
    is_handle,
    normalize_identifier,
    resolve_did,
    resolve_handle,
)
from fastmcp.server.auth.providers.atproto.login import create_login_html
from fastmcp.server.auth.providers.atproto.oauth import (
    ATPROTO_SCOPE,
    DPoPSession,
    exchange_code,
    push_authorization_request,
    revoke_grant,
)
from fastmcp.server.auth.redirect_validation import build_client_redirect
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.ui import create_secure_html_response

logger = get_logger(__name__)

DEFAULT_HANDLE_RESOLVER_URL = "https://public.api.bsky.app"
DEFAULT_PLC_DIRECTORY_URL = "https://plc.directory"
DEFAULT_TYPEAHEAD_URL = (
    "https://typeahead.waow.tech/xrpc/app.bsky.actor.searchActorsTypeahead"
)
LOGIN_PATH = "/atproto/login"
CLIENT_METADATA_PATH = "/oauth-client-metadata.json"
FLOW_TTL_SECONDS = 15 * 60


class ATProtoFlow(BaseModel):
    """Server-side state for one AT Protocol authorization, keyed by `state`."""

    state: str
    txn_id: str
    did: str
    handle: str | None
    issuer: str
    token_endpoint: str
    revocation_endpoint: str | None
    code_verifier: str
    dpop_jwk: dict[str, Any]
    dpop_nonce: str | None
    created_at: float


class _AllowedDIDs:
    def __init__(self, dids: Iterable[str] | None) -> None:
        if dids is None:
            self._dids: frozenset[str] | None = None
            return
        dids = frozenset(dids)
        invalid = sorted(did for did in dids if not is_did(did))
        if invalid:
            raise ValueError(f"allowed_dids must contain DIDs, got: {invalid}")
        self._dids = dids

    def __contains__(self, did: object) -> bool:
        return self._dids is None or did in self._dids


class ATProtoIdentityVerifier(TokenVerifier):
    """Verifies the identity tokens `ATProtoProvider` signs after sign-in.

    Re-checks the DID allowlist on every call, so removing a DID takes effect
    on that account's next request.
    """

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
    """Stands in for the upstream token endpoint when `OAuthProxy` refreshes.

    The PDS grant was revoked at sign-in, so "refreshing upstream" means
    minting a new identity token from this server's own refresh token.
    """

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
            ``http://127.0.0.1:<port>``; AT Protocol treats that as a loopback
            client and needs no hosted metadata. ``localhost`` is rejected
            because authorization servers refuse it as a redirect host.
        jwt_signing_key: Secret for the tokens this server issues. Keep it
            stable across restarts or every session ends on restart.
        allowed_dids: DIDs allowed to sign in. ``None`` allows any account.
            Handles are not accepted here because they can change hands.
        client_name: Name shown on the account's authorization screen.
            Defaults to the FastMCP server's name.
        identity_token_expiry_seconds: Lifetime of each access token.
        session_expiry_seconds: How long a sign-in lasts before the user must
            sign in again.
        handle_resolver_url: Service used for ``com.atproto.identity.resolveHandle``
            when a handle has no ``/.well-known/atproto-did``. ``None`` disables it.
        plc_directory_url: PLC directory used to resolve ``did:plc`` identities.
        typeahead_url: ``app.bsky.actor.searchActorsTypeahead`` endpoint for the
            login page's suggestions. ``None`` disables suggestions.

    The remaining arguments match `OAuthProxy`.
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
        handle_resolver_url: str | None = DEFAULT_HANDLE_RESOLVER_URL,
        plc_directory_url: str = DEFAULT_PLC_DIRECTORY_URL,
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
        self._atproto_loopback = parsed.scheme == "http" and parsed.hostname in (
            "127.0.0.1",
            "::1",
        )
        if parsed.scheme != "https" and not self._atproto_loopback:
            raise ValueError("base_url must use HTTPS outside of 127.0.0.1")

        callback_path = redirect_path or "/auth/callback"
        if not callback_path.startswith("/"):
            callback_path = f"/{callback_path}"
        self._atproto_redirect_uri = f"{base}{callback_path}"
        if self._atproto_loopback:
            atproto_client_id = "http://localhost?" + urlencode(
                {"redirect_uri": self._atproto_redirect_uri, "scope": ATPROTO_SCOPE}
            )
        else:
            atproto_client_id = f"{base}{CLIENT_METADATA_PATH}"

        if isinstance(jwt_signing_key, str):
            jwt_signing_key = derive_jwt_key(
                low_entropy_material=jwt_signing_key, salt="fastmcp-jwt-signing-key"
            )
        identity_key = derive_jwt_key(
            high_entropy_material=base64.urlsafe_b64encode(jwt_signing_key).decode(),
            salt="fastmcp-atproto-identity-key",
        )
        self._identity_issuer = JWTIssuer(
            issuer=base,
            audience=f"{base}{LOGIN_PATH}#identity",
            signing_key=identity_key,
        )
        self._allowed_dids = _AllowedDIDs(allowed_dids)
        if allowed_dids is None:
            logger.info("ATProtoProvider allows any AT Protocol account to sign in")

        super().__init__(
            upstream_authorization_endpoint=f"{base}{LOGIN_PATH}",
            upstream_token_endpoint=f"{base}{LOGIN_PATH}",
            upstream_client_id=atproto_client_id,
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
        self._atproto_client_name = client_name
        self._identity_token_expiry_seconds = identity_token_expiry_seconds
        self._session_expiry_seconds = session_expiry_seconds
        self._handle_resolver_url = handle_resolver_url
        self._plc_directory_url = plc_directory_url
        self._typeahead_url = typeahead_url
        self._flow_store: PydanticAdapter[ATProtoFlow] = PydanticAdapter[ATProtoFlow](
            key_value=self._client_storage,
            pydantic_model=ATProtoFlow,
            default_collection="mcp-atproto-flows",
            raise_on_validation_error=True,
        )

    # -------------------------------------------------------------------------
    # Identity tokens
    # -------------------------------------------------------------------------

    def _mint_identity_tokens(
        self, *, did: str, handle: str | None, client_id: str, scopes: list[str]
    ) -> dict[str, Any]:
        claims = {"handle": handle}
        access_token = self._identity_issuer.issue_access_token(
            client_id=client_id,
            scopes=scopes,
            jti=secrets.token_urlsafe(24),
            expires_in=self._identity_token_expiry_seconds,
            subject=did,
            extra_claims=claims,
        )
        refresh_token = self._identity_issuer.issue_access_token(
            client_id=client_id,
            scopes=scopes,
            jti=secrets.token_urlsafe(24),
            expires_in=self._session_expiry_seconds,
            subject=did,
            extra_claims={**claims, "token_use": "refresh"},
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
        scope = str(payload.get("scope") or "")
        access_token = self._identity_issuer.issue_access_token(
            client_id=str(payload.get("client_id") or did),
            scopes=scope.split(),
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

    # -------------------------------------------------------------------------
    # Routes
    # -------------------------------------------------------------------------

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

    async def _load_bound_transaction(
        self, request: Request, txn_id: str
    ) -> OAuthTransaction | HTMLResponse:
        """Load a transaction, requiring the browser that approved consent."""
        transaction = await self._transaction_store.get(key=txn_id) if txn_id else None
        if transaction is None:
            return create_secure_html_response(
                create_error_html(
                    error_title="Sign-in expired",
                    error_message="This sign-in expired. Start again from your app.",
                ),
                status_code=400,
            )
        if not self._validate_client_redirect_uri(transaction.client_redirect_uri):
            return create_secure_html_response(
                create_error_html(
                    error_title="OAuth Error", error_message="Invalid redirect URI"
                ),
                status_code=400,
            )
        if self._require_authorization_consent in (True, "remember") and not (
            transaction.consent_token
            and self._verify_consent_binding_cookie(
                request, txn_id, transaction.consent_token
            )
        ):
            logger.warning(
                "AT Protocol sign-in without consent binding for transaction %s",
                txn_id,
            )
            return create_secure_html_response(
                create_error_html(
                    error_title="Authorization Error",
                    error_message=(
                        "Authorization session mismatch. This can happen if you "
                        "followed a link from another person or your session expired. "
                        "Please try authenticating again."
                    ),
                ),
                status_code=403,
            )
        return transaction

    async def _render_login(
        self,
        request: Request,
        transaction: OAuthTransaction,
        *,
        identifier: str = "",
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        client = await self.get_client(transaction.client_id)
        client_name = getattr(client, "client_name", None) if client else None
        server_name, server_icon_url = self._server_display(request)
        return create_secure_html_response(
            create_login_html(
                txn_id=transaction.txn_id,
                action_url=f"{self._atproto_base}{LOGIN_PATH}",
                client_name=client_name,
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
            loaded = await self._load_bound_transaction(request, txn_id)
            if isinstance(loaded, HTMLResponse):
                return loaded
            return await self._render_login(
                request, loaded, error=request.query_params.get("error")
            )

        form = await request.form()
        txn_id = str(form.get("txn_id", ""))
        loaded = await self._load_bound_transaction(request, txn_id)
        if isinstance(loaded, HTMLResponse):
            return loaded
        identifier = normalize_identifier(str(form.get("identifier", "")))
        try:
            authorization_url = await self._start_authorization(txn_id, identifier)
        except ATProtoError as e:
            logger.info("AT Protocol sign-in could not start: %s", e)
            return await self._render_login(
                request,
                loaded,
                identifier=identifier,
                error=e.reason,
                status_code=400,
            )
        return RedirectResponse(authorization_url, status_code=303)

    async def _start_authorization(self, txn_id: str, identifier: str) -> str:
        if is_did(identifier):
            did = identifier
        elif is_handle(identifier):
            did = await resolve_handle(
                identifier, handle_resolver_url=self._handle_resolver_url
            )
        else:
            raise ATProtoError(
                "invalid_identifier", f"Not a handle or DID: {identifier!r}"
            )

        if did not in self._allowed_dids:
            raise ATProtoError("not_allowed", f"{did} is not in allowed_dids")

        identity = await resolve_did(did, plc_directory_url=self._plc_directory_url)
        server = await discover_authorization_server(identity.pds_url)

        dpop = DPoPSession()
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        authorization_url = await push_authorization_request(
            server,
            dpop,
            client_id=self._upstream_client_id,
            redirect_uri=self._atproto_redirect_uri,
            state=state,
            code_verifier=code_verifier,
            login_hint=identity.handle or did,
        )
        await self._flow_store.put(
            key=state,
            value=ATProtoFlow(
                state=state,
                txn_id=txn_id,
                did=did,
                handle=identity.handle,
                issuer=server.issuer,
                token_endpoint=server.token_endpoint,
                revocation_endpoint=server.revocation_endpoint,
                code_verifier=code_verifier,
                dpop_jwk=dpop.private_jwk,
                dpop_nonce=dpop.nonce,
                created_at=time.time(),
            ),
            ttl=FLOW_TTL_SECONDS,
        )
        return authorization_url

    @override
    async def _handle_idp_callback(
        self, request: Request
    ) -> HTMLResponse | RedirectResponse:
        """Finish AT Protocol sign-in and hand an authorization code to the MCP client."""
        params = request.query_params
        state = params.get("state")
        flow = await self._flow_store.get(key=state) if state else None
        if flow is None:
            return create_secure_html_response(
                create_error_html(
                    error_title="Sign-in expired",
                    error_message="This sign-in expired. Start again from your app.",
                ),
                status_code=400,
            )
        await self._flow_store.delete(key=flow.state)

        loaded = await self._load_bound_transaction(request, flow.txn_id)
        if isinstance(loaded, HTMLResponse):
            return loaded
        transaction = loaded

        def retry(reason: str) -> RedirectResponse:
            query = urlencode({"txn_id": flow.txn_id, "error": reason})
            return RedirectResponse(
                f"{self._atproto_base}{LOGIN_PATH}?{query}", status_code=303
            )

        if params.get("iss") != flow.issuer:
            logger.warning(
                "AT Protocol callback issuer mismatch: expected %s", flow.issuer
            )
            return retry("server_unavailable")
        if error := params.get("error"):
            logger.info("AT Protocol authorization returned error: %s", error)
            return retry("denied" if error == "access_denied" else "server_unavailable")
        code = params.get("code")
        if not code:
            return retry("server_unavailable")

        server = AuthorizationServer(
            issuer=flow.issuer,
            pushed_authorization_request_endpoint="",
            authorization_endpoint="",
            token_endpoint=flow.token_endpoint,
            revocation_endpoint=flow.revocation_endpoint,
        )
        dpop = DPoPSession(flow.dpop_jwk, flow.dpop_nonce)
        try:
            token_response = await exchange_code(
                server,
                dpop,
                client_id=self._upstream_client_id,
                redirect_uri=self._atproto_redirect_uri,
                code=code,
                code_verifier=flow.code_verifier,
            )
        except ATProtoError as e:
            logger.warning("AT Protocol token exchange failed: %s", e)
            return retry(e.reason)

        await revoke_grant(
            server,
            dpop,
            client_id=self._upstream_client_id,
            token_response=token_response,
        )

        did = token_response["sub"]
        if did != flow.did:
            logger.warning(
                "AT Protocol token subject %s does not match requested %s",
                did,
                flow.did,
            )
            return retry("identity_unavailable")
        if did not in self._allowed_dids:
            return retry("not_allowed")

        idp_tokens = self._mint_identity_tokens(
            did=did,
            handle=flow.handle,
            client_id=transaction.client_id,
            scopes=transaction.scopes,
        )

        client_code = secrets.token_urlsafe(32)
        await self._code_store.put(
            key=client_code,
            value=ClientCode(
                code=client_code,
                client_id=transaction.client_id,
                redirect_uri=transaction.client_redirect_uri,
                code_challenge=transaction.code_challenge,
                code_challenge_method=transaction.code_challenge_method,
                scopes=transaction.scopes,
                idp_tokens=idp_tokens,
                expires_at=int(time.time() + DEFAULT_AUTH_CODE_EXPIRY_SECONDS),
                created_at=time.time(),
            ),
            ttl=DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
        )
        await self._transaction_store.delete(key=transaction.txn_id)

        response = RedirectResponse(
            url=build_client_redirect(
                transaction.client_redirect_uri,
                {"code": client_code, "state": transaction.client_state},
                iss=str(self.issuer_url),
            ),
            status_code=302,
        )
        self._clear_consent_binding_cookie(request, response, transaction.txn_id)
        return response
