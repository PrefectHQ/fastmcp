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
from pydantic import TypeAdapter, ValidationError
from rich.console import Console
from rich.table import Table

import fastmcp
from fastmcp.cli import run as run_module
from fastmcp.cli.install import install_app
from fastmcp.server.server import FastMCP
from fastmcp.utilities.inspect import FastMCPInfo, inspect_fastmcp
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import get_cached_typeadapter

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
            sys.argv = [sys.argv[0]] + args
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
    # Convert None to empty lists for list parameters
    with_editable = with_editable or []
    with_packages = with_packages or []
    from pathlib import Path

    from fastmcp.utilities.fastmcp_config import FastMCPConfig
    from fastmcp.utilities.fastmcp_config.v1.sources.filesystem import FileSystemSource

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

    # Create FastMCPConfig from server_spec
    if server_spec.endswith(".json"):
        # Load existing config
        config = FastMCPConfig.from_file(Path(server_spec))

        # Merge environment settings with CLI args (CLI takes precedence)
        if config.environment:
            python = python or config.environment.python
            project = project or (
                Path(config.environment.project) if config.environment.project else None
            )
            with_requirements = with_requirements or (
                Path(config.environment.requirements)
                if config.environment.requirements
                else None
            )
            # Merge editable paths from config with CLI args
            if config.environment.editable and not with_editable:
                with_editable = [Path(p) for p in config.environment.editable]

            # Merge packages from both sources
            if config.environment.dependencies:
                packages = list(config.environment.dependencies)
                if with_packages:
                    packages.extend(with_packages)
                with_packages = packages

        # Get server port from deployment config if not specified
        if config.deployment and config.deployment.port:
            server_port = server_port or config.deployment.port
    else:
        # Create config from file path
        source = FileSystemSource(path=server_spec)
        config = FastMCPConfig(source=source)

    logger.debug(
        "Starting dev server",
        extra={
            "server_spec": server_spec,
            "with_editable": [str(p) for p in with_editable] if with_editable else None,
            "with_packages": with_packages,
            "ui_port": ui_port,
            "server_port": server_port,
        },
    )

    try:
        # Load server to check for deprecated dependencies
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

        # Create Environment object from CLI args
        from fastmcp.utilities.fastmcp_config import Environment

        env_config = Environment(
            python=python,
            dependencies=with_packages if with_packages else None,
            requirements=str(with_requirements) if with_requirements else None,
            project=str(project) if project else None,
            editable=[str(p) for p in with_editable] if with_editable else None,
        )
        uv_cmd = ["uv"] + env_config.build_uv_args(["fastmcp", "run", server_spec])

        # Add --no-banner flag for dev command
        uv_cmd.append("--no-banner")

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
    # Convert None to empty lists for list parameters
    with_packages = with_packages or []
    # Load configuration if needed
    from pathlib import Path

    from fastmcp.utilities.fastmcp_config import FastMCPConfig

    config = None
    config_path = None
    editable = None  # Initialize editable variable

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

    # Load config if server_spec is a .json file
    if server_spec.endswith(".json"):
        config_path = Path(server_spec)
        if config_path.exists():
            # Try to load as JSON and discriminate between FastMCPConfig and MCPConfig
            try:
                with open(config_path) as f:
                    data = json.load(f)

                # Check if it's an MCPConfig first (has canonical mcpServers key)
                if "mcpServers" in data:
                    # It's an MCPConfig, we don't process these in the run command
                    # They should be handled through different code paths
                    config = None
                else:
                    # Try to parse as FastMCPConfig
                    try:
                        adapter = get_cached_typeadapter(FastMCPConfig)
                        config = adapter.validate_python(data)

                        # Merge deployment config with CLI values (CLI takes precedence)
                        if config.deployment:
                            transport = transport or config.deployment.transport
                            host = host or config.deployment.host
                            port = port or config.deployment.port
                            path = path or config.deployment.path
                            log_level = log_level or config.deployment.log_level
                            server_args = (
                                tuple(server_args)
                                if server_args
                                else tuple(config.deployment.args or ())
                            )

                        # Merge environment config with CLI values (CLI takes precedence)
                        # BUT: Skip this if --skip-env is set
                        if config.environment and not skip_env:
                            python = python or config.environment.python
                            project = project or (
                                Path(config.environment.project)
                                if config.environment.project
                                else None
                            )
                            with_requirements = with_requirements or (
                                Path(config.environment.requirements)
                                if config.environment.requirements
                                else None
                            )
                            # Extract editable from config (no CLI override for this)
                            editable = config.environment.editable

                            # Merge packages from both sources
                            if config.environment.dependencies:
                                packages = list(config.environment.dependencies)
                                if with_packages:
                                    packages.extend(with_packages)
                                with_packages = packages
                    except ValidationError:
                        # Not a valid FastMCPConfig, treat as regular server spec
                        config = None
            except (json.JSONDecodeError, FileNotFoundError):
                # Not a valid JSON file, treat as regular server spec
                config = None
        else:
            config = None
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
    # When --skip-env is set, we ignore config.environment entirely
    needs_uv = python or with_packages or with_requirements or project or editable
    if not needs_uv and config and config.environment and not skip_env:
        # Check if config's environment needs uv (but only if not skipping env)
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
                editable=editable,
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
    # Convert None to empty lists for list parameters
    with_packages = with_packages or []
    from pathlib import Path

    from fastmcp.utilities.fastmcp_config import FastMCPConfig
    from fastmcp.utilities.fastmcp_config.v1.sources.filesystem import FileSystemSource

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

    # Create FastMCPConfig from server_spec
    if server_spec.endswith(".json"):
        config_path = Path(server_spec)
        if config_path.exists():
            try:
                with open(config_path) as f:
                    data = json.load(f)

                # Check if it's an MCPConfig (has mcpServers key)
                if "mcpServers" in data:
                    # MCPConfig - we don't process these in inspect
                    logger.error("MCPConfig files are not supported by inspect command")
                    sys.exit(1)
                else:
                    # It's a FastMCPConfig
                    config = FastMCPConfig.from_file(config_path)

                    # Merge environment settings from config with CLI (CLI takes precedence)
                    if config.environment:
                        python = python or config.environment.python
                        project = project or (
                            Path(config.environment.project)
                            if config.environment.project
                            else None
                        )
                        with_requirements = with_requirements or (
                            Path(config.environment.requirements)
                            if config.environment.requirements
                            else None
                        )

                        # Merge packages from both sources
                        if config.environment.dependencies:
                            packages = list(config.environment.dependencies)
                            if with_packages:
                                packages.extend(with_packages)
                            with_packages = packages
            except (json.JSONDecodeError, ValidationError) as e:
                logger.error(f"Invalid configuration file: {e}")
                sys.exit(1)
        else:
            logger.error(f"Configuration file not found: {config_path}")
            sys.exit(1)
    else:
        # Create config from file path
        source = FileSystemSource(path=server_spec)
        config = FastMCPConfig(source=source)

    # Check if we need to use uv run
    needs_uv = python or with_packages or with_requirements or project
    if not needs_uv and config and config.environment:
        needs_uv = config.environment.needs_uv()

    if needs_uv:
        # Build and run uv command
        from fastmcp.utilities.fastmcp_config import Environment

        # Create or update environment config
        env_config = Environment(
            python=python,
            dependencies=with_packages if with_packages else None,
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
        return  # run_with_uv exits the process

    logger.debug(
        "Inspecting server",
        extra={
            "server_spec": server_spec,
            "output": str(output),
        },
    )

    try:
        # Load the server using the config
        server = await config.source.load_server()

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

    from fastmcp.utilities.fastmcp_config import FastMCPConfig

    # Require output-dir
    if output_dir is None:
        logger.error(
            "The --output-dir parameter is required.\n"
            "Please specify where to create the persistent environment."
        )
        sys.exit(1)

    # Auto-detect fastmcp.json if not provided
    if config_path is None:
        found_config = FastMCPConfig.find_config()
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
        config = FastMCPConfig.from_file(config_file)

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
