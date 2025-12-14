"""
Tool Use Example

This example demonstrates sampling with tools, where the LLM can use helper
functions to complete a task. The server exposes a "research assistant" tool
that uses sampling with search capabilities.

Prerequisites:
    pip install fastmcp[openai]
    export OPENAI_API_KEY=your-key

Run:
    python examples/advanced_sampling/tool_use.py
"""

import asyncio

from pydantic import BaseModel, Field

from fastmcp import Client, Context, FastMCP
from fastmcp.experimental.sampling.handlers.openai import OpenAISamplingHandler


# Define tools (available to the LLM during sampling)
def search_web(query: str) -> str:
    """Search the web for information."""
    # Simulated search results
    results = {
        "python async": "Python's asyncio provides async/await syntax for concurrent code.",
        "fastmcp": "FastMCP is a framework for building MCP servers and clients in Python.",
        "mcp protocol": "MCP (Model Context Protocol) enables AI models to use tools and resources.",
    }
    for key, value in results.items():
        if key in query.lower():
            return value
    return f"No results found for: {query}"


def get_word_count(text: str) -> str:
    """Count words in text."""
    return str(len(text.split()))


# Structured output for the final response
class ResearchReport(BaseModel):
    summary: str
    sources_used: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


# Create the MCP server
mcp = FastMCP("Research Assistant")


@mcp.tool
async def research(question: str, ctx: Context) -> dict:
    """Research a question using available tools and return a structured report."""
    result = await ctx.sample(
        messages=f"Research this question and provide a comprehensive answer:\n\n{question}",
        system_prompt="You are a research assistant. Use the available tools to gather information, then call final_response with your structured report.",
        tools=[search_web, get_word_count],
        result_type=ResearchReport,
    )

    return result.result.model_dump()  # type: ignore[attr-defined]


async def main():
    sampling_handler = OpenAISamplingHandler(default_model="gpt-4o-mini")

    async with Client(mcp, sampling_handler=sampling_handler) as client:
        result = await client.call_tool(
            "research",
            {"question": "What is FastMCP and how does it relate to the MCP protocol?"},
        )

        print("Research Report:")
        print(f"  Summary: {result.data['summary']}")
        print(f"  Sources: {', '.join(result.data['sources_used'])}")
        print(f"  Confidence: {result.data['confidence'] * 100:.0f}%")


if __name__ == "__main__":
    asyncio.run(main())
