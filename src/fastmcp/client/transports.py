import abc
import asyncio
import contextlib
import datetime
import os
import shutil
import sys
import warnings
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal, TypedDict, TypeVar, cast, overload
from urllib.parse import urlparse, urlunparse

import anyio
import httpx
import mcp.types
from mcp import ClientSession, StdioServerParameters
from mcp.client.session import ListRootsFnT, LoggingFnT, MessageHandlerFnT, SamplingFnT
from mcp.server.fastmcp import FastMCP as FastMCP1Server
from mcp.shared.memory import create_client_server_memory_streams
from pydantic import AnyUrl
from typing_extensions import Unpack

import fastmcp
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.auth.oauth import OAuth
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.server import FastMCP
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_config import MCPConfig, infer_transport_type_from_url

logger = get_logger(__name__)

# TypeVar for preserving specific ClientTransport subclass types
ClientTransportT = TypeVar("ClientTransportT", bound="ClientTransport")

__all__ = [
    "ClientTransport",
    "SSETransport",
    "StreamableHttpTransport",
    "StdioTransport",
    "PythonStdioTransport",
    "FastMCPStdioTransport",
    "NodeStdioTransport",
    "UvxStdioTransport",
    "NpxStdioTransport",
    "FastMCPTransport",
    "infer_transport",
]


class SessionKwargs(TypedDict, total=False):
    """Keyword arguments for the MCP ClientSession constructor."""

    read_timeout_seconds: datetime.timedelta | None
    sampling_callback: SamplingFnT | None
    list_roots_callback: ListRootsFnT | None
    logging_callback: LoggingFnT | None
    message_handler: MessageHandlerFnT | None
    client_info: mcp.types.Implementation | None


class ClientTransport(abc.ABC):
    """
    Abstract base class for different MCP client transport mechanisms.

    A Transport is responsible for establishing and managing connections
    to an MCP server, and providing a ClientSession within an async context.

    """

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        """
        Establishes a connection and yields an active ClientSession.

        The ClientSession is *not* expected to be initialized in this context manager.

        The session is guaranteed to be valid only within the scope of the
        async context manager. Connection setup and teardown are handled
        within this context.

        Args:
            **session_kwargs: Keyword arguments to pass to the ClientSession
                              constructor (e.g., callbacks, timeouts).

        Yields:
            A mcp.ClientSession instance.
        """
        raise NotImplementedError
        yield  # type: ignore

    def __repr__(self) -> str:
        # Basic representation for subclasses
        return f"<{self.__class__.__name__}>"

    async def close(self):
        """Close the transport."""
        pass

    def _set_auth(self, auth: httpx.Auth | Literal["oauth"] | str | None):
        if auth is not None:
            raise ValueError("This transport does not support auth")


class WSTransport(ClientTransport):
    """Transport implementation that connects to an MCP server via WebSockets."""

    def __init__(self, url: str | AnyUrl):
        # we never really used this transport, so it can be removed at any time
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "WSTransport is a deprecated MCP transport and will be removed in a future version. Use StreamableHttpTransport instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("ws"):
            raise ValueError("Invalid WebSocket URL provided.")
        self.url = url

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        try:
            from mcp.client.websocket import websocket_client
        except ImportError:
            raise ImportError(
                "The websocket transport is not available. Please install fastmcp[websockets] or install the websockets package manually."
            )

        async with websocket_client(self.url) as transport:
            read_stream, write_stream = transport
            async with ClientSession(
                read_stream, write_stream, **session_kwargs
            ) as session:
                yield session

    def __repr__(self) -> str:
        return f"<WebSocketTransport(url='{self.url}')>"


class SSETransport(ClientTransport):
    """Transport implementation that connects to an MCP server via Server-Sent Events."""

    def __init__(
        self,
        url: str | AnyUrl,
        headers: dict[str, str] | None = None,
        auth: httpx.Auth | Literal["oauth"] | str | None = None,
        sse_read_timeout: datetime.timedelta | float | int | None = None,
        httpx_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ):
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("http"):
            raise ValueError("Invalid HTTP/S URL provided for SSE.")

        # Ensure the URL path ends with a trailing slash to avoid automatic redirects
        parsed = urlparse(url)
        if not parsed.path.endswith("/"):
            parsed = parsed._replace(path=parsed.path + "/")
            url = urlunparse(parsed)

        self.url = url
        self.headers = headers or {}
        self._set_auth(auth)
        self.httpx_client_factory = httpx_client_factory

        if isinstance(sse_read_timeout, int | float):
            sse_read_timeout = datetime.timedelta(seconds=sse_read_timeout)
        self.sse_read_timeout = sse_read_timeout

    def _set_auth(self, auth: httpx.Auth | Literal["oauth"] | str | None):
        if auth == "oauth":
            auth = OAuth(self.url)
        elif isinstance(auth, str):
            auth = BearerAuth(auth)
        self.auth = auth

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        from mcp.client.sse import sse_client

        client_kwargs: dict[str, Any] = {}

        # load headers from an active HTTP request, if available. This will only be true
        # if the client is used in a FastMCP Proxy, in which case the MCP client headers
        # need to be forwarded to the remote server.
        client_kwargs["headers"] = get_http_headers() | self.headers

        # sse_read_timeout has a default value set, so we can't pass None without overriding it
        # instead we simply leave the kwarg out if it's not provided
        if self.sse_read_timeout is not None:
            client_kwargs["sse_read_timeout"] = self.sse_read_timeout.total_seconds()
        if session_kwargs.get("read_timeout_seconds", None) is not None:
            read_timeout_seconds = cast(
                datetime.timedelta, session_kwargs.get("read_timeout_seconds")
            )
            client_kwargs["timeout"] = read_timeout_seconds.total_seconds()

        if self.httpx_client_factory is not None:
            client_kwargs["httpx_client_factory"] = self.httpx_client_factory

        async with sse_client(self.url, auth=self.auth, **client_kwargs) as transport:
            read_stream, write_stream = transport
            async with ClientSession(
                read_stream, write_stream, **session_kwargs
            ) as session:
                yield session

    def __repr__(self) -> str:
        return f"<SSETransport(url='{self.url}')>"


class StreamableHttpTransport(ClientTransport):
    """Transport implementation that connects to an MCP server via Streamable HTTP Requests."""

    def __init__(
        self,
        url: str | AnyUrl,
        headers: dict[str, str] | None = None,
        auth: httpx.Auth | Literal["oauth"] | str | None = None,
        sse_read_timeout: datetime.timedelta | float | int | None = None,
        httpx_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ):
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("http"):
            raise ValueError("Invalid HTTP/S URL provided for Streamable HTTP.")

        # Ensure the URL path ends with a trailing slash to avoid automatic redirects
        parsed = urlparse(url)
        if not parsed.path.endswith("/"):
            parsed = parsed._replace(path=parsed.path + "/")
            url = urlunparse(parsed)

        self.url = url
        self.headers = headers or {}
        self._set_auth(auth)
        self.httpx_client_factory = httpx_client_factory

        if isinstance(sse_read_timeout, int | float):
            sse_read_timeout = datetime.timedelta(seconds=sse_read_timeout)
        self.sse_read_timeout = sse_read_timeout

    def _set_auth(self, auth: httpx.Auth | Literal["oauth"] | str | None):
        if auth == "oauth":
            auth = OAuth(self.url)
        elif isinstance(auth, str):
            auth = BearerAuth(auth)
        self.auth = auth

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        from mcp.client.streamable_http import streamablehttp_client

        client_kwargs: dict[str, Any] = {}

        # load headers from an active HTTP request, if available. This will only be true
        # if the client is used in a FastMCP Proxy, in which case the MCP client headers
        # need to be forwarded to the remote server.
        client_kwargs["headers"] = get_http_headers() | self.headers

        # sse_read_timeout has a default value set, so we can't pass None without overriding it
        # instead we simply leave the kwarg out if it's not provided
        if self.sse_read_timeout is not None:
            client_kwargs["sse_read_timeout"] = self.sse_read_timeout
        if session_kwargs.get("read_timeout_seconds", None) is not None:
            client_kwargs["timeout"] = session_kwargs.get("read_timeout_seconds")

        if self.httpx_client_factory is not None:
            client_kwargs["httpx_client_factory"] = self.httpx_client_factory

        async with streamablehttp_client(
            self.url,
            auth=self.auth,
            **client_kwargs,
        ) as transport:
            read_stream, write_stream, _ = transport
            async with ClientSession(
                read_stream, write_stream, **session_kwargs
            ) as session:
                yield session

    def __repr__(self) -> str:
        return f"<StreamableHttpTransport(url='{self.url}')>"


class StdioTransport(ClientTransport):
    """
    Base transport for connecting to an MCP server via subprocess with stdio.

    This is a base class that can be subclassed for specific command-based
    transports like Python, Node, Uvx, etc.
    """

    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        keep_alive: bool | None = None,
    ):
        """
        Initialize a Stdio transport.

        Args:
            command: The command to run (e.g., "python", "node", "uvx")
            args: The arguments to pass to the command
            env: Environment variables to set for the subprocess
            cwd: Current working directory for the subprocess
            keep_alive: Whether to keep the subprocess alive between connections.
                       Defaults to True. When True, the subprocess remains active
                       after the connection context exits, allowing reuse in
                       subsequent connections.
        """
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd
        if keep_alive is None:
            keep_alive = True
        self.keep_alive = keep_alive

        self._session: ClientSession | None = None
        self._connect_task: asyncio.Task | None = None
        self._ready_event = anyio.Event()
        self._stop_event = anyio.Event()

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        try:
            await self.connect(**session_kwargs)
            assert self._session is not None
            yield self._session
        finally:
            if not self.keep_alive:
                await self.disconnect()
            else:
                logger.debug("Stdio transport has keep_alive=True, not disconnecting")

    async def connect(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> ClientSession | None:
        if self._connect_task is not None:
            return

        async def _connect_task():
            from mcp.client.stdio import stdio_client

            async with contextlib.AsyncExitStack() as stack:
                try:
                    server_params = StdioServerParameters(
                        command=self.command, args=self.args, env=self.env, cwd=self.cwd
                    )
                    transport = await stack.enter_async_context(
                        stdio_client(server_params)
                    )
                    read_stream, write_stream = transport
                    self._session = await stack.enter_async_context(
                        ClientSession(read_stream, write_stream, **session_kwargs)
                    )

                    logger.debug("Stdio transport connected")
                    self._ready_event.set()

                    # Wait until disconnect is requested (stop_event is set)
                    await self._stop_event.wait()
                finally:
                    # Clean up client on exit
                    self._session = None
                    logger.debug("Stdio transport disconnected")

        # start the connection task
        self._connect_task = asyncio.create_task(_connect_task())
        # wait for the client to be ready before returning
        await self._ready_event.wait()

    async def disconnect(self):
        if self._connect_task is None:
            return

        # signal the connection task to stop
        self._stop_event.set()

        # wait for the connection task to finish cleanly
        await self._connect_task

        # reset variables and events for potential future reconnects
        self._connect_task = None
        self._stop_event = anyio.Event()
        self._ready_event = anyio.Event()

    async def close(self):
        await self.disconnect()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(command='{self.command}', args={self.args})>"
        )


class PythonStdioTransport(StdioTransport):
    """Transport for running Python scripts."""

    def __init__(
        self,
        script_path: str | Path,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        python_cmd: str = sys.executable,
        keep_alive: bool | None = None,
    ):
        """
        Initialize a Python transport.

        Args:
            script_path: Path to the Python script to run
            args: Additional arguments to pass to the script
            env: Environment variables to set for the subprocess
            cwd: Current working directory for the subprocess
            python_cmd: Python command to use (default: "python")
            keep_alive: Whether to keep the subprocess alive between connections.
                       Defaults to True. When True, the subprocess remains active
                       after the connection context exits, allowing reuse in
                       subsequent connections.
        """
        script_path = Path(script_path).resolve()
        if not script_path.is_file():
            raise FileNotFoundError(f"Script not found: {script_path}")
        if not str(script_path).endswith(".py"):
            raise ValueError(f"Not a Python script: {script_path}")

        full_args = [str(script_path)]
        if args:
            full_args.extend(args)

        super().__init__(
            command=python_cmd,
            args=full_args,
            env=env,
            cwd=cwd,
            keep_alive=keep_alive,
        )
        self.script_path = script_path


class FastMCPStdioTransport(StdioTransport):
    """Transport for running FastMCP servers using the FastMCP CLI."""

    def __init__(
        self,
        script_path: str | Path,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        keep_alive: bool | None = None,
    ):
        script_path = Path(script_path).resolve()
        if not script_path.is_file():
            raise FileNotFoundError(f"Script not found: {script_path}")
        if not str(script_path).endswith(".py"):
            raise ValueError(f"Not a Python script: {script_path}")

        super().__init__(
            command="fastmcp",
            args=["run", str(script_path)],
            env=env,
            cwd=cwd,
            keep_alive=keep_alive,
        )
        self.script_path = script_path


class NodeStdioTransport(StdioTransport):
    """Transport for running Node.js scripts."""

    def __init__(
        self,
        script_path: str | Path,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        node_cmd: str = "node",
        keep_alive: bool | None = None,
    ):
        """
        Initialize a Node transport.

        Args:
            script_path: Path to the Node.js script to run
            args: Additional arguments to pass to the script
            env: Environment variables to set for the subprocess
            cwd: Current working directory for the subprocess
            node_cmd: Node.js command to use (default: "node")
            keep_alive: Whether to keep the subprocess alive between connections.
                       Defaults to True. When True, the subprocess remains active
                       after the connection context exits, allowing reuse in
                       subsequent connections.
        """
        script_path = Path(script_path).resolve()
        if not script_path.is_file():
            raise FileNotFoundError(f"Script not found: {script_path}")
        if not str(script_path).endswith(".js"):
            raise ValueError(f"Not a JavaScript script: {script_path}")

        full_args = [str(script_path)]
        if args:
            full_args.extend(args)

        super().__init__(
            command=node_cmd, args=full_args, env=env, cwd=cwd, keep_alive=keep_alive
        )
        self.script_path = script_path


class UvxStdioTransport(StdioTransport):
    """Transport for running commands via the uvx tool."""

    def __init__(
        self,
        tool_name: str,
        tool_args: list[str] | None = None,
        project_directory: str | None = None,
        python_version: str | None = None,
        with_packages: list[str] | None = None,
        from_package: str | None = None,
        env_vars: dict[str, str] | None = None,
        keep_alive: bool | None = None,
    ):
        """
        Initialize a Uvx transport.

        Args:
            tool_name: Name of the tool to run via uvx
            tool_args: Arguments to pass to the tool
            project_directory: Project directory (for package resolution)
            python_version: Python version to use
            with_packages: Additional packages to include
            from_package: Package to install the tool from
            env_vars: Additional environment variables
            keep_alive: Whether to keep the subprocess alive between connections.
                       Defaults to True. When True, the subprocess remains active
                       after the connection context exits, allowing reuse in
                       subsequent connections.
        """
        # Basic validation
        if project_directory and not Path(project_directory).exists():
            raise NotADirectoryError(
                f"Project directory not found: {project_directory}"
            )

        # Build uvx arguments
        uvx_args = []
        if python_version:
            uvx_args.extend(["--python", python_version])
        if from_package:
            uvx_args.extend(["--from", from_package])
        for pkg in with_packages or []:
            uvx_args.extend(["--with", pkg])

        # Add the tool name and tool args
        uvx_args.append(tool_name)
        if tool_args:
            uvx_args.extend(tool_args)

        # Get environment with any additional variables
        env = None
        if env_vars:
            env = os.environ.copy()
            env.update(env_vars)

        super().__init__(
            command="uvx",
            args=uvx_args,
            env=env,
            cwd=project_directory,
            keep_alive=keep_alive,
        )
        self.tool_name = tool_name


class NpxStdioTransport(StdioTransport):
    """Transport for running commands via the npx tool."""

    def __init__(
        self,
        package: str,
        args: list[str] | None = None,
        project_directory: str | None = None,
        env_vars: dict[str, str] | None = None,
        use_package_lock: bool = True,
        keep_alive: bool | None = None,
    ):
        """
        Initialize an Npx transport.

        Args:
            package: Name of the npm package to run
            args: Arguments to pass to the package command
            project_directory: Project directory with package.json
            env_vars: Additional environment variables
            use_package_lock: Whether to use package-lock.json (--prefer-offline)
            keep_alive: Whether to keep the subprocess alive between connections.
                       Defaults to True. When True, the subprocess remains active
                       after the connection context exits, allowing reuse in
                       subsequent connections.
        """
        # verify npx is installed
        if shutil.which("npx") is None:
            raise ValueError("Command 'npx' not found")

        # Basic validation
        if project_directory and not Path(project_directory).exists():
            raise NotADirectoryError(
                f"Project directory not found: {project_directory}"
            )

        # Build npx arguments
        npx_args = []
        if use_package_lock:
            npx_args.append("--prefer-offline")

        # Add the package name and args
        npx_args.append(package)
        if args:
            npx_args.extend(args)

        # Get environment with any additional variables
        env = None
        if env_vars:
            env = os.environ.copy()
            env.update(env_vars)

        super().__init__(
            command="npx",
            args=npx_args,
            env=env,
            cwd=project_directory,
            keep_alive=keep_alive,
        )
        self.package = package


class FastMCPTransport(ClientTransport):
    """In-memory transport for FastMCP servers.

    This transport connects directly to a FastMCP server instance in the same
    Python process. It works with both FastMCP 2.x servers and FastMCP 1.0
    servers from the low-level MCP SDK. This is particularly useful for unit
    tests or scenarios where client and server run in the same runtime.
    """

    def __init__(self, mcp: FastMCP | FastMCP1Server, raise_exceptions: bool = False):
        """Initialize a FastMCPTransport from a FastMCP server instance."""

        # Accept both FastMCP 2.x and FastMCP 1.0 servers. Both expose a
        # ``_mcp_server`` attribute pointing to the underlying MCP server
        # implementation, so we can treat them identically.
        self.server = mcp
        self.raise_exceptions = raise_exceptions

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        async with create_client_server_memory_streams() as (
            client_streams,
            server_streams,
        ):
            client_read, client_write = client_streams
            server_read, server_write = server_streams

            # Create a cancel scope for the server task
            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    lambda: self.server._mcp_server.run(
                        server_read,
                        server_write,
                        self.server._mcp_server.create_initialization_options(),
                        raise_exceptions=self.raise_exceptions,
                    )
                )

                try:
                    async with ClientSession(
                        read_stream=client_read,
                        write_stream=client_write,
                        **session_kwargs,
                    ) as client_session:
                        yield client_session
                finally:
                    tg.cancel_scope.cancel()

    def __repr__(self) -> str:
        return f"<FastMCPTransport(server='{self.server.name}')>"


class MCPConfigTransport(ClientTransport):
    """Transport for connecting to one or more MCP servers defined in an MCPConfig.

    This transport provides a unified interface to multiple MCP servers defined in an MCPConfig
    object or dictionary matching the MCPConfig schema. It supports two key scenarios:

    1. If the MCPConfig contains exactly one server, it creates a direct transport to that server.
    2. If the MCPConfig contains multiple servers, it creates a composite client by mounting
       all servers on a single FastMCP instance, with each server's name used as its mounting prefix.

    In the multi-server case, tools are accessible with the prefix pattern `{server_name}_{tool_name}`
    and resources with the pattern `protocol://{server_name}/path/to/resource`.

    This is particularly useful for creating clients that need to interact with multiple specialized
    MCP servers through a single interface, simplifying client code.

    Examples:
        ```python
        from fastmcp import Client
        from fastmcp.utilities.mcp_config import MCPConfig

        # Create a config with multiple servers
        config = {
            "mcpServers": {
                "weather": {
                    "url": "https://weather-api.example.com/mcp",
                    "transport": "http"
                },
                "calendar": {
                    "url": "https://calendar-api.example.com/mcp",
                    "transport": "http"
                }
            }
        }

        # Create a client with the config
        client = Client(config)

        async with client:
            # Access tools with prefixes
            weather = await client.call_tool("weather_get_forecast", {"city": "London"})
            events = await client.call_tool("calendar_list_events", {"date": "2023-06-01"})

            # Access resources with prefixed URIs
            icons = await client.read_resource("weather://weather/icons/sunny")
        ```
    """

    def __init__(self, config: MCPConfig | dict):
        from fastmcp.client.client import Client

        if isinstance(config, dict):
            config = MCPConfig.from_dict(config)
        self.config = config

        # if there are no servers, raise an error
        if len(self.config.mcpServers) == 0:
            raise ValueError("No MCP servers defined in the config")

        # if there's exactly one server, create a client for that server
        elif len(self.config.mcpServers) == 1:
            self.transport = list(self.config.mcpServers.values())[0].to_transport()

        # otherwise create a composite client
        else:
            composite_server = FastMCP()

            for name, server in self.config.mcpServers.items():
                server_client = Client(transport=server.to_transport())
                composite_server.mount(
                    prefix=name, server=FastMCP.as_proxy(server_client)
                )

            self.transport = FastMCPTransport(mcp=composite_server)

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        async with self.transport.connect_session(**session_kwargs) as session:
            yield session

    def __repr__(self) -> str:
        return f"<MCPConfigTransport(config='{self.config}')>"


@overload
def infer_transport(transport: ClientTransportT) -> ClientTransportT: ...


@overload
def infer_transport(transport: FastMCP) -> FastMCPTransport: ...


@overload
def infer_transport(transport: FastMCP1Server) -> FastMCPTransport: ...


@overload
def infer_transport(transport: MCPConfig) -> MCPConfigTransport: ...


@overload
def infer_transport(transport: dict[str, Any]) -> MCPConfigTransport: ...


@overload
def infer_transport(
    transport: AnyUrl,
) -> SSETransport | StreamableHttpTransport: ...


@overload
def infer_transport(
    transport: str,
) -> (
    PythonStdioTransport | NodeStdioTransport | SSETransport | StreamableHttpTransport
): ...


@overload
def infer_transport(transport: Path) -> PythonStdioTransport | NodeStdioTransport: ...


def infer_transport(
    transport: ClientTransport
    | FastMCP
    | FastMCP1Server
    | AnyUrl
    | Path
    | MCPConfig
    | dict[str, Any]
    | str,
) -> ClientTransport:
    """
    Infer the appropriate transport type from the given transport argument.

    This function attempts to infer the correct transport type from the provided
    argument, handling various input types and converting them to the appropriate
    ClientTransport subclass.

    The function supports these input types:
    - ClientTransport: Used directly without modification
    - FastMCP or FastMCP1Server: Creates an in-memory FastMCPTransport
    - Path or str (file path): Creates PythonStdioTransport (.py) or NodeStdioTransport (.js)
    - AnyUrl or str (URL): Creates StreamableHttpTransport (default) or SSETransport (for /sse endpoints)
    - MCPConfig or dict: Creates MCPConfigTransport, potentially connecting to multiple servers

    For HTTP URLs, they are assumed to be Streamable HTTP URLs unless they end in `/sse`.

    For MCPConfig with multiple servers, a composite client is created where each server
    is mounted with its name as prefix. This allows accessing tools and resources from multiple
    servers through a single unified client interface, using naming patterns like
    `servername_toolname` for tools and `protocol://servername/path` for resources.
    If the MCPConfig contains only one server, a direct connection is established without prefixing.

    Examples:
        ```python
        # Connect to a local Python script
        transport = infer_transport("my_script.py")

        # Connect to a remote server via HTTP
        transport = infer_transport("http://example.com/mcp")

        # Connect to multiple servers using MCPConfig
        config = {
            "mcpServers": {
                "weather": {"url": "http://weather.example.com/mcp"},
                "calendar": {"url": "http://calendar.example.com/mcp"}
            }
        }
        transport = infer_transport(config)
        ```
    """

    # the transport is already a ClientTransport
    if isinstance(transport, ClientTransport):
        return transport

    # the transport is a FastMCP server (2.x or 1.0)
    elif isinstance(transport, FastMCP | FastMCP1Server):
        inferred_transport = FastMCPTransport(mcp=transport)

    # the transport is a path to a script
    elif isinstance(transport, Path | str) and Path(transport).exists():
        if str(transport).endswith(".py"):
            inferred_transport = PythonStdioTransport(script_path=transport)
        elif str(transport).endswith(".js"):
            inferred_transport = NodeStdioTransport(script_path=transport)
        else:
            raise ValueError(f"Unsupported script type: {transport}")

    # the transport is an http(s) URL
    elif isinstance(transport, AnyUrl | str) and str(transport).startswith("http"):
        inferred_transport_type = infer_transport_type_from_url(transport)
        if inferred_transport_type == "sse":
            inferred_transport = SSETransport(url=transport)
        else:
            inferred_transport = StreamableHttpTransport(url=transport)

    # if the transport is a config dict or MCPConfig
    elif isinstance(transport, dict | MCPConfig):
        inferred_transport = MCPConfigTransport(config=transport)

    # the transport is an unknown type
    else:
        raise ValueError(f"Could not infer a valid transport from: {transport}")

    logger.debug(f"Inferred transport: {inferred_transport}")
    return inferred_transport
