from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urljoin

import anyio
import httpx
import mcp.client.sse
import mcp.client.streamable_http
import mcp.shared._httpx_utils
from authlib.integrations.httpx_client import AsyncOAuth2Client
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from uvicorn import Config, Server

from fastmcp.client.auth.oauth_cache import oauth_cache
from fastmcp.utilities.logging import get_logger

_current_mcp_endpoint: ContextVar[str | None] = ContextVar("mcp_endpoint", default=None)


logger = get_logger(__name__)


def create_mcp_http_client(
    headers: dict[str, Any] | None = None,
    timeout: httpx.Timeout | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    # re-implements logic from mcp.shared._httpx_utils.create_mcp_http_client, but with **kwargs support
    kwargs.setdefault("follow_redirects", True)
    if timeout is None:
        timeout = httpx.Timeout(30.0)

    return httpx.AsyncClient(headers=headers, timeout=timeout, **kwargs)


def find_available_port() -> int:
    """Find an available port by letting the OS assign one."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _get_redirect(
    port: int, path: str = "/callback", timeout: float = 100.0
) -> str:
    """
    Start a temporary server to handle OAuth redirect and get the full redirect URL.

    Args:
        port: The port to run the server on
        path: The path to listen for redirects on
        timeout: Number of seconds to wait before timing out

    Returns:
        The full redirect URL from the browser

    Raises:
        TimeoutError: If no redirect is received within the timeout period
    """
    fut = asyncio.get_running_loop().create_future()

    async def cb(request):
        if not fut.done():
            fut.set_result(str(request.url))  # full redirect URL
        return PlainTextResponse(
            "✅ FastMCP login complete! You can close this tab now."
        )

    server = Server(
        Config(
            app=Starlette(routes=[Route(path, cb)]),
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="error",
        )
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)  # background task for server

        try:
            # Use anyio.fail_after to implement timeout
            with anyio.fail_after(timeout):
                redirect_url = await fut  # wait for browser hit or timeout
                return redirect_url
        finally:
            server.should_exit = True  # stop the server loop
            tg.cancel_scope.cancel()  # tear down immediately


class OAuthBearerAuth(httpx.Auth):
    """Auth handler that adds the OAuth bearer token to requests."""

    def __init__(self, client: AsyncOAuth2Client) -> None:
        self._client = client

    async def async_auth_flow(self, request: httpx.Request):
        # Ensure token is loaded in the OAuth client
        if (
            not self._client.token
            or self._client.token.get("expires_at")
            and self._client.token["expires_at"] < time.time()
        ):
            # We'll refresh or reauthorize in create_mcp_oauth_client
            pass

        if self._client.token:
            request.headers["Authorization"] = (
                f"Bearer {self._client.token['access_token']}"
            )
        yield request


async def discover_oauth_metadata(base_url: str) -> dict[str, Any] | None:
    """
    Discover OAuth metadata from the server according to RFC 8414.

    Returns None if the server appears to not require authentication.
    """
    # First, try the well-known URL
    well_known_url = urljoin(base_url, "/.well-known/oauth-authorization-server")
    logger.debug(f"Attempting OAuth metadata discovery from: {well_known_url}")

    async with httpx.AsyncClient() as client:
        # First try the well-known URL
        try:
            response = await client.get(well_known_url, timeout=10)
            if response.status_code == 200:
                logger.debug("Successfully discovered OAuth metadata")
                return response.json()
        except httpx.RequestError as e:
            logger.debug(f"Failed to fetch OAuth metadata: {e}")

        # If well-known discovery fails, check WWW-Authenticate header
        try:
            response = await client.get(base_url, timeout=10)

            # If the base URL request succeeds without a 401/403 and has no WWW-Authenticate header,
            # the server likely doesn't require authentication
            if (
                response.status_code < 400
                and "WWW-Authenticate" not in response.headers
            ):
                logger.debug("Server appears to not require authentication")
                return None

            auth_header = response.headers.get("WWW-Authenticate")
            if auth_header and "resource_metadata" in auth_header:
                # Extract metadata URL from header
                import re

                metadata_match = re.search(r'resource_metadata="([^"]+)"', auth_header)
                if metadata_match:
                    metadata_url = metadata_match.group(1)
                    metadata_response = await client.get(metadata_url, timeout=10)
                    if metadata_response.status_code == 200:
                        logger.debug(
                            "Successfully discovered OAuth metadata from WWW-Authenticate header"
                        )
                        return metadata_response.json()
        except httpx.RequestError as e:
            logger.debug(f"Failed to fetch OAuth metadata from WWW-Authenticate: {e}")

    # Fallback to default endpoints based on the base URL
    logger.debug("Falling back to default OAuth endpoints")
    return {
        "issuer": base_url,
        "authorization_endpoint": urljoin(base_url, "/authorize"),
        "token_endpoint": urljoin(base_url, "/token"),
        "registration_endpoint": urljoin(base_url, "/register"),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
            "none",
        ],
        "code_challenge_methods_supported": ["S256"],
    }


async def register_client(
    registration_endpoint: str, redirect_uri: str
) -> dict[str, Any]:
    """
    Register an OAuth client using RFC 7591 dynamic registration.

    May raise httpx.HTTPStatusError if registration fails.
    """
    logger.debug(f"Registering client at: {registration_endpoint}")

    payload = {
        "client_name": "FastMCP Client",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",  # public PKCE client
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(registration_endpoint, json=payload, timeout=10)
        # Allow HTTPStatusError to propagate to the caller
        response.raise_for_status()
        logger.debug("Client registration successful")
        return response.json()


@asynccontextmanager
async def create_mcp_oauth_client(
    mcp_endpoint: str,
    redirect_uri: str | None = None,
    scope: list[str] | None = None,
    headers: dict[str, Any] | None = None,
    timeout: httpx.Timeout | None = None,
    **httpx_kwargs: Any,
) -> AsyncIterator[httpx.AsyncClient]:
    """
    Create an authenticated OAuth client for an MCP server from an endpoint URL.

    This function handles:
    1. OAuth metadata discovery
    2. Dynamic client registration if needed
    3. Authorization code flow with PKCE
    4. Token refreshing
    5. Token persistence

    If the server doesn't require authentication, a regular client will be returned.

    Args:
        mcp_endpoint: Full URL to an MCP endpoint (e.g.,
            https://mcp.example.com/sse). This will be used to discover the OAuth
            configuration.
        redirect_uri: OAuth redirect URI for the authorization flow. If None,
            a server will be started on an available port.
        scope: OAuth scopes to request
        headers: Additional headers to include in the requests
        timeout: Timeout for the requests
        **httpx_kwargs: Additional arguments for the httpx client

    Returns:
        An httpx.AsyncClient that handles authentication automatically
    """
    # Extract base URL for OAuth discovery
    base_url = oauth_cache.get_base_url(mcp_endpoint)
    logger.debug(f"MCP Endpoint: {mcp_endpoint}")
    logger.debug(f"Base URL for OAuth: {base_url}")

    # Discover OAuth metadata
    metadata = await discover_oauth_metadata(base_url)

    # If metadata is None, the server doesn't require authentication
    if metadata is None:
        logger.info("Server doesn't require authentication, creating regular client")
        async with create_mcp_http_client(
            headers=headers,
            timeout=timeout,
            **httpx_kwargs,
        ) as client:
            yield client
        return

    logger.debug(f"Using OAuth endpoints: {metadata}")

    # Use dynamic redirect URI if none provided
    # Generate port only once and reuse it for all operations
    port = None
    if redirect_uri is None:
        port = find_available_port()
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        logger.debug(f"Using dynamic redirect URI: {redirect_uri}")

    # Load or register client - check if we need to update registration due to new redirect URI
    creds = oauth_cache.load(mcp_endpoint, "client")
    if creds and redirect_uri not in creds.get("redirect_uris", []):
        logger.debug("Redirect URI not in registered URIs, re-registering client")
        creds = None  # Force re-registration

    # Register if needed
    if not creds:
        try:
            creds = await register_client(
                metadata["registration_endpoint"], redirect_uri
            )
            oauth_cache.save(mcp_endpoint, creds, "client")
            logger.debug(f"Client registered with redirect URI: {redirect_uri}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # If registration endpoint returns 404, server likely doesn't support OAuth
                logger.info("Registration endpoint not found, creating regular client")
                async with create_mcp_http_client(
                    headers=headers,
                    timeout=timeout,
                    **httpx_kwargs,
                ) as client:
                    yield client
                return
            else:
                # Other HTTP errors should be propagated
                raise

    # Create the OAuth client
    oauth_client = AsyncOAuth2Client(
        client_id=creds["client_id"],
        client_secret=creds.get("client_secret"),  # "public" clients omit secret
        scope=scope or ["openid", "profile", "email"],
        redirect_uri=redirect_uri,
        **httpx_kwargs,
    )

    # Load token if exists - passing mcp_endpoint directly
    token = oauth_cache.load(mcp_endpoint, "token")
    if token:
        oauth_client.token = token

    try:
        # Ensure we have a valid token
        if (
            not oauth_client.token
            or not oauth_client.token.get("expires_at")
            or oauth_client.token["expires_at"] < time.time()
        ):
            # Try to refresh if possible
            if oauth_client.token and oauth_client.token.get("refresh_token"):
                logger.debug("Refreshing token")
                try:
                    # ignore type because refresh_token is awaitable but not typed as such
                    token = await oauth_client.refresh_token(  # type: ignore[await-expr]
                        url=metadata["token_endpoint"],
                        refresh_token=oauth_client.token["refresh_token"],
                    )
                except Exception as e:
                    logger.warning(f"Failed to refresh token: {e}")
                    token = None
            else:
                token = None

            # If token is still not available, start authorization flow
            if not token:
                # Start authorization flow with PKCE
                logger.info("Starting authorization flow")
                uri, _ = oauth_client.create_authorization_url(
                    metadata["authorization_endpoint"],
                    redirect_uri=redirect_uri,
                    code_challenge_method="S256",
                )
                import webbrowser

                webbrowser.open(uri)

                # Wait for redirect after user approval - reuse the same port
                try:
                    # We need to ensure port is not None for the _get_redirect function
                    redirect_port = port if port is not None else find_available_port()
                    redirect_url = await _get_redirect(port=redirect_port)
                    logger.info("Received redirect, fetching token")

                    # ignore type because fetch_token is awaitable but not typed as such
                    token = await oauth_client.fetch_token(  # type: ignore[await-expr]
                        url=metadata["token_endpoint"],
                        authorization_response=redirect_url,
                        timeout=15,  # seconds
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch token: {e}")
                    token = None

            # Save token for future use - passing mcp_endpoint directly
            if token is not None:
                oauth_cache.save(mcp_endpoint, token, "token")
                logger.debug("Token saved successfully")

        # Create a standard httpx client with the OAuth bearer auth
        async with create_mcp_http_client(
            auth=OAuthBearerAuth(oauth_client),
            headers=headers,
            timeout=timeout,
            **httpx_kwargs,
        ) as client:
            # Yield the authenticated client
            yield client
    finally:
        await oauth_client.aclose()


@contextmanager
def patch_mcp_httpx_client(mcp_endpoint: str):
    """
    This context manager can be used to monkeypatch the low-level function that
    returns an httpx.AsyncClient. It replaces it with a function that returns an
    MCP OAuth-aware client.

    This is ugly, but it lets us reuse the low-level SDK without maintaining a fork.
    """
    original_shttp_client_fn = mcp.client.streamable_http.create_mcp_http_client  # type: ignore
    original_sse_client_fn = mcp.client.sse.create_mcp_http_client  # type: ignore

    # use tokens to manage context across concurrent requests
    token = _current_mcp_endpoint.set(mcp_endpoint)

    def patched_mcp_client(**kwargs):
        url = _current_mcp_endpoint.get()
        if url is None:
            return mcp.shared._httpx_utils.create_mcp_http_client(**kwargs)
        return create_mcp_oauth_client(mcp_endpoint=url, **kwargs)

    try:
        mcp.client.streamable_http.create_mcp_http_client = patched_mcp_client  # type: ignore
        mcp.client.sse.create_mcp_http_client = patched_mcp_client  # type: ignore
        yield
    finally:
        _current_mcp_endpoint.reset(token)
        mcp.client.streamable_http.create_mcp_http_client = original_shttp_client_fn  # type: ignore
        mcp.client.sse.create_mcp_http_client = original_sse_client_fn  # type: ignore
