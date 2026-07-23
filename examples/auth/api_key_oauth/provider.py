"""An OAuth provider that authenticates users by their existing API key.

OAuth-only MCP clients (Claude Desktop, ChatGPT's connectors) cannot send a
custom header to a remote server the way Claude Code can with
`-H "X-API-Key: ..."`. When they hit a server that requires auth, the only
mechanism they implement is the MCP OAuth 2.1 handshake. That leaves services
with a perfectly good API-key auth model unable to reach those clients.

This provider bridges the gap. It speaks full OAuth so the clients are happy,
but the "login" step is not a username/password form — it is a consent page that
names the requesting client and asks the user to paste the API key they already
have. From there the provider reuses the same primitives FastMCP's OAuth proxy
is built on:

- The SDK's `AuthorizationHandler` stays on `/authorize` and performs all the
  standard request validation (response type, PKCE presence, redirect URI,
  scopes). `authorize()` then redirects the browser to our consent page, exactly
  as the proxy redirects to its `/consent` page.
- `derive_jwt_key` turns a configured secret into the HS256 signing key (and a
  Fernet storage-encryption key), so nothing depends on an ephemeral key that a
  restart would invalidate.
- `JWTIssuer` issues FastMCP's own tokens as *reference tokens*: each token
  carries only a `jti`, never the API key itself.
- A Fernet-encrypted key-value store holds the transient transaction, the
  authorization code, and the API key — each keyed and TTL-bound, encrypted at
  rest. The key never appears in a URL or in the issued token; `load_access_token`
  validates the JWT and looks the key back up. (The user submits it once in the
  consent POST, so serve the server over HTTPS.)

Tools read the key from the access token claims (e.g. via `CurrentAccessToken`).

Deployment notes:

- The encrypted store defaults to an on-disk file store (the same default the
  OAuth proxy uses). It is single-host. For a multi-worker or multi-replica
  deployment, pass a shared `client_storage` (e.g. a Redis-backed store) so a
  token issued by one worker resolves on another.
- Registered clients are kept in process memory; they are cheaply re-created via
  dynamic client registration. A production server may prefer to persist them.
"""

from __future__ import annotations

import hashlib
import html
import inspect
import secrets
import time
from collections.abc import Awaitable, Callable

import anyio
from cryptography.fernet import Fernet
from joserfc.errors import JoseError
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from fastmcp import settings
from fastmcp.server.auth.auth import (
    AccessToken,
    ClientRegistrationOptions,
    OAuthProvider,
)
from fastmcp.server.auth.jwt_issuer import JWTIssuer, derive_jwt_key

# The JWT claim the API key is surfaced under, and the store collections that
# hold (transiently) each stage of the flow and (longer) the key itself.
API_KEY_CLAIM = "api_key"
KEY_COLLECTION = "api-keys"
TXN_COLLECTION = "auth-txns"
CODE_COLLECTION = "auth-codes"

# The path of the consent/key-entry page, relative to the server's base URL.
KEY_ENTRY_PATH = "/authorize/key"

TXN_EXPIRY_SECONDS = 15 * 60
AUTH_CODE_EXPIRY_SECONDS = 5 * 60
DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS = 60 * 60
DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS = 60 * 60 * 24 * 30  # 30 days


class APIKeyOAuthProvider(OAuthProvider):
    """OAuth provider whose consent page collects an API key.

    The provider runs the standard OAuth authorization-code flow — the SDK's
    handler validates each authorize request — but the user-facing step is a
    consent page that names the client and asks for an API key instead of a
    password. The key is stored encrypted and bound to the issued token's `jti`;
    tools recover it from the request's access token claims.

    Args:
        base_url: The public URL of this FastMCP server (include any mount path).
            Used as the JWT issuer, the OAuth issuer, and the base for the
            consent page URL.
        jwt_signing_key: A secret string. The HS256 signing key and the storage
            encryption key are both derived from it, so the same secret across
            restarts keeps previously issued tokens valid.
        validate_api_key: Optional callable that receives the pasted key and
            returns True if it is valid. May be sync or async — return a
            coroutine to verify the key against your backend over HTTP. Use it to
            reject bad keys at the consent step instead of minting a token that
            fails later. Defaults to accepting any non-empty key.
        client_storage: Optional key-value store for the encrypted transaction,
            code, and key records. Defaults to an on-disk Fernet-encrypted file
            store. Pass a shared store for multi-worker deployments.
        token_expiry_seconds: Lifetime of issued access tokens.
        refresh_expiry_seconds: Lifetime of issued refresh tokens.
        required_scopes: Scopes required on every request.
    """

    def __init__(
        self,
        *,
        base_url: AnyHttpUrl | str,
        jwt_signing_key: str,
        validate_api_key: Callable[[str], bool | Awaitable[bool]] | None = None,
        client_storage: AsyncKeyValue | None = None,
        token_expiry_seconds: int = DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
        refresh_expiry_seconds: int = DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS,
        required_scopes: list[str] | None = None,
    ):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            required_scopes=required_scopes,
        )

        # Derive the HS256 signing key from the secret (PBKDF2). The JWTIssuer is
        # created in set_mcp_path() once the audience (resource URL) is known.
        self._signing_key = derive_jwt_key(
            low_entropy_material=jwt_signing_key,
            salt="fastmcp-api-key-oauth-signing",
        )
        self._jwt_issuer: JWTIssuer | None = None

        # Encrypted store, defaulting to the same Fernet-wrapped file store the
        # OAuth proxy uses, with the encryption key derived from the same secret.
        if client_storage is None:
            storage_key = derive_jwt_key(
                high_entropy_material=jwt_signing_key,
                salt="fastmcp-storage-encryption-key",
            )
            fingerprint = hashlib.sha256(storage_key).hexdigest()[:12]
            storage_dir = settings.home / "api-key-oauth" / fingerprint
            storage_dir.mkdir(parents=True, exist_ok=True)
            file_store = FileTreeStore(
                data_directory=storage_dir,
                key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(
                    storage_dir
                ),
                collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(
                    storage_dir
                ),
            )
            client_storage = FernetEncryptionWrapper(
                key_value=file_store,
                fernet=Fernet(key=storage_key),
                raise_on_decryption_error=False,
            )
        self._store: AsyncKeyValue = client_storage

        self._validate_api_key = validate_api_key or (lambda key: bool(key))
        self._token_expiry = token_expiry_seconds
        self._refresh_expiry = refresh_expiry_seconds

        self._clients: dict[str, OAuthClientInformationFull] = {}
        # Per-grant locks so concurrent exchanges of the same code or refresh
        # token cannot each consume it and mint a fresh pair.
        self._grant_locks: dict[str, anyio.Lock] = {}

    def set_mcp_path(self, mcp_path: str | None) -> None:
        # Bind the JWT audience to the resource URL, mirroring OAuthProxy.
        super().set_mcp_path(mcp_path)
        self._jwt_issuer = JWTIssuer(
            issuer=str(self.base_url),
            audience=str(self._resource_url),
            signing_key=self._signing_key,
        )

    @property
    def jwt_issuer(self) -> JWTIssuer:
        if self._jwt_issuer is None:
            raise RuntimeError(
                "JWT issuer not initialized; ensure get_routes() has run."
            )
        return self._jwt_issuer

    @property
    def _key_entry_url(self) -> str:
        return f"{str(self.base_url).rstrip('/')}{KEY_ENTRY_PATH}"

    # -- Client registration -------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is required for client registration")
        self._clients[client_info.client_id] = client_info

    # -- Authorization -------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # The SDK's AuthorizationHandler has already validated the request
        # (response type, PKCE presence, redirect URI, scopes). Persist the
        # transaction and send the browser to our consent/key-entry page.
        txn_id = secrets.token_urlsafe(32)
        await self._store.put(
            key=txn_id,
            value={
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "state": params.state or "",
                "code_challenge": params.code_challenge or "",
                "scopes": params.scopes or [],
            },
            collection=TXN_COLLECTION,
            ttl=TXN_EXPIRY_SECONDS,
        )
        return f"{self._key_entry_url}?txn_id={txn_id}"

    async def _render_form(self, request: Request) -> Response:
        """GET the consent page — name the client and ask for the API key."""
        txn_id = request.query_params.get("txn_id", "")
        txn = await self._store.get(key=txn_id, collection=TXN_COLLECTION)
        if txn is None:
            return HTMLResponse("Authorization request expired.", status_code=400)

        client = await self.get_client(txn["client_id"])
        client_name = (client.client_name if client else None) or txn["client_id"]
        page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authorize</title></head>
<body style="font-family: system-ui; max-width: 28rem; margin: 4rem auto;">
  <h2>Authorize {html.escape(client_name)}</h2>
  <p><strong>{html.escape(client_name)}</strong> is requesting access. Paste your
     API key to authorize it. Only do this if you started this connection.</p>
  <form method="post" action="{html.escape(self._key_entry_url, quote=True)}">
    <input type="hidden" name="txn_id" value="{html.escape(txn_id, quote=True)}">
    <input type="password" name="api_key" placeholder="API key"
           autocomplete="off" required
           style="width: 100%; padding: 0.5rem; font-size: 1rem;">
    <button type="submit" style="margin-top: 1rem; padding: 0.5rem 1rem;">
      Authorize
    </button>
  </form>
</body></html>"""
        return HTMLResponse(page)

    async def _handle_submit(self, request: Request) -> Response:
        """POST the consent page — validate the key and issue an auth code."""
        # Block cross-site form submission; the consent action must originate
        # from our own page (or a top-level navigation).
        sec_fetch_site = request.headers.get("sec-fetch-site")
        if sec_fetch_site not in (None, "same-origin", "none"):
            return HTMLResponse("Cross-site authorization blocked.", status_code=403)

        form = await request.form()
        txn_id = str(form.get("txn_id", ""))
        api_key = str(form.get("api_key", ""))

        txn = await self._store.get(key=txn_id, collection=TXN_COLLECTION)
        if txn is None:
            return HTMLResponse("Authorization request expired.", status_code=400)

        result = self._validate_api_key(api_key)
        if inspect.isawaitable(result):
            result = await result
        if not result:
            return RedirectResponse(
                f"{self._key_entry_url}?txn_id={txn_id}", status_code=303
            )

        # Consume the transaction and bind the key to a fresh, opaque code.
        await self._store.delete(key=txn_id, collection=TXN_COLLECTION)
        code = f"code_{secrets.token_hex(16)}"
        await self._store.put(
            key=code,
            value={
                **txn,
                "api_key": api_key,
                "expires_at": time.time() + AUTH_CODE_EXPIRY_SECONDS,
            },
            collection=CODE_COLLECTION,
            ttl=AUTH_CODE_EXPIRY_SECONDS,
        )
        location = construct_redirect_uri(
            txn["redirect_uri"], code=code, state=txn["state"]
        )
        return RedirectResponse(location, status_code=303)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        rec = await self._store.get(key=authorization_code, collection=CODE_COLLECTION)
        if rec is None or rec["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=rec["client_id"],
            redirect_uri=AnyHttpUrl(rec["redirect_uri"]),
            redirect_uri_provided_explicitly=rec["redirect_uri_provided_explicitly"],
            scopes=rec["scopes"],
            expires_at=rec["expires_at"],
            code_challenge=rec["code_challenge"],
        )

    # -- Token issuance ------------------------------------------------------

    async def _issue_tokens(
        self, *, api_key: str, client_id: str, scopes: list[str]
    ) -> OAuthToken:
        """Mint a reference-token pair and store the key encrypted under each jti."""
        access_jti = secrets.token_urlsafe(16)
        refresh_jti = secrets.token_urlsafe(16)
        record = {"api_key": api_key, "client_id": client_id}

        await self._store.put(
            key=access_jti,
            value=record,
            collection=KEY_COLLECTION,
            ttl=self._token_expiry,
        )
        await self._store.put(
            key=refresh_jti,
            value=record,
            collection=KEY_COLLECTION,
            ttl=self._refresh_expiry,
        )

        access_token = self.jwt_issuer.issue_access_token(
            client_id=client_id,
            scopes=scopes,
            jti=access_jti,
            expires_in=self._token_expiry,
        )
        refresh_token = self.jwt_issuer.issue_refresh_token(
            client_id=client_id,
            scopes=scopes,
            jti=refresh_jti,
            expires_in=self._refresh_expiry,
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self._token_expiry,
            refresh_token=refresh_token,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        code = authorization_code.code
        lock_key = f"code:{code}"
        # Serialize concurrent exchanges of the same code so only the first
        # consumes it; the rest find it gone and are rejected.
        try:
            async with self._grant_lock(lock_key):
                rec = await self._store.get(key=code, collection=CODE_COLLECTION)
                # Consume the code so it cannot be replayed.
                await self._store.delete(key=code, collection=CODE_COLLECTION)
                if rec is None:
                    raise TokenError(
                        "invalid_grant", "Authorization code not found or used."
                    )
                return await self._issue_tokens(
                    api_key=rec["api_key"],
                    client_id=client.client_id or "",
                    scopes=authorization_code.scopes,
                )
        finally:
            self._grant_locks.pop(lock_key, None)

    def _grant_lock(self, key: str) -> anyio.Lock:
        lock = self._grant_locks.get(key)
        if lock is None:
            lock = anyio.Lock()
            self._grant_locks[key] = lock
        return lock

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        try:
            payload = self.jwt_issuer.verify_token(
                refresh_token, expected_token_use="refresh"
            )
        except JoseError:
            return None
        if payload.get("client_id") != client.client_id:
            return None
        if await self._store.get(key=payload["jti"], collection=KEY_COLLECTION) is None:
            return None
        scope = payload.get("scope", "")
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id or "",
            scopes=scope.split() if scope else [],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        try:
            payload = self.jwt_issuer.verify_token(
                refresh_token.token, expected_token_use="refresh"
            )
        except JoseError as exc:
            raise TokenError("invalid_grant", "Invalid refresh token.") from exc
        jti = payload["jti"]

        if not set(scopes).issubset(set(refresh_token.scopes)):
            raise TokenError("invalid_scope", "Requested scopes exceed grant.")
        granted = scopes or refresh_token.scopes

        lock_key = f"refresh:{jti}"
        # Serialize refreshes of the same token so concurrent calls cannot each
        # mint a fresh pair from one refresh token.
        try:
            async with self._grant_lock(lock_key):
                record = await self._store.get(key=jti, collection=KEY_COLLECTION)
                if record is None:
                    raise TokenError("invalid_grant", "Refresh token not found.")
                # Rotate: invalidate this refresh token's stored key.
                await self._store.delete(key=jti, collection=KEY_COLLECTION)
                return await self._issue_tokens(
                    api_key=record["api_key"],
                    client_id=record["client_id"],
                    scopes=granted,
                )
        finally:
            self._grant_locks.pop(lock_key, None)

    # -- Verification & revocation -------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        try:
            payload = self.jwt_issuer.verify_token(token)
        except JoseError:
            return None

        record = await self._store.get(key=payload["jti"], collection=KEY_COLLECTION)
        if record is None:
            return None

        scope = payload.get("scope", "")
        # Surface the decrypted key on the token's claims so tools can read it
        # via get_access_token(). It lives only in memory here, never in the token.
        return AccessToken(
            token=token,
            client_id=payload.get("client_id", ""),
            scopes=scope.split() if scope else [],
            expires_at=payload.get("exp"),
            claims={API_KEY_CLAIM: record["api_key"]},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        try:
            payload = self.jwt_issuer.verify_token(
                token.token,
                expected_token_use="refresh"
                if isinstance(token, RefreshToken)
                else "access",
            )
        except JoseError:
            return
        await self._store.delete(key=payload["jti"], collection=KEY_COLLECTION)

    # -- Routes --------------------------------------------------------------

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        # Keep the SDK's /authorize route (it validates the request); add the
        # consent/key-entry page authorize() redirects to.
        routes = super().get_routes(mcp_path)
        routes.append(Route(KEY_ENTRY_PATH, self._render_form, methods=["GET"]))
        routes.append(Route(KEY_ENTRY_PATH, self._handle_submit, methods=["POST"]))
        return routes
