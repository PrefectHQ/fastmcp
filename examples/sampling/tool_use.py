# /// script
# dependencies = ["anthropic", "fastmcp", "rich"]
# ///
"""
Sampling with Tools

Demonstrates giving an LLM tools to use during sampling. The LLM can call
helper functions to gather information before responding.

Run:
    uv run examples/sampling/tool_use.py
"""

import asyncio
import random
from datetime import datetime

from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import SamplingMessage, SamplingParams
from fastmcp.client.sampling.handlers.anthropic import AnthropicSamplingHandler

console = Console()


class LoggingAnthropicHandler(AnthropicSamplingHandler):
    async def __call__(
        self, messages: list[SamplingMessage], params: SamplingParams, context
    ):  # type: ignore[override]
        console.print("      [bold blue]SAMPLING[/] Calling Claude API...")
        result = await super().__call__(messages, params, context)
        console.print("      [bold blue]SAMPLING[/] Response received")
        return result


# Define tools available to the LLM during sampling
def add(a: float, b: float) -> str:
    """Add two numbers together."""
    result = a + b
    console.print(f"        [bold magenta]TOOL[/] add({a}, {b}) = {result}")
    return str(result)


def multiply(a: float, b: float) -> str:
    """Multiply two numbers together."""
    result = a * b
    console.print(f"        [bold magenta]TOOL[/] multiply({a}, {b}) = {result}")
    return str(result)


def get_current_time() -> str:
    """Get the current date and time."""
    console.print("        [bold magenta]TOOL[/] get_current_time()")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def roll_dice(sides: int = 6) -> str:
    """Roll a dice with the specified number of sides."""
    result = random.randint(1, sides)
    console.print(f"        [bold magenta]TOOL[/] roll_dice({sides}) = {result}")
    return str(result)


# Structured output for the response
class AssistantResponse(BaseModel):
    answer: str = Field(description="The answer to the user's question")
    tools_used: list[str] = Field(description="List of tools that were used")
    reasoning: str = Field(
        description="Brief explanation of how the answer was determined"
    )


# Create the MCP server
mcp = FastMCP("Smart Assistant")


@mcp.tool
async def ask_assistant(question: str, ctx: Context) -> dict:
    """Ask the assistant a question. It can use tools to help answer."""
    console.print("    [bold cyan]SERVER[/] Processing question...")

    result = await ctx.sample(
        messages=question,
        system_prompt="You are a helpful assistant with access to tools. Use them when needed to answer questions accurately.",
        tools=[add, multiply, get_current_time, roll_dice],
        result_type=AssistantResponse,
    )

    console.print("    [bold cyan]SERVER[/] Response ready")
    return result.result.model_dump()  # type: ignore[attr-defined]


async def main():
    console.print(Panel.fit("[bold]MCP Sampling Flow Demo[/]", subtitle="tool_use.py"))
    console.print()

    handler = LoggingAnthropicHandler(default_model="claude-sonnet-4-5")

    async with Client(mcp, sampling_handler=handler) as client:
        questions = [
            "What is 15 times 7, plus 23?",
            "Roll a 20-sided dice for me",
            "What time is it right now?",
        ]

        for question in questions:
            console.print(f"[bold green]CLIENT[/] Question: {question}")
            console.print()

            result = await client.call_tool("ask_assistant", {"question": question})
            data = result.data

            console.print(f"[bold green]CLIENT[/] Answer: {data['answer']}")  # type: ignore[index]
            console.print(
                f"         Tools used: {', '.join(data['tools_used']) or 'none'}"
            )  # type: ignore[index]
            console.print(f"         Reasoning: {data['reasoning']}")  # type: ignore[index]
            console.print()


if __name__ == "__main__":
    asyncio.run(main())
