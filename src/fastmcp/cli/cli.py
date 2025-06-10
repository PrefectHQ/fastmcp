"""FastMCP CLI tools."""

import importlib.metadata
import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import dotenv
import typer
from rich.console import Console
from rich.table import Table
from typer import Context, Exit

import fastmcp
from fastmcp.cli import claude
from fastmcp.cli import run as run_module
from fastmcp.server.server import FastMCP
from fastmcp.utilities.logging import get_logger

logger = get_logger("cli")
console = Console()

app = typer.Typer(
    name="fastmcp",
    help="FastMCP CLI",
    add_completion=False,
    no_args_is_help=True,  # Show help if no args provided
)


def _get_npx_command():
    """Get the correct npx command for the current platform."""
    if sys.platform == "win32":
        # Try both npx.cmd and npx.exe on Windows
        for cmd in ["npx.cmd", "npx.exe", "npx"]:
            try:
                subprocess.run(
                    [cmd, "--version"], check=True, capture_output=True, shell=True
                )
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


def _build_uv_command(
    server_spec: str,
    with_editable: Path | None = None,
    with_packages: list[str] | None = None,
) -> list[str]:
    """Build the uv run command that runs a MCP server through mcp run."""
    cmd = ["uv"]

    cmd.extend(["run", "--with", "fastmcp"])

    if with_editable:
        cmd.extend(["--with-editable", str(with_editable)])

    if with_packages:
        for pkg in with_packages:
            if pkg:
                cmd.extend(["--with", pkg])

    # Add mcp run command
    cmd.extend(["fastmcp", "run", server_spec])
    return cmd


@app.command()
def version(ctx: Context):
    if ctx.resilient_parsing:
        return

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
    console.print(g)

    raise Exit()


@app.command()
def dev(
    server_spec: str = typer.Argument(
        ...,
        help="Python file to run, optionally with :object suffix",
    ),
    with_editable: Annotated[
        Path | None,
        typer.Option(
            "--with-editable",
            "-e",
            help="Directory containing pyproject.toml to install in editable mode",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ] = None,
    with_packages: Annotated[
        list[str],
        typer.Option(
            "--with",
            help="Additional packages to install",
        ),
    ] = [],
    inspector_version: Annotated[
        str | None,
        typer.Option(
            "--inspector-version",
            help="Version of the MCP Inspector to use",
        ),
    ] = None,
    ui_port: Annotated[
        int | None,
        typer.Option(
            "--ui-port",
            help="Port for the MCP Inspector UI",
        ),
    ] = None,
    server_port: Annotated[
        int | None,
        typer.Option(
            "--server-port",
            help="Port for the MCP Inspector Proxy server",
        ),
    ] = None,
) -> None:
    """Run a MCP server with the MCP Inspector."""
    file, server_object = run_module.parse_file_path(server_spec)

    logger.debug(
        "Starting dev server",
        extra={
            "file": str(file),
            "server_object": server_object,
            "with_editable": str(with_editable) if with_editable else None,
            "with_packages": with_packages,
            "ui_port": ui_port,
            "server_port": server_port,
        },
    )

    try:
        # Import server to get dependencies
        server: FastMCP = run_module.import_server(file, server_object)
        if server.dependencies is not None:
            with_packages = list(set(with_packages + server.dependencies))

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

        uv_cmd = _build_uv_command(server_spec, with_editable, with_packages)

        # Run the MCP Inspector command with shell=True on Windows
        shell = sys.platform == "win32"
        process = subprocess.run(
            [npx_cmd, inspector_cmd] + uv_cmd,
            check=True,
            shell=shell,
            env=dict(os.environ.items()) | env_vars,
        )
        sys.exit(process.returncode)
    except subprocess.CalledProcessError as e:
        logger.error(
            "Dev server failed",
            extra={
                "file": str(file),
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
            extra={"file": str(file)},
        )
        sys.exit(1)


@app.command(context_settings={"allow_extra_args": True})
def run(
    ctx: typer.Context,
    server_spec: str = typer.Argument(
        ...,
        help="Python file, object specification (file:obj), or URL",
    ),
    transport: Annotated[
        str | None,
        typer.Option(
            "--transport",
            "-t",
            help="Transport protocol to use (stdio, streamable-http, or sse)",
        ),
    ] = None,
    host: Annotated[
        str | None,
        typer.Option(
            "--host",
            help="Host to bind to when using http transport (default: 127.0.0.1)",
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            "--port",
            "-p",
            help="Port to bind to when using http transport (default: 8000)",
        ),
    ] = None,
    log_level: Annotated[
        str | None,
        typer.Option(
            "--log-level",
            "-l",
            help="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
        ),
    ] = None,
) -> None:
    """Run a MCP server or connect to a remote one.

    The server can be specified in three ways:
    1. Module approach: server.py - runs the module directly, looking for an object named mcp/server/app.\n
    2. Import approach: server.py:app - imports and runs the specified server object.\n
    3. URL approach: http://server-url - connects to a remote server and creates a proxy.\n\n

    Note: This command runs the server directly. You are responsible for ensuring
    all dependencies are available.

    Server arguments can be passed after -- :
    fastmcp run server.py -- --config config.json --debug
    """
    server_args = ctx.args  # extra args after --

    logger.debug(
        "Running server or client",
        extra={
            "server_spec": server_spec,
            "transport": transport,
            "host": host,
            "port": port,
            "log_level": log_level,
            "server_args": server_args,
        },
    )

    try:
        run_module.run_command(
            server_spec=server_spec,
            transport=transport,
            host=host,
            port=port,
            log_level=log_level,
            server_args=server_args,
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


@app.command()
def install(
    server_spec: str = typer.Argument(
        ...,
        help="Python file to run, optionally with :object suffix",
    ),
    server_name: Annotated[
        str | None,
        typer.Option(
            "--name",
            "-n",
            help="Custom name for the server (defaults to server's name attribute or"
            " file name)",
        ),
    ] = None,
    with_editable: Annotated[
        Path | None,
        typer.Option(
            "--with-editable",
            "-e",
            help="Directory containing pyproject.toml to install in editable mode",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ] = None,
    with_packages: Annotated[
        list[str],
        typer.Option(
            "--with",
            help="Additional packages to install",
        ),
    ] = [],
    env_vars: Annotated[
        list[str],
        typer.Option(
            "--env-var",
            "-v",
            help="Environment variables in KEY=VALUE format",
        ),
    ] = [],
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            "-f",
            help="Load environment variables from a .env file",
            exists=True,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Install a MCP server in the Claude desktop app.

    Environment variables are preserved once added and only updated if new values
    are explicitly provided.
    """
    file, server_object = run_module.parse_file_path(server_spec)

    logger.debug(
        "Installing server",
        extra={
            "file": str(file),
            "server_name": server_name,
            "server_object": server_object,
            "with_editable": str(with_editable) if with_editable else None,
            "with_packages": with_packages,
        },
    )

    if not claude.get_claude_config_path():
        logger.error("Claude app not found")
        sys.exit(1)

    # Try to import server to get its name, but fall back to file name if dependencies
    # missing
    name = server_name
    server = None
    if not name:
        try:
            server = run_module.import_server(file, server_object)
            name = server.name
        except (ImportError, ModuleNotFoundError) as e:
            logger.debug(
                "Could not import server (likely missing dependencies), using file"
                " name",
                extra={"error": str(e)},
            )
            name = file.stem

    # Get server dependencies if available
    server_dependencies = getattr(server, "dependencies", []) if server else []
    if server_dependencies:
        with_packages = list(set(with_packages + server_dependencies))

    # Process environment variables if provided
    env_dict: dict[str, str] | None = None
    if env_file or env_vars:
        env_dict = {}
        # Load from .env file if specified
        if env_file:
            try:
                env_dict |= {
                    k: v
                    for k, v in dotenv.dotenv_values(env_file).items()
                    if v is not None
                }
            except Exception as e:
                logger.error(f"Failed to load .env file: {e}")
                sys.exit(1)

        # Add command line environment variables
        for env_var in env_vars:
            key, value = _parse_env_var(env_var)
            env_dict[key] = value

    if claude.update_claude_config(
        server_spec,
        name,
        with_editable=with_editable,
        with_packages=with_packages,
        env_vars=env_dict,
    ):
        logger.info(f"Successfully installed {name} in Claude app")
    else:
        logger.error(f"Failed to install {name} in Claude app")
        sys.exit(1)
