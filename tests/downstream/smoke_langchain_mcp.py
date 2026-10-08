"""Drive FastMCP through `langchain.mcp`, LangChain's own MCP integration.

`langchain[mcp]` depends on `fastmcp` directly: `MCPAdapter` wraps a
`fastmcp.Client` or a `fastmcp.client.group.ClientGroup` and turns their tools
into LangChain tools. This runs in its own environment with the build under
test, against servers started from the checkout:

    uv build --wheel --out-dir dist . fastmcp_slim
    uv run --isolated --no-project --no-config --with 'langchain[mcp]' \\
        --with "fastmcp @ file://$PWD/$(ls dist/fastmcp-4*.whl)" \\
        --with "fastmcp-slim @ file://$PWD/$(ls dist/fastmcp_slim-*.whl)" \\
        python tests/downstream/smoke_langchain_mcp.py
"""

import asyncio
import os
import warnings
from collections.abc import Awaitable, Callable
from importlib.metadata import version
from typing import Any

import anyio
from _harness import (
    LEGACY_PROXY_GAPS,
    PROXY_GAPS,
    TOOLS,
    Report,
    quiet,
    serve,
    server_env,
    stdio_command,
    stdio_transport,
)
from langchain.agents import create_agent
from langchain_core._api import LangChainBetaWarning
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage

import fastmcp
from fastmcp.client import Client
from fastmcp.client.group import ClientGroup
from fastmcp.mcp_config import MCPConfig

warnings.filterwarnings("ignore", category=LangChainBetaWarning)
from langchain.mcp import MCPAdapter  # noqa: E402

MODERN = "2026-07-28"


class ScriptedToolModel(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedToolModel":
        return self


def scripted_model(add: str, forecast: str) -> ScriptedToolModel:
    return ScriptedToolModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": add, "args": {"a": 2, "b": 3}, "id": "call-add"},
                        {
                            "name": forecast,
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


async def accept(message: str, response_type: Any, params: Any, context: Any) -> Any:
    return {"approved": True}


class Observed:
    """Progress and log notifications a client received."""

    def __init__(self) -> None:
        self.progress: list[float] = []
        self.logs: list[str] = []

    async def on_progress(
        self, value: float, total: float | None, message: str | None
    ) -> None:
        self.progress.append(value)

    async def on_log(self, message: Any) -> None:
        self.logs.append(str(message.data))

    def client(self, target: Any, **kwargs: Any) -> Client[Any]:
        return Client(
            target,
            elicitation_handler=accept,
            progress_handler=self.on_progress,
            log_handler=self.on_log,
            **kwargs,
        )


async def run_checks(
    report: Report,
    checks: list[Callable[[], Awaitable[object]]],
    gaps: dict[str, str] | None = None,
) -> None:
    for check in checks:
        name = check.__name__.replace("_", " ")
        await report.check(name, check, known_gap=(gaps or {}).get(name))


async def exercise(
    report: Report,
    target: Any,
    gaps: dict[str, str] | None = None,
    **client_kwargs: Any,
) -> None:
    seen = Observed()
    async with MCPAdapter(seen.client(target, **client_kwargs)) as adapter:
        tools: dict[str, Any] = {}

        async def lists_tools() -> None:
            tools.update({t.name: t for t in await adapter.list_tools()})
            assert TOOLS <= set(tools), sorted(tools)

        async def refreshes_tool_list() -> None:
            refreshed = {t.name for t in await adapter.list_tools(cache_mode="refresh")}
            assert TOOLS <= refreshed, sorted(refreshed)

        async def server_metadata() -> None:
            server = tools["add"].metadata["mcp"]["server"]
            assert server["name"] == "downstream-smoke", server

        async def agent_loop() -> None:
            agent = create_agent(
                scripted_model("add", "forecast"), list(tools.values())
            )
            state = await agent.ainvoke({"messages": [("user", "add, then forecast")]})
            returned = {
                m.name: m.content for m in state["messages"] if m.type == "tool"
            }
            assert "5" in str(returned["add"]), returned
            assert "sunny" in str(returned["forecast"]), returned

        async def structured_output() -> None:
            message = await call(tools["forecast"], {"city": "Oslo"})
            expected = {"city": "Oslo", "celsius": 21.5, "conditions": ["sunny"]}
            assert message.artifact == {"structured_content": expected}, (
                message.artifact
            )

        async def float_count_output_schema() -> None:
            message = await call(tools["stamp"], {})
            expected = {"when": "2026-09-22T00:00:00Z"}
            assert message.artifact == {"structured_content": expected}, message

        async def tool_error() -> None:
            message = await call(tools["divide"], {"a": 1, "b": 0})
            assert message.status == "error", message
            assert "divide by zero" in str(message.content), message

        async def image() -> None:
            [block] = (await call(tools["snapshot"], {})).content
            assert block["type"] == "image" and block["mime_type"] == "image/png", block

        async def mixed_content() -> None:
            blocks = (await call(tools["attachments"], {})).content
            shapes = [(b["type"], b.get("text") or b.get("url")) for b in blocks]
            assert shapes == [
                ("text", "see attached"),
                ("text", "buy milk"),
                ("file", "config://app"),
            ], shapes

        async def elicitation() -> None:
            message = await call(tools["confirm"], {"action": "deploy"})
            assert "deploy: approved" in str(message.content), message

        async def progress_notifications() -> None:
            seen.progress.clear()
            await call(tools["count_to"], {"n": 3})
            assert seen.progress == [1, 2, 3], seen.progress

        async def logging() -> None:
            seen.logs.clear()
            await call(tools["count_to"], {"n": 1})
            assert any("counted to 1" in line for line in seen.logs), seen.logs

        async def timed_out_call_leaves_connection_usable() -> None:
            try:
                with anyio.fail_after(0.5):
                    await call(tools["sleep"], {"seconds": 5})
            except TimeoutError:
                pass
            else:
                raise AssertionError("sleep(5) finished inside a 0.5s deadline")
            with anyio.fail_after(10):
                assert "2" in str((await call(tools["add"], {"a": 1, "b": 1})).content)
            await anyio.sleep(0.2)

        await run_checks(
            report,
            [
                lists_tools,
                refreshes_tool_list,
                server_metadata,
                agent_loop,
                structured_output,
                float_count_output_schema,
                tool_error,
                image,
                mixed_content,
                elicitation,
                progress_notifications,
                logging,
                timed_out_call_leaves_connection_usable,
            ],
            gaps,
        )


async def exercise_group(report: Report, legacy_url: str, token: str) -> None:
    """One adapter over a ClientGroup: a handshake-era HTTP server and a modern stdio one."""
    command, args = stdio_command()
    legacy, modern = Observed(), Observed()
    group = ClientGroup(
        {
            "legacy": legacy.client(legacy_url, auth=token, mode="legacy"),
            "modern": modern.client(stdio_transport(), mode="auto"),
        }
    )
    async with MCPAdapter(group) as adapter:
        tools: dict[str, Any] = {}

        async def lists_prefixed_tools() -> None:
            tools.update({t.name: t for t in await adapter.list_tools()})
            expected = {
                f"{server}_{name}" for server in ("legacy", "modern") for name in TOOLS
            }
            assert expected <= set(tools), sorted(tools)

        async def each_member_keeps_its_protocol_era() -> None:
            members = adapter.client.clients  # ty: ignore[unresolved-attribute]
            assert members["legacy"].protocol_version not in (None, MODERN), members
            assert members["modern"].protocol_version == MODERN, members

        async def agent_loop_across_servers() -> None:
            model = scripted_model("legacy_add", "modern_forecast")
            agent = create_agent(model, list(tools.values()))
            state = await agent.ainvoke({"messages": [("user", "add, then forecast")]})
            returned = {
                m.name: m.content for m in state["messages"] if m.type == "tool"
            }
            assert "5" in str(returned["legacy_add"]), returned
            assert "sunny" in str(returned["modern_forecast"]), returned

        async def elicitation_in_both_eras() -> None:
            for server in ("legacy", "modern"):
                message = await call(tools[f"{server}_confirm"], {"action": server})
                assert f"{server}: approved" in str(message.content), (server, message)

        async def calls_route_to_their_own_server() -> None:
            legacy.progress.clear()
            modern.progress.clear()
            await call(tools["modern_count_to"], {"n": 2})
            assert modern.progress == [1, 2] and legacy.progress == [], (
                legacy.progress,
                modern.progress,
            )

        async def tool_error_from_one_member() -> None:
            message = await call(tools["legacy_divide"], {"a": 1, "b": 0})
            assert message.status == "error", message

        await run_checks(
            report,
            [
                lists_prefixed_tools,
                each_member_keeps_its_protocol_era,
                agent_loop_across_servers,
                elicitation_in_both_eras,
                calls_route_to_their_own_server,
                tool_error_from_one_member,
            ],
        )


async def main() -> None:
    report = Report(
        "langchain.mcp",
        {
            "fastmcp": fastmcp.__version__,
            "langchain": version("langchain"),
            "mcp": version("mcp"),
        },
    )
    command, args = stdio_command()

    async def installed_build_is_under_test() -> None:
        expected = os.environ.get("FASTMCP_EXPECTED_VERSION")
        assert expected in (None, fastmcp.__version__), (
            f"expected {expected}, got {fastmcp.__version__}"
        )

    with report.transport("in-process FastMCP server"):
        from server import build

        await report.check(
            "installed build is under test", installed_build_is_under_test
        )
        await exercise(report, build())

    with report.transport("stdio"):
        await exercise(report, stdio_transport())

    with report.transport("MCPConfig"):

        async def adapter_from_config() -> None:
            config = MCPConfig.from_dict(
                {
                    "mcpServers": {
                        "smoke": {"command": command, "args": args, "env": server_env()}
                    }
                }
            )
            async with MCPAdapter(config) as adapter:
                names = {t.name for t in await adapter.list_tools()}
            assert any(name.endswith("add") for name in names), sorted(names)

        await report.check("adapter from MCPConfig", adapter_from_config)

    for transport, label, client_kwargs, gaps in (
        ("http", "streamable HTTP", {}, None),
        ("sse", "SSE", {}, None),
        ("proxy", "FastMCP proxy", {}, PROXY_GAPS),
        (
            "proxy",
            "FastMCP proxy, handshake-era client",
            {"mode": "legacy"},
            LEGACY_PROXY_GAPS,
        ),
    ):
        # The SDK's SSE reader logs a traceback when a check cancels a call on purpose.
        with (
            report.transport(label),
            serve(transport) as server,
            quiet("mcp.client.sse"),
        ):
            await exercise(
                report, server.url, gaps=gaps, auth=server.token, **client_kwargs
            )

    with (
        report.transport("ClientGroup: legacy HTTP + modern stdio"),
        serve("http") as server,
    ):
        await exercise_group(report, server.url, server.token)

    report.finish()


if __name__ == "__main__":
    asyncio.run(main())
