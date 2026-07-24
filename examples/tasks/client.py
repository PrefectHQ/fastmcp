"""FastMCP background-tasks example client (SEP-2663).

Start the server first (`python examples/tasks/server.py`), then run any of the
commands below against it over HTTP.

    # Transparent: call_tool drives the background task and returns its result
    python examples/tasks/client.py --duration 8

    # Explicit handle: return immediately, poll it yourself, then collect
    python examples/tasks/client.py handle --duration 6

    # Parallel: fire several tasks at once and watch them overlap
    python examples/tasks/client.py parallel

Importing `fastmcp_tasks` (below) enables client task support for every Client
in the process — without it, a Client never advertises the tasks capability and
the server runs its calls synchronously.
"""

import asyncio
import time
from typing import Annotated

import cyclopts
from mcp_types import TextContent
from rich.console import Console

from fastmcp.client import Client
from fastmcp_tasks import call_tool_task  # importing enables client task support

SERVER_URL = "http://127.0.0.1:8000/mcp"

console = Console()
app = cyclopts.App(name="tasks-client", help="FastMCP background-tasks example client")


def _text(result) -> str:
    assert isinstance(result.content[0], TextContent)
    return result.content[0].text


@app.default
async def transparent(
    duration: Annotated[int, cyclopts.Parameter(help="Seconds (1-60)")] = 8,
):
    """Call the tool transparently: the client drives the task to completion.

    The server runs `slow_computation` as a background task, but `call_tool`
    polls it under the hood and returns the tool's real result — the calling
    code looks exactly like an ordinary synchronous tool call.
    """
    async with Client(SERVER_URL, mode="auto") as client:
        console.print(f"\n[bold]Transparent call[/bold] (duration={duration})\n")
        started = time.perf_counter()
        result = await client.call_tool(
            "slow_computation",
            {"label": "transparent", "duration": duration},
        )
        console.print(f"[green]{_text(result)}[/green]")
        console.print(f"[dim]elapsed {time.perf_counter() - started:.1f}s[/dim]")


@app.command
async def handle(
    duration: Annotated[int, cyclopts.Parameter(help="Seconds (1-60)")] = 6,
):
    """Use the explicit handle: return immediately, then drive the task yourself."""
    async with Client(SERVER_URL, mode="auto") as client:
        console.print(f"\n[bold]Explicit handle[/bold] (duration={duration})\n")
        task = await call_tool_task(
            client, "slow_computation", {"label": "handle", "duration": duration}
        )
        console.print(f"Task started: [cyan]{task.task_id}[/cyan]\n")

        # Do other work while the task runs, checking its status as you go.
        while True:
            status = await task.status()
            if status.status in ("completed", "failed", "cancelled"):
                break
            console.print(f"[dim]still {status.status}: {status.status_message}[/dim]")
            await asyncio.sleep(1)

        result = await task.result()
        console.print(f"\n[green]{_text(result)}[/green]")


@app.command
async def parallel(
    durations: Annotated[
        list[int] | None,
        cyclopts.Parameter(help="One task per duration (default: 5 4 3 2)"),
    ] = None,
):
    """Fire several background tasks at once and drive them concurrently.

    Each `call_tool_task` returns immediately, so we start every task before
    awaiting any of them. The worker runs them in parallel, so total wall-clock
    tracks the *longest* task, not the sum — proof the work actually overlaps.
    """
    durations = durations or [5, 4, 3, 2]

    async with Client(SERVER_URL, mode="auto") as client:
        console.print(f"\n[bold]Parallel tasks[/bold]: durations={durations}\n")
        started = time.perf_counter()

        # Start every task up front — none of these await completion.
        tasks = [
            await call_tool_task(
                client,
                "slow_computation",
                {"label": f"task-{i}({d}s)", "duration": d},
            )
            for i, d in enumerate(durations)
        ]
        for task in tasks:
            console.print(f"  started [cyan]{task.task_id}[/cyan]")

        # Await them together; results print as each task finishes.
        async def collect(task):
            result = await task.result()
            console.print(
                f"[green]✓[/green] {_text(result)} "
                f"[dim](+{time.perf_counter() - started:.1f}s)[/dim]"
            )

        console.print()
        await asyncio.gather(*(collect(task) for task in tasks))

        total = time.perf_counter() - started
        console.print(
            f"\n[bold]All {len(tasks)} tasks done in {total:.1f}s[/bold] "
            f"[dim](longest single task: {max(durations)}s)[/dim]"
        )


if __name__ == "__main__":
    app()
