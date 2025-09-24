from __future__ import annotations

import asyncio
import time
import webbrowser
from asyncio import Future
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import anyio
import httpx
from kv_store_adapter.adapters.pydantic import PydanticAdapter
from kv_store_adapter.stores.disk import DiskStore
from kv_store_adapter.types import KVStoreProtocol
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyHttpUrl, BaseModel
from uvicorn.server import Server

from fastmcp import settings
from fastmcp.client.oauth_callback import (
    create_oauth_callback_server,
)
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.logging import get_logger

__all__ = ["OAuth"]

logger = get_logger(__name__)


class ClientNotFoundError(Exception):
    """Raised when OAuth client credentials are not found on the server."""

    pass


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
            if "WWW-Authenticate" in response.headers:
                return True

            # If we get a successful response, auth may not be required
            return False

        except httpx.RequestError:
            # If we can't connect, assume auth might be required
            return True


class TokenStorageAdapter(TokenStorage):
    _server_url: str
    _kv_store_protocol: KVStoreProtocol
    _storage_oauth_token: PydanticAdapter[OAuthToken]
    _storage_client_info: PydanticAdapter[OAuthClientInformationFull]

    def __init__(self, kv_store_protocol: KVStoreProtocol, server_url: str):
        self._server_url = server_url
        self._kv_store_protocol = kv_store_protocol
        self._storage_oauth_token = PydanticAdapter[OAuthToken](
            store_protocol=kv_store_protocol, pydantic_model=OAuthToken
        )
        self._storage_client_info = PydanticAdapter[OAuthClientInformationFull](
            store_protocol=kv_store_protocol, pydantic_model=OAuthClientInformationFull
        )

    def _get_token_cache_key(self) -> str:
        return f"{self._server_url}/tokens"

    def _get_client_info_cache_key(self) -> str:
        return f"{self._server_url}/client_info"

    async def clear(self) -> None:
        await self._storage_oauth_token.delete(
            collection="oauth-mcp-client-cache", key=self._get_token_cache_key()
        )
        await self._storage_client_info.delete(
            collection="oauth-mcp-client-cache", key=self._get_client_info_cache_key()
        )

    async def get_tokens(self) -> OAuthToken | None:
        return await self._storage_oauth_token.get(
            collection="oauth-mcp-client-cache", key=self._get_token_cache_key()
        )

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._storage_oauth_token.put(
            collection="oauth-mcp-client-cache",
            key=self._get_token_cache_key(),
            value=tokens,
            ttl=tokens.expires_in,
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return await self._storage_client_info.get(
            collection="oauth-mcp-client-cache", key=self._get_client_info_cache_key()
        )

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        ttl: int | None = None

        if client_info.client_secret_expires_at:
            ttl = client_info.client_secret_expires_at - int(time.time())

        await self._storage_client_info.put(
            collection="oauth-mcp-client-cache",
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
        token_storage: KVStoreProtocol | None = None,
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
            token_storage: KVStoreProtocol for token storage, the default disk store is used if not provided
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
        token_storage = token_storage or settings.data_store

        self.token_storage_adapter: TokenStorageAdapter = TokenStorageAdapter(
            kv_store_protocol=token_storage, server_url=server_base_url
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
        # Create a future to capture the OAuth response
        response_future: Future[Any] = asyncio.get_running_loop().create_future()

        # Create server with the future
        server: Server = create_oauth_callback_server(
            port=self.redirect_port,
            server_url=self.server_base_url,
            response_future=response_future,
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
                    auth_code, state = await response_future
                    return auth_code, state
            except TimeoutError:
                raise TimeoutError(f"OAuth callback timed out after {TIMEOUT} seconds")
            finally:
                server.should_exit = True
                await asyncio.sleep(0.1)  # Allow server to shut down gracefully
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
