from __future__ import annotations

import asyncio
import copy
import multiprocessing
import socket
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, urlparse

import httpx2
import uvicorn
from mcp.shared.auth import AuthorizationCodeResult

from fastmcp import settings
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.client import Client
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.utilities.asgi_transport import (
    StreamingASGITransport,
    run_asgi_lifespan,
)
from fastmcp.utilities.http import find_available_port

if TYPE_CHECKING:
    from starlette.types import ASGIApp

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


async def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> None:
    """Poll until a TCP connection to `host:port` is accepted, or raise on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection(host, port)
        except (ConnectionRefusedError, OSError):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Server did not start listening on {host}:{port} "
                    f"within {timeout} seconds"
                ) from None
            await asyncio.sleep(0.001)
        else:
            writer.close()
            with suppress(ConnectionResetError, BrokenPipeError):
                await writer.wait_closed()
            return


@asynccontextmanager
async def run_server_async(
    server: FastMCP,
    port: int | None = None,
    transport: Literal["http", "streamable-http", "sse"] = "http",
    path: str = "/mcp",
    host: str = "127.0.0.1",
) -> AsyncGenerator[str, None]:
    """
    Start a FastMCP server on a real port as an asyncio task.

    This runs a real uvicorn server in the current process, bound to a real TCP port,
    and yields its URL. Use it when the behaviour under test is genuinely about the
    network — real sockets, TLS, or a server that must be reachable by something other
    than an in-process client. Otherwise prefer `asgi_client` or `asgi_server`, which
    exercise the same HTTP stack without binding a port.

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
    if port is None:
        port = find_available_port()

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

    # Wait for server lifespan to be ready
    await server._started.wait()

    # The lifespan completing does not guarantee uvicorn has bound the port yet, so
    # poll until the socket accepts a connection rather than guessing at a sleep.
    await _wait_for_port(host, port)

    try:
        yield f"http://{host}:{port}{path}"
    finally:
        # Cleanup: cancel the task with timeout to avoid hanging on Windows
        server_task.cancel()
        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(server_task, timeout=2.0)


@dataclass(frozen=True)
class ASGIServer:
    """A FastMCP server's real HTTP app, reachable in-process with no sockets.

    Yielded by `asgi_server`. The `url` looks like an ordinary server URL and the app
    behind it is the genuine article — auth middleware, session manager, SSE framing and
    redirects all run — but every request is dispatched straight into the ASGI
    application on the current event loop.

    Because nothing is listening on the network, a plain `httpx2.AsyncClient()` cannot
    reach this server. Use `client()` for a FastMCP client, `http_client()` for raw HTTP
    assertions, and `transport()` when you need to build the client transport yourself.
    """

    url: str
    app: ASGIApp
    transport_type: Literal["http", "streamable-http", "sse"]

    def http_client(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kwargs: Any,
    ) -> httpx2.AsyncClient:
        """An `httpx2.AsyncClient` bound to the in-process app, for raw HTTP assertions.

        Relative URLs resolve against the server's base URL, and absolute URLs on the
        same origin work too, so `client.get(f"{server.url}/health")` reads the same as
        it would against a real server.

        The signature matches `McpHttpClientFactory`, so this method can also be handed
        to anything that takes an `httpx_client_factory`.
        """
        # The legacy SSE transport runs the whole MCP session inside its GET request and
        # only releases its streams once that request observes a disconnect, so the
        # bridge must let the application drain rather than cancelling at close.
        cancel_on_close = self.transport_type != "sse"
        return httpx2.AsyncClient(
            transport=StreamingASGITransport(self.app, cancel_on_close=cancel_on_close),
            base_url=self.url,
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    def transport(self, **kwargs: Any) -> StreamableHttpTransport | SSETransport:
        """A FastMCP client transport wired to the in-process app.

        Accepts the same keyword arguments as the underlying transport (`headers`,
        `auth`, ...); `httpx_client_factory` is supplied automatically.
        """
        kwargs.setdefault("httpx_client_factory", self.http_client)
        if self.transport_type == "sse":
            return SSETransport(self.url, **kwargs)
        return StreamableHttpTransport(self.url, **kwargs)

    def client(
        self,
        *,
        headers: dict[str, str] | None = None,
        auth: httpx2.Auth | Literal["oauth"] | str | None = None,
        **client_kwargs: Any,
    ) -> Client:
        """An unconnected FastMCP `Client` pointed at the in-process app.

        `headers` and `auth` configure the underlying HTTP transport; every other
        keyword argument is passed to `Client` (`timeout`, `elicitation_handler`, ...).
        Use it as a context manager, exactly like any other client.

        Args:
            headers: HTTP headers to send with every request.
            auth: Client authentication, as accepted by the HTTP transports.
            **client_kwargs: Additional arguments forwarded to `Client`.
        """
        return Client(self.transport(headers=headers, auth=auth), **client_kwargs)


@asynccontextmanager
async def asgi_server(
    server: FastMCP,
    transport: Literal["http", "streamable-http", "sse"] = "http",
    path: str | None = None,
    **http_app_kwargs: Any,
) -> AsyncGenerator[ASGIServer, None]:
    """
    Serve a FastMCP server's HTTP app in-process, with no socket and no uvicorn.

    This is the fastest way to test a FastMCP server over HTTP. The server's real
    Starlette app is built with `http_app()` and its lifespan is started, then every
    request is dispatched directly into the app on the current event loop. That skips
    port binding, uvicorn startup and connection setup entirely, while still exercising
    the full HTTP stack: middleware, authentication, session management and SSE
    streaming all run exactly as they do in production.

    Use this as a fixture when several tests share one server but each needs its own
    client. For a single test, `asgi_client` hands you a connected client in one step.

    Args:
        server: FastMCP server instance.
        transport: Transport type ("http", "streamable-http", or "sse").
        path: URL path for the server (defaults to "/mcp", or "/sse" for SSE).
        **http_app_kwargs: Additional arguments forwarded to `server.http_app()`.

    Yields:
        An `ASGIServer` describing how to reach the app.

    Example:
        ```python
        import pytest
        from fastmcp import FastMCP
        from fastmcp.utilities.tests import ASGIServer, asgi_server

        mcp = FastMCP("test")

        @mcp.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        @pytest.fixture
        async def server():
            async with asgi_server(mcp) as running_server:
                yield running_server

        async def test_greet(server: ASGIServer):
            async with server.client() as client:
                result = await client.call_tool("greet", {"name": "World"})
                assert result.data == "Hello, World!"

        async def test_greet_with_headers(server: ASGIServer):
            async with server.client(headers={"X-Tenant": "acme"}) as client:
                result = await client.call_tool("greet", {"name": "World"})
                assert result.data == "Hello, World!"
        ```
    """
    if path is None:
        path = "/sse" if transport == "sse" else "/mcp"

    app = server.http_app(transport=transport, path=path, **http_app_kwargs)

    # Nothing listens on this origin; it exists so that URLs are well-formed and so
    # that host-header checks see a loopback address, as they would locally.
    base_url = "http://127.0.0.1"

    async with run_asgi_lifespan(app):
        yield ASGIServer(
            url=f"{base_url}{path}",
            app=app,
            transport_type=transport,
        )


@asynccontextmanager
async def asgi_client(
    server: FastMCP,
    transport: Literal["http", "streamable-http", "sse"] = "http",
    path: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    auth: httpx2.Auth | Literal["oauth"] | str | None = None,
    **client_kwargs: Any,
) -> AsyncGenerator[Client, None]:
    """
    Serve a FastMCP server over HTTP in-process and yield a connected `Client`.

    This is the shortest path to testing a server over a real HTTP stack. The server's
    Starlette app is built and started, and requests are dispatched straight into it on
    the current event loop — no port, no uvicorn, no subprocess — but middleware,
    authentication, session management and SSE streaming all behave as in production.

    Reach for `asgi_server` instead when a fixture must serve several tests that each
    build their own client, or when a test needs raw HTTP access to the app.

    Args:
        server: FastMCP server instance.
        transport: Transport type ("http", "streamable-http", or "sse").
        path: URL path for the server (defaults to "/mcp", or "/sse" for SSE).
        headers: HTTP headers to send with every request.
        auth: Client authentication, as accepted by the HTTP transports.
        **client_kwargs: Additional arguments forwarded to `Client`.

    Yields:
        A connected `Client`.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.utilities.tests import asgi_client

        async def test_greet():
            mcp = FastMCP("test")

            @mcp.tool
            def greet(name: str) -> str:
                return f"Hello, {name}!"

            async with asgi_client(mcp) as client:
                result = await client.call_tool("greet", {"name": "World"})
                assert result.data == "Hello, World!"
        ```
    """
    async with (
        asgi_server(server, transport=transport, path=path) as running_server,
        running_server.client(headers=headers, auth=auth, **client_kwargs) as client,
    ):
        yield client


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
        async with httpx2.AsyncClient() as client:
            response = await client.get(authorization_url, follow_redirects=False)
            self._stored_response = response

    async def callback_handler(self) -> AuthorizationCodeResult:
        """Parse stored response and return the authorization code result."""
        if not self._stored_response:
            raise RuntimeError(
                "No authorization response stored. redirect_handler must be called first."
            )

        response = self._stored_response

        # Extract auth code from redirect location
        if response.status_code == 302:
            redirect_url = response.headers["location"]
            parsed = urlparse(redirect_url)
            # keep_blank_values=True so explicitly-empty params (e.g. ?state=)
            # survive parsing instead of being silently dropped. Real OAuth
            # callbacks can include empty `state` or `error_description`,
            # and downstream code distinguishes "" from missing.
            query_params = parse_qs(parsed.query, keep_blank_values=True)

            if "error" in query_params:
                error = query_params["error"][0]
                error_desc = query_params.get("error_description", ["Unknown error"])[0]
                raise RuntimeError(
                    f"OAuth authorization failed: {error} - {error_desc}"
                )

            auth_code = query_params["code"][0]
            state = query_params.get("state", [None])[0]
            iss = query_params.get("iss", [None])[0]
            return AuthorizationCodeResult(code=auth_code, state=state, iss=iss)
        else:
            raise RuntimeError(f"Authorization failed: {response.status_code}")
