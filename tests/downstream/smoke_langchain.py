# /// script
# requires-python = ">=3.10"
# dependencies = ["langchain-mcp-adapters", "langchain"]
# ///
"""Exercise a FastMCP server through langchain-mcp-adapters.

The adapters pin `mcp<2` while FastMCP 4 needs `mcp>=2`, so they cannot share an
environment. This script runs in its own environment and reaches the server
over the wire, the way LangChain users do:

    uv run tests/downstream/smoke_langchain.py
"""

import asyncio
from typing import Any

from _http import SERVER, http_server
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_mcp_adapters.callbacks import Callbacks
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.types import ElicitResult

REPO = SERVER.parents[2]
FASTMCP_PYTHON = ["uv", "run", "--project", str(REPO), "--frozen", "python"]


class ScriptedToolModel(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedToolModel":
        return self


def scripted_model() -> ScriptedToolModel:
    return ScriptedToolModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "add", "args": {"a": 2, "b": 3}, "id": "call-add"},
                        {
                            "name": "forecast",
                            "args": {"city": "Chicago", "days": 2},
                            "id": "call-forecast",
                        },
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )


async def exercise(label: str, connection: dict[str, Any]) -> None:
    progress: list[float] = []
    logs: list[str] = []

    async def on_progress(
        value: float, total: float | None, message: str | None, context: Any
    ) -> None:
        progress.append(value)

    async def on_log(params: Any, context: Any) -> None:
        logs.append(str(params.data))

    async def on_elicitation(
        mcp_context: Any, params: Any, context: Any
    ) -> ElicitResult:
        return ElicitResult(action="accept", content={"approved": True})

    client = MultiServerMCPClient(
        {"smoke": connection},
        callbacks=Callbacks(
            on_progress=on_progress,
            on_logging_message=on_log,
            on_elicitation=on_elicitation,
        ),
    )

    tools = {tool.name: tool for tool in await client.get_tools()}
    assert {"add", "forecast", "divide", "count_to", "confirm"} <= set(tools), tools

    agent = create_agent(scripted_model(), list(tools.values()))
    state = await agent.ainvoke(
        {"messages": [("user", "add 2 and 3, then get the Chicago forecast")]}
    )
    returned = {m.name: m.content for m in state["messages"] if m.type == "tool"}
    assert "5" in str(returned["add"]), returned
    assert "sunny" in str(returned["forecast"]), returned

    assert "42" in str(await tools["add"].ainvoke({"a": 40, "b": 2}))
    failed = await tools["divide"].ainvoke(
        {
            "type": "tool_call",
            "name": "divide",
            "args": {"a": 1, "b": 0},
            "id": "call-divide",
        }
    )
    assert failed.status == "error" and "divide by zero" in str(failed.content), failed

    assert "deploy: approved" in str(
        await tools["confirm"].ainvoke({"action": "deploy"})
    )
    assert "counted to 3" in str(await tools["count_to"].ainvoke({"n": 3}))
    assert progress == [1, 2, 3], progress
    assert any("counted to 3" in line for line in logs), logs

    async with client.session("smoke") as session:
        assert {"add", "forecast"} <= {
            tool.name for tool in await load_mcp_tools(session)
        }

    blobs = await client.get_resources(
        "smoke", uris=["config://app", "greeting://nate"]
    )
    assert [blob.as_string() for blob in blobs] == [
        '{"mode": "smoke"}',
        "hello, nate",
    ], blobs

    messages = await client.get_prompt("smoke", "review", arguments={"code": "x = 1"})
    assert "x = 1" in str(messages[0].content), messages
    print(f"ok  langchain via {label}")


async def main() -> None:
    from importlib.metadata import version

    print(
        f"langchain-mcp-adapters {version('langchain-mcp-adapters')}, mcp {version('mcp')}"
    )
    await exercise(
        "stdio",
        {
            "transport": "stdio",
            "command": FASTMCP_PYTHON[0],
            "args": [*FASTMCP_PYTHON[1:], str(SERVER), "stdio"],
        },
    )
    with http_server(FASTMCP_PYTHON) as url:
        await exercise("streamable HTTP", {"transport": "streamable_http", "url": url})


if __name__ == "__main__":
    asyncio.run(main())
