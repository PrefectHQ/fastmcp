"""
Background task elicitation demo.

A background task (Docket) that pauses mid-execution to ask the user a
question, waits for the answer, then resumes and finishes.

Works with both in-memory and Redis backends:

    # In-memory (single process, no Redis needed)
    FASTMCP_DOCKET_URL=memory:// uv run python examples/task_elicitation.py

    # Redis (distributed, needs a worker running separately)
    #   Terminal 1: docker compose -f examples/tasks/docker-compose.yml up -d
    #   Terminal 2: FASTMCP_DOCKET_URL=redis://localhost:24242/0 \
    #               uv run fastmcp tasks worker examples/task_elicitation.py
    #   Terminal 3: FASTMCP_DOCKET_URL=redis://localhost:24242/0 \
    #               uv run python examples/task_elicitation.py

Requires the `docket` extra (included in dev dependencies).
"""

import asyncio
from dataclasses import dataclass

from mcp.types import TextContent

from fastmcp import Context, FastMCP
from fastmcp.client import Client
from fastmcp.server.elicitation import AcceptedElicitation

mcp = FastMCP("Task Elicitation Demo")


@dataclass
class DinnerPrefs:
    cuisine: str
    vegetarian: bool


@mcp.tool(task=True)
async def plan_dinner(ctx: Context) -> str:
    """Plan a dinner menu, asking the user what they're in the mood for."""

    await ctx.report_progress(0, 2, "Asking what you'd like...")

    result = await ctx.elicit(
        "What kind of dinner are you in the mood for?",
        response_type=DinnerPrefs,
    )

    if not isinstance(result, AcceptedElicitation):
        return "Dinner cancelled!"

    prefs = result.data
    await ctx.report_progress(1, 2, "Planning your menu...")
    await asyncio.sleep(1)
    await ctx.report_progress(2, 2, "Done!")

    veg = "vegetarian " if prefs.vegetarian else ""
    return f"Tonight's menu: a lovely {veg}{prefs.cuisine} dinner!"


async def handle_elicitation(message, response_type, params, context):
    """Handle elicitation requests from background tasks."""
    print(f"  Server asks: {message}")
    print("  Responding with: cuisine=Thai, vegetarian=True")
    return DinnerPrefs(cuisine="Thai", vegetarian=True)


async def main():
    async with Client(mcp, elicitation_handler=handle_elicitation) as client:
        print("Starting background task...")
        task = await client.call_tool("plan_dinner", {}, task=True)
        print(f"  task_id = {task.task_id}\n")

        result = await task.result()
        assert isinstance(result.content[0], TextContent)
        print(f"\nResult: {result.content[0].text}")


if __name__ == "__main__":
    asyncio.run(main())
