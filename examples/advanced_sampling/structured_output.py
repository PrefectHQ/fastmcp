"""
Structured Output Example

This example demonstrates using `result_type` to get structured responses from an LLM.
The server exposes a tool that uses sampling to analyze text sentiment, returning
a structured Pydantic model instead of raw text.

Prerequisites:
    pip install fastmcp[openai]
    export OPENAI_API_KEY=your-key

Run:
    python examples/advanced_sampling/structured_output.py
"""

import asyncio

from pydantic import BaseModel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling.handlers.openai import OpenAISamplingHandler


# Define a structured output model
class SentimentAnalysis(BaseModel):
    sentiment: str  # "positive", "negative", or "neutral"
    confidence: float  # 0.0 to 1.0
    keywords: list[str]  # Key words that influenced the analysis


# Create an MCP server with a sampling tool
mcp = FastMCP("Sentiment Analyzer")


@mcp.tool
async def analyze_sentiment(text: str, ctx: Context) -> dict:
    """Analyze the sentiment of the given text."""
    result = await ctx.sample(
        messages=f"Analyze the sentiment of this text:\n\n{text}",
        system_prompt="You are a sentiment analysis expert. Analyze text and return structured results.",
        result_type=SentimentAnalysis,
    )
    # result.result is a validated SentimentAnalysis instance
    return result.result.model_dump()  # type: ignore[attr-defined]


async def main():
    # Create an OpenAI-backed sampling handler
    sampling_handler = OpenAISamplingHandler(default_model="gpt-4o-mini")

    # Connect to the server with the sampling handler
    async with Client(mcp, sampling_handler=sampling_handler) as client:
        # Call the tool with some test text
        result = await client.call_tool(
            "analyze_sentiment",
            {
                "text": "I absolutely love this product! It exceeded all my expectations."
            },
        )

        print("Analysis Result:")
        print(f"  Sentiment: {result.data['sentiment']}")
        print(f"  Confidence: {result.data['confidence']:.1%}")
        print(f"  Keywords: {', '.join(result.data['keywords'])}")


if __name__ == "__main__":
    asyncio.run(main())
