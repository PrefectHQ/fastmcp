# /// script
# dependencies = ["anthropic", "fastmcp", "rich"]
# ///
"""
Simple Text Sampling

Demonstrates the basic MCP sampling flow where a server tool requests
an LLM completion from the client.

Run:
    uv run examples/sampling/text.py
"""

import asyncio

from rich.console import Console
from rich.panel import Panel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import SamplingMessage, SamplingParams
from fastmcp.client.sampling.handlers.anthropic import AnthropicSamplingHandler

console = Console()


# Create a wrapper handler that logs when the LLM is called
class LoggingAnthropicHandler(AnthropicSamplingHandler):
    async def __call__(
        self, messages: list[SamplingMessage], params: SamplingParams, context
    ):  # type: ignore[override]
        console.print("      [bold blue]SAMPLING[/] Calling Claude API...")
        result = await super().__call__(messages, params, context)
        console.print("      [bold blue]SAMPLING[/] Response received")
        return result


# Create the MCP server
mcp = FastMCP("Haiku Generator")


@mcp.tool
async def write_haiku(topic: str, ctx: Context) -> str:
    """Write a haiku about any topic."""
    console.print(
        f"    [bold cyan]SERVER[/] Tool 'write_haiku' called with topic: {topic}"
    )

    result = await ctx.sample(
        messages=f"Write a haiku about: {topic}",
        system_prompt="You are a poet. Write only the haiku, nothing else.",
        max_tokens=100,
    )

    console.print("    [bold cyan]SERVER[/] Returning haiku to client")
    return result.text or ""


async def main():
    console.print(Panel.fit("[bold]MCP Sampling Flow Demo[/]", subtitle="text.py"))
    console.print()

    # Create the sampling handler
    handler = LoggingAnthropicHandler(default_model="claude-sonnet-4-5")

    # Connect client to server with the sampling handler
    async with Client(mcp, sampling_handler=handler) as client:
        console.print("[bold green]CLIENT[/] Calling tool 'write_haiku'...")
        console.print()

        result = await client.call_tool("write_haiku", {"topic": "Python programming"})

        console.print()
        console.print("[bold green]CLIENT[/] Received result:")
        console.print(Panel(result.data, title="Haiku", border_style="green"))  # type: ignore[arg-type]


if __name__ == "__main__":
    asyncio.run(main())
