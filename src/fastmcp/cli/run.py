"""FastMCP run command implementation with enhanced type hints."""

import asyncio
import contextlib
import json
import re
import signal
import sys
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP as FastMCP1x
from watchfiles import Change, awatch

from fastmcp.server.server import FastMCP, create_proxy
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config import (
    MCPServerConfig,
)
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource

logger = get_logger("cli.run")

# Type aliases for better type safety
TransportType = Literal["stdio", "http", "sse", "streamable-http"]
LogLevelType = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def is_url(path: str) -> bool:
    """Check if a string is a URL."""
    url_pattern = re.compile(r"^https?://")
    return bool(url_pattern.match(path))


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
        server = create_proxy(client)
        return server
    except Exception as e:
        logger.error(f"Failed to create client for URL {url}: {e}")
        sys.exit(1)


def create_mcp_config_server(mcp_config_path: Path) -> FastMCP[None]:
    """Create a FastMCP server from a MCPConfig."""
    with mcp_config_path.open() as src:
        mcp_config = json.load(src)

    server = create_proxy(mcp_config)
    return server


def load_mcp_server_config(config_path: Path) -> MCPServerConfig:
    """Load a FastMCP configuration from a fastmcp.json file.

    Args:
        config_path: Path to fastmcp.json file

    Returns:
        MCPServerConfig object
    """
    config = MCPServerConfig.from_file(config_path)

    # Apply runtime settings from deployment config
    config.deployment.apply_runtime_settings(config_path)

    return config


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
    skip_source: bool = False,
    stateless: bool = False,
) -> None:
    """Run a MCP server or connect to a remote one.

    Args:
        server_spec: Python file, object specification (file:obj), config file, or URL
        transport: Transport protocol to use
        host: Host to bind to when using http transport
        port: Port to bind to when using http transport
        path: Path to bind to when using http transport
        log_level: Log level
        server_args: Additional arguments to pass to the server
        show_banner: Whether to show the server banner
        use_direct_import: Whether to use direct import instead of subprocess
        skip_source: Whether to skip source preparation step
        stateless: Whether to run in stateless mode (no session)
    """
    # Special case: URLs
    if is_url(server_spec):
        # Handle URL case
        server = create_client_server(server_spec)
        logger.debug(f"Created client proxy server for {server_spec}")
    # Special case: MCPConfig files (legacy)
    elif server_spec.endswith(".json"):
        # Load JSON and check which type of config it is
        config_path = Path(server_spec)
        with open(config_path) as f:
            data = json.load(f)

        # Check if it's an MCPConfig first (has canonical mcpServers key)
        if "mcpServers" in data:
            # It's an MCP config
            server = create_mcp_config_server(config_path)
        else:
            # It's a FastMCP config - load it properly
            config = load_mcp_server_config(config_path)

            # Merge deployment config with CLI arguments (CLI takes precedence)
            transport = transport or config.deployment.transport
            host = host or config.deployment.host
            port = port or config.deployment.port
            path = path or config.deployment.path
            log_level = log_level or config.deployment.log_level
            server_args = (
                server_args if server_args is not None else config.deployment.args
            )

            # Prepare source only (environment is handled by uv run)
            await config.prepare_source() if not skip_source else None

            # Load the server using the source
            from contextlib import nullcontext

            from fastmcp.cli.cli import with_argv

            # Use sys.argv context manager if deployment args specified
            argv_context = with_argv(server_args) if server_args else nullcontext()

            with argv_context:
                server = await config.source.load_server()

            logger.debug(f'Found server "{server.name}" from config {config_path}')
    else:
        # Regular file case - create a MCPServerConfig with FileSystemSource
        source = FileSystemSource(path=server_spec)
        config = MCPServerConfig(source=source)

        # Prepare source only (environment is handled by uv run)
        await config.prepare_source() if not skip_source else None

        # Load the server
        from contextlib import nullcontext

        from fastmcp.cli.cli import with_argv

        # Use sys.argv context manager if server_args specified
        argv_context = with_argv(server_args) if server_args else nullcontext()

        with argv_context:
            server = await config.source.load_server()

        logger.debug(f'Found server "{server.name}" in {source.path}')

    # Run the server

    # handle v1 servers
    if isinstance(server, FastMCP1x):
        await run_v1_server_async(server, host=host, port=port, transport=transport)
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
    if stateless:
        kwargs["stateless"] = True

    if not show_banner:
        kwargs["show_banner"] = False

    try:
        await server.run_async(**kwargs)
    except Exception as e:
        logger.error(f"Failed to run server: {e}")
        sys.exit(1)


async def run_v1_server_async(
    server: FastMCP1x,
    host: str | None = None,
    port: int | None = None,
    transport: TransportType | None = None,
) -> None:
    """Run a FastMCP 1.x server using async methods.

    Args:
        server: FastMCP 1.x server instance
        host: Host to bind to
        port: Port to bind to
        transport: Transport protocol to use
    """
    if host:
        server.settings.host = host
    if port:
        server.settings.port = port

    match transport:
        case "stdio":
            await server.run_stdio_async()
        case "http" | "streamable-http" | None:
            await server.run_streamable_http_async()
        case "sse":
            await server.run_sse_async()


def _python_file_filter(change: Change, path: str) -> bool:
    """Filter for Python files only."""
    return path.endswith(".py")


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess immediately."""
    if process.returncode is not None:
        return
    process.kill()
    await process.wait()


async def run_with_reload(
    cmd: list[str],
    reload_dirs: list[Path] | None = None,
    is_stdio: bool = False,
) -> None:
    """Run a command with file watching and auto-reload.

    Args:
        cmd: Command to run as subprocess (should include --no-reload)
        reload_dirs: Directories to watch for changes (default: cwd)
        is_stdio: Whether this is stdio transport
    """
    watch_paths = reload_dirs or [Path.cwd()]
    process: asyncio.subprocess.Process | None = None
    first_run = True

    if is_stdio:
        logger.info("Reload mode enabled (using stateless sessions)")
    else:
        logger.info(
            "Reload mode enabled (using stateless HTTP). "
            "Some features requiring bidirectional communication "
            "(like elicitation) are not available."
        )

    # Handle SIGTERM/SIGINT gracefully with proper asyncio integration
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def signal_handler() -> None:
        logger.info("Received shutdown signal, stopping...")
        shutdown_event.set()

    # Windows doesn't support add_signal_handler
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, signal_handler)
        loop.add_signal_handler(signal.SIGINT, signal_handler)

    try:
        while not shutdown_event.is_set():
            # Build command - add --no-banner on restarts to reduce noise
            if first_run or "--no-banner" in cmd:
                run_cmd = cmd
            else:
                run_cmd = [*cmd, "--no-banner"]
            first_run = False

            process = await asyncio.create_subprocess_exec(
                *run_cmd,
                stdin=None,
                stdout=None,
                stderr=None,
            )

            # Watch for either: file changes OR process death
            watch_task = asyncio.create_task(
                anext(aiter(awatch(*watch_paths, watch_filter=_python_file_filter)))
            )
            wait_task = asyncio.create_task(process.wait())
            shutdown_task = asyncio.create_task(shutdown_event.wait())

            done, pending = await asyncio.wait(
                [watch_task, wait_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            if shutdown_task in done:
                # User requested shutdown
                break

            if wait_task in done:
                # Server died on its own - wait for file change before restart
                code = wait_task.result()
                if code != 0:
                    logger.error(
                        f"Server exited with code {code}, waiting for file change..."
                    )
                else:
                    logger.info("Server exited, waiting for file change...")

                # Wait for file change or shutdown (avoid hot loop on crash)
                watch_task = asyncio.create_task(
                    anext(aiter(awatch(*watch_paths, watch_filter=_python_file_filter)))
                )
                shutdown_task = asyncio.create_task(shutdown_event.wait())
                done, pending = await asyncio.wait(
                    [watch_task, shutdown_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                if shutdown_task in done:
                    break
                logger.info("Detected changes, restarting...")
            else:
                # File changed - restart server
                changes = watch_task.result()
                logger.info(
                    f"Detected changes in {len(changes)} file(s), restarting..."
                )
                await _terminate_process(process)

    except KeyboardInterrupt:
        # Handle Ctrl+C on Windows (where add_signal_handler isn't available)
        logger.info("Received shutdown signal, stopping...")

    finally:
        # Clean up signal handlers
        if sys.platform != "win32":
            loop.remove_signal_handler(signal.SIGTERM)
            loop.remove_signal_handler(signal.SIGINT)
        if process and process.returncode is None:
            await _terminate_process(process)
