from __future__ import annotations

import asyncio
import webbrowser
from asyncio import Future
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import anyio
import httpx
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
)
from mcp.shared.auth import (
    OAuthToken as OAuthToken,
)
from pydantic import AnyHttpUrl, BaseModel, TypeAdapter, ValidationError
from uvicorn.server import Server

from fastmcp import settings as fastmcp_global_settings
from fastmcp.client.oauth_callback import (
    create_oauth_callback_server,
)
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.storage import JSONFileStorage

__all__ = ["OAuth"]

logger = get_logger(__name__)


class ClientNotFoundError(Exception):
    """Raised when OAuth client credentials are not found on the server."""

    pass


class StoredToken(BaseModel):
    """Token storage format with absolute expiry time."""

    token_payload: OAuthToken
    expires_at: datetime | None


# Create TypeAdapter at module level for efficient parsing
stored_token_adapter = TypeAdapter(StoredToken)


def default_cache_dir() -> Path:
    return fastmcp_global_settings.home / "oauth-mcp-client-cache"


class FileTokenStorage(TokenStorage):
    """
    File-based token storage implementation for OAuth credentials and tokens.
    Implements the mcp.client.auth.TokenStorage protocol.

    Each instance is tied to a specific server URL for proper token isolation.
    Uses JSONFileStorage internally for consistent file handling.
    """

    def __init__(self, server_url: str, cache_dir: Path | None = None):
        """Initialize storage for a specific server URL."""
        self.server_url = server_url
        # Use JSONFileStorage for actual file operations
        self._storage = JSONFileStorage(cache_dir or default_cache_dir())

    @staticmethod
    def get_base_url(url: str) -> str:
        """Extract the base URL (scheme + host) from a URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _get_storage_key(self, file_type: Literal["client_info", "tokens"]) -> str:
        """Get the storage key for the specified data type.

        JSONFileStorage will handle making the key filesystem-safe.
        """
        base_url = self.get_base_url(self.server_url)
        return f"{base_url}_{file_type}"

    def _get_file_path(self, file_type: Literal["client_info", "tokens"]) -> Path:
        """Get the file path for the specified cache file type.

        This method is kept for backward compatibility with tests that access _get_file_path.
        """
        key = self._get_storage_key(file_type)
        return self._storage._get_file_path(key)

    async def get_tokens(self) -> OAuthToken | None:
        """Load tokens from file storage."""
        key = self._get_storage_key("tokens")
        data = await self._storage.get(key)

        if data is None:
            return None

        try:
            # Parse and validate as StoredToken
            stored = stored_token_adapter.validate_python(data)

            # Check if token is expired
            if stored.expires_at is not None:
                now = datetime.now(timezone.utc)
                if now >= stored.expires_at:
                    logger.debug(
                        f"Token expired for {self.get_base_url(self.server_url)}"
                    )
                    return None

                # Recalculate expires_in to be correct relative to now
                if stored.token_payload.expires_in is not None:
                    remaining = stored.expires_at - now
                    stored.token_payload.expires_in = max(
                        0, int(remaining.total_seconds())
                    )

            return stored.token_payload

        except ValidationError as e:
            logger.debug(
                f"Could not validate tokens for {self.get_base_url(self.server_url)}: {e}"
            )
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Save tokens to file storage."""
        key = self._get_storage_key("tokens")

        # Calculate absolute expiry time if expires_in is present
        expires_at = None
        if tokens.expires_in is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=tokens.expires_in
            )

        # Create StoredToken and save using storage
        # Note: JSONFileStorage will wrap this in {"data": ..., "timestamp": ...}
        stored = StoredToken(token_payload=tokens, expires_at=expires_at)
        await self._storage.set(key, stored.model_dump(mode="json"))
        logger.debug(f"Saved tokens for {self.get_base_url(self.server_url)}")

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Load client information from file storage."""
        key = self._get_storage_key("client_info")
        data = await self._storage.get(key)

        if data is None:
            return None

        try:
            client_info = OAuthClientInformationFull.model_validate(data)
            # Check if we have corresponding valid tokens
            # If no tokens exist, the OAuth flow was incomplete and we should
            # force a fresh client registration
            tokens = await self.get_tokens()
            if tokens is None:
                logger.debug(
                    f"No tokens found for client info at {self.get_base_url(self.server_url)}. "
                    "OAuth flow may have been incomplete. Clearing client info to force fresh registration."
                )
                # Clear the incomplete client info
                await self._storage.delete(key)
                return None

            return client_info
        except ValidationError as e:
            logger.debug(
                f"Could not validate client info for {self.get_base_url(self.server_url)}: {e}"
            )
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Save client information to file storage."""
        key = self._get_storage_key("client_info")
        await self._storage.set(key, client_info.model_dump(mode="json"))
        logger.debug(f"Saved client info for {self.get_base_url(self.server_url)}")

    def clear(self) -> None:
        """Clear all cached data for this server.

        Note: This is a synchronous method for backward compatibility.
        Uses direct file operations instead of async storage methods.
        """
        file_types: list[Literal["client_info", "tokens"]] = ["client_info", "tokens"]
        for file_type in file_types:
            # Use the file path directly for synchronous deletion
            path = self._get_file_path(file_type)
            path.unlink(missing_ok=True)
        logger.debug(f"Cleared OAuth cache for {self.get_base_url(self.server_url)}")

    @classmethod
    def clear_all(cls, cache_dir: Path | None = None) -> None:
        """Clear all cached data for all servers."""
        cache_dir = cache_dir or default_cache_dir()
        if not cache_dir.exists():
            return

        file_types: list[Literal["client_info", "tokens"]] = ["client_info", "tokens"]
        for file_type in file_types:
            for file in cache_dir.glob(f"*_{file_type}.json"):
                file.unlink(missing_ok=True)
        logger.info("Cleared all OAuth client cache data.")


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
        token_storage_cache_dir: Path | None = None,
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
            token_storage_cache_dir: Directory for FileTokenStorage
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
        storage = FileTokenStorage(
            server_url=server_base_url, cache_dir=token_storage_cache_dir
        )

        # Store server_base_url for use in callback_handler
        self.server_base_url = server_base_url

        # Initialize parent class
        super().__init__(
            server_url=server_base_url,
            client_metadata=client_metadata,
            storage=storage,
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

            # Try to clear storage if it supports it
            if hasattr(self.context.storage, "clear"):
                try:
                    self.context.storage.clear()
                except Exception as e:
                    logger.warning(f"Failed to clear OAuth storage cache: {e}")
                    # Can't retry without clearing cache, re-raise original error
                    raise ClientNotFoundError(
                        "OAuth client not found and cache could not be cleared"
                    ) from e
            else:
                logger.warning(
                    "Storage does not support clear() - cannot retry with fresh credentials"
                )
                # Can't retry without clearing cache, re-raise original error
                raise

            gen = super().async_auth_flow(request)
            response = None
            while True:
                try:
                    yielded_request = await gen.asend(response)
                    response = yield yielded_request
                except StopAsyncIteration:
                    break
