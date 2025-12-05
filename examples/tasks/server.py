"""
FastMCP Tasks Example Server

Demonstrates background task execution with progress tracking using Docket.

Setup:
    1. Start Redis: docker compose up -d
    2. Load environment: source .envrc
    3. Run server: fastmcp run server.py

The example uses Redis by default to demonstrate distributed task execution
and the fastmcp tasks CLI commands.
"""

import asyncio
import logging
from typing import Annotated

from docket import Logged

from fastmcp import FastMCP
from fastmcp.dependencies import Progress

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create server
mcp = FastMCP("Tasks Example")


@mcp.tool(task=True)
async def slow_computation(
    duration: Annotated[int, Logged],
    progress: Progress = Progress(),
) -> str:
    """
    Perform a slow computation that takes `duration` seconds.

    This tool demonstrates progress tracking with background tasks.
    It logs progress every 1-2 seconds and reports progress via Docket.

    Args:
        duration: Number of seconds the computation should take (1-60)

    Returns:
        A completion message with the total duration
    """
    if duration < 1 or duration > 60:
        raise ValueError("Duration must be between 1 and 60 seconds")

    logger.info(f"Starting slow computation for {duration} seconds")

    # Set total progress units
    await progress.set_total(duration)

    # Process each second
    for i in range(duration):
        # Sleep for 1 second
        await asyncio.sleep(1)

        # Update progress
        elapsed = i + 1
        remaining = duration - elapsed
        await progress.increment()
        await progress.set_message(
            f"Working... {elapsed}/{duration}s ({remaining}s remaining)"
        )

        # Log every 1-2 seconds
        if elapsed % 2 == 0 or elapsed == duration:
            logger.info(f"Progress: {elapsed}/{duration}s")

    logger.info(f"Completed computation in {duration} seconds")
    return f"Computation completed successfully in {duration} seconds!"
