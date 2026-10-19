"""FastMCP tasks CLI for Docket task management."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Annotated

import cyclopts
from rich.console import Console

from fastmcp.utilities.cli import load_and_merge_config
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from fastmcp_tasks.settings import DocketSettings

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger("cli.tasks")
console = Console()

tasks_app = cyclopts.App(
    name="tasks",
    help="Manage FastMCP background tasks using Docket",
)


def resolve_docket_settings(server: FastMCP) -> DocketSettings:
    """The effective Docket settings for `server`'s registered tasks extension.

    Reads the *registered* `TasksExtension`'s resolved settings, not the
    env-only module-level default: a server that configures
    `TasksExtension(url="redis://...")` in code has settings the environment
    alone cannot see, and checking those defaults instead would report the
    wrong backend (see #4603 review — the CLI checked before the server, and
    therefore the extension, was even loaded).
    """
    extension = server._extensions.get(TASKS_EXTENSION_ID)
    if extension is None:
        console.print(
            f"[bold red]✗ No tasks extension registered[/bold red]\n\n"
            f"[cyan]{server.name}[/cyan] has no `TasksExtension` registered "
            "(`mcp.add_extension(TasksExtension())`), so there is nothing for "
            "this worker to serve."
        )
        sys.exit(1)
    from fastmcp_tasks.extension import TasksExtension

    assert isinstance(extension, TasksExtension)
    return extension.docket_settings


def check_distributed_backend(settings: DocketSettings) -> None:
    """Check if Docket is configured with a distributed backend.

    The CLI worker runs as a separate process, so it needs Redis/Valkey
    to coordinate with the main server process.

    Raises:
        SystemExit: If using memory:// URL
    """
    # Check for memory:// URL and provide helpful error
    if settings.url.startswith("memory://"):
        console.print(
            "[bold red]✗ In-memory backend not supported by CLI[/bold red]\n\n"
            "Your Docket configuration uses an in-memory backend (memory://) which\n"
            "only works within a single process.\n\n"
            "To use [cyan]fastmcp tasks[/cyan] CLI commands (which run in separate\n"
            "processes), you need a distributed backend:\n\n"
            "[bold]1. Install Redis or Valkey:[/bold]\n"
            "   [dim]macOS:[/dim]     brew install redis\n"
            "   [dim]Ubuntu:[/dim]    apt install redis-server\n"
            "   [dim]Valkey:[/dim]    See https://valkey.io/\n\n"
            "[bold]2. Start the service:[/bold]\n"
            "   redis-server\n\n"
            "[bold]3. Configure Docket URL:[/bold]\n"
            "   [dim]Environment variable:[/dim]\n"
            "   export FASTMCP_DOCKET_URL=redis://localhost:6379/0\n\n"
            "[bold]4. Try again[/bold]\n\n"
            "The memory backend works great for single-process servers, but the CLI\n"
            "commands need a distributed backend to coordinate across processes.\n\n"
            "Need help? See: [cyan]https://gofastmcp.com/docs/tasks[/cyan]"
        )
        sys.exit(1)


@tasks_app.command
def worker(
    server_spec: Annotated[
        str | None,
        cyclopts.Parameter(
            help="Python file to run, optionally with :object suffix, or None to auto-detect fastmcp.json"
        ),
    ] = None,
) -> None:
    """Start an additional worker to process background tasks.

    Connects to your Docket backend and processes tasks in parallel with
    any other running workers. Configure via environment variables
    (FASTMCP_DOCKET_*).

    Example:
        fastmcp tasks worker server.py
        fastmcp tasks worker examples/tasks/server.py
    """
    # Load server to get task functions
    try:
        config, _resolved_spec = load_and_merge_config(server_spec)
    except FileNotFoundError:
        sys.exit(1)

    # Load the server
    server = asyncio.run(config.source.load_server())

    # Validate against the server's actual registered extension, not an
    # env-only guess — a constructor-configured Redis URL isn't visible
    # until the server (and its extension) has loaded.
    settings = resolve_docket_settings(server)
    check_distributed_backend(settings)

    async def run_worker():
        """Enter server lifespan and camp forever."""
        async with server._lifespan_manager():
            console.print(
                f"[bold green]✓[/bold green] Starting worker for [cyan]{server.name}[/cyan]"
            )
            console.print(f"  Docket: {settings.name}")
            console.print(f"  Backend: {settings.url}")
            console.print(f"  Concurrency: {settings.concurrency}")

            # Server's lifespan has started its worker - just camp here forever
            while True:
                await asyncio.sleep(3600)

    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    # Enables `python -m fastmcp_tasks.worker_cli worker <server>` for running an
    # out-of-process worker now that core dropped the `fastmcp tasks` subcommand.
    tasks_app()
