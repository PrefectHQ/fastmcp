from __future__ import annotations

import asyncio
import json
import webbrowser
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
from pydantic import AnyHttpUrl, ValidationError

from fastmcp import settings as fastmcp_global_settings
from fastmcp.client.oauth_callback import (
    create_oauth_callback_server,
)
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.logging import get_logger

__all__ = ["OAuth"]

logger = get_logger(__name__)


def default_cache_dir() -> Path:
    return fastmcp_global_settings.home / "oauth-mcp-client-cache"


class FileTokenStorage(TokenStorage):
    """
    File-based token storage implementation for OAuth credentials and tokens.
    Implements the mcp.client.auth.TokenStorage protocol.

    Each instance is tied to a specific server URL for proper token isolation.
    """

    def __init__(self, server_url: str, cache_dir: Path | None = None):
        """Initialize storage for a specific server URL."""
        self.server_url = server_url
        self.cache_dir = cache_dir or default_cache_dir()
        self.cache_dir.mkdir(exist_ok=True, parents=True)

    @staticmethod
    def get_base_url(url: str) -> str:
        """Extract the base URL (scheme + host) from a URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def get_cache_key(self) -> str:
        """Generate a safe filesystem key from the server's base URL."""
        base_url = self.get_base_url(self.server_url)
        return (
            base_url.replace("://", "_")
            .replace(".", "_")
            .replace("/", "_")
            .replace(":", "_")
        )

    def _get_file_path(self, file_type: Literal["client_info", "tokens"]) -> Path:
        """Get the file path for the specified cache file type."""
        key = self.get_cache_key()
        return self.cache_dir / f"{key}_{file_type}.json"

    async def get_tokens(self) -> OAuthToken | None:
        """Load tokens from file storage."""
        path = self._get_file_path("tokens")

        try:
            tokens = OAuthToken.model_validate_json(path.read_text())
            # now = datetime.datetime.now(datetime.timezone.utc)
            # if tokens.expires_at is not None and tokens.expires_at <= now:
            #     logger.debug(f"Token expired for {self.get_base_url(self.server_url)}")
            #     return None
            return tokens
        except (FileNotFoundError, json.JSONDecodeError, ValidationError) as e:
            logger.debug(
                f"Could not load tokens for {self.get_base_url(self.server_url)}: {e}"
            )
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Save tokens to file storage."""
        path = self._get_file_path("tokens")
        path.write_text(tokens.model_dump_json(indent=2))
        logger.debug(f"Saved tokens for {self.get_base_url(self.server_url)}")

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Load client information from file storage."""
        path = self._get_file_path("client_info")
        try:
            client_info = OAuthClientInformationFull.model_validate_json(
                path.read_text()
            )
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
                client_info_path = self._get_file_path("client_info")
                client_info_path.unlink(missing_ok=True)
                return None

            return client_info
        except (FileNotFoundError, json.JSONDecodeError, ValidationError) as e:
            logger.debug(
                f"Could not load client info for {self.get_base_url(self.server_url)}: {e}"
            )
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Save client information to file storage."""
        path = self._get_file_path("client_info")
        path.write_text(client_info.model_dump_json(indent=2))
        logger.debug(f"Saved client info for {self.get_base_url(self.server_url)}")

    def clear(self) -> None:
        """Clear all cached data for this server."""
        file_types: list[Literal["client_info", "tokens"]] = ["client_info", "tokens"]
        for file_type in file_types:
            path = self._get_file_path(file_type)
            path.unlink(missing_ok=True)
        logger.info(f"Cleared OAuth cache for {self.get_base_url(self.server_url)}")

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

        if isinstance(scopes, list):
            scopes = " ".join(scopes)

        client_metadata = OAuthClientMetadata(
            client_name=client_name,
            redirect_uris=[AnyHttpUrl(redirect_uri)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            # token_endpoint_auth_method="client_secret_post",
            scope=scopes,
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

    async def redirect_handler(self, authorization_url: str) -> None:
        """Open browser for authorization."""
        logger.info(f"OAuth authorization URL: {authorization_url}")
        webbrowser.open(authorization_url)

    async def callback_handler(self) -> tuple[str, str | None]:
        """Handle OAuth callback and return (auth_code, state)."""
        # Create a future to capture the OAuth response
        response_future = asyncio.get_running_loop().create_future()

        # Create server with the future
        server = create_oauth_callback_server(
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
                await asyncio.sleep(0.1)  # Allow server to shutdown gracefully
                tg.cancel_scope.cancel()
