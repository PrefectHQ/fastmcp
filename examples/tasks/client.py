"""
FastMCP Tasks Example Client

Demonstrates calling tools both immediately and as background tasks,
with real-time progress updates via status callbacks.

Usage:
    # Make sure environment is configured (source .envrc or use direnv)
    source .envrc

    # Background task execution with progress callbacks (default)
    python client.py --duration 10

    # Immediate execution (blocks until complete)
    python client.py immediate --duration 5
"""

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from mcp.types import GetTaskResult, TextContent
from rich.console import Console

from fastmcp.client import Client

console = Console()
app = cyclopts.App(name="tasks-client", help="FastMCP Tasks Example Client")


def load_server():
    """Load the example server."""
    examples_dir = Path(__file__).parent.parent.parent
    if str(examples_dir) not in sys.path:
        sys.path.insert(0, str(examples_dir))

    import examples.tasks.server as server_module

    return server_module.mcp


# Track last message to deduplicate consecutive identical notifications
# Note: Docket fires separate events for progress.increment() and progress.set_message(),
# but MCP's statusMessage field only carries the text message (no numerical progress).
# This means we often get duplicate notifications with identical messages.
_last_notification_message = None


def print_notification(status: GetTaskResult) -> None:
    """Callback function for push notifications from server.

    This is called automatically when the server sends notifications/tasks/status.
    Deduplicates identical consecutive messages to keep output clean.
    """
    global _last_notification_message

    # Skip if this is the same message we just printed
    if status.statusMessage == _last_notification_message:
        return

    _last_notification_message = status.statusMessage

    color = {
        "working": "yellow",
        "completed": "green",
        "failed": "red",
    }.get(status.status, "yellow")

    icon = {
        "working": "🚀",
        "completed": "✅",
        "failed": "❌",
    }.get(status.status, "⚠️")

    console.print(
        f"[{color}]📢 Notification: {status.status} {icon} - {status.statusMessage}[/{color}]"
    )


@app.default
async def task(
    duration: Annotated[
        int,
        cyclopts.Parameter(help="Duration of computation in seconds (1-60)"),
    ] = 10,
):
    """Execute as background task with real-time progress callbacks."""
    if duration < 1 or duration > 60:
        console.print("[red]Error: Duration must be between 1 and 60 seconds[/red]")
        sys.exit(1)

    server = load_server()

    console.print(f"\n[bold]Calling slow_computation(duration={duration})[/bold]")
    console.print("Mode: [cyan]Background task[/cyan]\n")

    async with Client(server) as client:
        task_obj = await client.call_tool(
            "slow_computation",
            arguments={"duration": duration},
            task=True,
        )

        console.print(f"Task started: [cyan]{task_obj.task_id}[/cyan]\n")

        # Register callback for real-time push notifications
        task_obj.on_status_change(print_notification)

        console.print(
            "[dim]Notifications will appear as the server sends them...[/dim]\n"
        )

        # Do other work while task runs in background
        for i in range(3):
            await asyncio.sleep(0.5)
            console.print(f"[dim]Client doing other work... ({i + 1}/3)[/dim]")

        console.print()

        # Wait for task to complete
        console.print("[dim]Waiting for final result...[/dim]")
        result = await task_obj.result()

        console.print("\n[bold]Result:[/bold]")
        assert isinstance(result.content[0], TextContent)
        console.print(f"  {result.content[0].text}")


@app.command
async def immediate(
    duration: Annotated[
        int,
        cyclopts.Parameter(help="Duration of computation in seconds (1-60)"),
    ] = 5,
):
    """Execute the tool immediately (blocks until complete)."""
    if duration < 1 or duration > 60:
        console.print("[red]Error: Duration must be between 1 and 60 seconds[/red]")
        sys.exit(1)

    server = load_server()

    console.print(f"\n[bold]Calling slow_computation(duration={duration})[/bold]")
    console.print("Mode: [cyan]Immediate execution[/cyan]\n")

    async with Client(server) as client:
        result = await client.call_tool(
            "slow_computation",
            arguments={"duration": duration},
        )

        console.print("\n[bold]Result:[/bold]")
        assert isinstance(result.content[0], TextContent)
        console.print(f"  {result.content[0].text}")


if __name__ == "__main__":
    app()
