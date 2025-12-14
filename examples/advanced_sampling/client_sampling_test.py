"""
Client Sampling Test

This example demonstrates advanced sampling features using a Client with an
OpenAI handler. It tests:
- Primitive result_type (int, list[str]) with automatic schema wrapping
- The sample_step() method for fine-grained loop control
- History tracking including assistant messages
- Tool execution with manual control

Prerequisites:
    pip install fastmcp[openai]
    export OPENAI_API_KEY=your-key

Run:
    python examples/advanced_sampling/client_sampling_test.py
"""

import asyncio

from pydantic import BaseModel

from fastmcp import Client, Context, FastMCP
from fastmcp.experimental.sampling.handlers.openai import OpenAISamplingHandler

# Create the MCP server
mcp = FastMCP("Sampling Test Server")


# --- Test 1: Primitive result_type (int) ---
@mcp.tool
async def count_vowels(text: str, ctx: Context) -> int:
    """Count the number of vowels in the given text using LLM sampling."""
    result = await ctx.sample(
        messages=f"Count the number of vowels (a, e, i, o, u) in this text and return just the count as an integer:\n\n{text}",
        system_prompt="You are a precise counting assistant. Return only the numeric count.",
        result_type=int,
    )
    return result.result


# --- Test 2: Primitive result_type (list[str]) ---
@mcp.tool
async def extract_keywords(text: str, ctx: Context) -> list[str]:
    """Extract keywords from the given text."""
    result = await ctx.sample(
        messages=f"Extract the 3 most important keywords from this text:\n\n{text}",
        system_prompt="You are a keyword extraction expert. Return a list of keywords.",
        result_type=list[str],
    )
    return result.result


# --- Test 3: sample_step() with history tracking ---
@mcp.tool
async def multi_step_reasoning(question: str, ctx: Context) -> str:
    """Demonstrate multi-step reasoning with sample_step()."""

    def think(thought: str) -> str:
        """Record a thought in the reasoning chain."""
        return f"Thought recorded: {thought}"

    # First step: initial reasoning
    messages: list[str] = [question]
    step_count = 0
    max_steps = 5

    while step_count < max_steps:
        step = await ctx.sample_step(
            messages=messages,
            tools=[think],
            system_prompt="Think step by step. Use the think() tool to record your reasoning, then provide your final answer.",
        )

        step_count += 1

        # History should always include the assistant's response
        assert len(step.history) > len(messages), (
            "History should grow with assistant message"
        )

        if not step.is_tool_use:
            return f"Answer (after {step_count} steps): {step.text or ''}"

        # Continue with updated history
        messages = step.history

    return "Max steps reached without final answer"


# --- Test 4: Structured output with Pydantic model ---
class AnalysisResult(BaseModel):
    main_topic: str
    sentiment: str
    word_count: int


@mcp.tool
async def analyze_text(text: str, ctx: Context) -> dict:
    """Analyze text and return structured results."""
    result = await ctx.sample(
        messages=f"Analyze this text:\n\n{text}",
        system_prompt="Analyze the text and provide structured results.",
        result_type=AnalysisResult,
    )
    return result.result.model_dump()


async def main():
    sampling_handler = OpenAISamplingHandler(default_model="gpt-4o-mini")

    async with Client(mcp, sampling_handler=sampling_handler) as client:
        print("=" * 60)
        print("Test 1: Primitive result_type (int)")
        print("=" * 60)
        result = await client.call_tool(
            "count_vowels",
            {"text": "Hello, world!"},
        )
        print(f"  Vowel count: {result.data}")
        print()

        print("=" * 60)
        print("Test 2: Primitive result_type (list[str])")
        print("=" * 60)
        result = await client.call_tool(
            "extract_keywords",
            {
                "text": "FastMCP is a Python framework for building Model Context Protocol servers and clients."
            },
        )
        print(f"  Keywords: {result.data}")
        print()

        print("=" * 60)
        print("Test 3: sample_step() with history tracking")
        print("=" * 60)
        result = await client.call_tool(
            "multi_step_reasoning",
            {"question": "What is 15 + 27? Think step by step."},
        )
        print(f"  Result: {result.data}")
        print()

        print("=" * 60)
        print("Test 4: Structured output (Pydantic model)")
        print("=" * 60)
        result = await client.call_tool(
            "analyze_text",
            {"text": "I really enjoyed learning about FastMCP today!"},
        )
        print(f"  Analysis: {result.data}")
        print()

        print("All tests completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
