"""
Example of using sampling to request an LLM completion via Marvin
"""

import asyncio

import marvin
from mcp.types import TextContent

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams

# -- Create a server that sends a sampling request to the LLM

mcp = FastMCP("Sampling Example")


@mcp.tool
async def example_tool(prompt: str, context: Context) -> str:
    """Sample a completion from the LLM."""
    response = await context.sample(
        "What is your favorite programming language?",
        system_prompt="You love languages named after snakes.",
    )
    assert isinstance(response, TextContent)
    return response.text


# -- Create a client that can handle the sampling request


async def sampling_fn(
    messages: list[SamplingMessage],
    params: SamplingParams,
    ctx: RequestContext,
) -> str:
    return await marvin.say_async(
        message=[m.content.text for m in messages],
        instructions=params.systemPrompt,
    )


async def run():
    async with Client(mcp, sampling_handler=sampling_fn) as client:
        result = await client.call_tool(
            "example_tool", {"prompt": "What is the best programming language?"}
        )
        print(result)


if __name__ == "__main__":
    asyncio.run(run())
