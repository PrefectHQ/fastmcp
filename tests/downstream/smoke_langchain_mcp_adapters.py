"""Drive a FastMCP server through langchain-mcp-adapters, the way LangChain users reach it.

The adapters pin `mcp<2` while FastMCP 4 needs `mcp>=2`, so they cannot share an
environment. This runs in its own environment against servers started from the
checkout, which also covers handshake-era (mcp v1) clients:

    uv run --isolated --no-project --no-config --with langchain-mcp-adapters \
        --with langchain python tests/downstream/smoke_langchain_mcp_adapters.py
"""

import asyncio
from importlib.metadata import version
from typing import Any

from _harness import (
    INSTRUCTIONS,
    LEGACY_PROXY_GAPS,
    TEMPLATE_READS,
    TOOLS,
    Report,
    quiet,
    serve,
    server_env,
    stdio_command,
)
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.callbacks import Callbacks
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.types import ElicitResult


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


async def call(tool: Any, args: dict[str, Any]) -> ToolMessage:
    """Invoke a tool the way an agent does, so the result is a ToolMessage with its artifact."""
    return await tool.ainvoke(
        {"type": "tool_call", "name": tool.name, "args": args, "id": "call-1"}
    )


async def exercise(
    report: Report, connection: dict[str, Any], gaps: dict[str, str] | None = None
) -> None:
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
    tools: dict[str, Any] = {}

    async def lists_tools() -> None:
        tools.update({tool.name: tool for tool in await client.get_tools()})
        assert TOOLS <= set(tools), sorted(tools)

    async def server_info() -> None:
        info = (await client.get_server_info(server_name="smoke"))["smoke"]
        assert info.instructions == INSTRUCTIONS, info.instructions
        assert info.serverInfo.name == "downstream-smoke", info.serverInfo

    async def agent_loop() -> None:
        agent = create_agent(scripted_model(), list(tools.values()))
        state = await agent.ainvoke(
            {"messages": [("user", "add 2 and 3, then get the Chicago forecast")]}
        )
        returned = {m.name: m.content for m in state["messages"] if m.type == "tool"}
        assert "5" in str(returned["add"]) and "sunny" in str(returned["forecast"]), (
            returned
        )

    async def structured_output() -> None:
        message = await call(tools["forecast"], {"city": "Oslo"})
        expected = {"city": "Oslo", "celsius": 21.5, "conditions": ["sunny"]}
        assert message.artifact == {"structured_content": expected}, message.artifact

    async def tool_error() -> None:
        message = await call(tools["divide"], {"a": 1, "b": 0})
        assert message.status == "error" and "divide by zero" in str(message.content), (
            message
        )

    async def image() -> None:
        [block] = (await call(tools["snapshot"], {})).content
        assert (
            block["type"] == "image"
            and block["mime_type"] == "image/png"
            and block["base64"]
        ), block

    async def mixed_content() -> None:
        blocks = (await call(tools["attachments"], {})).content
        shapes = [(b["type"], b.get("text") or b.get("url")) for b in blocks]
        assert shapes == [
            ("text", "see attached"),
            ("text", "buy milk"),
            ("file", "config://app"),
        ], shapes

    async def elicitation() -> None:
        assert "deploy: approved" in str(
            (await call(tools["confirm"], {"action": "deploy"})).content
        )

    async def progress_notifications() -> None:
        progress.clear()
        assert "counted to 3" in str((await call(tools["count_to"], {"n": 3})).content)
        assert progress == [1, 2, 3], progress

    async def log_messages() -> None:
        logs.clear()
        await call(tools["count_to"], {"n": 1})
        assert any("counted to 1" in line for line in logs), logs

    async def template_literals_and_list_queries() -> None:
        blobs = await client.get_resources("smoke", uris=list(TEMPLATE_READS))
        assert [b.as_string() for b in blobs] == list(TEMPLATE_READS.values()), blobs

    async def float_count_output_schema() -> None:
        message = await call(tools["stamp"], {})
        expected = {"structured_content": {"when": "2026-09-22T00:00:00Z"}}
        assert message.artifact == expected, message.artifact

    async def explicit_session() -> None:
        async with client.session("smoke") as session:
            assert TOOLS <= {tool.name for tool in await load_mcp_tools(session)}

    async def resources() -> None:
        [blob] = await client.get_resources("smoke", uris=["config://app"])
        assert (
            blob.as_string() == '{"mode": "smoke"}'
            and blob.mimetype == "application/json"
        ), blob

    async def resource_template() -> None:
        [blob] = await client.get_resources("smoke", uris=["greeting://nate"])
        assert blob.as_string() == "hello, nate", blob

    async def prompt() -> None:
        messages = await client.get_prompt(
            "smoke", "review", arguments={"code": "x = 1"}
        )
        assert "x = 1" in str(messages[0].content), messages

    for check in (
        lists_tools,
        server_info,
        agent_loop,
        structured_output,
        tool_error,
        image,
        mixed_content,
        elicitation,
        progress_notifications,
        explicit_session,
        resources,
        resource_template,
        prompt,
        template_literals_and_list_queries,
        float_count_output_schema,
    ):
        name = check.__name__.replace("_", " ")
        await report.check(name, check, known_gap=(gaps or {}).get(name))
    await report.check("logging", log_messages, known_gap=(gaps or {}).get("logging"))


async def main() -> None:
    report = Report(
        "langchain-mcp-adapters",
        {
            "langchain-mcp-adapters": version("langchain-mcp-adapters"),
            "langchain": version("langchain"),
            "mcp": version("mcp"),
        },
    )
    command, args = stdio_command()
    with report.transport("stdio"):
        await exercise(
            report,
            {
                "transport": "stdio",
                "command": command,
                "args": args,
                "env": server_env(),
            },
        )

    for transport, label, kind in (
        ("http", "streamable HTTP", "streamable_http"),
        ("sse", "SSE", "sse"),
        ("proxy", "FastMCP proxy", "streamable_http"),
    ):
        # The SDK's SSE reader logs a traceback when a session closes mid-stream.
        with (
            report.transport(label),
            serve(transport) as server,
            quiet("mcp.client.sse"),
        ):

            async def rejects_missing_token() -> None:
                client = MultiServerMCPClient(
                    {"smoke": {"transport": kind, "url": server.url}}
                )
                try:
                    await client.get_tools()
                except BaseException as error:
                    if isinstance(error, KeyboardInterrupt):
                        raise
                    return
                raise AssertionError("listed tools without a bearer token")

            await report.check("rejects missing bearer token", rejects_missing_token)
            await exercise(
                report,
                {"transport": kind, "url": server.url, "headers": server.headers},
                gaps=LEGACY_PROXY_GAPS if transport == "proxy" else None,
            )

    report.finish()


if __name__ == "__main__":
    asyncio.run(main())
