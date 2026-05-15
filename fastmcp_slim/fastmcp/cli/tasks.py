"""FastMCP tasks CLI for Docket task management."""

import asyncio
import sys
from typing import Annotated

import cyclopts
from rich.console import Console

from fastmcp.utilities.cli import load_and_merge_config
from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.tasks")
console = Console()

tasks_app = cyclopts.App(
    name="tasks",
    help="Manage FastMCP background tasks using Docket",
)


def check_distributed_backend() -> None:
    """Check if Docket is configured with a distributed backend.

    The CLI worker runs as a separate process, so it needs Redis/Valkey
    to coordinate with the main server process.

    Raises:
        SystemExit: If using memory:// URL
    """
    import fastmcp

    docket_url = fastmcp.settings.docket.url

    # Check for memory:// URL and provide helpful error
    if docket_url.startswith("memory://"):
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
    import fastmcp

    check_distributed_backend()

    # Load server to get task functions
    try:
        config, _resolved_spec = load_and_merge_config(server_spec)
    except FileNotFoundError:
        sys.exit(1)

    # Load the server
    server = asyncio.run(config.source.load_server())

    async def run_worker():
        """Enter server lifespan and camp forever."""
        async with server._lifespan_manager():
            console.print(
                f"[bold green]✓[/bold green] Starting worker for [cyan]{server.name}[/cyan]"
            )
            console.print(f"  Docket: {fastmcp.settings.docket.name}")
            console.print(f"  Backend: {fastmcp.settings.docket.url}")
            console.print(f"  Concurrency: {fastmcp.settings.docket.concurrency}")

            # Server's lifespan has started its worker - just camp here forever
            while True:
                await asyncio.sleep(3600)

    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped[/yellow]")
        sys.exit(0)
