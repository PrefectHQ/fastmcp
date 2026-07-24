"""
Background task input demo (SEP-2663 guard pattern).

A background task that pauses to ask the user a question, waits for the answer,
then resumes and finishes. Under SEP-2663 a task gathers input by the *guard
pattern*: instead of awaiting `ctx.elicit()` (which would block a worker), the
tool *returns* an `InputRequiredResult`. That ends the leg; the client answers
via the tasks protocol; the framework re-runs the tool with the answer on
`ctx.input_responses`. No worker is ever blocked.

The client side is transparent: `client.call_tool(...)` drives the whole
round-trip — poll, answer via the `elicitation_handler`, poll again — and returns
the finished result.

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

import mcp_types
from mcp_types import TextContent

from fastmcp import Context, FastMCP
from fastmcp.client import Client
from fastmcp_tasks import TasksExtension

mcp = FastMCP("Task Elicitation Demo")
mcp.add_extension(TasksExtension())


@dataclass
class DinnerPrefs:
    cuisine: str
    vegetarian: bool


def _ask_dinner_prefs() -> mcp_types.InputRequiredResult:
    """Return the input request that pauses the task until the client answers."""
    request = mcp_types.ElicitRequest(
        params=mcp_types.ElicitRequestFormParams(
            message="What kind of dinner are you in the mood for?",
            requested_schema={
                "type": "object",
                "properties": {
                    "cuisine": {"type": "string"},
                    "vegetarian": {"type": "boolean"},
                },
                "required": ["cuisine", "vegetarian"],
            },
        )
    )
    return mcp_types.InputRequiredResult(
        result_type="input_required",
        input_requests={"prefs": request},
    )


@mcp.tool(task=True)
async def plan_dinner(ctx: Context) -> str | mcp_types.InputRequiredResult:
    """Plan a dinner menu, asking the user what they're in the mood for."""
    responses = ctx.input_responses
    if responses is None:
        # First leg: ask for preferences and end the leg.
        return _ask_dinner_prefs()

    # Re-entered leg: the client's answer is on ctx.input_responses.
    answer = responses["prefs"]
    assert isinstance(answer, mcp_types.ElicitResult)
    if answer.action != "accept" or answer.content is None:
        return "Dinner cancelled!"

    await asyncio.sleep(1)  # "planning the menu"
    veg = "vegetarian " if answer.content["vegetarian"] else ""
    return f"Tonight's menu: a lovely {veg}{answer.content['cuisine']} dinner!"


async def handle_elicitation(message, response_type, params, context):
    """Answer elicitation requests raised by the background task."""
    print(f"  Server asks: {message}")
    print("  Responding with: cuisine=Thai, vegetarian=True")
    return DinnerPrefs(cuisine="Thai", vegetarian=True)


async def main():
    client = Client(mcp, mode="auto", elicitation_handler=handle_elicitation)
    async with client:
        print("Calling plan_dinner (runs as a background task)...")
        # call_tool drives the whole round-trip transparently: it polls, answers
        # the task's input request via handle_elicitation, and returns the result.
        result = await client.call_tool("plan_dinner", {})
        assert isinstance(result.content[0], TextContent)
        print(f"\nResult: {result.content[0].text}")


if __name__ == "__main__":
    asyncio.run(main())
