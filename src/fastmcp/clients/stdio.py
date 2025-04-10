import contextlib
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typing_extensions import Unpack

from fastmcp.clients.base import BaseClient, ClientKwargs


class StdioClient(BaseClient):
    def __init__(
        self,
        server_script_path: str,
        **kwargs: Unpack[ClientKwargs],
    ):
        super().__init__(**kwargs)
        self.server_script_path = server_script_path

    @contextlib.asynccontextmanager
    async def _connect(self):
        """Set up stdio connection and session"""
        is_python = self.server_script_path.endswith(".py")
        is_js = self.server_script_path.endswith(".js")
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command, args=[self.server_script_path], env=None
        )

        async with stdio_client(server_params) as transport:
            stdio, write = transport

            async with ClientSession(
                read_stream=stdio,
                write_stream=write,
                **self._session_kwargs(),
            ) as session:
                async with self._set_session(transport, session):
                    yield self


class UvxClient(BaseClient):
    """Client that uses uvx to run Python tools in isolated environments.

    uvx automatically installs and manages dependencies from pyproject.toml.
    """

    def __init__(
        self,
        tool_name: str,
        tool_args: list[str] | None = None,
        project_directory: str | None = None,
        python_version: str | None = None,
        with_packages: list[str] | None = None,
        from_package: str | None = None,
        env_vars: dict[str, str] | None = None,
        **kwargs: Unpack[ClientKwargs],
    ):
        """Initialize a UvxClient that uses uvx to run Python tools in isolated environments.

        Args:
            tool_name: Name of the tool/command to run
            tool_args: Arguments to pass to the tool
            project_directory: Path to the project directory (optional)
            python_version: Specific Python version to use (e.g., "3.10")
            with_packages: Additional packages to include
            from_package: Package that provides the tool if different from tool_name
            env_vars: Environment variables to set for the process
            **kwargs: Additional arguments for BaseClient
        """
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.tool_args = tool_args or []
        self.project_directory = project_directory
        self.python_version = python_version
        self.with_packages = with_packages or []
        self.from_package = from_package
        self.env_vars = env_vars or {}

    @contextlib.asynccontextmanager
    async def _connect(self):
        """Set up uvx connection and session"""
        # Check if project directory exists if provided
        if self.project_directory and not os.path.isdir(self.project_directory):
            raise ValueError(
                f"Project directory does not exist: {self.project_directory}"
            )

        # Build the uvx command arguments
        args = []

        # Add Python version if specified
        if self.python_version:
            args.extend(["--python", self.python_version])

        # Add from package if specified
        if self.from_package:
            args.extend(["--from", self.from_package])

        # Add with packages if specified
        for pkg in self.with_packages:
            args.extend(["--with", pkg])

        # Add the tool name
        args.append(self.tool_name)

        # Add the tool arguments
        args.extend(self.tool_args)

        # Create environment variables dictionary
        env = os.environ.copy()
        env.update(self.env_vars)

        # Configure the server parameters
        server_params = StdioServerParameters(
            command="uvx",
            args=args,
            env=env,
            cwd=self.project_directory,
        )

        async with stdio_client(server_params) as transport:
            stdio, write = transport

            async with ClientSession(
                read_stream=stdio,
                write_stream=write,
                **self._session_kwargs(),
            ) as session:
                async with self._set_session(transport, session):
                    yield self
