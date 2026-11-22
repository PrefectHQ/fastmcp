"""Drive a FastMCP server through the OpenAI Agents SDK's MCP servers.

The Agents SDK connects with the `mcp` SDK's own client, not FastMCP's, so it
reaches FastMCP over the wire. It runs in its own environment against servers
started from the checkout:

    uv run --isolated --no-project --no-config --with openai-agents \
        python tests/downstream/smoke_openai_agents.py
"""

import asyncio
import json
from importlib.metadata import version
from typing import Any

from _harness import (
    PROXY_GAPS,
    TEMPLATE_READS,
    TOOLS,
    Report,
    quiet,
    serve,
    server_env,
    stdio_command,
)
from agents import Agent, ModelResponse, Runner, Usage
from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from agents.models.interface import Model
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)


class ScriptedModel(Model):
    """Call add and forecast on the first turn, then answer with what they returned."""

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        items = kwargs.get("input", args[1] if len(args) > 1 else [])
        outputs = [
            str(item.get("output"))
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        if outputs:
            output: list[Any] = [
                ResponseOutputMessage(
                    id="msg-1",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            type="output_text", text=" | ".join(outputs), annotations=[]
                        )
                    ],
                )
            ]
        else:
            output = [
                ResponseFunctionToolCall(
                    id="fc-add",
                    call_id="call-add",
                    type="function_call",
                    name="add",
                    arguments='{"a": 2, "b": 3}',
                ),
                ResponseFunctionToolCall(
                    id="fc-forecast",
                    call_id="call-forecast",
                    type="function_call",
                    name="forecast",
                    arguments='{"city": "Chicago", "days": 2}',
                ),
            ]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


async def exercise(
    report: Report, make_server: Any, gaps: dict[str, str] | None = None
) -> None:
    gaps = gaps or {}
    async with make_server() as server:
        assert isinstance(server, MCPServer)

        async def lists_tools() -> None:
            names = {tool.name for tool in await server.list_tools()}
            assert TOOLS <= names, sorted(names)

        async def server_info() -> None:
            # The SDK fills `server_initialize_result` only from a handshake-era
            # initialize, which the 2026-07-28 era no longer sends, so read the
            # server's identity off the session it negotiated.
            info = server.session.server_info
            assert info.name == "downstream-smoke", info

        async def agent_loop() -> None:
            agent = Agent(name="smoke", model=ScriptedModel(), mcp_servers=[server])
            result = await Runner.run(
                agent, "add 2 and 3, then get the Chicago forecast"
            )
            output = str(result.final_output)
            assert "5" in output and "sunny" in output, output

        async def structured_output() -> None:
            result = await server.call_tool("forecast", {"city": "Oslo"})
            expected = {"city": "Oslo", "celsius": 21.5, "conditions": ["sunny"]}
            assert result.structured_content == expected, result

        async def tool_error() -> None:
            try:
                result = await server.call_tool("divide", {"a": 1, "b": 0})
            except Exception as error:
                assert "divide by zero" in str(error), error
                return
            assert result.is_error and "divide by zero" in str(result.content), result

        async def image() -> None:
            [block] = (await server.call_tool("snapshot", {})).content
            assert block.type == "image" and block.mime_type == "image/png", block

        async def mixed_content() -> None:
            blocks = (await server.call_tool("attachments", {})).content
            shapes = [
                (b.type, b.text if b.type == "text" else getattr(b, "resource", b))
                for b in blocks
            ]
            assert [t for t, _ in shapes] == ["text", "resource", "resource_link"], (
                shapes
            )
            assert shapes[0][1] == "see attached" and shapes[1][1].text == "buy milk"
            assert str(blocks[2].uri) == "config://app", blocks[2]

        async def float_count_output_schema() -> None:
            result = await server.call_tool("stamp", {})
            assert result.structured_content == {"when": "2026-09-22T00:00:00Z"}, result

        async def prompt() -> None:
            result = await server.get_prompt("review", {"code": "x = 1"})
            assert "x = 1" in str(result.messages[0].content), result

        async def resources() -> None:
            [content] = (await server.read_resource("config://app")).contents
            assert json.loads(content.text) == {"mode": "smoke"}, content

        async def resource_templates() -> None:
            listed = await server.list_resource_templates()
            assert "greeting://{name}" in {
                t.uri_template for t in listed.resource_templates
            }, listed
            [content] = (await server.read_resource("greeting://nate")).contents
            assert content.text == "hello, nate", content

        async def template_literals_and_list_queries() -> None:
            texts = [
                (await server.read_resource(uri)).contents[0].text
                for uri in TEMPLATE_READS
            ]
            assert texts == list(TEMPLATE_READS.values()), texts

        for check in (
            lists_tools,
            server_info,
            agent_loop,
            structured_output,
            tool_error,
            image,
            mixed_content,
            float_count_output_schema,
            prompt,
            resources,
            resource_templates,
            template_literals_and_list_queries,
        ):
            name = check.__name__.replace("_", " ")
            await report.check(name, check, known_gap=gaps.get(name))

    async def call_after_timeout() -> None:
        async with make_server(client_session_timeout_seconds=1) as short:
            try:
                result = await short.call_tool("sleep", {"seconds": 5})
            except Exception:
                pass
            else:
                raise AssertionError(f"a 5s call finished inside 1s: {result}")
            result = await short.call_tool("add", {"a": 1, "b": 1})
            assert result.structured_content == {"result": 2}, result

    await report.check(
        "call after timeout",
        call_after_timeout,
        known_gap=gaps.get("call after timeout"),
    )


async def main() -> None:
    report = Report(
        "openai-agents",
        {"openai-agents": version("openai-agents"), "mcp": version("mcp")},
    )
    command, args = stdio_command()
    with report.transport("stdio"):
        await exercise(
            report,
            lambda **kw: MCPServerStdio(
                {"command": command, "args": args, "env": server_env()}, **kw
            ),
        )

    for transport, label, server_class in (
        ("http", "streamable HTTP", MCPServerStreamableHttp),
        ("sse", "SSE", MCPServerSse),
        ("proxy", "FastMCP proxy", MCPServerStreamableHttp),
    ):
        # The SDK's SSE reader logs a traceback when a session closes mid-stream.
        with (
            report.transport(label),
            serve(transport) as server,
            quiet("mcp.client.sse", "openai.agents"),
        ):

            async def rejects_missing_token() -> None:
                try:
                    async with server_class({"url": server.url}) as mcp:
                        await mcp.list_tools()
                except Exception:
                    return
                raise AssertionError("listed tools without a bearer token")

            await report.check("rejects missing bearer token", rejects_missing_token)
            await exercise(
                report,
                lambda server_class=server_class, server=server, **kw: server_class(
                    {"url": server.url, "headers": server.headers}, **kw
                ),
                gaps=PROXY_GAPS if transport == "proxy" else None,
            )

    report.finish()


if __name__ == "__main__":
    asyncio.run(main())
