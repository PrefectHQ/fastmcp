"""Machine-to-machine (M2M) OAuth client authentication for FastMCP.

These providers authenticate a FastMCP client to a protected MCP server without
a browser, using the OAuth 2.0 ``client_credentials`` grant:

- `ClientCredentialsOAuthProvider` authenticates with a ``client_id`` and
  ``client_secret`` (the common M2M case).
- `PrivateKeyJWTOAuthProvider` authenticates with an RFC 7523 ``private_key_jwt``
  client assertion (workload identity federation, or a locally signed JWT).

Both are thin wrappers over the MCP SDK's client-credentials providers. Like the
interactive `OAuth` provider, they can be constructed without an ``mcp_url`` and
bound to the server URL automatically when passed to `Client(auth=...)`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextvars import ContextVar
from typing import Literal

import httpx2
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from mcp.client.auth.extensions.client_credentials import (
    ClientCredentialsOAuthProvider as _SDKClientCredentialsOAuthProvider,
)
from mcp.client.auth.extensions.client_credentials import (
    PrivateKeyJWTOAuthProvider as _SDKPrivateKeyJWTOAuthProvider,
)
from mcp.client.auth.extensions.client_credentials import (
    SignedJWTParameters,
    static_assertion_provider,
)
from mcp.client.auth.oauth2 import OAuthContext
from mcp.client.auth.utils import extract_field_from_www_auth
from typing_extensions import override

from fastmcp.client.auth.oauth import TokenStorageAdapter

__all__ = [
    "ClientCredentialsOAuthProvider",
    "PrivateKeyJWTOAuthProvider",
    "SignedJWTParameters",
    "static_assertion_provider",
]

# Whether the auth flow currently being driven is a 403 step-up rather than an
# initial authorization. A ContextVar (not instance state) so it stays scoped to
# the single flow driving it: concurrent flows run in separate tasks and never
# see each other's value, and each flow resets it on exit.
_in_step_up: ContextVar[bool] = ContextVar("fastmcp_m2m_in_step_up", default=False)


def _normalize_scopes(scopes: str | list[str] | None) -> str | None:
    """Normalize scopes to a space-separated string (or None)."""
    if isinstance(scopes, list):
        return " ".join(scopes)
    return scopes


def _cache_namespace(client_id: str, scopes: str | None) -> str:
    """Namespace cached tokens by both client identity and requested scopes.

    Two providers that differ in either their ``client_id`` or their requested
    scopes must not share cached tokens: a token issued for one client or one
    scope set is not interchangeable with another. Hashing a canonical
    ``(client_id, scopes)`` pair keeps the namespace unambiguous regardless of the
    characters either value contains.
    """
    identity = json.dumps([client_id, scopes], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def _resolve_token_storage(
    token_storage: AsyncKeyValue | None,
    mcp_url: str,
    client_id: str,
    scopes: str | None,
) -> TokenStorageAdapter:
    """Wrap a token store in the FastMCP adapter, defaulting to in-memory.

    Unlike the interactive `OAuth` provider, M2M providers do not warn when using
    in-memory storage: re-acquiring a token is a single non-interactive request,
    so losing the cache on restart is cheap rather than disruptive.

    The cache is namespaced by client identity and requested scopes so that
    providers with different credentials or scope sets can share one store against
    the same MCP endpoint without overwriting each other's tokens.
    """
    store = token_storage or MemoryStore()
    return TokenStorageAdapter(
        async_key_value=store,
        server_url=mcp_url,
        cache_namespace=_cache_namespace(client_id, scopes),
    )


def _is_insufficient_scope_challenge(response: httpx2.Response) -> bool:
    """True when a response is an RFC 6750 ``insufficient_scope`` step-up challenge."""
    if response.status_code != 403:
        return False
    return extract_field_from_www_auth(response, "error") == "insufficient_scope"


async def _restore_token_expiry(context: OAuthContext) -> None:
    """Restore the persisted absolute token expiry after a token is reloaded.

    The inherited initializer reloads the stored token but not its expiry, so a
    provider recreated with persistent storage would treat an already-expired
    token as still valid. Reading the absolute expiry back keeps `is_token_valid`
    honest, prompting a fresh token request when the stored one has expired.

    The restore is skipped unless the reloaded token itself declares an
    `expires_in`. A token whose response omitted `expires_in` (``None``) is
    non-expiring, and the store may still hold a stale expiry from a previous
    token it replaced; applying that would wrongly force a re-exchange. A token
    that declares `expires_in=0` is immediately expired and keeps its recorded
    expiry, so it is distinguished from an omitted one.
    """
    storage = context.storage
    tokens = context.current_tokens
    if tokens is None or tokens.expires_in is None:
        return
    if not isinstance(storage, TokenStorageAdapter):
        return
    expiry = await storage.get_token_expiry()
    if expiry is not None:
        context.token_expiry_time = expiry


async def _drive_flow_tracking_step_up(
    flow: AsyncGenerator[httpx2.Request, httpx2.Response],
) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
    """Delegate to the inherited auth flow, flagging step-up challenges.

    On a 403 ``insufficient_scope`` the inherited flow unions the challenged scope
    with the current one before re-requesting the token. Setting `_in_step_up`
    lets `_perform_authorization` leave the accumulated scope in place instead of
    re-pinning the caller's explicit scopes over it. The flag is set and reset
    inside this generator, so it is scoped to exactly this flow.
    """
    token = _in_step_up.set(False)
    try:
        try:
            outgoing = await anext(flow)
        except StopAsyncIteration:
            return
        while True:
            response = yield outgoing
            if _is_insufficient_scope_challenge(response):
                _in_step_up.set(True)
            try:
                outgoing = await flow.asend(response)
            except StopAsyncIteration:
                return
    finally:
        _in_step_up.reset(token)
        await flow.aclose()


class ClientCredentialsOAuthProvider(_SDKClientCredentialsOAuthProvider):
    """OAuth ``client_credentials`` provider using a client ID and secret.

    This is the standard machine-to-machine flow: the client exchanges its
    ``client_id`` and ``client_secret`` at the authorization server's token
    endpoint for an access token, which is then attached to every request. The
    token endpoint is discovered from the MCP server's OAuth metadata, so callers
    provide the MCP server URL rather than a raw token endpoint.

    Example:
        ```python
        from fastmcp import Client
        from fastmcp.client.auth import ClientCredentialsOAuthProvider

        auth = ClientCredentialsOAuthProvider(
            client_id="my-client-id",
            client_secret="my-client-secret",
            scopes=["read", "write"],
        )

        async with Client("https://example.com/mcp", auth=auth) as client:
            await client.list_tools()
        ```
    """

    _bound: bool

    def __init__(
        self,
        mcp_url: str | None = None,
        *,
        client_id: str,
        client_secret: str,
        scopes: str | list[str] | None = None,
        token_endpoint_auth_method: Literal[
            "client_secret_basic", "client_secret_post"
        ] = "client_secret_basic",
        token_storage: AsyncKeyValue | None = None,
    ) -> None:
        """Initialize a client_credentials OAuth provider.

        Args:
            mcp_url: Full URL to the MCP endpoint (e.g. "https://host/mcp").
                Optional when the provider is passed to `Client(auth=...)`, which
                supplies the URL automatically from the transport.
            client_id: The pre-registered OAuth client ID.
            client_secret: The OAuth client secret.
            scopes: OAuth scopes to request, as a space-separated string or a list
                of strings.
            token_endpoint_auth_method: How client credentials are presented to the
                token endpoint. "client_secret_basic" (default) sends them in an
                HTTP Basic ``Authorization`` header; "client_secret_post" sends them
                in the request body.
            token_storage: An AsyncKeyValue-compatible token store. Tokens are kept
                in memory if not provided.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = _normalize_scopes(scopes)
        self._token_endpoint_auth_method = token_endpoint_auth_method
        self._token_storage = token_storage
        self._bound = False

        if mcp_url is not None:
            self._bind(mcp_url)

    def _bind(self, mcp_url: str) -> None:
        """Bind this provider to a specific MCP server URL.

        Called automatically when ``mcp_url`` is provided to ``__init__``, or by the
        transport when the provider is used without an explicit URL.
        """
        if self._bound:
            return

        mcp_url = mcp_url.rstrip("/")
        super().__init__(
            server_url=mcp_url,
            storage=_resolve_token_storage(
                self._token_storage, mcp_url, self._client_id, self._scopes
            ),
            client_id=self._client_id,
            client_secret=self._client_secret,
            token_endpoint_auth_method=self._token_endpoint_auth_method,
            scopes=self._scopes,
        )
        self._bound = True

    @override
    async def _initialize(self) -> None:
        await super()._initialize()
        await _restore_token_expiry(self.context)

    @override
    def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if not self._bound:
            raise RuntimeError(
                "ClientCredentialsOAuthProvider has no server URL. Either pass "
                "mcp_url to the constructor or use it with Client(auth=...), which "
                "provides the URL automatically from the transport."
            )
        return _drive_flow_tracking_step_up(super().async_auth_flow(request))

    @override
    async def _perform_authorization(self) -> httpx2.Request:
        # The inherited flow overwrites client_metadata.scope with the
        # server-advertised scopes during 401 handling. Restore the caller's
        # explicit scopes so the token request carries what the caller asked for.
        # On a step-up the SDK unions the challenged scope with the current one;
        # leave that accumulated scope in place instead of clobbering it.
        if self._scopes is not None and not _in_step_up.get():
            self.context.client_metadata.scope = self._scopes
        return await super()._perform_authorization()


class PrivateKeyJWTOAuthProvider(_SDKPrivateKeyJWTOAuthProvider):
    """OAuth ``client_credentials`` provider using ``private_key_jwt`` (RFC 7523).

    Instead of a shared client secret, the client authenticates to the token
    endpoint with a signed JWT assertion. The ``assertion_provider`` callback
    receives the authorization server's issuer identifier (the required JWT
    audience) and returns the assertion. Use
    `SignedJWTParameters.create_assertion_provider()` to sign locally with a
    private key, `static_assertion_provider()` for a pre-built JWT, or supply your
    own callback for workload identity federation.

    Example:
        ```python
        from pathlib import Path

        from fastmcp import Client
        from fastmcp.client.auth import (
            PrivateKeyJWTOAuthProvider,
            SignedJWTParameters,
        )

        private_key_pem = Path("client-signing-key.pem").read_text()

        jwt_params = SignedJWTParameters(
            issuer="my-client-id",
            subject="my-client-id",
            signing_key=private_key_pem,
        )
        auth = PrivateKeyJWTOAuthProvider(
            client_id="my-client-id",
            assertion_provider=jwt_params.create_assertion_provider(),
        )

        async with Client("https://example.com/mcp", auth=auth) as client:
            await client.list_tools()
        ```
    """

    _bound: bool

    def __init__(
        self,
        mcp_url: str | None = None,
        *,
        client_id: str,
        assertion_provider: Callable[[str], Awaitable[str]],
        scopes: str | list[str] | None = None,
        token_storage: AsyncKeyValue | None = None,
    ) -> None:
        """Initialize a private_key_jwt OAuth provider.

        Args:
            mcp_url: Full URL to the MCP endpoint (e.g. "https://host/mcp").
                Optional when the provider is passed to `Client(auth=...)`, which
                supplies the URL automatically from the transport.
            client_id: The OAuth client ID.
            assertion_provider: Async callback that receives the authorization
                server's issuer identifier (the JWT audience) and returns a signed
                JWT assertion. Use `SignedJWTParameters.create_assertion_provider()`
                for locally signed JWTs, `static_assertion_provider()` for a
                pre-built JWT, or provide your own callback for workload identity
                federation.
            scopes: OAuth scopes to request, as a space-separated string or a list
                of strings.
            token_storage: An AsyncKeyValue-compatible token store. Tokens are kept
                in memory if not provided.
        """
        self._client_id = client_id
        self._assertion_provider = assertion_provider
        self._scopes = _normalize_scopes(scopes)
        self._token_storage = token_storage
        self._bound = False

        if mcp_url is not None:
            self._bind(mcp_url)

    def _bind(self, mcp_url: str) -> None:
        """Bind this provider to a specific MCP server URL.

        Called automatically when ``mcp_url`` is provided to ``__init__``, or by the
        transport when the provider is used without an explicit URL.
        """
        if self._bound:
            return

        mcp_url = mcp_url.rstrip("/")
        super().__init__(
            server_url=mcp_url,
            storage=_resolve_token_storage(
                self._token_storage, mcp_url, self._client_id, self._scopes
            ),
            client_id=self._client_id,
            assertion_provider=self._assertion_provider,
            scopes=self._scopes,
        )
        self._bound = True

    @override
    async def _initialize(self) -> None:
        await super()._initialize()
        await _restore_token_expiry(self.context)

    @override
    def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if not self._bound:
            raise RuntimeError(
                "PrivateKeyJWTOAuthProvider has no server URL. Either pass mcp_url "
                "to the constructor or use it with Client(auth=...), which provides "
                "the URL automatically from the transport."
            )
        return _drive_flow_tracking_step_up(super().async_auth_flow(request))

    @override
    async def _perform_authorization(self) -> httpx2.Request:
        # The inherited flow overwrites client_metadata.scope with the
        # server-advertised scopes during 401 handling. Restore the caller's
        # explicit scopes so the token request carries what the caller asked for.
        # On a step-up the SDK unions the challenged scope with the current one;
        # leave that accumulated scope in place instead of clobbering it.
        if self._scopes is not None and not _in_step_up.get():
            self.context.client_metadata.scope = self._scopes
        return await super()._perform_authorization()
