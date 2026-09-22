"""Exercise FastMCP through pydantic-ai's MCPToolset, which is built on fastmcp.Client.

Runs inside the FastMCP project environment with pydantic-ai layered on top:

    uv run --with 'pydantic-ai-slim[mcp]' tests/downstream/smoke_pydantic_ai.py
"""

import asyncio
import sys
from typing import Any

from _http import SERVER, http_server
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from server import mcp

import fastmcp


def scripted_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Call add and forecast on the first turn, then answer with what they returned."""
    returns = [
        p
        for m in messages
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolReturnPart)
    ]
    if not returns:
        return ModelResponse(
            parts=[
                ToolCallPart("add", {"a": 2, "b": 3}),
                ToolCallPart("forecast", {"city": "Chicago", "days": 2}),
            ]
        )
    return ModelResponse(parts=[TextPart(" | ".join(str(p.content) for p in returns))])


async def accept(
    message: str, response_type: Any, params: Any, context: Any
) -> dict[str, bool]:
    return {"approved": True}


async def exercise(label: str, target: Any) -> None:
    progress: list[float] = []
    logs: list[str] = []

    async def on_progress(
        value: float, total: float | None, message: str | None
    ) -> None:
        progress.append(value)

    async def on_log(message: Any) -> None:
        logs.append(str(message.data))

    toolset = MCPToolset(
        target,
        elicitation_handler=accept,
        progress_handler=on_progress,
        log_handler=on_log,
    )
    async with toolset:
        names = {tool.name for tool in await toolset.list_tools()}
        assert {"add", "forecast", "divide", "count_to", "confirm"} <= names, names

        agent = Agent(FunctionModel(scripted_model), toolsets=[toolset])
        result = await agent.run("add 2 and 3, then get the Chicago forecast")
        assert "5" in result.output and "sunny" in result.output, result.output

        assert await toolset.direct_call_tool("add", {"a": 40, "b": 2}) == 42
        forecast = await toolset.direct_call_tool("forecast", {"city": "Oslo"})
        assert forecast == {"city": "Oslo", "celsius": 21.5, "conditions": ["sunny"]}, (
            forecast
        )

        try:
            await toolset.direct_call_tool("divide", {"a": 1, "b": 0})
        except ModelRetry as error:
            assert "divide by zero" in str(error), error
        else:
            raise AssertionError("divide by zero did not raise")

        assert (
            await toolset.direct_call_tool("confirm", {"action": "deploy"})
            == "deploy: approved"
        )

        assert await toolset.direct_call_tool("count_to", {"n": 3}) == "counted to 3"
        assert progress == [1, 2, 3], progress
        assert any("counted to 3" in line for line in logs), logs

        assert "config://app" in {str(r.uri) for r in await toolset.list_resources()}
        assert await toolset.read_resource("config://app") == '{"mode": "smoke"}'
        assert await toolset.read_resource("greeting://nate") == "hello, nate"

        prompt = await toolset.get_prompt("review", {"code": "x = 1"})
        assert "x = 1" in str(prompt.messages[0].content), prompt
    print(f"ok  pydantic-ai via {label}")


async def main() -> None:
    import pydantic_ai

    print(f"fastmcp {fastmcp.__version__}, pydantic-ai {pydantic_ai.__version__}")
    await exercise("in-process FastMCP server", mcp)
    await exercise("stdio", str(SERVER))
    with http_server([sys.executable]) as url:
        await exercise("streamable HTTP", url)


if __name__ == "__main__":
    asyncio.run(main())
