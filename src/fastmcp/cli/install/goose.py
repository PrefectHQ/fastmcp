"""Goose integration for FastMCP install using Cyclopts."""

import re
import sys
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import cyclopts
from rich import print

from fastmcp.utilities.logging import get_logger

from .shared import open_deeplink, process_common_args

logger = get_logger(__name__)


def _slugify(name: str) -> str:
    """Convert a display name to a URL-safe identifier.

    Lowercases, replaces non-alphanumeric runs with hyphens,
    and strips leading/trailing hyphens.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "fastmcp-server"


def generate_goose_deeplink(
    name: str,
    command: str,
    args: list[str],
    *,
    description: str = "MCP server installed via FastMCP",
) -> str:
    """Generate a Goose deeplink for installing an MCP extension.

    Args:
        name: Human-readable display name for the extension.
        command: The executable command (e.g. "uv").
        args: Arguments to the command.
        description: Short description shown in Goose.

    Returns:
        A goose://extension?... deeplink URL.
    """
    extension_id = _slugify(name)

    params: list[str] = [f"cmd={quote(command, safe='')}"]
    for arg in args:
        params.append(f"arg={quote(arg, safe='')}")
    params.append(f"id={quote(extension_id, safe='')}")
    params.append(f"name={quote(name, safe='')}")
    params.append(f"description={quote(description, safe='')}")

    return f"goose://extension?{'&'.join(params)}"


def _build_uvx_command(
    server_spec: str,
    *,
    python_version: str | None = None,
    with_packages: list[str] | None = None,
) -> list[str]:
    """Build a uvx command for running a FastMCP server.

    Goose requires uvx (not uv run) as the command. The uvx format is:
        uvx [--with pkg] [--python X] fastmcp run <spec>

    uvx automatically infers that the `fastmcp` command comes from the
    `fastmcp` package, so --from is not needed.
    """
    args: list[str] = ["uvx"]

    if python_version:
        args.extend(["--python", python_version])

    for pkg in sorted(set(with_packages or [])):
        if pkg != "fastmcp":
            args.extend(["--with", pkg])

    args.extend(["fastmcp", "run", server_spec])
    return args


def install_goose(
    file: Path,
    server_object: str | None,
    name: str,
    *,
    with_packages: list[str] | None = None,
    python_version: str | None = None,
) -> bool:
    """Install FastMCP server in Goose via deeplink.

    Args:
        file: Path to the server file.
        server_object: Optional server object name (for :object suffix).
        name: Name for the extension in Goose.
        with_packages: Optional list of additional packages to install.
        python_version: Optional Python version to use.

    Returns:
        True if installation was successful, False otherwise.
    """
    if server_object:
        server_spec = f"{file.resolve()}:{server_object}"
    else:
        server_spec = str(file.resolve())

    full_command = _build_uvx_command(
        server_spec,
        python_version=python_version,
        with_packages=with_packages,
    )

    deeplink = generate_goose_deeplink(
        name=name,
        command=full_command[0],
        args=full_command[1:],
    )

    print(f"[blue]Opening Goose to install '{name}'[/blue]")

    if open_deeplink(deeplink, expected_scheme="goose"):
        print("[green]Goose should now open with the installation dialog[/green]")
        return True
    else:
        print(
            "[red]Could not open Goose automatically.[/red]\n"
            f"[blue]Please copy this link and open it in Goose: {deeplink}[/blue]"
        )
        return False


async def goose_command(
    server_spec: str,
    *,
    server_name: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--name", "-n"],
            help="Custom name for the extension in Goose",
        ),
    ] = None,
    with_packages: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--with",
            help="Additional packages to install (can be used multiple times)",
        ),
    ] = None,
    env_vars: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--env",
            help="Environment variables in KEY=VALUE format (can be used multiple times)",
        ),
    ] = None,
    env_file: Annotated[
        Path | None,
        cyclopts.Parameter(
            "--env-file",
            help="Load environment variables from .env file",
        ),
    ] = None,
    python: Annotated[
        str | None,
        cyclopts.Parameter(
            "--python",
            help="Python version to use (e.g., 3.10, 3.11)",
        ),
    ] = None,
) -> None:
    """Install an MCP server in Goose.

    Uses uvx to run the server. Environment variables are not included
    in the deeplink; use `fastmcp install mcp-json` to generate a full
    config for manual installation.

    Args:
        server_spec: Python file to install, optionally with :object suffix
    """
    with_packages = with_packages or []
    env_vars = env_vars or []

    if env_vars or env_file:
        print(
            "[red]Goose deeplinks cannot include environment variables.[/red]\n"
            "[yellow]Use `fastmcp install mcp-json` to generate a config, then add it "
            "to your Goose config file with env vars: "
            "https://block.github.io/goose/docs/getting-started/using-extensions/#config-entry[/yellow]"
        )
        sys.exit(1)

    file, server_object, name, with_packages, _env_dict = await process_common_args(
        server_spec, server_name, with_packages, env_vars, env_file
    )

    success = install_goose(
        file=file,
        server_object=server_object,
        name=name,
        with_packages=with_packages,
        python_version=python,
    )

    if not success:
        sys.exit(1)
