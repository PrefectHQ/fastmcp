"""Generated commands must work whatever their tools are called."""

from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.cli.generate import generate_cli_script


@pytest.mark.parametrize("name", ["app", "console", "Client"])
async def test_tool_named_like_a_script_global(name: str) -> None:
    server = FastMCP("Desktop")
    received: list[str] = []

    @server.tool(name=name)
    def record(text: str) -> str:
        received.append(text)
        return text

    async with Client(server) as client:
        tools = await client.list_tools()
    script = generate_cli_script(
        server_name="desktop",
        server_spec="desktop",
        transport_code=repr("http://unused/mcp"),
        extra_imports=set(),
        tools=tools,
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(script, "<generated>", "exec"), namespace)
    namespace["CLIENT_SPEC"] = server

    await namespace["app"].run_async(
        ["call-tool", name, "--text", "hi"], result_action="return_value"
    )

    assert received == ["hi"]
