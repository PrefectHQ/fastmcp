"""Drive FastMCP through pydantic-ai's MCPToolset, as a pydantic-ai user installs it.

`pydantic-ai-slim[mcp]` depends on the client-only `fastmcp-slim[client]`, so
this runs in its own environment with no FastMCP server dependencies, resolved
together with the build under test. The servers run from the checkout:

    uv build --wheel --out-dir dist fastmcp_slim
    uv run --isolated --no-project --with 'pydantic-ai-slim[mcp]' \
        --with "fastmcp-slim[client] @ file://$PWD/$(ls dist/fastmcp_slim-*.whl)" \
        python tests/downstream/smoke_pydantic_ai.py
"""

import asyncio
import json
import os
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any

import anyio
from _harness import (
    INSTRUCTIONS,
    PROXY_GAPS,
    TEMPLATE_READS,
    TOOLS,
    Report,
    quiet,
    serve,
    server_env,
    stdio_command,
    stdio_transport,
)
from pydantic_ai import Agent, BinaryContent, ModelRetry
from pydantic_ai.mcp import MCPToolset, load_mcp_toolsets
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

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
        # load_mcp_toolsets prefixes tool names with the server's config key.
        name = {t.name.rsplit("_", 1)[-1]: t.name for t in info.function_tools}
        name.update({t.name: t.name for t in info.function_tools})
        return ModelResponse(
            parts=[
                ToolCallPart(name["add"], {"a": 2, "b": 3}),
                ToolCallPart(name["forecast"], {"city": "Chicago", "days": 2}),
            ]
        )
    return ModelResponse(parts=[TextPart(" | ".join(str(p.content) for p in returns))])


async def accept(
    message: str, response_type: Any, params: Any, context: Any
) -> dict[str, bool]:
    return {"approved": True}


async def exercise(
    report: Report,
    target: Any,
    headers: dict[str, str] | None = None,
    gaps: dict[str, str] | None = None,
) -> None:
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
        headers=headers,
        include_instructions=True,
        elicitation_handler=accept,
        progress_handler=on_progress,
        log_handler=on_log,
    )
    async with toolset:

        async def lists_tools() -> None:
            names = {tool.name for tool in await toolset.list_tools()}
            assert TOOLS <= names, names

        async def instructions() -> None:
            assert toolset.instructions == INSTRUCTIONS, toolset.instructions

        async def agent_loop() -> None:
            agent = Agent(FunctionModel(scripted_model), toolsets=[toolset])
            result = await agent.run("add 2 and 3, then get the Chicago forecast")
            assert "5" in result.output and "sunny" in result.output, result.output

        async def structured_output() -> None:
            forecast = await toolset.direct_call_tool("forecast", {"city": "Oslo"})
            assert forecast == {
                "city": "Oslo",
                "celsius": 21.5,
                "conditions": ["sunny"],
            }, forecast

        async def tool_error() -> None:
            try:
                await toolset.direct_call_tool("divide", {"a": 1, "b": 0})
            except ModelRetry as error:
                assert "divide by zero" in str(error), error
            else:
                raise AssertionError("divide by zero did not raise ModelRetry")

        async def image() -> None:
            result = await toolset.direct_call_tool("snapshot", {})
            assert (
                isinstance(result, BinaryContent) and result.media_type == "image/png"
            ), result

        async def audio() -> None:
            result = await toolset.direct_call_tool("chime", {})
            assert (
                isinstance(result, BinaryContent) and result.media_type == "audio/wav"
            ), result

        async def mixed_content() -> None:
            result = await toolset.direct_call_tool("attachments", {})
            assert result == ["see attached", "buy milk", "config://app"], result

        async def elicitation() -> None:
            result = await toolset.direct_call_tool("confirm", {"action": "deploy"})
            assert result == "deploy: approved", result

        async def progress_notifications() -> None:
            progress.clear()
            assert (
                await toolset.direct_call_tool("count_to", {"n": 3}) == "counted to 3"
            )
            assert progress == [1, 2, 3], progress

        async def log_messages() -> None:
            logs.clear()
            await toolset.direct_call_tool("count_to", {"n": 1})
            assert any("counted to 1" in line for line in logs), logs

        async def resources() -> None:
            assert "config://app" in {
                str(r.uri) for r in await toolset.list_resources()
            }
            assert await toolset.read_resource("config://app") == '{"mode": "smoke"}'

        async def resource_template() -> None:
            templates = {
                t.uri_template for t in await toolset.list_resource_templates()
            }
            assert "greeting://{name}" in templates, templates
            assert await toolset.read_resource("greeting://nate") == "hello, nate"

        async def prompt() -> None:
            result = await toolset.get_prompt("review", {"code": "x = 1"})
            assert "x = 1" in str(result.messages[0].content), result

        async def template_literals_and_list_queries() -> None:
            for uri, expected in TEMPLATE_READS.items():
                assert await toolset.read_resource(uri) == expected, uri

        async def float_count_output_schema() -> None:
            result = await toolset.direct_call_tool("stamp", {})
            assert result == {"when": "2026-09-22T00:00:00Z"}, result

        async def timed_out_call_leaves_connection_usable() -> None:
            with quiet("mcp.client.sse"):
                try:
                    with anyio.fail_after(0.5):
                        await toolset.direct_call_tool("sleep", {"seconds": 5})
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("sleep(5) finished inside a 0.5s deadline")
                with anyio.fail_after(10):
                    assert await toolset.direct_call_tool("add", {"a": 1, "b": 1}) == 2
                await anyio.sleep(0.2)

        for check in (
            lists_tools,
            instructions,
            agent_loop,
            structured_output,
            tool_error,
            image,
            audio,
            mixed_content,
            elicitation,
            progress_notifications,
            resources,
            resource_template,
            prompt,
            template_literals_and_list_queries,
            float_count_output_schema,
            timed_out_call_leaves_connection_usable,
        ):
            name = check.__name__.replace("_", " ")
            await report.check(name, check, known_gap=(gaps or {}).get(name))
        await report.check(
            "logging", log_messages, known_gap=(gaps or {}).get("logging")
        )


async def main() -> None:
    report = Report(
        "pydantic-ai",
        {
            "fastmcp": fastmcp.__version__,
            "pydantic-ai-slim": version("pydantic-ai-slim"),
            "mcp": version("mcp"),
        },
    )

    async def installed_build_is_under_test() -> None:
        expected = os.environ.get("FASTMCP_EXPECTED_VERSION")
        assert expected in (None, fastmcp.__version__), (
            f"expected {expected}, got {fastmcp.__version__}"
        )

    command, args = stdio_command()
    with report.transport("stdio"):
        await report.check(
            "installed build is under test", installed_build_is_under_test
        )
        await exercise(report, stdio_transport())

    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "mcp.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "smoke": {"command": command, "args": args, "env": server_env()}
                    }
                }
            )
        )
        with report.transport("mcp.json config"):

            async def agent_loop_from_config() -> None:
                agent = Agent(
                    FunctionModel(scripted_model), toolsets=load_mcp_toolsets(config)
                )
                result = await agent.run("add 2 and 3, then get the Chicago forecast")
                assert "5" in result.output and "sunny" in result.output, result.output

            await report.check(
                "agent loop from load_mcp_toolsets", agent_loop_from_config
            )

    for transport, label in (
        ("http", "streamable HTTP"),
        ("sse", "SSE"),
        ("proxy", "FastMCP proxy"),
    ):
        # The SDK's SSE reader logs a traceback when a check cancels a call on purpose.
        with (
            report.transport(label),
            serve(transport) as server,
            quiet("mcp.client.sse"),
        ):

            async def rejects_missing_token() -> None:
                try:
                    async with MCPToolset(server.url):
                        pass
                except Exception:
                    return
                raise AssertionError("connected without a bearer token")

            await report.check("rejects missing bearer token", rejects_missing_token)
            await exercise(
                report,
                server.url,
                headers=server.headers,
                gaps=PROXY_GAPS if transport == "proxy" else None,
            )

    report.finish()


if __name__ == "__main__":
    asyncio.run(main())
