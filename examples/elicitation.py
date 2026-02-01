"""
FastMCP Elicitation Example

Demonstrates tools that ask users for input during execution.

Try it with the CLI:

    fastmcp list examples/elicitation.py
    fastmcp call examples/elicitation.py greet
    fastmcp call examples/elicitation.py survey
"""

from dataclasses import dataclass

from fastmcp import Context, FastMCP

mcp = FastMCP("Elicitation Demo")


@mcp.tool
async def greet(ctx: Context) -> str:
    """Greet the user by name (asks for their name)."""
    result = await ctx.elicit("What is your name?", response_type=str)

    if result.action == "accept":
        return f"Hello, {result.data}!"
    return "Maybe next time!"


@mcp.tool
async def survey(ctx: Context) -> str:
    """Run a short survey collecting structured info."""

    @dataclass
    class SurveyResponse:
        favorite_color: str
        lucky_number: int

    result = await ctx.elicit(
        "Quick survey — tell us about yourself:",
        response_type=SurveyResponse,
    )

    if result.action == "accept":
        resp = result.data
        return f"Got it — you like {resp.favorite_color} and your lucky number is {resp.lucky_number}."
    return "Survey skipped."
