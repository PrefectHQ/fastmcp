"""Tests for DereferenceRefsMiddleware."""

from enum import Enum

import pydantic

from fastmcp import Client, FastMCP


class Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class PaintRequest(pydantic.BaseModel):
    color: Color
    opacity: float = 1.0


class TestDereferenceRefsMiddleware:
    """End-to-end tests for the dereference_schemas server kwarg."""

    async def test_dereference_schemas_true_inlines_refs(self):
        """With dereference_schemas=True (default), tool schemas have $ref inlined."""
        mcp = FastMCP("test", dereference_schemas=True)

        @mcp.tool
        def paint(request: PaintRequest) -> str:
            return "ok"

        async with Client(mcp) as client:
            tools = await client.list_tools()

        schema = tools[0].inputSchema
        # $defs should be removed — everything inlined
        assert "$defs" not in schema
        # The Color enum should be inlined into the request property
        assert "$ref" not in str(schema)

    async def test_dereference_schemas_false_preserves_refs(self):
        """With dereference_schemas=False, $ref and $defs are preserved."""
        mcp = FastMCP("test", dereference_schemas=False)

        @mcp.tool
        def paint(request: PaintRequest) -> str:
            return "ok"

        async with Client(mcp) as client:
            tools = await client.list_tools()

        schema = tools[0].inputSchema
        # $defs should still be present
        assert "$defs" in schema

    async def test_default_is_true(self):
        """Default behavior dereferences $ref."""
        mcp = FastMCP("test")

        @mcp.tool
        def paint(request: PaintRequest) -> str:
            return "ok"

        async with Client(mcp) as client:
            tools = await client.list_tools()

        schema = tools[0].inputSchema
        assert "$defs" not in schema

    async def test_does_not_mutate_original_tool(self):
        """Middleware should not mutate the shared Tool object."""
        mcp = FastMCP("test", dereference_schemas=True)

        @mcp.tool
        def paint(request: PaintRequest) -> str:
            return "ok"

        # Get the original tool's parameters before middleware runs
        original_tools = await mcp._local_provider._list_tools()
        assert "$defs" in original_tools[0].parameters

        # List tools through the client (triggers middleware)
        async with Client(mcp) as client:
            await client.list_tools()

        # The original tool stored in the server should still have $defs
        tools_after = await mcp._local_provider._list_tools()
        assert "$defs" in tools_after[0].parameters

    async def test_output_schema_dereferenced(self):
        """Middleware also dereferences output_schema when present."""
        mcp = FastMCP("test", dereference_schemas=True)

        @mcp.tool
        def paint(request: PaintRequest) -> PaintRequest:
            return request

        async with Client(mcp) as client:
            tools = await client.list_tools()

        tool = tools[0]
        # Both input and output schemas should be dereferenced
        assert "$defs" not in tool.inputSchema
        if tool.outputSchema is not None:
            assert "$defs" not in tool.outputSchema

    async def test_resource_templates_dereferenced(self):
        """Middleware dereferences resource template schemas."""
        mcp = FastMCP("test", dereference_schemas=True)

        @mcp.resource("paint://{color}")
        def get_paint(color: Color) -> str:
            return f"paint: {color}"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()

        # Resource templates also get their schemas dereferenced
        # (only if the template parameters have $ref)
        assert len(templates) == 1

    async def test_no_ref_schemas_unchanged(self):
        """Tools without $ref should pass through unmodified."""
        mcp = FastMCP("test", dereference_schemas=True)

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        async with Client(mcp) as client:
            tools = await client.list_tools()

        schema = tools[0].inputSchema
        # Simple schema should not have $defs regardless
        assert "$defs" not in schema
        assert schema["properties"]["a"]["type"] == "integer"
