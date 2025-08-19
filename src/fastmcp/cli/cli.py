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
    server_spec: str | None = None,
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
        server_spec: Python file to run, optionally with :object suffix, or None to auto-detect fastmcp.json
    """
    # Auto-detect fastmcp.json if no server_spec provided
    if server_spec is None:
        from pathlib import Path

        from fastmcp.utilities.fastmcp_config import FastMCPConfig

        config_path = Path("fastmcp.json")
        if not config_path.exists():
            # Check if fastmcp.json exists in current directory
            found_config = FastMCPConfig.find_config()
            if found_config:
                config_path = found_config
            else:
                logger.error(
                    "No server specification provided and no fastmcp.json found in current directory.\n"
                    "Please specify a server file or create a fastmcp.json configuration."
                )
                sys.exit(1)

        # Load the config to get settings
        config = FastMCPConfig.from_file(config_path)
        entrypoint = config.get_entrypoint(config_path)

        # Convert entrypoint to string format for dev command
        if entrypoint.object:
            server_spec = f"{entrypoint.file}:{entrypoint.object}"
        else:
            server_spec = entrypoint.file

        # Merge environment settings with CLI args (CLI takes precedence)
        if config.environment:
            merged_env = config.environment.merge_with_cli_args(
                python=python,
                with_packages=with_packages,
                with_requirements=with_requirements,
                project=project,
                with_editable=with_editable,
            )
            python = merged_env["python"]
            with_packages = merged_env["with_packages"]
            with_requirements = merged_env["with_requirements"]
            project = merged_env["project"]
            with_editable = merged_env["with_editable"]

        # Get server port from deployment config if not specified
        if config.deployment and config.deployment.port:
            server_port = server_port or config.deployment.port

        logger.info(f"Using configuration from {config_path}")
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
        # TODO: Remove dependencies handling (deprecated in v2.11.4)
        server: FastMCP = await run_module.import_server(file, server_object)
        if server.dependencies:
            import warnings

            warnings.warn(
                f"Server '{server.name}' uses deprecated 'dependencies' parameter (deprecated in FastMCP 2.11.4). "
                "Please migrate to fastmcp.json configuration file. "
                "See https://gofastmcp.com/docs/deployment/server-configuration for details.",
                DeprecationWarning,
                stacklevel=2,
            )
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
    # Load configuration if needed
    from pathlib import Path

    from fastmcp.utilities.fastmcp_config import FastMCPConfig

    config = None
    config_path = None

    # Auto-detect fastmcp.json if no server_spec provided
    if server_spec is None:
        config_path = Path("fastmcp.json")
        if not config_path.exists():
            # Check if fastmcp.json exists in current directory
            found_config = FastMCPConfig.find_config()
            if found_config:
                config_path = found_config
            else:
                logger.error(
                    "No server specification provided and no fastmcp.json found in current directory.\n"
                    "Please specify a server file or create a fastmcp.json configuration."
                )
                sys.exit(1)

        server_spec = str(config_path)
        logger.info(f"Using configuration from {config_path}")

    # Load config if server_spec is a fastmcp.json file
    if server_spec.endswith("fastmcp.json"):
        config_path = Path(server_spec)
        if config_path.exists():
            config = FastMCPConfig.from_file(config_path)

            # Merge deployment config with CLI values (CLI takes precedence)
            if config.deployment:
                merged_deploy = config.deployment.merge_with_cli_args(
                    transport=transport,
                    host=host,
                    port=port,
                    path=path,
                    log_level=log_level,
                    server_args=list(server_args) if server_args else None,
                )
                transport = merged_deploy["transport"]
                host = merged_deploy["host"]
                port = merged_deploy["port"]
                path = merged_deploy["path"]
                log_level = merged_deploy["log_level"]
                server_args = merged_deploy["server_args"] or ()

            # Merge environment config with CLI values (CLI takes precedence)
            if config.environment:
                merged_env = config.environment.merge_with_cli_args(
                    python=python,
                    with_packages=with_packages,
                    with_requirements=with_requirements,
                    project=project,
                )
                python = merged_env["python"]
                with_packages = merged_env["with_packages"]
                with_requirements = merged_env["with_requirements"]
                project = merged_env["project"]
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

    # Check if we need to use uv run (either from CLI args or config)
    needs_uv = python or with_packages or with_requirements or project
    if not needs_uv and config and config.environment:
        # Check if config's environment needs uv
        needs_uv = config.environment.needs_uv()

    if needs_uv:
        # Use uv run subprocess - always use run_with_uv which handles output correctly
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
    server_spec: str | None = None,
    *,
    output: Annotated[
        Path,
        cyclopts.Parameter(
            name=["--output", "-o"],
            help="Output file path for the JSON report (default: server-info.json)",
        ),
    ] = Path("server-info.json"),
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
    """Inspect an MCP server and generate a JSON report.

    This command analyzes an MCP server and generates a comprehensive JSON report
    containing information about the server's name, instructions, version, tools,
    prompts, resources, templates, and capabilities.

    Examples:
        fastmcp inspect server.py
        fastmcp inspect server.py -o report.json
        fastmcp inspect server.py:mcp -o analysis.json
        fastmcp inspect path/to/server.py:app -o /tmp/server-info.json
        fastmcp inspect fastmcp.json
        fastmcp inspect  # auto-detect fastmcp.json

    Args:
        server_spec: Python file to inspect, optionally with :object suffix, or fastmcp.json
    """
    # Load configuration if needed
    from pathlib import Path

    from fastmcp.utilities.fastmcp_config import FastMCPConfig

    config = None
    config_path = None

    # Auto-detect fastmcp.json if no server_spec provided
    if server_spec is None:
        config_path = Path("fastmcp.json")
        if not config_path.exists():
            # Check if fastmcp.json exists in current directory
            found_config = FastMCPConfig.find_config()
            if found_config:
                config_path = found_config
            else:
                logger.error(
                    "No server specification provided and no fastmcp.json found in current directory.\n"
                    "Please specify a server file or create a fastmcp.json configuration."
                )
                sys.exit(1)

        server_spec = str(config_path)
        logger.info(f"Using configuration from {config_path}")

    # Load config if server_spec is a fastmcp.json file
    if server_spec.endswith("fastmcp.json"):
        config_path = Path(server_spec)
        if config_path.exists():
            config = FastMCPConfig.from_file(config_path)
            # Get the actual entrypoint with resolved paths
            entrypoint = config.get_entrypoint(config_path)

            if entrypoint.object:
                server_spec = f"{entrypoint.file}:{entrypoint.object}"
            else:
                server_spec = entrypoint.file

            # Merge environment settings from config with CLI (CLI takes precedence)
            if config.environment:
                merged_env = config.environment.merge_with_cli_args(
                    python=python,
                    with_packages=with_packages,
                    with_requirements=with_requirements,
                    project=project,
                )
                python = merged_env["python"]
                with_packages = merged_env["with_packages"]
                with_requirements = merged_env["with_requirements"]
                project = merged_env["project"]

    # Check if we need to use uv run
    needs_uv = python or with_packages or with_requirements or project
    if not needs_uv and config and config.environment:
        needs_uv = config.environment.needs_uv()

    if needs_uv:
        # Build and run uv command
        if config and config.environment:
            # Use environment config's run_with_uv method
            inspect_command = [
                "fastmcp",
                "inspect",
                server_spec,
                "--output",
                str(output),
            ]
            config.environment.run_with_uv(inspect_command)
        else:
            # Build an EnvironmentConfig from CLI args for consistency
            from fastmcp.utilities.fastmcp_config import (
                EnvironmentConfig,
            )

            env_config = EnvironmentConfig(
                python=python,
                dependencies=with_packages,
                requirements=str(with_requirements) if with_requirements else None,
                project=str(project) if project else None,
            )

            inspect_command = [
                "fastmcp",
                "inspect",
                server_spec,
                "--output",
                str(output),
            ]
            env_config.run_with_uv(inspect_command)

    # Direct import path (no uv needed)
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


@app.command
def generate_schema(
    *,
    output: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--output", "-o"],
            help="Output file path for the JSON schema",
        ),
    ] = None,
) -> None:
    """Generate JSON schema for fastmcp.json configuration files.

    This generates a JSON schema that can be used by IDEs and validators
    to provide auto-completion and validation for fastmcp.json files.

    Examples:
        fastmcp generate-schema
        fastmcp generate-schema -o schema.json
    """
    import json

    from fastmcp.utilities.fastmcp_config import (
        generate_schema as gen_schema,
    )

    schema = gen_schema()
    schema_json = json.dumps(schema, indent=2)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(schema_json)
        logger.info(f"Schema written to {output}")
    else:
        console.print(schema_json)


# Add install subcommands using proper Cyclopts pattern
app.command(install_app)


if __name__ == "__main__":
    app()
