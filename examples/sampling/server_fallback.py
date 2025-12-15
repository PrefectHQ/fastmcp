# /// script
# dependencies = ["anthropic", "fastmcp", "rich"]
# ///
"""
Server-Side Fallback Handler

Demonstrates configuring a sampling handler on the server. This ensures
sampling works even when the client doesn't provide a handler.

The server runs as an HTTP server that can be connected to by any MCP client.

Run:
    uv run examples/sampling/server_fallback.py

Then connect with any MCP client (e.g., Claude Desktop) or test with:
    curl http://localhost:8000/mcp/
"""

import asyncio

from rich.console import Console
from rich.panel import Panel

from fastmcp import FastMCP
from fastmcp.client.sampling.handlers.anthropic import AnthropicSamplingHandler
from fastmcp.server.context import Context

console = Console()


# Create server with a fallback sampling handler
# This handler is used when the client doesn't support sampling
mcp = FastMCP(
    "Server with Fallback Handler",
    sampling_handler=AnthropicSamplingHandler(default_model="claude-sonnet-4-5"),
    sampling_handler_behavior="fallback",  # Use only if client lacks sampling
)


@mcp.tool
async def summarize(text: str, ctx: Context) -> str:
    """Summarize the given text."""
    console.print(f"[bold cyan]SERVER[/] Summarizing text ({len(text)} chars)...")

    result = await ctx.sample(
        messages=f"Summarize this text in 1-2 sentences:\n\n{text}",
        system_prompt="You are a concise summarizer.",
        max_tokens=150,
    )

    console.print("[bold cyan]SERVER[/] Summary complete")
    return result.text or ""


@mcp.tool
async def translate(text: str, target_language: str, ctx: Context) -> str:
    """Translate text to the target language."""
    console.print(f"[bold cyan]SERVER[/] Translating to {target_language}...")

    result = await ctx.sample(
        messages=f"Translate to {target_language}:\n\n{text}",
        system_prompt=f"You are a translator. Output only the {target_language} translation.",
        max_tokens=500,
    )

    console.print("[bold cyan]SERVER[/] Translation complete")
    return result.text or ""


async def main():
    console.print(
        Panel.fit(
            "[bold]Server-Side Fallback Handler Demo[/]\n\n"
            "This server has a built-in Anthropic handler that activates\n"
            "when clients don't provide their own sampling support.",
            subtitle="server_fallback.py",
        )
    )
    console.print()
    console.print("[bold yellow]Starting HTTP server on http://localhost:8000[/]")
    console.print("Connect with an MCP client or press Ctrl+C to stop")
    console.print()

    await mcp.run_http_async(host="localhost", port=8000)


if __name__ == "__main__":
    asyncio.run(main())
