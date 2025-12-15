# /// script
# dependencies = ["anthropic", "fastmcp", "rich"]
# ///
"""
Structured Output Sampling

Demonstrates using `result_type` to get validated Pydantic models from an LLM.
The server exposes a sentiment analysis tool that returns structured data.

Run:
    uv run examples/sampling/structured_output.py
"""

import asyncio

from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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


# Define a structured output model
class SentimentAnalysis(BaseModel):
    sentiment: str  # "positive", "negative", or "neutral"
    confidence: float  # 0.0 to 1.0
    keywords: list[str]  # Key words that influenced the analysis
    explanation: str  # Brief explanation of the analysis


# Create the MCP server
mcp = FastMCP("Sentiment Analyzer")


@mcp.tool
async def analyze_sentiment(text: str, ctx: Context) -> dict:
    """Analyze the sentiment of the given text."""
    console.print("    [bold cyan]SERVER[/] Analyzing sentiment...")

    result = await ctx.sample(
        messages=f"Analyze the sentiment of this text:\n\n{text}",
        system_prompt="You are a sentiment analysis expert. Analyze text carefully.",
        result_type=SentimentAnalysis,
    )

    console.print("    [bold cyan]SERVER[/] Analysis complete")
    return result.result.model_dump()  # type: ignore[attr-defined]


async def main():
    console.print(
        Panel.fit("[bold]MCP Sampling Flow Demo[/]", subtitle="structured_output.py")
    )
    console.print()

    handler = LoggingAnthropicHandler(default_model="claude-sonnet-4-5")

    async with Client(mcp, sampling_handler=handler) as client:
        texts = [
            "I absolutely love this product! It exceeded all my expectations.",
            "The service was okay, nothing special but got the job done.",
            "This is the worst experience I've ever had. Never again.",
        ]

        for text in texts:
            console.print(f"[bold green]CLIENT[/] Analyzing: [italic]{text[:50]}...[/]")
            console.print()

            result = await client.call_tool("analyze_sentiment", {"text": text})
            data = result.data

            # Display results in a table
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column(style="bold")
            table.add_column()

            sentiment_color = {
                "positive": "green",
                "negative": "red",
                "neutral": "yellow",
            }.get(
                data["sentiment"],
                "white",  # type: ignore[union-attr]
            )
            table.add_row("Sentiment", f"[{sentiment_color}]{data['sentiment']}[/]")  # type: ignore[index]
            table.add_row("Confidence", f"{data['confidence']:.0%}")  # type: ignore[index]
            table.add_row("Keywords", ", ".join(data["keywords"]))  # type: ignore[index]
            table.add_row("Explanation", data["explanation"])  # type: ignore[index]

            console.print(Panel(table, border_style=sentiment_color))
            console.print()


if __name__ == "__main__":
    asyncio.run(main())
