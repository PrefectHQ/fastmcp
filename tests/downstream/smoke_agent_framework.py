"""Drive a FastMCP server through Microsoft Agent Framework's MCP tools.

Agent Framework connects with the `mcp` SDK's own client, not FastMCP's, so it
reaches FastMCP over the wire. It runs in its own environment against servers
started from the checkout:

    uv run --isolated --no-project --no-config --with agent-framework-core \
        --with "mcp>=2.2,<3" --with tenacity \
        python tests/downstream/smoke_agent_framework.py
"""

import asyncio
import json
from collections.abc import MutableSequence
from importlib.metadata import version
from typing import Any

from _harness import (
    PROXY_GAPS,
    TOOLS,
    Report,
    serve,
    server_env,
    stdio_command,
)
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    MCPStdioTool,
    MCPStreamableHTTPTool,
    Message,
)
from agent_framework.exceptions import ToolExecutionException


class ScriptedChatClient(FunctionInvocationLayer, BaseChatClient):
    """Call add and forecast on the first turn, then answer with what they returned."""

    def _inner_get_response(
        self,
        *,
        messages: MutableSequence[Message],
        stream: bool,
        options: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        async def respond() -> ChatResponse:
            results = [
                str(c.result)
                for m in messages
                for c in m.contents
                if c.type == "function_result"
            ]
            if results:
                return ChatResponse(
                    messages=Message(role="assistant", contents=[" | ".join(results)])
                )
            calls = [
                Content.from_function_call(
                    call_id="call-add", name="add", arguments='{"a": 2, "b": 3}'
                ),
                Content.from_function_call(
                    call_id="call-forecast",
                    name="forecast",
                    arguments='{"city": "Chicago", "days": 2}',
                ),
            ]
            return ChatResponse(messages=Message(role="assistant", contents=calls))

        return respond()


def texts(contents: Any) -> list[str]:
    return [c.text for c in contents if c.type == "text"]


async def exercise(
    report: Report, make_tool: Any, gaps: dict[str, str] | None = None
) -> None:
    gaps = gaps or {}
    async with make_tool() as mcp:

        async def lists_tools() -> None:
            names = {f.name for f in mcp.functions}
            assert TOOLS <= names, sorted(names)

        async def agent_loop() -> None:
            agent = Agent(client=ScriptedChatClient(), tools=[mcp])
            response = await agent.run("add 2 and 3, then get the Chicago forecast")
            assert "5" in response.text and "sunny" in response.text, response.text

        async def structured_output() -> None:
            [text] = texts(await mcp.call_tool("forecast", city="Oslo"))
            expected = {"city": "Oslo", "celsius": 21.5, "conditions": ["sunny"]}
            assert json.loads(text) == expected, text

        async def tool_error() -> None:
            try:
                await mcp.call_tool("divide", a=1, b=0)
            except ToolExecutionException as error:
                assert "divide by zero" in str(error), error
                return
            raise AssertionError("divide by zero did not raise")

        async def image() -> None:
            [block] = await mcp.call_tool("snapshot")
            assert block.type == "data" and block.media_type == "image/png", (
                block.to_dict()
            )
            assert block.uri.startswith("data:image/png;base64,"), block.uri

        async def mixed_content() -> None:
            blocks = await mcp.call_tool("attachments")
            shapes = [(b.type, b.text or b.uri) for b in blocks]
            assert shapes[:2] == [("text", "see attached"), ("text", "buy milk")], (
                shapes
            )
            assert shapes[2][1] == "config://app", shapes

        async def float_count_output_schema() -> None:
            [text] = texts(await mcp.call_tool("stamp"))
            assert json.loads(text) == {"when": "2026-09-22T00:00:00Z"}, text

        async def prompt() -> None:
            assert "x = 1" in await mcp.get_prompt("review", code="x = 1")

        async def call_after_timeout() -> None:
            async with make_tool(request_timeout=1) as short:
                try:
                    await short.call_tool("sleep", seconds=5)
                except ToolExecutionException:
                    pass
                else:
                    raise AssertionError("a 5s call finished inside a 1s timeout")
                assert texts(await short.call_tool("add", a=1, b=1)) == [
                    '{"result": 2}'
                ]

        for check in (
            lists_tools,
            agent_loop,
            structured_output,
            tool_error,
            image,
            mixed_content,
            float_count_output_schema,
            prompt,
            call_after_timeout,
        ):
            name = check.__name__.replace("_", " ")
            await report.check(name, check, known_gap=gaps.get(name))


async def main() -> None:
    report = Report(
        "agent-framework",
        {
            "agent-framework-core": version("agent-framework-core"),
            "mcp": version("mcp"),
        },
    )
    command, args = stdio_command()
    with report.transport("stdio"):
        await exercise(
            report,
            lambda **kw: MCPStdioTool(
                name="smoke", command=command, args=args, env=server_env(), **kw
            ),
        )

    # Agent Framework has no SSE client, only stdio, streamable HTTP, and websocket.
    for transport, label in (("http", "streamable HTTP"), ("proxy", "FastMCP proxy")):
        with report.transport(label), serve(transport) as server:

            async def rejects_missing_token() -> None:
                try:
                    async with MCPStreamableHTTPTool(name="smoke", url=server.url):
                        pass
                except Exception:
                    return
                raise AssertionError("connected without a bearer token")

            async def header_provider() -> None:
                seen: list[dict[str, Any]] = []

                def provide(kwargs: dict[str, Any]) -> dict[str, str]:
                    seen.append(kwargs)
                    return server.headers

                async with MCPStreamableHTTPTool(
                    name="smoke", url=server.url, header_provider=provide
                ) as mcp:
                    assert texts(await mcp.call_tool("add", a=4, b=4)) == [
                        '{"result": 8}'
                    ]
                assert seen, "header_provider was never called"

            await report.check("rejects missing bearer token", rejects_missing_token)
            await report.check("header provider", header_provider)
            await exercise(
                report,
                lambda **kw: MCPStreamableHTTPTool(
                    name="smoke", url=server.url, static_headers=server.headers, **kw
                ),
                gaps=PROXY_GAPS if transport == "proxy" else None,
            )

    report.finish()


if __name__ == "__main__":
    asyncio.run(main())
