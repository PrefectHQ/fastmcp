"""FastMCP run command implementation with enhanced type hints."""

import importlib.util
import inspect
import json
import re
import subprocess
import sys
from functools import partial
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP as FastMCP1x

from fastmcp.server.server import FastMCP
from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.run")

# Type aliases for better type safety
TransportType = Literal["stdio", "http", "sse", "streamable-http"]
LogLevelType = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def is_url(path: str) -> bool:
    """Check if a string is a URL."""
    url_pattern = re.compile(r"^https?://")
    return bool(url_pattern.match(path))


def parse_file_path(server_spec: str) -> tuple[Path, str | None]:
    """Parse a file path that may include a server object specification.

    Args:
        server_spec: Path to file, optionally with :object suffix

    Returns:
        Tuple of (file_path, server_object)
    """
    # First check if we have a Windows path (e.g., C:\...)
    has_windows_drive = len(server_spec) > 1 and server_spec[1] == ":"

    # Split on the last colon, but only if it's not part of the Windows drive letter
    # and there's actually another colon in the string after the drive letter
    if ":" in (server_spec[2:] if has_windows_drive else server_spec):
        file_str, server_object = server_spec.rsplit(":", 1)
    else:
        file_str, server_object = server_spec, None

    # Resolve the file path
    file_path = Path(file_str).expanduser().resolve()
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        sys.exit(1)
    if not file_path.is_file():
        logger.error(f"Not a file: {file_path}")
        sys.exit(1)

    return file_path, server_object


async def import_server(file: Path, server_or_factory: str | None = None) -> Any:
    """Import a MCP server from a file.

    Args:
        file: Path to the file
        server_or_factory: Optional object name in format "module:object" or just "object"

    Returns:
        The server object (or result of calling a factory function)
    """
    # Add parent directory to Python path so imports can be resolved
    file_dir = str(file.parent)
    if file_dir not in sys.path:
        sys.path.insert(0, file_dir)

    # Import the module
    spec = importlib.util.spec_from_file_location("server_module", file)
    if not spec or not spec.loader:
        logger.error("Could not load module", extra={"file": str(file)})
        sys.exit(1)

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # If no object specified, try common server names
    if not server_or_factory:
        # Look for common server instance names
        for name in ["mcp", "server", "app"]:
            if hasattr(module, name):
                obj = getattr(module, name)
                return await _resolve_server_or_factory(obj, file, name)

        logger.error(
            f"No server object found in {file}. Please either:\n"
            "1. Use a standard variable name (mcp, server, or app)\n"
            "2. Specify the object name with file:object syntax",
            extra={"file": str(file)},
        )
        sys.exit(1)

    assert server_or_factory is not None

    # Handle module:object syntax
    if ":" in server_or_factory:
        module_name, object_name = server_or_factory.split(":", 1)
        try:
            server_module = importlib.import_module(module_name)
            obj = getattr(server_module, object_name, None)
        except ImportError:
            logger.error(
                f"Could not import module '{module_name}'",
                extra={"file": str(file)},
            )
            sys.exit(1)
    else:
        # Just object name
        obj = getattr(module, server_or_factory, None)

    if obj is None:
        logger.error(
            f"Server object '{server_or_factory}' not found",
            extra={"file": str(file)},
        )
        sys.exit(1)

    return await _resolve_server_or_factory(obj, file, server_or_factory)


async def _resolve_server_or_factory(obj: Any, file: Path, name: str) -> Any:
    """Resolve a server object or factory function to a server instance.

    Args:
        obj: The object that might be a server or factory function
        file: Path to the file for error messages
        name: Name of the object for error messages

    Returns:
        A server instance
    """
    # Check if it's a function or coroutine function
    if inspect.isfunction(obj) or inspect.iscoroutinefunction(obj):
        logger.debug(f"Found factory function '{name}' in {file}")

        try:
            if inspect.iscoroutinefunction(obj):
                # Async factory function
                server = await obj()
            else:
                # Sync factory function
                server = obj()

            # Validate the result is a FastMCP server
            if not isinstance(server, FastMCP | FastMCP1x):
                logger.error(
                    f"Factory function '{name}' must return a FastMCP server instance, "
                    f"got {type(server).__name__}",
                    extra={"file": str(file)},
                )
                sys.exit(1)

            logger.debug(f"Factory function '{name}' created server: {server.name}")
            return server

        except Exception as e:
            logger.error(
                f"Failed to call factory function '{name}': {e}",
                extra={"file": str(file)},
            )
            sys.exit(1)

    # Not a function, return as-is (should be a server instance)
    return obj


def run_with_uv(
    server_spec: str,
    python_version: str | None = None,
    with_packages: list[str] | None = None,
    with_requirements: Path | None = None,
    project: Path | None = None,
    transport: TransportType | None = None,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
    log_level: LogLevelType | None = None,
    show_banner: bool = True,
) -> None:
    """Run a MCP server using uv run subprocess.

    Args:
        server_spec: Python file, object specification (file:obj), or URL
        python_version: Python version to use (e.g. "3.10")
        with_packages: Additional packages to install
        with_requirements: Requirements file to use
        project: Run the command within the given project directory
        transport: Transport protocol to use
        host: Host to bind to when using http transport
        port: Port to bind to when using http transport
        path: Path to bind to when using http transport
        log_level: Log level
        show_banner: Whether to show the server banner
    """
    cmd = ["uv", "run"]

    # Add Python version if specified
    if python_version:
        cmd.extend(["--python", python_version])

    # Add project if specified
    if project:
        cmd.extend(["--project", str(project)])

    # Add fastmcp package
    cmd.extend(["--with", "fastmcp"])

    # Add additional packages
    if with_packages:
        for pkg in with_packages:
            if pkg:
                cmd.extend(["--with", pkg])

    # Add requirements file
    if with_requirements:
        cmd.extend(["--with-requirements", str(with_requirements)])

    # Add fastmcp run command
    cmd.extend(["fastmcp", "run", server_spec])

    # Add transport options
    if transport:
        cmd.extend(["--transport", transport])
    if host:
        cmd.extend(["--host", host])
    if port:
        cmd.extend(["--port", str(port)])
    if path:
        cmd.extend(["--path", path])
    if log_level:
        cmd.extend(["--log-level", log_level])
    if not show_banner:
        cmd.append("--no-banner")

    # Run the command
    logger.debug(f"Running command: {' '.join(cmd)}")
    try:
        process = subprocess.run(cmd, check=True)
        sys.exit(process.returncode)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to run server: {e}")
        sys.exit(e.returncode)


def create_client_server(url: str) -> Any:
    """Create a FastMCP server from a client URL.

    Args:
        url: The URL to connect to

    Returns:
        A FastMCP server instance
    """
    try:
        import fastmcp

        client = fastmcp.Client(url)
        server = fastmcp.FastMCP.as_proxy(client)
        return server
    except Exception as e:
        logger.error(f"Failed to create client for URL {url}: {e}")
        sys.exit(1)


def create_mcp_config_server(mcp_config_path: Path) -> FastMCP[None]:
    """Create a FastMCP server from a MCPConfig."""
    from fastmcp import FastMCP

    with mcp_config_path.open() as src:
        mcp_config = json.load(src)

    server = FastMCP.as_proxy(mcp_config)
    return server


async def import_server_with_args(
    file: Path,
    server_or_factory: str | None = None,
    server_args: list[str] | None = None,
) -> Any:
    """Import a server with optional command line arguments.

    Args:
        file: Path to the server file
        server_or_factory: Optional server object or factory function name
        server_args: Optional command line arguments to inject

    Returns:
        The imported server object
    """
    if server_args:
        original_argv = sys.argv[:]
        try:
            sys.argv = [str(file)] + server_args
            return await import_server(file, server_or_factory)
        finally:
            sys.argv = original_argv
    else:
        return await import_server(file, server_or_factory)


async def run_command(
    server_spec: str,
    transport: TransportType | None = None,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
    log_level: LogLevelType | None = None,
    server_args: list[str] | None = None,
    show_banner: bool = True,
    use_direct_import: bool = False,
) -> None:
    """Run a MCP server or connect to a remote one.

    Args:
        server_spec: Python file, object specification (file:obj), MCPConfig file, or URL
        transport: Transport protocol to use
        host: Host to bind to when using http transport
        port: Port to bind to when using http transport
        path: Path to bind to when using http transport
        log_level: Log level
        server_args: Additional arguments to pass to the server
        show_banner: Whether to show the server banner
        use_direct_import: Whether to use direct import instead of subprocess
    """
    if is_url(server_spec):
        # Handle URL case
        server = create_client_server(server_spec)
        logger.debug(f"Created client proxy server for {server_spec}")
    elif server_spec.endswith(".json"):
        server = create_mcp_config_server(Path(server_spec))
    else:
        # Handle file case
        file, server_or_factory = parse_file_path(server_spec)
        server = await import_server_with_args(file, server_or_factory, server_args)
        logger.debug(f'Found server "{server.name}" in {file}')

    # Run the server

    # handle v1 servers
    if isinstance(server, FastMCP1x):
        run_v1_server(server, host=host, port=port, transport=transport)
        return

    kwargs = {}
    if transport:
        kwargs["transport"] = transport
    if host:
        kwargs["host"] = host
    if port:
        kwargs["port"] = port
    if path:
        kwargs["path"] = path
    if log_level:
        kwargs["log_level"] = log_level

    if not show_banner:
        kwargs["show_banner"] = False

    try:
        await server.run_async(**kwargs)
    except Exception as e:
        logger.error(f"Failed to run server: {e}")
        sys.exit(1)


def run_v1_server(
    server: FastMCP1x,
    host: str | None = None,
    port: int | None = None,
    transport: TransportType | None = None,
) -> None:
    if host:
        server.settings.host = host
    if port:
        server.settings.port = port
    match transport:
        case "stdio":
            runner = partial(server.run)
        case "http" | "streamable-http" | None:
            runner = partial(server.run, transport="streamable-http")
        case "sse":
            runner = partial(server.run, transport="sse")

    runner()
