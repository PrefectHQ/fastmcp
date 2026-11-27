import asyncio

import pytest

from fastmcp import Client, Context, FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.transforms import ToolCallIntent
from fastmcp.tools.base import Tool


async def test_intent_is_optional_and_isolated_per_call():
    server = FastMCP("intent")
    server.add_transform(ToolCallIntent())

    @server.tool
    async def inspect_call(value: int, ctx: Context) -> str:
        await asyncio.sleep(0)
        return f"{value}: {ctx.tool_call_intent}"

    async with Client(server) as client:
        (tool,) = await client.list_tools()
        assert set(tool.input_schema["properties"]) == {"value", "intent"}
        assert tool.input_schema["required"] == ["value"]
        results = await asyncio.gather(
            client.call_tool("inspect_call", {"value": 1, "intent": "Check inventory"}),
            client.call_tool("inspect_call", {"value": 2}),
        )
        assert [r.data for r in results] == ["1: Check inventory", "2: None"]


async def test_existing_intent_belongs_to_tool():
    server = FastMCP("intent")
    server.add_transform(ToolCallIntent())

    @server.tool
    def select(intent: int, ctx: Context) -> int:
        assert ctx.tool_call_intent is None
        return intent

    async with Client(server) as client:
        (tool,) = await client.list_tools()
        assert tool.input_schema["properties"]["intent"]["type"] == "integer"
        assert (await client.call_tool("select", {"intent": 12})).data == 12


@pytest.mark.parametrize("proxy", [False, True])
async def test_intent_is_consumed_before_forwarding(proxy: bool):
    backend = FastMCP("backend")

    @backend.tool
    def lookup(name: str) -> str:
        return name

    gateway = FastMCP("gateway")
    gateway.mount(create_proxy(backend) if proxy else backend)
    gateway.add_transform(ToolCallIntent())
    async with Client(gateway) as client:
        result = await client.call_tool(
            "lookup", {"name": "order", "intent": "Find today's order"}
        )
        assert result.data == "order"


async def test_schema_keeps_provider_constraints():
    schema = {
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "additionalProperties": {"type": "number"},
        "dependentRequired": {"amount": ["code"]},
    }
    source = Tool(name="report", parameters=schema)
    (wrapped,) = await ToolCallIntent().list_tools([source])
    expected = dict(wrapped.parameters)
    properties = dict(expected["properties"])
    assert properties.pop("intent")["description"]
    expected["properties"] = properties
    assert expected == schema
    assert "intent" not in source.parameters["properties"]
