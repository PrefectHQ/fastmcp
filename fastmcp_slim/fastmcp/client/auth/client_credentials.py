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

from collections.abc import AsyncGenerator, Awaitable, Callable
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
from typing_extensions import override

from fastmcp.client.auth.oauth import TokenStorageAdapter
from fastmcp.utilities.logging import get_logger

__all__ = [
    "ClientCredentialsOAuthProvider",
    "PrivateKeyJWTOAuthProvider",
    "SignedJWTParameters",
    "static_assertion_provider",
]

logger = get_logger(__name__)


def _normalize_scopes(scopes: str | list[str] | None) -> str | None:
    """Normalize scopes to a space-separated string (or None)."""
    if isinstance(scopes, list):
        return " ".join(scopes)
    return scopes


def _resolve_token_storage(
    token_storage: AsyncKeyValue | None, mcp_url: str
) -> TokenStorageAdapter:
    """Wrap a token store in the FastMCP adapter, defaulting to in-memory.

    Unlike the interactive `OAuth` provider, M2M providers do not warn when using
    in-memory storage: re-acquiring a token is a single non-interactive request,
    so losing the cache on restart is cheap rather than disruptive.
    """
    store = token_storage or MemoryStore()
    return TokenStorageAdapter(async_key_value=store, server_url=mcp_url)


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
            storage=_resolve_token_storage(self._token_storage, mcp_url),
            client_id=self._client_id,
            client_secret=self._client_secret,
            token_endpoint_auth_method=self._token_endpoint_auth_method,
            scopes=self._scopes,
        )
        self._bound = True

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
        return super().async_auth_flow(request)


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
        from fastmcp import Client
        from fastmcp.client.auth import (
            PrivateKeyJWTOAuthProvider,
            SignedJWTParameters,
        )

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
            storage=_resolve_token_storage(self._token_storage, mcp_url),
            client_id=self._client_id,
            assertion_provider=self._assertion_provider,
            scopes=self._scopes,
        )
        self._bound = True

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
        return super().async_auth_flow(request)
