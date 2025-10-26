from __future__ import annotations

import copy
import logging
import multiprocessing
import socket
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager, suppress
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, urlparse

import httpx
import uvicorn

from fastmcp import settings
from fastmcp.client.auth.oauth import OAuth
from fastmcp.utilities.http import find_available_port

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP


@contextmanager
def temporary_settings(**kwargs: Any):
    """
    Temporarily override FastMCP setting values.

    Args:
        **kwargs: The settings to override, including nested settings.

    Example:
        Temporarily override a setting:
        ```python
        import fastmcp
        from fastmcp.utilities.tests import temporary_settings

        with temporary_settings(log_level='DEBUG'):
            assert fastmcp.settings.log_level == 'DEBUG'
        assert fastmcp.settings.log_level == 'INFO'
        ```
    """
    old_settings = copy.deepcopy(settings)

    try:
        # apply the new settings
        for attr, value in kwargs.items():
            settings.set_setting(attr, value)
        yield

    finally:
        # restore the old settings
        for attr in kwargs:
            settings.set_setting(attr, old_settings.get_setting(attr))


def _run_server(mcp_server: FastMCP, transport: Literal["sse"], port: int) -> None:
    # Some Starlette apps are not pickleable, so we need to create them here based on the indicated transport
    if transport == "sse":
        app = mcp_server.http_app(transport="sse")
    else:
        raise ValueError(f"Invalid transport: {transport}")
    uvicorn_server = uvicorn.Server(
        config=uvicorn.Config(
            app=app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            ws="websockets-sansio",
        )
    )
    uvicorn_server.run()


@contextmanager
def run_server_in_process(
    server_fn: Callable[..., None],
    *args: Any,
    provide_host_and_port: bool = True,
    host: str = "127.0.0.1",
    port: int | None = None,
    **kwargs: Any,
) -> Generator[str, None, None]:
    """
    Context manager that runs a FastMCP server in a separate process and
    returns the server URL. When the context manager is exited, the server process is killed.

    Args:
        server_fn: The function that runs a FastMCP server. FastMCP servers are
            not pickleable, so we need a function that creates and runs one.
        *args: Arguments to pass to the server function.
        provide_host_and_port: Whether to provide the host and port to the server function as kwargs.
        host: Host to bind the server to (default: "127.0.0.1").
        port: Port to bind the server to (default: find available port).
        **kwargs: Keyword arguments to pass to the server function.

    Returns:
        The server URL.
    """
    # Use provided port or find an available one
    if port is None:
        port = find_available_port()

    if provide_host_and_port:
        kwargs |= {"host": host, "port": port}

    proc = multiprocessing.Process(
        target=server_fn, args=args, kwargs=kwargs, daemon=True
    )
    proc.start()

    # Wait for server to be running
    max_attempts = 30
    attempt = 0
    while attempt < max_attempts and proc.is_alive():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((host, port))
                break
        except ConnectionRefusedError:
            if attempt < 5:
                time.sleep(0.05)
            elif attempt < 15:
                time.sleep(0.1)
            else:
                time.sleep(0.2)
            attempt += 1
    else:
        raise RuntimeError(f"Server failed to start after {max_attempts} attempts")

    yield f"http://{host}:{port}"

    proc.terminate()
    proc.join(timeout=5)
    if proc.is_alive():
        # If it's still alive, then force kill it
        proc.kill()
        proc.join(timeout=2)
        if proc.is_alive():
            raise RuntimeError("Server process failed to terminate even after kill")


@asynccontextmanager
async def run_server_async(
    server: FastMCP,
    port: int | None = None,
    transport: Literal["http", "streamable-http", "sse"] = "http",
    path: str = "/mcp",
    host: str = "127.0.0.1",
) -> AsyncGenerator[str, None]:
    """
    Start a FastMCP server as an asyncio task for in-process async testing.

    This is the recommended way to test FastMCP servers. It runs the server
    as an async task in the same process, eliminating subprocess coordination,
    sleeps, and cleanup issues.

    Args:
        server: FastMCP server instance
        port: Port to bind to (default: find available port)
        transport: Transport type ("http", "streamable-http", or "sse")
        path: URL path for the server (default: "/mcp")
        host: Host to bind to (default: "127.0.0.1")

    Yields:
        Server URL string

    Example:
        ```python
        import pytest
        from fastmcp import FastMCP, Client
        from fastmcp.client.transports import StreamableHttpTransport
        from fastmcp.utilities.tests import run_server_async

        @pytest.fixture
        async def server():
            mcp = FastMCP("test")

            @mcp.tool()
            def greet(name: str) -> str:
                return f"Hello, {name}!"

            async with run_server_async(mcp) as url:
                yield url

        async def test_greet(server: str):
            async with Client(StreamableHttpTransport(server)) as client:
                result = await client.call_tool("greet", {"name": "World"})
                assert result.content[0].text == "Hello, World!"
        ```
    """
    import asyncio

    if port is None:
        port = find_available_port()

    # Wait a tiny bit for the port to be released if it was just used
    await asyncio.sleep(0.01)

    # Start server as a background task
    server_task = asyncio.create_task(
        server.run_http_async(
            host=host,
            port=port,
            transport=transport,
            path=path,
            show_banner=False,
        )
    )

    # Give the server a moment to start
    await asyncio.sleep(0.1)

    try:
        yield f"http://{host}:{port}{path}"
    finally:
        # Cleanup: cancel the task
        server_task.cancel()
        with suppress(asyncio.CancelledError):
            await server_task


@contextmanager
def caplog_for_fastmcp(caplog):
    """Context manager to capture logs from FastMCP loggers even when propagation is disabled."""
    caplog.clear()
    logger = logging.getLogger("fastmcp")
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


class HeadlessOAuth(OAuth):
    """
    OAuth provider that bypasses browser interaction for testing.

    This simulates the complete OAuth flow programmatically by making HTTP requests
    instead of opening a browser and running a callback server. Useful for automated testing.
    """

    def __init__(self, mcp_url: str, **kwargs):
        """Initialize HeadlessOAuth with stored response tracking."""
        self._stored_response = None
        super().__init__(mcp_url, **kwargs)

    async def redirect_handler(self, authorization_url: str) -> None:
        """Make HTTP request to authorization URL and store response for callback handler."""
        async with httpx.AsyncClient() as client:
            response = await client.get(authorization_url, follow_redirects=False)
            self._stored_response = response

    async def callback_handler(self) -> tuple[str, str | None]:
        """Parse stored response and return (auth_code, state)."""
        if not self._stored_response:
            raise RuntimeError(
                "No authorization response stored. redirect_handler must be called first."
            )

        response = self._stored_response

        # Extract auth code from redirect location
        if response.status_code == 302:
            redirect_url = response.headers["location"]
            parsed = urlparse(redirect_url)
            query_params = parse_qs(parsed.query)

            if "error" in query_params:
                error = query_params["error"][0]
                error_desc = query_params.get("error_description", ["Unknown error"])[0]
                raise RuntimeError(
                    f"OAuth authorization failed: {error} - {error_desc}"
                )

            auth_code = query_params["code"][0]
            state = query_params.get("state", [None])[0]
            return auth_code, state
        else:
            raise RuntimeError(f"Authorization failed: {response.status_code}")
