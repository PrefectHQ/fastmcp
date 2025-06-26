"""FastMCP run command implementation."""

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.run")


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


def import_server(file: Path, server_object: str | None = None) -> Any:
    """Import a MCP server from a file.

    Args:
        file: Path to the file
        server_object: Optional object name in format "module:object" or just "object"

    Returns:
        The server object
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
    if not server_object:
        # Look for the most common server object names
        for name in ["mcp", "server", "app"]:
            if hasattr(module, name):
                return getattr(module, name)

        logger.error(
            f"No server object found in {file}. Please either:\n"
            "1. Use a standard variable name (mcp, server, or app)\n"
            "2. Specify the object name with file:object syntax",
            extra={"file": str(file)},
        )
        sys.exit(1)

    assert server_object is not None

    # Handle module:object syntax
    if ":" in server_object:
        module_name, object_name = server_object.split(":", 1)
        try:
            server_module = importlib.import_module(module_name)
            server = getattr(server_module, object_name, None)
        except ImportError:
            logger.error(
                f"Could not import module '{module_name}'",
                extra={"file": str(file)},
            )
            sys.exit(1)
    else:
        # Just object name
        server = getattr(module, server_object, None)

    if server is None:
        logger.error(
            f"Server object '{server_object}' not found",
            extra={"file": str(file)},
        )
        sys.exit(1)

    return server


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
        server = fastmcp.FastMCP.from_client(client)
        return server
    except Exception as e:
        logger.error(f"Failed to create client for URL {url}: {e}")
        sys.exit(1)


def import_server_with_args(
    file: Path, server_object: str | None = None, server_args: list[str] | None = None
) -> Any:
    """Import a server with optional command line arguments.

    Args:
        file: Path to the server file
        server_object: Optional server object name
        server_args: Optional command line arguments to inject

    Returns:
        The imported server object
    """
    if server_args:
        original_argv = sys.argv[:]
        try:
            sys.argv = [str(file)] + server_args
            return import_server(file, server_object)
        finally:
            sys.argv = original_argv
    else:
        return import_server(file, server_object)


def run_command(
    server_spec: str,
    transport: str | None = None,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
    server_args: list[str] | None = None,
) -> None:
    """Run a MCP server or connect to a remote one.

    Args:
        server_spec: Python file, object specification (file:obj), or URL
        transport: Transport protocol to use
        host: Host to bind to when using http transport
        port: Port to bind to when using http transport
        log_level: Log level
        server_args: Additional arguments to pass to the server
    """
    if is_url(server_spec):
        # Handle URL case
        server = create_client_server(server_spec)
        logger.debug(f"Created client proxy server for {server_spec}")
    else:
        # Handle file case
        file, server_object = parse_file_path(server_spec)
        server = import_server_with_args(file, server_object, server_args)
        logger.debug(f'Found server "{server.name}" in {file}')

    # Run the server
    kwargs = {}
    if transport:
        kwargs["transport"] = transport
    if host:
        kwargs["host"] = host
    if port:
        kwargs["port"] = port
    if log_level:
        kwargs["log_level"] = log_level

    try:
        server.run(**kwargs)
    except Exception as e:
        logger.error(f"Failed to run server: {e}")
        sys.exit(1)
