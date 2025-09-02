# /// script
# dependencies = ["openai", "fastmcp"]
# ///

import asyncio
import os

from mcp.types import ContentBlock
from openai import OpenAI

from fastmcp import FastMCP
from fastmcp.experimental.sampling.handlers.openai import OpenAISamplingHandler
from fastmcp.server.context import Context


async def async_main():
    server = FastMCP(
        name="OpenAI Sampling Fallback Example",
        sampling_handler=OpenAISamplingHandler(
            default_model=os.getenv("MODEL") or "gpt-4o-mini",  # pyright: ignore[reportArgumentType]
            client=OpenAI(
                api_key=os.getenv("API_KEY"),
                base_url=os.getenv("BASE_URL"),
            ),
        ),
    )

    @server.tool
    async def test_sample_fallback(ctx: Context) -> ContentBlock:
        return await ctx.sample(
            messages=["hello world!"],
        )

    await server.run_http_async()


if __name__ == "__main__":
    asyncio.run(async_main())
