from __future__ import annotations

import time
import webbrowser
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse

import anyio
import httpx
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyHttpUrl
from typing_extensions import override
from uvicorn.server import Server

from fastmcp.client.oauth_callback import (
    OAuthCallbackResult,
    create_oauth_callback_server,
)
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.logging import get_logger

__all__ = ["OAuth"]

logger = get_logger(__name__)


class ClientNotFoundError(Exception):
    """Raised when OAuth client credentials are not found on the server."""


async def check_if_auth_required(
    mcp_url: str, httpx_kwargs: dict[str, Any] | None = None
) -> bool:
    """
    Check if the MCP endpoint requires authentication by making a test request.

    Returns:
        True if auth appears to be required, False otherwise
    """
    async with httpx.AsyncClient(**(httpx_kwargs or {})) as client:
        try:
            # Try a simple request to the endpoint
            response = await client.get(mcp_url, timeout=5.0)

            # If we get 401/403, auth is likely required
            if response.status_code in (401, 403):
                return True

            # Check for WWW-Authenticate header
            if "WWW-Authenticate" in response.headers:  # noqa: SIM103
                return True

            # If we get a successful response, auth may not be required
            return False

        except httpx.RequestError:
            # If we can't connect, assume auth might be required
            return True


class TokenStorageAdapter(TokenStorage):
    _server_url: str
    _key_value_store: AsyncKeyValue
    _storage_oauth_token: PydanticAdapter[OAuthToken]
    _storage_client_info: PydanticAdapter[OAuthClientInformationFull]

    def __init__(self, async_key_value: AsyncKeyValue, server_url: str):
        self._server_url = server_url
        self._key_value_store = async_key_value
        self._storage_oauth_token = PydanticAdapter[OAuthToken](
            default_collection="mcp-oauth-token",
            key_value=async_key_value,
            pydantic_model=OAuthToken,
            raise_on_validation_error=True,
        )
        self._storage_client_info = PydanticAdapter[OAuthClientInformationFull](
            default_collection="mcp-oauth-client-info",
            key_value=async_key_value,
            pydantic_model=OAuthClientInformationFull,
            raise_on_validation_error=True,
        )

    def _get_token_cache_key(self) -> str:
        return f"{self._server_url}/tokens"

    def _get_client_info_cache_key(self) -> str:
        return f"{self._server_url}/client_info"

    async def clear(self) -> None:
        await self._storage_oauth_token.delete(key=self._get_token_cache_key())
        await self._storage_client_info.delete(key=self._get_client_info_cache_key())

    @override
    async def get_tokens(self) -> OAuthToken | None:
        return await self._storage_oauth_token.get(key=self._get_token_cache_key())

    @override
    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._storage_oauth_token.put(
            key=self._get_token_cache_key(),
            value=tokens,
            ttl=tokens.expires_in,
        )

    @override
    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return await self._storage_client_info.get(
            key=self._get_client_info_cache_key()
        )

    @override
    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        ttl: int | None = None

        if client_info.client_secret_expires_at:
            ttl = client_info.client_secret_expires_at - int(time.time())

        await self._storage_client_info.put(
            key=self._get_client_info_cache_key(),
            value=client_info,
            ttl=ttl,
        )


class OAuth(OAuthClientProvider):
    """
    OAuth client provider for MCP servers with browser-based authentication.

    This class provides OAuth authentication for FastMCP clients by opening
    a browser for user authorization and running a local callback server.
    """

    def __init__(
        self,
        mcp_url: str,
        scopes: str | list[str] | None = None,
        client_name: str = "FastMCP Client",
        token_storage: AsyncKeyValue | None = None,
        additional_client_metadata: dict[str, Any] | None = None,
        callback_port: int | None = None,
    ):
        """
        Initialize OAuth client provider for an MCP server.

        Args:
            mcp_url: Full URL to the MCP endpoint (e.g. "http://host/mcp/sse/")
            scopes: OAuth scopes to request. Can be a
            space-separated string or a list of strings.
            client_name: Name for this client during registration
            token_storage: An AsyncKeyValue-compatible token store, tokens are stored in memory if not provided
            additional_client_metadata: Extra fields for OAuthClientMetadata
            callback_port: Fixed port for OAuth callback (default: random available port)
        """
        parsed_url = urlparse(mcp_url)
        server_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

        # Setup OAuth client
        self.redirect_port = callback_port or find_available_port()
        redirect_uri = f"http://localhost:{self.redirect_port}/callback"

        scopes_str: str
        if isinstance(scopes, list):
            scopes_str = " ".join(scopes)
        elif scopes is not None:
            scopes_str = str(scopes)
        else:
            scopes_str = ""

        client_metadata = OAuthClientMetadata(
            client_name=client_name,
            redirect_uris=[AnyHttpUrl(redirect_uri)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            # token_endpoint_auth_method="client_secret_post",
            scope=scopes_str,
            **(additional_client_metadata or {}),
        )

        # Create server-specific token storage
        token_storage = token_storage or MemoryStore()

        if isinstance(token_storage, MemoryStore):
            from warnings import warn

            warn(
                message="Using in-memory token storage is not recommended for production use -- "
                + "tokens will be lost on server restart.",
                stacklevel=2,
            )

        self.token_storage_adapter: TokenStorageAdapter = TokenStorageAdapter(
            async_key_value=token_storage, server_url=server_base_url
        )

        # Store server_base_url for use in callback_handler
        self.server_base_url = server_base_url

        # Initialize parent class
        super().__init__(
            server_url=server_base_url,
            client_metadata=client_metadata,
            storage=self.token_storage_adapter,
            redirect_handler=self.redirect_handler,
            callback_handler=self.callback_handler,
        )

    async def _initialize(self) -> None:
        """Load stored tokens and client info, properly setting token expiry."""
        # Call parent's _initialize to load tokens and client info
        await super()._initialize()

        # If tokens were loaded and have expires_in, update the context's token_expiry_time
        if self.context.current_tokens and self.context.current_tokens.expires_in:
            self.context.update_token_expiry(self.context.current_tokens)

    async def redirect_handler(self, authorization_url: str) -> None:
        """Open browser for authorization, with pre-flight check for invalid client."""
        # Pre-flight check to detect invalid client_id before opening browser
        async with httpx.AsyncClient() as client:
            response = await client.get(authorization_url, follow_redirects=False)

            # Check for client not found error (400 typically means bad client_id)
            if response.status_code == 400:
                raise ClientNotFoundError(
                    "OAuth client not found - cached credentials may be stale"
                )

            # OAuth typically returns redirects, but some providers return 200 with HTML login pages
            if response.status_code not in (200, 302, 303, 307, 308):
                raise RuntimeError(
                    f"Unexpected authorization response: {response.status_code}"
                )

        logger.info(f"OAuth authorization URL: {authorization_url}")
        webbrowser.open(authorization_url)

    async def callback_handler(self) -> tuple[str, str | None]:
        """Handle OAuth callback and return (auth_code, state)."""
        # Create result container and event to capture the OAuth response
        result = OAuthCallbackResult()
        result_ready = anyio.Event()

        # Create server with result tracking
        server: Server = create_oauth_callback_server(
            port=self.redirect_port,
            server_url=self.server_base_url,
            result_container=result,
            result_ready=result_ready,
        )

        # Run server until response is received with timeout logic
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            logger.info(
                f"🎧 OAuth callback server started on http://localhost:{self.redirect_port}"
            )

            TIMEOUT = 300.0  # 5 minute timeout
            try:
                with anyio.fail_after(TIMEOUT):
                    await result_ready.wait()
                    if result.error:
                        raise result.error
                    return result.code, result.state  # type: ignore
            except TimeoutError as e:
                raise TimeoutError(
                    f"OAuth callback timed out after {TIMEOUT} seconds"
                ) from e
            finally:
                server.should_exit = True
                await anyio.sleep(0.1)  # Allow server to shut down gracefully
                tg.cancel_scope.cancel()

        raise RuntimeError("OAuth callback handler could not be started")

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """HTTPX auth flow with automatic retry on stale cached credentials.

        If the OAuth flow fails due to invalid/stale client credentials,
        clears the cache and retries once with fresh registration.
        """
        try:
            # First attempt with potentially cached credentials
            gen = super().async_auth_flow(request)
            response = None
            while True:
                try:
                    yielded_request = await gen.asend(response)
                    response = yield yielded_request
                except StopAsyncIteration:
                    break

        except ClientNotFoundError:
            logger.debug(
                "OAuth client not found on server, clearing cache and retrying..."
            )

            # Clear cached state and retry once
            self._initialized = False
            await self.token_storage_adapter.clear()

            gen = super().async_auth_flow(request)
            response = None
            while True:
                try:
                    yielded_request = await gen.asend(response)
                    response = yield yielded_request
                except StopAsyncIteration:
                    break
