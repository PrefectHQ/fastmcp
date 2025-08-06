"""FastMCP CLI tools using Cyclopts."""

import importlib.metadata
import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

import cyclopts
import pyperclip
from pydantic import TypeAdapter
from rich.console import Console
from rich.table import Table

import fastmcp
from fastmcp.cli import run as run_module
from fastmcp.cli.install import install_app
from fastmcp.server.server import FastMCP
from fastmcp.utilities.inspect import FastMCPInfo, inspect_fastmcp
from fastmcp.utilities.logging import get_logger

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
    no_banner: bool = False,
    python_version: str | None = None,
    with_requirements: Path | None = None,
    project: Path | None = None,
) -> list[str]:
    """Build the uv run command that runs a MCP server through mcp run."""
    cmd = ["uv", "run"]

    # Add Python version if specified
    if python_version:
        cmd.extend(["--python", python_version])

    # Add project if specified
    if project:
        cmd.extend(["--project", str(project)])

    cmd.extend(["--with", "fastmcp"])

    if with_editable:
        cmd.extend(["--with-editable", str(with_editable)])

    if with_packages:
        for pkg in with_packages:
            if pkg:
                cmd.extend(["--with", pkg])

    if with_requirements:
        cmd.extend(["--with-requirements", str(with_requirements)])

    # Add mcp run command
    cmd.extend(["fastmcp", "run", server_spec])

    if no_banner:
        cmd.append("--no-banner")

    return cmd


@app.command
def version(
    *,
    copy: Annotated[
        bool,
        cyclopts.Parameter(
            "--copy",
            help="Copy version information to clipboard",
            negative=False,
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
    server_spec: str,
    *,
    with_editable: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--with-editable", "-e"],
            help="Directory containing pyproject.toml to install in editable mode",
        ),
    ] = None,
    with_packages: Annotated[
        list[str],
        cyclopts.Parameter(
            "--with",
            help="Additional packages to install",
            negative=False,
        ),
    ] = [],
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
        server_spec: Python file to run, optionally with :object suffix
    """
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
        server: FastMCP = await run_module.import_server(file, server_object)
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

        uv_cmd = _build_uv_command(
            server_spec,
            with_editable,
            with_packages,
            no_banner=True,
            python_version=python,
            with_requirements=with_requirements,
            project=project,
        )

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


@app.command
async def run(
    server_spec: str,
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
            negative=False,
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
        list[str],
        cyclopts.Parameter(
            "--with",
            help="Additional packages to install (can be used multiple times)",
            negative=False,
        ),
    ] = [],
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
) -> None:
    """Run an MCP server or connect to a remote one.

    The server can be specified in four ways:
    1. Module approach: "server.py" - runs the module directly, looking for an object named 'mcp', 'server', or 'app'
    2. Import approach: "server.py:app" - imports and runs the specified server object
    3. URL approach: "http://server-url" - connects to a remote server and creates a proxy
    4. MCPConfig file: "mcp.json" - runs as a proxy server for the MCP Servers in the MCPConfig file

    Server arguments can be passed after -- :
    fastmcp run server.py -- --config config.json --debug

    Args:
        server_spec: Python file, object specification (file:obj), MCPConfig file, or URL
    """
    logger.debug(
        "Running server or client",
        extra={
            "server_spec": server_spec,
            "transport": transport,
            "host": host,
            "port": port,
            "path": path,
            "log_level": log_level,
            "server_args": list(server_args),
        },
    )

    # If any uv-specific options are provided, use uv run
    if python or with_packages or with_requirements or project:
        try:
            run_module.run_with_uv(
                server_spec=server_spec,
                python_version=python,
                with_packages=with_packages,
                with_requirements=with_requirements,
                project=project,
                transport=transport,
                host=host,
                port=port,
                path=path,
                log_level=log_level,
                show_banner=not no_banner,
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
    else:
        # Use direct import for backwards compatibility
        try:
            await run_module.run_command(
                server_spec=server_spec,
                transport=transport,
                host=host,
                port=port,
                path=path,
                log_level=log_level,
                server_args=list(server_args),
                show_banner=not no_banner,
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
    server_spec: str,
    *,
    output: Annotated[
        Path,
        cyclopts.Parameter(
            name=["--output", "-o"],
            help="Output file path for the JSON report (default: server-info.json)",
        ),
    ] = Path("server-info.json"),
) -> None:
    """Inspect an MCP server and generate a JSON report.

    This command analyzes an MCP server and generates a comprehensive JSON report
    containing information about the server's name, instructions, version, tools,
    prompts, resources, templates, and capabilities.

    Examples:
        fastmcp inspect server.py
        fastmcp inspect server.py -o report.json
        fastmcp inspect server.py:mcp -o analysis.json
        fastmcp inspect path/to/server.py:app -o /tmp/server-info.json

    Args:
        server_spec: Python file to inspect, optionally with :object suffix
    """
    # Parse the server specification
    file, server_object = run_module.parse_file_path(server_spec)

    logger.debug(
        "Inspecting server",
        extra={
            "file": str(file),
            "server_object": server_object,
            "output": str(output),
        },
    )

    try:
        # Import the server
        server = await run_module.import_server(file, server_object)

        # Get server information - using native async support
        info = await inspect_fastmcp(server)

        info_json = TypeAdapter(FastMCPInfo).dump_json(info, indent=2)

        # Ensure output directory exists
        output.parent.mkdir(parents=True, exist_ok=True)

        # Write JSON report (always pretty-printed)
        with output.open("w", encoding="utf-8") as f:
            f.write(info_json.decode("utf-8"))

        logger.info(f"Server inspection complete. Report saved to {output}")

        # Print summary to console
        console.print(
            f"[bold green]✓[/bold green] Inspected server: [bold]{info.name}[/bold]"
        )
        console.print(f"  Tools: {len(info.tools)}")
        console.print(f"  Prompts: {len(info.prompts)}")
        console.print(f"  Resources: {len(info.resources)}")
        console.print(f"  Templates: {len(info.templates)}")
        console.print(f"  Report saved to: [cyan]{output}[/cyan]")

    except Exception as e:
        logger.error(
            f"Failed to inspect server: {e}",
            extra={
                "server_spec": server_spec,
                "error": str(e),
            },
        )
        console.print(f"[bold red]✗[/bold red] Failed to inspect server: {e}")
        sys.exit(1)


# Add install subcommands using proper Cyclopts pattern
app.command(install_app)


if __name__ == "__main__":
    app()
