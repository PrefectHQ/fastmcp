"""FastMCP background-tasks example server (SEP-2663).

Run this in one terminal, then drive it from `client.py` in another. It exposes
one `task=True` tool that reports progress as it works, so you can watch the
client poll a real background task over HTTP.

    # From the fastmcp root (memory:// backend, no Redis needed):
    python examples/tasks/server.py

The server listens on http://localhost:8000/mcp. The tasks extension runs its
Docket worker in-process on the default `memory://` backend, so several tasks
submitted at once execute concurrently (worker concurrency defaults to 10).
Point `FASTMCP_DOCKET_URL` at Redis to distribute work across separate worker
processes instead — see README.md.
"""

import asyncio
import logging
from datetime import timedelta
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.dependencies import Progress
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
logger = logging.getLogger("tasks-example")

# Enable SEP-2663 background tasks. With no arguments the extension reads the
# FASTMCP_DOCKET_* environment and falls back to an in-process memory:// worker.
mcp = FastMCP("Tasks Example")
mcp.add_extension(TasksExtension())


# A short poll interval keeps the example snappy: the client observes each
# task finishing within ~1s. The default is 5s, tuned for real workloads.
@mcp.tool(task=TaskConfig(poll_interval=timedelta(seconds=1)))
async def slow_computation(
    label: Annotated[str, "A name for this run, echoed back in progress logs"],
    duration: Annotated[int, "How many seconds the computation should take (1-60)"],
    progress: Progress = Progress(),
) -> str:
    """Spend `duration` seconds working, reporting progress once per second.

    Marked `task=True`, so a task-aware client runs it in the background and
    polls for progress and the final result instead of blocking on the call.
    """
    if not 1 <= duration <= 60:
        raise ValueError("duration must be between 1 and 60 seconds")

    logger.info("[%s] starting — %ds", label, duration)
    await progress.set_total(duration)

    for elapsed in range(1, duration + 1):
        await asyncio.sleep(1)
        await progress.increment()
        await progress.set_message(f"{label}: {elapsed}/{duration}s")

    logger.info("[%s] done", label)
    return f"{label} finished in {duration}s"


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8000)
