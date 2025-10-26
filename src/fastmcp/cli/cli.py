"""FastMCP CLI tools using Cyclopts."""

import importlib.metadata
import importlib.util
import json
import os
import platform
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

import cyclopts
import pyperclip
from rich.console import Console
from rich.table import Table

import fastmcp
from fastmcp.cli import run as run_module
from fastmcp.cli.install import install_app
from fastmcp.server.server import FastMCP
from fastmcp.utilities.cli import is_already_in_uv_subprocess, load_and_merge_config
from fastmcp.utilities.inspect import (
    InspectFormat,
    format_info,
    inspect_fastmcp,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config import MCPServerConfig
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment

logger = get_logger("cli")
console = Console()

app = cyclopts.App(
    name="fastmcp",
    help="FastMCP 2.0 - The fast, Pythonic way to build MCP servers and clients.",
    version=fastmcp.__version__,
)


def _get_npx_command():
    """Get the correct npx command for the current platform."""
    if sys.platform == "win32":
        # Try both npx.cmd and npx.exe on Windows
        for cmd in ["npx.cmd", "npx.exe", "npx"]:
            try:
                subprocess.run([cmd, "--version"], check=True, capture_output=True)
                return cmd
            except subprocess.CalledProcessError:
                continue
        return None
    return "npx"  # On Unix-like systems, just use npx


def _parse_env_var(env_var: str) -> tuple[str, str]:
    """Parse environment variable string in format KEY=VALUE."""
    if "=" not in env_var:
        logger.error("Invalid environment variable format. Must be KEY=VALUE")
        sys.exit(1)
    key, value = env_var.split("=", 1)
    return key.strip(), value.strip()


@contextmanager
def with_argv(args: list[str] | None):
    """Temporarily replace sys.argv if args provided.

    This context manager is used at the CLI boundary to inject
    server arguments when needed, without mutating sys.argv deep
    in the source loading logic.

    Args are provided without the script name, so we preserve sys.argv[0]
    and replace the rest.
    """
    if args is not None:
        original = sys.argv[:]
        try:
            # Preserve the script name (sys.argv[0]) and replace the rest
            sys.argv = [sys.argv[0], *args]
            yield
        finally:
            sys.argv = original
    else:
        yield


@app.command
def version(
    *,
    copy: Annotated[
        bool,
        cyclopts.Parameter(
            "--copy",
            help="Copy version information to clipboard",
            negative="",
        ),
    ] = False,
):
    """Display version information and platform details."""
    info = {
        "FastMCP version": fastmcp.__version__,
        "MCP version": importlib.metadata.version("mcp"),
        "Python version": platform.python_version(),
        "Platform": platform.platform(),
        "FastMCP root path": Path(fastmcp.__file__).resolve().parents[1],
    }

    g = Table.grid(padding=(0, 1))
    g.add_column(style="bold", justify="left")
    g.add_column(style="cyan", justify="right")
    for k, v in info.items():
        g.add_row(k + ":", str(v).replace("\n", " "))

    if copy:
        # Use Rich's plain text rendering for copying
        plain_console = Console(file=None, force_terminal=False, legacy_windows=False)
        with plain_console.capture() as capture:
            plain_console.print(g)
        pyperclip.copy(capture.get())
        console.print("[green]✓[/green] Version information copied to clipboard")
    else:
        console.print(g)


@app.command
async def dev(
    server_spec: str | None = None,
    *,
    with_editable: Annotated[
        list[Path] | None,
        cyclopts.Parameter(
            "--with-editable",
            help="Directory containing pyproject.toml to install in editable mode (can be used multiple times)",
            negative="",
        ),
    ] = None,
    with_packages: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--with",
            help="Additional packages to install (can be used multiple times)",
            negative="",
        ),
    ] = None,
    inspector_version: Annotated[
        str | None,
        cyclopts.Parameter(
            "--inspector-version",
            help="Version of the MCP Inspector to use",
        ),
    ] = None,
    ui_port: Annotated[
        int | None,
        cyclopts.Parameter(
            "--ui-port",
            help="Port for the MCP Inspector UI",
        ),
    ] = None,
    server_port: Annotated[
        int | None,
        cyclopts.Parameter(
            "--server-port",
            help="Port for the MCP Inspector Proxy server",
        ),
    ] = None,
    python: Annotated[
        str | None,
        cyclopts.Parameter(
            "--python",
            help="Python version to use (e.g., 3.10, 3.11)",
        ),
    ] = None,
    with_requirements: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--with-requirements",
            help="Requirements file to install dependencies from",
        ),
    ] = None,
    project: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--project",
            help="Run the command within the given project directory",
        ),
    ] = None,
) -> None:
    """Run an MCP server with the MCP Inspector for development.

    Args:
        server_spec: Python file to run, optionally with :object suffix, or None to auto-detect fastmcp.json
    """

    try:
        # Load config and apply CLI overrides
        config, server_spec = load_and_merge_config(
            server_spec,
            python=python,
            with_packages=with_packages or [],
            with_requirements=with_requirements,
            project=project,
            editable=[str(p) for p in with_editable] if with_editable else None,
            port=server_port,  # Use deployment config for server port
        )

        # Get server port from config if not specified via CLI
        if not server_port:
            server_port = config.deployment.port

    except FileNotFoundError:
        sys.exit(1)

    logger.debug(
        "Starting dev server",
        extra={
            "server_spec": server_spec,
            "with_editable": config.environment.editable,
            "with_packages": config.environment.dependencies,
            "ui_port": ui_port,
            "server_port": server_port,
        },
    )

    try:
        # Load server to check for deprecated dependencies
        if not config:
            logger.error("No configuration available")
            sys.exit(1)
        assert config is not None  # For type checker
        server: FastMCP = await config.source.load_server()
        if server.dependencies:
            import warnings

            warnings.warn(
                f"Server '{server.name}' uses deprecated 'dependencies' parameter (deprecated in FastMCP 2.11.4). "
                "Please migrate to fastmcp.json configuration file. "
                "See https://gofastmcp.com/docs/deployment/server-configuration for details.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Merge server dependencies with environment dependencies
            env_deps = config.environment.dependencies or []
            all_deps = list(set(env_deps + server.dependencies))
            if not config.environment:
                config.environment = UVEnvironment(dependencies=all_deps)
            else:
                config.environment.dependencies = all_deps

        env_vars = {}
        if ui_port:
            env_vars["CLIENT_PORT"] = str(ui_port)
        if server_port:
            env_vars["SERVER_PORT"] = str(server_port)

        # Get the correct npx command
        npx_cmd = _get_npx_command()
        if not npx_cmd:
            logger.error(
                "npx not found. Please ensure Node.js and npm are properly installed "
                "and added to your system PATH."
            )
            sys.exit(1)

        inspector_cmd = "@modelcontextprotocol/inspector"
        if inspector_version:
            inspector_cmd += f"@{inspector_version}"

        # Use the environment from config (already has CLI overrides applied)
        uv_cmd = config.environment.build_command(
            ["fastmcp", "run", server_spec, "--no-banner"]
        )

        # Set marker to prevent infinite loops when subprocess calls FastMCP
        env = dict(os.environ.items()) | env_vars | {"FASTMCP_UV_SPAWNED": "1"}

        # Run the MCP Inspector command
        process = subprocess.run(
            [npx_cmd, inspector_cmd, *uv_cmd],
            check=True,
            env=env,
        )
        sys.exit(process.returncode)
    except subprocess.CalledProcessError as e:
        logger.error(
            "Dev server failed",
            extra={
                "file": str(server_spec),
                "error": str(e),
                "returncode": e.returncode,
            },
        )
        sys.exit(e.returncode)
    except FileNotFoundError:
        logger.error(
            "npx not found. Please ensure Node.js and npm are properly installed "
            "and added to your system PATH. You may need to restart your terminal "
            "after installation.",
            extra={"file": str(server_spec)},
        )
        sys.exit(1)


@app.command
async def run(
    server_spec: str | None = None,
    *server_args: str,
    transport: Annotated[
        run_module.TransportType | None,
        cyclopts.Parameter(
            name=["--transport", "-t"],
            help="Transport protocol to use",
        ),
    ] = None,
    host: Annotated[
        str | None,
        cyclopts.Parameter(
            "--host",
            help="Host to bind to when using http transport (default: 127.0.0.1)",
        ),
    ] = None,
    port: Annotated[
        int | None,
        cyclopts.Parameter(
            name=["--port", "-p"],
            help="Port to bind to when using http transport (default: 8000)",
        ),
    ] = None,
    path: Annotated[
        str | None,
        cyclopts.Parameter(
            "--path",
            help="The route path for the server (default: /mcp/ for http transport, /sse/ for sse transport)",
        ),
    ] = None,
    log_level: Annotated[
        Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None,
        cyclopts.Parameter(
            name=["--log-level", "-l"],
            help="Log level",
        ),
    ] = None,
    no_banner: Annotated[
        bool,
        cyclopts.Parameter(
            "--no-banner",
            help="Don't show the server banner",
            negative="",
        ),
    ] = False,
    python: Annotated[
        str | None,
        cyclopts.Parameter(
            "--python",
            help="Python version to use (e.g., 3.10, 3.11)",
        ),
    ] = None,
    with_packages: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--with",
            help="Additional packages to install (can be used multiple times)",
            negative="",
        ),
    ] = None,
    project: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--project",
            help="Run the command within the given project directory",
        ),
    ] = None,
    with_requirements: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--with-requirements",
            help="Requirements file to install dependencies from",
        ),
    ] = None,
    skip_source: Annotated[
        bool,
        cyclopts.Parameter(
            "--skip-source",
            help="Skip source preparation step (use when source is already prepared)",
            negative="",
        ),
    ] = False,
    skip_env: Annotated[
        bool,
        cyclopts.Parameter(
            "--skip-env",
            help="Skip environment configuration (for internal use when already in a uv environment)",
            negative="",
        ),
    ] = False,
) -> None:
    """Run an MCP server or connect to a remote one.

    The server can be specified in several ways:
    1. Module approach: "server.py" - runs the module directly, looking for an object named 'mcp', 'server', or 'app'
    2. Import approach: "server.py:app" - imports and runs the specified server object
    3. URL approach: "http://server-url" - connects to a remote server and creates a proxy
    4. MCPConfig file: "mcp.json" - runs as a proxy server for the MCP Servers in the MCPConfig file
    5. FastMCP config: "fastmcp.json" - runs server using FastMCP configuration
    6. No argument: looks for fastmcp.json in current directory

    Server arguments can be passed after -- :
    fastmcp run server.py -- --config config.json --debug

    Args:
        server_spec: Python file, object specification (file:obj), config file, URL, or None to auto-detect
    """

    # Check if we were spawned by uv (or user explicitly set --skip-env)
    if skip_env or is_already_in_uv_subprocess():
        skip_env = True

    try:
        # Load config and apply CLI overrides
        config, server_spec = load_and_merge_config(
            server_spec,
            python=python,
            with_packages=with_packages or [],
            with_requirements=with_requirements,
            project=project,
            transport=transport,
            host=host,
            port=port,
            path=path,
            log_level=log_level,
            server_args=list(server_args) if server_args else None,
        )
    except FileNotFoundError:
        sys.exit(1)

    # Get effective values (CLI overrides take precedence)
    final_transport = transport or config.deployment.transport
    final_host = host or config.deployment.host
    final_port = port or config.deployment.port
    final_path = path or config.deployment.path
    final_log_level = log_level or config.deployment.log_level
    final_server_args = server_args or config.deployment.args
    # Use CLI override if provided, otherwise use settings
    # no_banner CLI flag overrides the show_cli_banner setting
    final_no_banner = no_banner if no_banner else not fastmcp.settings.show_cli_banner

    logger.debug(
        "Running server or client",
        extra={
            "server_spec": server_spec,
            "transport": final_transport,
            "host": final_host,
            "port": final_port,
            "path": final_path,
            "log_level": final_log_level,
            "server_args": list(final_server_args) if final_server_args else [],
        },
    )

    # Check if we need to use uv run (but skip if we're already in uv or user said to skip)
    # We check if the environment would modify the command
    test_cmd = ["test"]
    needs_uv = config.environment.build_command(test_cmd) != test_cmd and not skip_env

    if needs_uv:
        # Build the inner fastmcp command
        inner_cmd = ["fastmcp", "run", server_spec]

        # Add transport options to the inner command
        if final_transport:
            inner_cmd.extend(["--transport", final_transport])
        # Only add HTTP-specific options for non-stdio transports
        if final_transport != "stdio":
            if final_host:
                inner_cmd.extend(["--host", final_host])
            if final_port:
                inner_cmd.extend(["--port", str(final_port)])
            if final_path:
                inner_cmd.extend(["--path", final_path])
        if final_log_level:
            inner_cmd.extend(["--log-level", final_log_level])
        if final_no_banner:
            inner_cmd.append("--no-banner")
        # Add skip-env flag to prevent infinite recursion
        inner_cmd.append("--skip-env")

        # Add server args if any
        if final_server_args:
            inner_cmd.append("--")
            inner_cmd.extend(final_server_args)

        # Build the full uv command using the config's environment
        cmd = config.environment.build_command(inner_cmd)

        # Set marker to prevent infinite loops when subprocess calls FastMCP again
        env = os.environ | {"FASTMCP_UV_SPAWNED": "1"}

        # Run the command
        logger.debug(f"Running command: {' '.join(cmd)}")
        try:
            process = subprocess.run(cmd, check=True, env=env)
            sys.exit(process.returncode)
        except subprocess.CalledProcessError as e:
            logger.error(
                f"Failed to run: {e}",
                extra={
                    "server_spec": server_spec,
                    "error": str(e),
                    "returncode": e.returncode,
                },
            )
            sys.exit(e.returncode)
    else:
        # Use direct import for backwards compatibility
        try:
            await run_module.run_command(
                server_spec=server_spec,
                transport=final_transport,
                host=final_host,
                port=final_port,
                path=final_path,
                log_level=final_log_level,
                server_args=list(final_server_args) if final_server_args else [],
                show_banner=not final_no_banner,
                skip_source=skip_source,
            )
        except Exception as e:
            logger.error(
                f"Failed to run: {e}",
                extra={
                    "server_spec": server_spec,
                    "error": str(e),
                },
            )
            sys.exit(1)


@app.command
async def inspect(
    server_spec: str | None = None,
    *,
    format: Annotated[
        InspectFormat | None,
        cyclopts.Parameter(
            name=["--format", "-f"],
            help="Output format: fastmcp (FastMCP-specific) or mcp (MCP protocol). Required when using -o.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--output", "-o"],
            help="Output file path for the JSON report. If not specified, outputs to stdout when format is provided.",
        ),
    ] = None,
    python: Annotated[
        str | None,
        cyclopts.Parameter(
            "--python",
            help="Python version to use (e.g., 3.10, 3.11)",
        ),
    ] = None,
    with_packages: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--with",
            help="Additional packages to install (can be used multiple times)",
            negative="",
        ),
    ] = None,
    project: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--project",
            help="Run the command within the given project directory",
        ),
    ] = None,
    with_requirements: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--with-requirements",
            help="Requirements file to install dependencies from",
        ),
    ] = None,
    skip_env: Annotated[
        bool,
        cyclopts.Parameter(
            "--skip-env",
            help="Skip environment configuration (for internal use when already in a uv environment)",
            negative="",
        ),
    ] = False,
) -> None:
    """Inspect an MCP server and display information or generate a JSON report.

    This command analyzes an MCP server. Without flags, it displays a text summary.
    Use --format to output complete JSON data.

    Examples:
        # Show text summary
        fastmcp inspect server.py

        # Output FastMCP format JSON to stdout
        fastmcp inspect server.py --format fastmcp

        # Save MCP protocol format to file (format required with -o)
        fastmcp inspect server.py --format mcp -o manifest.json

        # Inspect from fastmcp.json configuration
        fastmcp inspect fastmcp.json
        fastmcp inspect  # auto-detect fastmcp.json

    Args:
        server_spec: Python file to inspect, optionally with :object suffix, or fastmcp.json
    """

    # Check if we were spawned by uv (or user explicitly set --skip-env)
    if skip_env or is_already_in_uv_subprocess():
        skip_env = True

    try:
        # Load config and apply CLI overrides
        config, server_spec = load_and_merge_config(
            server_spec,
            python=python,
            with_packages=with_packages or [],
            with_requirements=with_requirements,
            project=project,
        )

        # Check if it's an MCPConfig (which inspect doesn't support)
        if server_spec.endswith(".json") and config is None:
            # This might be an MCPConfig, check the file
            try:
                with open(Path(server_spec)) as f:
                    data = json.load(f)
                if "mcpServers" in data:
                    logger.error("MCPConfig files are not supported by inspect command")
                    sys.exit(1)
            except (json.JSONDecodeError, FileNotFoundError):
                pass

    except FileNotFoundError:
        sys.exit(1)

    # Check if we need to use uv run (but skip if we're already in uv or user said to skip)
    # We check if the environment would modify the command
    test_cmd = ["test"]
    needs_uv = config.environment.build_command(test_cmd) != test_cmd and not skip_env

    if needs_uv:
        # Build and run uv command
        # The environment is already configured in the config object
        inspect_command = [
            "fastmcp",
            "inspect",
            server_spec,
            "--skip-env",  # Prevent infinite recursion
        ]

        # Add format and output flags if specified
        if format:
            inspect_command.extend(["--format", format.value])
        if output:
            inspect_command.extend(["--output", str(output)])

        # Run the command using subprocess
        import subprocess

        cmd = config.environment.build_command(inspect_command)
        env = os.environ | {"FASTMCP_UV_SPAWNED": "1"}
        process = subprocess.run(cmd, check=True, env=env)
        sys.exit(process.returncode)

    logger.debug(
        "Inspecting server",
        extra={
            "server_spec": server_spec,
            "format": format,
            "output": str(output) if output else None,
        },
    )

    try:
        # Load the server using the config
        if not config:
            logger.error("No configuration available")
            sys.exit(1)
        assert config is not None  # For type checker
        server = await config.source.load_server()

        # Get basic server information
        info = await inspect_fastmcp(server)

        # Check for invalid combination
        if output and not format:
            console.print(
                "[bold red]Error:[/bold red] --format is required when using -o/--output"
            )
            console.print(
                "[dim]Use --format fastmcp or --format mcp to specify the output format[/dim]"
            )
            sys.exit(1)

        # If no format specified, show text summary
        if format is None:
            # Display text summary
            console.print()

            # Server section
            console.print("[bold]Server[/bold]")
            console.print(f"  Name:         {info.name}")
            if info.version:
                console.print(f"  Version:      {info.version}")
            if info.website_url:
                console.print(f"  Website:      {info.website_url}")
            if info.icons:
                console.print(f"  Icons:        {len(info.icons)}")
            console.print(f"  Generation:   {info.server_generation}")
            if info.instructions:
                console.print(f"  Instructions: {info.instructions}")
            console.print()

            # Components section
            console.print("[bold]Components[/bold]")
            console.print(f"  Tools:        {len(info.tools)}")
            console.print(f"  Prompts:      {len(info.prompts)}")
            console.print(f"  Resources:    {len(info.resources)}")
            console.print(f"  Templates:    {len(info.templates)}")
            console.print()

            # Environment section
            console.print("[bold]Environment[/bold]")
            console.print(f"  FastMCP:      {info.fastmcp_version}")
            console.print(f"  MCP:          {info.mcp_version}")
            console.print()

            console.print(
                "[dim]Use --format \\[fastmcp|mcp] for complete JSON output[/dim]"
            )
            return

        # Generate formatted JSON output
        formatted_json = await format_info(server, format, info)

        # Output to file or stdout
        if output:
            # Ensure output directory exists
            output.parent.mkdir(parents=True, exist_ok=True)

            # Write JSON report
            with output.open("wb") as f:
                f.write(formatted_json)

            logger.info(f"Server inspection complete. Report saved to {output}")

            # Print confirmation to console
            console.print(
                f"[bold green]✓[/bold green] Server inspection saved to: [cyan]{output}[/cyan]"
            )
            console.print(f"  Server: [bold]{info.name}[/bold]")
            console.print(f"  Format: {format.value}")
        else:
            # Output JSON to stdout
            console.print(formatted_json.decode("utf-8"))

    except Exception as e:
        logger.error(
            f"Failed to inspect server: {e}",
            extra={
                "server_spec": server_spec,
                "error": str(e),
            },
            exc_info=True,
        )
        console.print(f"[bold red]✗[/bold red] Failed to inspect server: {e}")
        sys.exit(1)


# Create project subcommand group
project_app = cyclopts.App(name="project", help="Manage FastMCP projects")


@project_app.command
async def prepare(
    config_path: Annotated[
        str | None,
        cyclopts.Parameter(help="Path to fastmcp.json configuration file"),
    ] = None,
    output_dir: Annotated[
        str | None,
        cyclopts.Parameter(help="Directory to create the persistent environment in"),
    ] = None,
    skip_source: Annotated[
        bool,
        cyclopts.Parameter(help="Skip source preparation (e.g., git clone)"),
    ] = False,
) -> None:
    """Prepare a FastMCP project by creating a persistent uv environment.

    This command creates a persistent uv project with all dependencies installed:
    - Creates a pyproject.toml with dependencies from the config
    - Installs all Python packages into a .venv
    - Prepares the source (git clone, download, etc.) unless --skip-source

    After running this command, you can use:
    fastmcp run <config> --project <output-dir>

    This is useful for:
    - CI/CD pipelines with separate build and run stages
    - Docker images where you prepare during build
    - Production deployments where you want fast startup times

    Example:
        fastmcp project prepare myserver.json --output-dir ./prepared-env
        fastmcp run myserver.json --project ./prepared-env
    """
    from pathlib import Path

    # Require output-dir
    if output_dir is None:
        logger.error(
            "The --output-dir parameter is required.\n"
            "Please specify where to create the persistent environment."
        )
        sys.exit(1)

    # Auto-detect fastmcp.json if not provided
    if config_path is None:
        found_config = MCPServerConfig.find_config()
        if found_config:
            config_path = str(found_config)
            logger.info(f"Using configuration from {config_path}")
        else:
            logger.error(
                "No configuration file specified and no fastmcp.json found.\n"
                "Please specify a configuration file or create a fastmcp.json."
            )
            sys.exit(1)

    config_file = Path(config_path)
    if not config_file.exists():
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)

    output_path = Path(output_dir)

    try:
        # Load the configuration
        config = MCPServerConfig.from_file(config_file)

        # Prepare environment and source
        await config.prepare(
            skip_source=skip_source,
            output_dir=output_path,
        )

        console.print(
            f"[bold green]✓[/bold green] Project prepared successfully in {output_path}!\n"
            f"You can now run the server with:\n"
            f"  [cyan]fastmcp run {config_path} --project {output_dir}[/cyan]"
        )

    except Exception as e:
        logger.error(f"Failed to prepare project: {e}")
        console.print(f"[bold red]✗[/bold red] Failed to prepare project: {e}")
        sys.exit(1)


# Add project subcommand group
app.command(project_app)

# Add install subcommands using proper Cyclopts pattern
app.command(install_app)


if __name__ == "__main__":
    app()
