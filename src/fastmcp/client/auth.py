from __future__ import annotations

import asyncio
import json
import socket
import webbrowser
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urljoin, urlparse

import anyio
import httpx
from mcp.client.auth import OAuthClientProvider as _MCPOAuthClientProvider
from mcp.client.auth import TokenStorage
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from mcp.shared.auth import (
    OAuthMetadata as _MCPServerOAuthMetadata,
)
from pydantic import AnyHttpUrl, ValidationError
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from uvicorn import Config, Server

from fastmcp.settings import settings as fastmcp_global_settings
from fastmcp.utilities.logging import get_logger

__all__ = ["OAuth"]

logger = get_logger(__name__)


# Flexible OAuth models for real-world compatibility
class ServerOAuthMetadata(_MCPServerOAuthMetadata):
    """
    More flexible OAuth metadata model that accepts broader ranges of values
    than the restrictive MCP standard model.

    This handles real-world OAuth servers like PayPal that may support
    additional methods not in the MCP specification.
    """

    # Allow any code challenge methods, not just S256
    code_challenge_methods_supported: list[str] | None = None

    # Allow any token endpoint auth methods
    token_endpoint_auth_methods_supported: list[str] | None = None

    # Allow any grant types
    grant_types_supported: list[str] | None = None

    # Allow any response types
    response_types_supported: list[str] = ["code"]

    # Allow any response modes
    response_modes_supported: list[str] | None = None


class OAuthClientProvider(_MCPOAuthClientProvider):
    """
    OAuth client provider with more flexible OAuth metadata discovery.

    This subclass handles real-world OAuth servers that may not conform
    strictly to the MCP OAuth specification but are still valid OAuth 2.0 servers.
    """

    async def _discover_oauth_metadata(
        self, server_url: str
    ) -> ServerOAuthMetadata | None:
        """
        Discover OAuth metadata with flexible validation.

        This is nearly identical to the parent implementation but uses
        ServerOAuthMetadata instead of the restrictive MCP OAuthMetadata.
        """
        # Extract base URL per MCP spec
        auth_base_url = self._get_authorization_base_url(server_url)
        url = urljoin(auth_base_url, "/.well-known/oauth-authorization-server")

        from mcp.types import LATEST_PROTOCOL_VERSION

        headers = {"MCP-Protocol-Version": LATEST_PROTOCOL_VERSION}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                metadata_json = response.json()
                logger.debug(f"OAuth metadata discovered: {metadata_json}")
                return ServerOAuthMetadata.model_validate(metadata_json)
            except Exception:
                # Retry without MCP header for CORS compatibility
                try:
                    response = await client.get(url)
                    if response.status_code == 404:
                        return None
                    response.raise_for_status()
                    metadata_json = response.json()
                    logger.debug(
                        f"OAuth metadata discovered (no MCP header): {metadata_json}"
                    )
                    return ServerOAuthMetadata.model_validate(metadata_json)
                except Exception:
                    logger.exception("Failed to discover OAuth metadata")
                    return None


class FileTokenStorage(TokenStorage):
    """
    File-based token storage implementation for OAuth credentials and tokens.
    Implements the mcp.client.auth.TokenStorage protocol.

    Each instance is tied to a specific server URL for proper token isolation.
    """

    def __init__(self, server_url: str, cache_dir: Path | None = None):
        """Initialize storage for a specific server URL."""
        self.server_url = server_url
        self.cache_dir = (
            cache_dir or fastmcp_global_settings.home / "oauth-mcp-client-cache"
        )
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
            data = json.loads(path.read_text())
            return OAuthToken.model_validate(data)
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
            data = json.loads(path.read_text())
            return OAuthClientInformationFull.model_validate(data)
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

    def clear_cache(self) -> None:
        """Clear all cached data for this server."""
        # Use explicit literals to satisfy type checker
        for file_type in [
            cast(Literal["client_info", "tokens"], "client_info"),
            cast(Literal["client_info", "tokens"], "tokens"),
        ]:
            path = self._get_file_path(file_type)
            path.unlink(missing_ok=True)
        logger.info(f"Cleared OAuth cache for {self.get_base_url(self.server_url)}")

    def has_valid_token(self) -> bool:
        """Check if there's a valid non-expired token (synchronous check)."""
        path = self._get_file_path("tokens")
        try:
            data = json.loads(path.read_text())
            token = OAuthToken.model_validate(data)

            # Check if token has expiration info
            if not token.expires_in:
                return True  # Assume valid if no expiration

            # We need to check when the token was saved vs current time
            # For simplicity, we'll assume the token is fresh enough for now
            # A more robust implementation would store the timestamp when saved
            return True

        except (FileNotFoundError, json.JSONDecodeError, ValidationError):
            return False

    @classmethod
    def list_cached_servers(cls, cache_dir: Path | None = None) -> list[str]:
        """List all servers with cached data."""
        cache_dir = cache_dir or fastmcp_global_settings.home / "oauth-mcp-client-cache"
        if not cache_dir.exists():
            return []

        servers = set()
        for file in cache_dir.glob("*_tokens.json"):
            # Extract server info from filename
            key_part = file.stem.replace("_tokens", "")
            # Attempt to reconstruct URL (best effort)
            if "_" in key_part:
                try:
                    # Handle common patterns like "https_example_com_8080"
                    parts = key_part.split("_")
                    if len(parts) >= 3:
                        scheme = parts[0]
                        host_parts = parts[1:-1] if parts[-1].isdigit() else parts[1:]
                        port = parts[-1] if parts[-1].isdigit() else None

                        host = ".".join(host_parts)
                        url = f"{scheme}://{host}"
                        if port:
                            url += f":{port}"
                        servers.add(url)
                except Exception:
                    # If reconstruction fails, at least show the key
                    servers.add(key_part)

        return sorted(list(servers))

    @classmethod
    def clear_all_cache(cls, cache_dir: Path | None = None) -> None:
        """Clear all cached data for all servers."""
        cache_dir = cache_dir or fastmcp_global_settings.home / "oauth-mcp-client-cache"
        if not cache_dir.exists():
            return

        # Use explicit literals to satisfy type checker
        for file_type in [
            cast(Literal["client_info", "tokens"], "client_info"),
            cast(Literal["client_info", "tokens"], "tokens"),
        ]:
            for file in cache_dir.glob(f"*_{file_type}.json"):
                file.unlink(missing_ok=True)
        logger.info("Cleared all OAuth client cache data.")


def find_available_port() -> int:
    """Find an available port by letting the OS assign one."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _get_redirect_callback(
    port: int, path: str = "/callback", timeout: float = 300.0
) -> tuple[str, str | None]:
    """
    Start a temporary server to handle OAuth redirect and return auth code and state.

    Returns:
        Tuple of (authorization_code, state)
    """
    response_future = asyncio.get_running_loop().create_future()

    async def callback_handler(request):
        if not response_future.done():
            query_params = dict(request.query_params)
            auth_code = query_params.get("code")
            state = query_params.get("state")
            error = query_params.get("error")

            if error:
                error_desc = query_params.get("error_description", "Unknown error")
                response_future.set_exception(
                    RuntimeError(f"OAuth error: {error} - {error_desc}")
                )
                return PlainTextResponse(
                    f"❌ OAuth Error: {error}\n{error_desc}\nYou can close this tab.",
                    status_code=400,
                )

            if not auth_code:
                response_future.set_exception(
                    RuntimeError("OAuth callback missing authorization code")
                )
                return PlainTextResponse(
                    "❌ OAuth Error: No authorization code received.\nYou can close this tab.",
                    status_code=400,
                )

            response_future.set_result((auth_code, state))
            return PlainTextResponse(
                "✅ FastMCP OAuth login complete!\nYou can close this tab now."
            )

        return PlainTextResponse("Callback already processed. You can close this tab.")

    server = Server(
        Config(
            app=Starlette(routes=[Route(path, callback_handler)]),
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="warning",
        )
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)
        logger.info(
            f"🎧 OAuth callback server started on http://127.0.0.1:{port}{path}"
        )

        try:
            with anyio.fail_after(timeout):
                auth_code, state = await response_future
                return auth_code, state
        except TimeoutError:
            raise TimeoutError(f"OAuth callback timed out after {timeout} seconds")
        finally:
            server.should_exit = True
            await asyncio.sleep(0.1)  # Allow server to shutdown gracefully
            tg.cancel_scope.cancel()


async def discover_oauth_metadata(
    server_base_url: str, httpx_kwargs: dict[str, Any] | None = None
) -> _MCPServerOAuthMetadata | None:
    """
    Discover OAuth metadata from the server using RFC 8414 well-known endpoint.

    Args:
        server_base_url: Base URL of the OAuth server (e.g., "https://example.com")
        httpx_kwargs: Additional kwargs for httpx client

    Returns:
        OAuth metadata if found, None otherwise
    """
    well_known_url = urljoin(server_base_url, "/.well-known/oauth-authorization-server")
    logger.debug(f"Discovering OAuth metadata from: {well_known_url}")

    async with httpx.AsyncClient(**(httpx_kwargs or {})) as client:
        try:
            response = await client.get(well_known_url, timeout=10.0)
            if response.status_code == 200:
                logger.debug("Successfully discovered OAuth metadata")
                return _MCPServerOAuthMetadata.model_validate(response.json())
            elif response.status_code == 404:
                logger.debug(
                    "OAuth metadata not found (404) - server may not require auth"
                )
                return None
            else:
                logger.warning(f"OAuth metadata request failed: {response.status_code}")
                return None
        except (httpx.RequestError, json.JSONDecodeError, ValidationError) as e:
            logger.debug(f"OAuth metadata discovery failed: {e}")
            return None


async def check_if_auth_required(
    mcp_endpoint_url: str, httpx_kwargs: dict[str, Any] | None = None
) -> bool:
    """
    Check if the MCP endpoint requires authentication by making a test request.

    Returns:
        True if auth appears to be required, False otherwise
    """
    async with httpx.AsyncClient(**(httpx_kwargs or {})) as client:
        try:
            # Try a simple request to the endpoint
            response = await client.get(mcp_endpoint_url, timeout=5.0)

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


def OAuth(
    mcp_endpoint_url: str,
    scopes: str | list[str] | None = None,
    client_name: str = "FastMCP Client",
    token_storage_cache_dir: Path | None = None,
    additional_client_metadata: dict[str, Any] | None = None,
) -> _MCPOAuthClientProvider:
    """
    Create an OAuthClientProvider for an MCP server.

    Args:
        mcp_endpoint_url: Full URL to the MCP endpoint (e.g., "http://host/mcp/sse")
        scopes: OAuth scopes to request. Can be a space-separated string or a list of strings.
        client_name: Name for this client during registration
        token_storage_cache_dir: Directory for FileTokenStorage
        additional_client_metadata: Extra fields for OAuthClientMetadata

    Returns:
        OAuthClientProvider
    """
    parsed_url = urlparse(mcp_endpoint_url)
    server_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    # Setup OAuth client
    redirect_port = find_available_port()
    redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"

    if isinstance(scopes, list):
        scopes = " ".join(scopes)

    client_metadata = OAuthClientMetadata(
        client_name=client_name,
        redirect_uris=[AnyHttpUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=scopes,
        **(additional_client_metadata or {}),
    )

    # Create server-specific token storage
    storage = FileTokenStorage(
        server_url=server_base_url, cache_dir=token_storage_cache_dir
    )

    # Define OAuth handlers
    async def redirect_handler(authorization_url: str) -> None:
        """Open browser for authorization."""
        logger.info(f"Opening browser for OAuth authorization: {authorization_url}")
        webbrowser.open(authorization_url)

    async def callback_handler() -> tuple[str, str | None]:
        """Handle OAuth callback and return (auth_code, state)."""
        return await _get_redirect_callback(port=redirect_port)

    # Create OAuth provider
    oauth_provider = OAuthClientProvider(
        server_url=server_base_url,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    return oauth_provider
