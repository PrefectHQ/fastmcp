import abc
import contextlib
import datetime
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import (
    TypedDict,
)

from exceptiongroup import BaseExceptionGroup, catch
from mcp import ClientSession, McpError, StdioServerParameters
from mcp.client.session import (
    ListRootsFnT,
    LoggingFnT,
    MessageHandlerFnT,
    SamplingFnT,
)
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.websocket import websocket_client
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl
from typing_extensions import Unpack

from fastmcp.exceptions import ClientError
from fastmcp.server import FastMCP as FastMCPServer


class SessionKwargs(TypedDict, total=False):
    """Keyword arguments for the MCP ClientSession constructor."""

    sampling_callback: SamplingFnT | None
    list_roots_callback: ListRootsFnT | None
    logging_callback: LoggingFnT | None
    message_handler: MessageHandlerFnT | None
    read_timeout_seconds: datetime.timedelta | None


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
        Establishes a connection and yields an active, initialized ClientSession.

        The session is guaranteed to be valid only within the scope of the
        async context manager. Connection setup and teardown are handled
        within this context.

        Args:
            **session_kwargs: Keyword arguments to pass to the ClientSession
                              constructor (e.g., callbacks, timeouts).

        Yields:
            An initialized mcp.ClientSession instance.
        """
        raise NotImplementedError
        yield None  # type: ignore

    def __repr__(self) -> str:
        # Basic representation for subclasses
        return f"<{self.__class__.__name__}>"


class WSTransport(ClientTransport):
    """Transport implementation that connects to an MCP server via WebSockets."""

    def __init__(self, url: str | AnyUrl):
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("ws"):
            raise ValueError("Invalid WebSocket URL provided.")
        self.url = url

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        async with websocket_client(self.url) as transport:
            read_stream, write_stream = transport
            async with ClientSession(
                read_stream, write_stream, **session_kwargs
            ) as session:
                await session.initialize()  # Initialize after session creation
                yield session

    def __repr__(self) -> str:
        return f"<WebSocket(url='{self.url}')>"


class SSETransport(ClientTransport):
    """Transport implementation that connects to an MCP server via Server-Sent Events."""

    def __init__(self, url: str | AnyUrl, headers: dict[str, str] | None = None):
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("http"):
            raise ValueError("Invalid HTTP/S URL provided for SSE.")
        self.url = url
        self.headers = headers or {}

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        async with sse_client(self.url, headers=self.headers) as transport:
            read_stream, write_stream = transport
            async with ClientSession(
                read_stream, write_stream, **session_kwargs
            ) as session:
                await session.initialize()
                yield session

    def __repr__(self) -> str:
        return f"<SSE(url='{self.url}')>"


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
    ):
        """
        Initialize a Stdio transport.

        Args:
            command: The command to run (e.g., "python", "node", "uvx")
            args: The arguments to pass to the command
            env: Environment variables to set for the subprocess
            cwd: Current working directory for the subprocess
        """
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        server_params = StdioServerParameters(
            command=self.command, args=self.args, env=self.env, cwd=self.cwd
        )
        async with stdio_client(server_params) as transport:
            read_stream, write_stream = transport
            async with ClientSession(
                read_stream, write_stream, **session_kwargs
            ) as session:
                await session.initialize()
                yield session

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
        python_cmd: str = "python",
    ):
        """
        Initialize a Python transport.

        Args:
            script_path: Path to the Python script to run
            args: Additional arguments to pass to the script
            env: Environment variables to set for the subprocess
            cwd: Current working directory for the subprocess
            python_cmd: Python command to use (default: "python")
        """
        script_path = Path(script_path).resolve()
        if not script_path.is_file():
            raise FileNotFoundError(f"Script not found: {script_path}")
        if not str(script_path).endswith(".py"):
            raise ValueError(f"Not a Python script: {script_path}")

        full_args = [str(script_path)]
        if args:
            full_args.extend(args)

        super().__init__(command=python_cmd, args=full_args, env=env, cwd=cwd)
        self.script_path = script_path


class FastMCPStdioTransport(StdioTransport):
    """Transport for running FastMCP servers using the FastMCP CLI."""

    def __init__(
        self,
        script_path: str | Path,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ):
        script_path = Path(script_path).resolve()
        if not script_path.is_file():
            raise FileNotFoundError(f"Script not found: {script_path}")
        if not str(script_path).endswith(".py"):
            raise ValueError(f"Not a Python script: {script_path}")

        super().__init__(
            command="fastmcp", args=["run", str(script_path)], env=env, cwd=cwd
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
    ):
        """
        Initialize a Node transport.

        Args:
            script_path: Path to the Node.js script to run
            args: Additional arguments to pass to the script
            env: Environment variables to set for the subprocess
            cwd: Current working directory for the subprocess
            node_cmd: Node.js command to use (default: "node")
        """
        script_path = Path(script_path).resolve()
        if not script_path.is_file():
            raise FileNotFoundError(f"Script not found: {script_path}")
        if not str(script_path).endswith(".js"):
            raise ValueError(f"Not a JavaScript script: {script_path}")

        full_args = [str(script_path)]
        if args:
            full_args.extend(args)

        super().__init__(command=node_cmd, args=full_args, env=env, cwd=cwd)
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

        super().__init__(command="uvx", args=uvx_args, env=env, cwd=project_directory)
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
    ):
        """
        Initialize an Npx transport.

        Args:
            package: Name of the npm package to run
            args: Arguments to pass to the package command
            project_directory: Project directory with package.json
            env_vars: Additional environment variables
            use_package_lock: Whether to use package-lock.json (--prefer-offline)
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

        super().__init__(command="npx", args=npx_args, env=env, cwd=project_directory)
        self.package = package


class FastMCPTransport(ClientTransport):
    """
    Special transport for in-memory connections to an MCP server.

    This is particularly useful for testing or when client and server
    are in the same process.
    """

    def __init__(self, mcp: FastMCPServer):
        self._fastmcp = mcp  # Can be FastMCP or MCPServer

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        def exception_handler(excgroup: BaseExceptionGroup):
            for exc in excgroup.exceptions:
                if isinstance(exc, BaseExceptionGroup):
                    exception_handler(exc)
                raise exc

        def mcperror_handler(excgroup: BaseExceptionGroup):
            for exc in excgroup.exceptions:
                if isinstance(exc, BaseExceptionGroup):
                    mcperror_handler(exc)
                raise ClientError(exc)

        # backport of 3.11's except* syntax
        with catch({McpError: mcperror_handler, Exception: exception_handler}):
            # create_connected_server_and_client_session manages the session lifecycle itself
            async with create_connected_server_and_client_session(
                server=self._fastmcp._mcp_server,
                **session_kwargs,
            ) as session:
                yield session

    def __repr__(self) -> str:
        return f"<FastMCP(server='{self._fastmcp.name}')>"


def infer_transport(
    transport: ClientTransport | FastMCPServer | AnyUrl | Path | str,
) -> ClientTransport:
    """
    Infer the appropriate transport type from the given transport argument.

    This function attempts to infer the correct transport type from the provided
    argument, handling various input types and converting them to the appropriate
    ClientTransport subclass.
    """
    # the transport is already a ClientTransport
    if isinstance(transport, ClientTransport):
        return transport

    # the transport is a FastMCP server
    elif isinstance(transport, FastMCPServer):
        return FastMCPTransport(mcp=transport)

    # the transport is a path to a script
    elif isinstance(transport, Path | str) and Path(transport).exists():
        if str(transport).endswith(".py"):
            return PythonStdioTransport(script_path=transport)
        elif str(transport).endswith(".js"):
            return NodeStdioTransport(script_path=transport)
        else:
            raise ValueError(f"Unsupported script type: {transport}")

    # the transport is an http(s) URL
    elif isinstance(transport, AnyUrl | str) and str(transport).startswith("http"):
        return SSETransport(url=transport)

    # the transport is a websocket URL
    elif isinstance(transport, AnyUrl | str) and str(transport).startswith("ws"):
        return WSTransport(url=transport)

    # the transport is an unknown type
    else:
        raise ValueError(f"Could not infer a valid transport from: {transport}")
