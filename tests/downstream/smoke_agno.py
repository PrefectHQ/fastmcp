"""Drive FastMCP through agno's MCPTools, as an agno user installs it.

`agno[mcp]` depends on `fastmcp` and builds every connection with
`fastmcp.Client` and FastMCP's transports, so this runs in its own environment
with the build under test. The servers run from the checkout:

    uv run --isolated --no-project --no-config --with 'agno[mcp]' \
        --with "fastmcp @ file://$PWD/$(ls dist/fastmcp-4*.whl)" \
        --with "fastmcp-slim @ file://$PWD/$(ls dist/fastmcp_slim-*.whl)" \
        python tests/downstream/smoke_agno.py
"""

import asyncio
import json
import os
from importlib.metadata import version
from typing import Any

from _harness import (
    PROXY_GAPS,
    TOOLS,
    Report,
    quiet,
    serve,
    server_env,
    stdio_command,
)
from agno.agent import Agent
from agno.models.base import Model
from agno.models.message import MessageMetrics
from agno.models.response import ModelResponse
from agno.tools.mcp import MCPTools
from mcp import StdioServerParameters

import fastmcp


class ScriptedModel(Model):
    """Call add and forecast on the first turn, then answer with what they returned."""

    def __init__(self) -> None:
        super().__init__(id="scripted", name="scripted", provider="smoke")

    def _respond(self, messages: list[Any]) -> ModelResponse:
        results = [str(m.content) for m in messages if m.role == "tool"]
        if results:
            return ModelResponse(
                role="assistant",
                content=" | ".join(results),
                response_usage=MessageMetrics(),
            )
        return ModelResponse(
            role="assistant",
            tool_calls=[
                {
                    "id": "call-add",
                    "type": "function",
                    "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
                },
                {
                    "id": "call-forecast",
                    "type": "function",
                    "function": {
                        "name": "forecast",
                        "arguments": '{"city": "Chicago", "days": 2}',
                    },
                },
            ],
            response_usage=MessageMetrics(),
        )

    def invoke(self, messages: list[Any], *args: Any, **kwargs: Any) -> ModelResponse:
        return self._respond(messages)

    async def ainvoke(
        self, messages: list[Any], *args: Any, **kwargs: Any
    ) -> ModelResponse:
        return self._respond(messages)

    def invoke_stream(self, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
        yield self._respond(messages)

    async def ainvoke_stream(
        self, messages: list[Any], *args: Any, **kwargs: Any
    ) -> Any:
        yield self._respond(messages)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


async def exercise(
    report: Report, make_tools: Any, gaps: dict[str, str] | None = None
) -> None:
    gaps = gaps or {}
    async with make_tools() as mcp:

        async def call(name: str, **kwargs: Any) -> Any:
            return await mcp.functions[name].entrypoint(**kwargs)

        async def lists_tools() -> None:
            assert TOOLS <= set(mcp.functions), sorted(mcp.functions)

        async def uses_fastmcp_client() -> None:
            assert isinstance(mcp.session, fastmcp.Client), type(mcp.session)

        async def agent_loop() -> None:
            agent = Agent(model=ScriptedModel(), tools=[mcp], telemetry=False)
            run = await agent.arun("add 2 and 3, then get the Chicago forecast")
            assert "5" in str(run.content) and "sunny" in str(run.content), run.content

        async def structured_output() -> None:
            result = await call("forecast", city="Oslo")
            expected = {"city": "Oslo", "celsius": 21.5, "conditions": ["sunny"]}
            assert json.loads(result.content) == expected, result.content

        async def tool_error() -> None:
            result = await call("divide", a=1, b=0)
            assert result.content.startswith("Error from MCP tool 'divide'"), result
            assert "divide by zero" in result.content, result.content

        async def image() -> None:
            result = await call("snapshot")
            [picture] = result.images
            assert picture.mime_type == "image/png" and picture.content, picture

        async def audio() -> None:
            result = await call("chime")
            [sound] = result.audios
            assert sound.mime_type == "audio/wav" and sound.content, sound

        async def mixed_content() -> None:
            result = await call("attachments")
            assert "see attached" in result.content, result.content
            assert "buy milk" in result.content, result.content

        async def float_count_output_schema() -> None:
            result = await call("stamp")
            assert json.loads(result.content) == {"when": "2026-09-22T00:00:00Z"}, (
                result.content
            )

        async def liveness() -> None:
            assert await mcp.is_alive()

        for check in (
            lists_tools,
            uses_fastmcp_client,
            agent_loop,
            structured_output,
            tool_error,
            image,
            audio,
            mixed_content,
            float_count_output_schema,
            liveness,
        ):
            name = check.__name__.replace("_", " ")
            await report.check(name, check, known_gap=gaps.get(name))

    async def call_after_timeout() -> None:
        # agno's timeout also bounds the handshake, and a stdio server can take
        # over a second to start, so leave it room.
        async with make_tools(timeout_seconds=3) as short:
            result = await short.functions["sleep"].entrypoint(seconds=6)
            assert "Error" in str(result.content) or "timed out" in str(
                result.content
            ), result
            result = await short.functions["add"].entrypoint(a=1, b=1)
            assert result.content == "2", result.content

    await report.check(
        "call after timeout",
        call_after_timeout,
        known_gap=gaps.get("call after timeout"),
    )


async def main() -> None:
    report = Report(
        "agno",
        {
            "agno": version("agno"),
            "fastmcp": fastmcp.__version__,
            "mcp": version("mcp"),
        },
    )

    async def installed_build_is_under_test() -> None:
        expected = os.environ.get("FASTMCP_EXPECTED_VERSION")
        assert expected in (None, fastmcp.__version__), (
            f"expected {expected}, got {fastmcp.__version__}"
        )

    command, args = stdio_command()
    stdio = StdioServerParameters(command=command, args=args, env=server_env())
    for mode in ("legacy", "auto"):
        # agno logs the timeout a check causes on purpose.
        with report.transport(f"stdio ({mode})"), quiet("agno", "agno-agent"):
            if mode == "legacy":
                await report.check(
                    "installed build is under test", installed_build_is_under_test
                )
            await exercise(
                report,
                lambda mode=mode, **kw: MCPTools(
                    server_params=stdio, transport="stdio", protocol_mode=mode, **kw
                ),
            )

    for transport, label, kind in (
        ("http", "streamable HTTP", "streamable-http"),
        ("sse", "SSE", "sse"),
        ("proxy", "FastMCP proxy", "streamable-http"),
    ):
        # The SDK's SSE reader logs a traceback when a session closes mid-stream.
        with (
            report.transport(label),
            serve(transport) as server,
            quiet("mcp.client.sse", "agno", "agno-agent"),
        ):

            async def rejects_missing_token() -> None:
                try:
                    async with MCPTools(url=server.url, transport=kind) as mcp:
                        listed = sorted(mcp.functions)
                except Exception:
                    return
                assert not listed, f"listed tools without a bearer token: {listed}"

            async def header_provider() -> None:
                seen: list[int] = []

                def provide() -> dict[str, str]:
                    seen.append(1)
                    return server.headers

                async with MCPTools(
                    url=server.url, transport=kind, header_provider=provide
                ) as mcp:
                    result = await mcp.functions["add"].entrypoint(a=4, b=4)
                    assert result.content == "8", result
                assert seen, "header_provider was never called"

            await report.check("rejects missing bearer token", rejects_missing_token)
            await report.check("header provider", header_provider)
            await exercise(
                report,
                lambda kind=kind, server=server, **kw: MCPTools(
                    url=server.url, transport=kind, headers=server.headers, **kw
                ),
                gaps=PROXY_GAPS if transport == "proxy" else None,
            )

    report.finish()


if __name__ == "__main__":
    asyncio.run(main())
