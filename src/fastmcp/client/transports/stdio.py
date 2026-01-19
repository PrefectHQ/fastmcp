import asyncio
import contextlib
import os
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TextIO, cast

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typing_extensions import Unpack

from fastmcp.client.transports.base import ClientTransport, SessionKwargs
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment

logger = get_logger(__name__)


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
        log_file: Path | TextIO | None = None,
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
            log_file: Optional path or file-like object where subprocess stderr will
                   be written. Can be a Path or TextIO object. Defaults to sys.stderr
                   if not provided. When a Path is provided, the file will be created
                   if it doesn't exist, or appended to if it does. When set, server
                   errors will be written to this file instead of appearing in the console.
        """
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd
        if keep_alive is None:
            keep_alive = True
        self.keep_alive = keep_alive
        self.log_file = log_file

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
            yield cast(ClientSession, self._session)
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

        session_future: asyncio.Future[ClientSession] = asyncio.Future()

        # start the connection task
        self._connect_task = asyncio.create_task(
            _stdio_transport_connect_task(
                command=self.command,
                args=self.args,
                env=self.env,
                cwd=self.cwd,
                log_file=self.log_file,
                # TODO(ty): remove when ty supports Unpack[TypedDict] inference
                session_kwargs=session_kwargs,  # type: ignore[arg-type]
                ready_event=self._ready_event,
                stop_event=self._stop_event,
                session_future=session_future,
            )
        )

        # wait for the client to be ready before returning
        await self._ready_event.wait()

        # Check if connect task completed with an exception (early failure)
        if self._connect_task.done():
            exception = self._connect_task.exception()
            if exception is not None:
                raise exception

        self._session = await session_future
        return self._session

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

    def __del__(self):
        """Ensure that we send a disconnection signal to the transport task if we are being garbage collected."""
        if not self._stop_event.is_set():
            self._stop_event.set()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(command='{self.command}', args={self.args})>"
        )


async def _stdio_transport_connect_task(
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    cwd: str | None,
    log_file: Path | TextIO | None,
    session_kwargs: SessionKwargs,
    ready_event: anyio.Event,
    stop_event: anyio.Event,
    session_future: asyncio.Future[ClientSession],
):
    """A standalone connection task for a stdio transport. It is not a part of the StdioTransport class
    to ensure that the connection task does not hold a reference to the Transport object."""

    try:
        async with contextlib.AsyncExitStack() as stack:
            try:
                server_params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env,
                    cwd=cwd,
                )
                # Handle log_file: Path needs to be opened, TextIO used as-is
                if log_file is None:
                    log_file_handle = sys.stderr
                elif isinstance(log_file, Path):
                    log_file_handle = stack.enter_context(log_file.open("a"))
                else:
                    # Must be TextIO - use it directly
                    log_file_handle = log_file

                transport = await stack.enter_async_context(
                    stdio_client(server_params, errlog=log_file_handle)
                )
                read_stream, write_stream = transport
                session_future.set_result(
                    await stack.enter_async_context(
                        ClientSession(read_stream, write_stream, **session_kwargs)
                    )
                )

                logger.debug("Stdio transport connected")
                ready_event.set()

                # Wait until disconnect is requested (stop_event is set)
                await stop_event.wait()
            finally:
                # Clean up client on exit
                logger.debug("Stdio transport disconnected")
    except Exception:
        # Ensure ready event is set even if connection fails
        ready_event.set()
        raise


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
        log_file: Path | TextIO | None = None,
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
            log_file: Optional path or file-like object where subprocess stderr will
                   be written. Can be a Path or TextIO object. Defaults to sys.stderr
                   if not provided. When a Path is provided, the file will be created
                   if it doesn't exist, or appended to if it does. When set, server
                   errors will be written to this file instead of appearing in the console.
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
            log_file=log_file,
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
        log_file: Path | TextIO | None = None,
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
            log_file=log_file,
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
        log_file: Path | TextIO | None = None,
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
            log_file: Optional path or file-like object where subprocess stderr will
                   be written. Can be a Path or TextIO object. Defaults to sys.stderr
                   if not provided. When a Path is provided, the file will be created
                   if it doesn't exist, or appended to if it does. When set, server
                   errors will be written to this file instead of appearing in the console.
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
            command=node_cmd,
            args=full_args,
            env=env,
            cwd=cwd,
            keep_alive=keep_alive,
            log_file=log_file,
        )
        self.script_path = script_path


class UvStdioTransport(StdioTransport):
    """Transport for running commands via the uv tool."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        module: bool = False,
        project_directory: Path | None = None,
        python_version: str | None = None,
        with_packages: list[str] | None = None,
        with_requirements: Path | None = None,
        env_vars: dict[str, str] | None = None,
        keep_alive: bool | None = None,
    ):
        # Basic validation
        if project_directory and not project_directory.exists():
            raise NotADirectoryError(
                f"Project directory not found: {project_directory}"
            )

        # Create Environment from provided parameters (internal use)
        env_config = UVEnvironment(
            python=python_version,
            dependencies=with_packages,
            requirements=with_requirements,
            project=project_directory,
            editable=None,  # Not exposed in this transport
        )

        # Build uv arguments using the config
        uv_args: list[str] = []

        # Check if we need any environment setup
        if env_config._must_run_with_uv():
            # Use the config to build args, but we need to handle the command differently
            # since transport has specific needs
            uv_args = ["run"]

            if python_version:
                uv_args.extend(["--python", python_version])
            if project_directory:
                uv_args.extend(["--directory", str(project_directory)])

            # Note: Don't add fastmcp as dependency here, transport is for general use
            for pkg in with_packages or []:
                uv_args.extend(["--with", pkg])
            if with_requirements:
                uv_args.extend(["--with-requirements", str(with_requirements)])
        else:
            # No environment setup needed
            uv_args = ["run"]

        if module:
            uv_args.append("--module")

        if not args:
            args = []

        uv_args.extend([command, *args])

        # Get environment with any additional variables
        env: dict[str, str] | None = None
        if env_vars or project_directory:
            env = os.environ.copy()
            if project_directory:
                env["UV_PROJECT_DIR"] = str(project_directory)
            if env_vars:
                env.update(env_vars)

        super().__init__(
            command="uv",
            args=uv_args,
            env=env,
            cwd=None,  # Use --directory flag instead of cwd
            keep_alive=keep_alive,
        )


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
        uvx_args: list[str] = []
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

        env: dict[str, str] | None = None
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
        self.tool_name: str = tool_name


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
