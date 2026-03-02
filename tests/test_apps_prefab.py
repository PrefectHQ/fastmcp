"""Tests for MCP Apps Phase 2 — Prefab integration.

Covers ``convert_result`` for PrefabApp/Component, ``app=True`` auto-wiring,
return-type inference, output-schema suppression, and end-to-end round trips.
"""

from __future__ import annotations

from typing import Annotated

from mcp.types import TextContent
from prefab_ui.app import PrefabApp
from prefab_ui.components import Column, Heading, Text
from prefab_ui.components.base import Component

from fastmcp import Client, FastMCP
from fastmcp.resources.types import TextResource
from fastmcp.server.apps import UI_MIME_TYPE, AppConfig
from fastmcp.server.providers.local_provider.decorators.tools import (
    PREFAB_RENDERER_URI,
)
from fastmcp.tools.tool import Tool, ToolResult

# ---------------------------------------------------------------------------
# convert_result
# ---------------------------------------------------------------------------


class TestConvertResult:
    def test_prefab_app(self):
        with Column() as view:
            Heading("Hello")
        app = PrefabApp(view=view, state={"name": "Alice"})

        tool = Tool(name="t", parameters={})
        result = tool.convert_result(app)

        assert isinstance(result, ToolResult)
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "[Rendered Prefab UI]"
        assert result.structured_content is not None
        assert result.structured_content["version"] == "0.2"
        assert result.structured_content["state"] == {"name": "Alice"}
        assert result.structured_content["view"]["type"] == "Column"

    def test_bare_component(self):
        heading = Heading("World")

        tool = Tool(name="t", parameters={})
        result = tool.convert_result(heading)

        assert isinstance(result, ToolResult)
        assert result.structured_content is not None
        assert result.structured_content["version"] == "0.2"
        assert result.structured_content["view"]["type"] == "Heading"

    def test_tool_result_with_prefab_structured_content(self):
        """ToolResult with PrefabApp as structured_content preserves custom text."""
        app = PrefabApp(view=Heading("Hello"), state={"x": 1})

        tool = Tool(name="t", parameters={})
        result = tool.convert_result(
            ToolResult(content="Custom fallback text", structured_content=app)
        )

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Custom fallback text"
        assert result.structured_content is not None
        assert result.structured_content["version"] == "0.2"
        assert result.structured_content["view"]["type"] == "Heading"

    def test_tool_result_with_component_structured_content(self):
        """ToolResult with bare Component as structured_content."""
        tool = Tool(name="t", parameters={})
        result = tool.convert_result(
            ToolResult(content="My text", structured_content=Heading("Hi"))
        )

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "My text"
        assert result.structured_content is not None
        assert result.structured_content["version"] == "0.2"
        assert result.structured_content["view"]["type"] == "Heading"

    def test_tool_result_passthrough(self):
        """ToolResult without prefab structured_content passes through unchanged."""
        original = ToolResult(content="hello")
        tool = Tool(name="t", parameters={})
        assert tool.convert_result(original) is original


# ---------------------------------------------------------------------------
# app=True auto-wiring
# ---------------------------------------------------------------------------


class TestAppTrue:
    def test_app_true_sets_meta(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def my_tool() -> str:
            return "hello"

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is not None
        assert "ui" in tool.meta
        assert tool.meta["ui"]["resourceUri"] == PREFAB_RENDERER_URI

    def test_app_true_registers_renderer_resource(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def my_tool() -> str:
            return "hello"

        renderer_key = f"resource:{PREFAB_RENDERER_URI}@"
        assert renderer_key in mcp._local_provider._components

    def test_renderer_resource_has_correct_mime_type(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def my_tool() -> str:
            return "hello"

        renderer_key = f"resource:{PREFAB_RENDERER_URI}@"
        resource = mcp._local_provider._components[renderer_key]
        assert isinstance(resource, TextResource)
        assert resource.mime_type == UI_MIME_TYPE

    def test_renderer_resource_has_csp(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def my_tool() -> str:
            return "hello"

        renderer_key = f"resource:{PREFAB_RENDERER_URI}@"
        resource = mcp._local_provider._components[renderer_key]
        assert resource.meta is not None
        assert "ui" in resource.meta
        assert "csp" in resource.meta["ui"]

    def test_multiple_tools_share_renderer(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def tool_a() -> str:
            return "a"

        @mcp.tool(app=True)
        def tool_b() -> str:
            return "b"

        renderer_keys = [
            k for k in mcp._local_provider._components if k.startswith("resource:ui://")
        ]
        assert len(renderer_keys) == 1

    def test_explicit_app_config_not_overridden(self):
        mcp = FastMCP("test")

        @mcp.tool(app=AppConfig(resource_uri="ui://custom/app.html"))
        def my_tool() -> PrefabApp:
            return PrefabApp(view=Heading("hi"))

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is not None
        assert tool.meta["ui"]["resourceUri"] == "ui://custom/app.html"


# ---------------------------------------------------------------------------
# Return type inference
# ---------------------------------------------------------------------------


class TestInference:
    def test_prefab_app_annotation_inferred(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> PrefabApp:
            return PrefabApp(view=Heading("hi"))

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is not None
        assert tool.meta["ui"]["resourceUri"] == PREFAB_RENDERER_URI

    def test_component_annotation_inferred(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> Component:
            return Heading("hi")

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is not None
        assert tool.meta["ui"]["resourceUri"] == PREFAB_RENDERER_URI

    def test_no_annotation_no_inference(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool():
            return "hello"

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is None or "ui" not in (tool.meta or {})

    def test_non_prefab_annotation_no_inference(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> str:
            return "hello"

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is None or "ui" not in (tool.meta or {})

    def test_optional_prefab_app_inferred(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> PrefabApp | None:
            return None

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is not None
        assert tool.meta["ui"]["resourceUri"] == PREFAB_RENDERER_URI

    def test_annotated_prefab_app_inferred(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> Annotated[PrefabApp | None, "some metadata"]:
            return None

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is not None
        assert tool.meta["ui"]["resourceUri"] == PREFAB_RENDERER_URI

    def test_component_subclass_union_inferred(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> Column | None:
            return None

        tools = mcp._local_provider._components
        tool = next(
            v
            for v in tools.values()
            if hasattr(v, "parameters") and v.name == "my_tool"
        )
        assert tool.meta is not None
        assert tool.meta["ui"]["resourceUri"] == PREFAB_RENDERER_URI


# ---------------------------------------------------------------------------
# Output schema suppression
# ---------------------------------------------------------------------------


class TestOutputSchema:
    def test_prefab_app_return_no_output_schema(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> PrefabApp:
            return PrefabApp(view=Heading("hi"))

        tools = mcp._local_provider._components
        tool: Tool = next(
            v for v in tools.values() if isinstance(v, Tool) and v.name == "my_tool"
        )
        assert tool.output_schema is None

    def test_component_return_no_output_schema(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> Column:
            with Column() as view:
                Heading("hi")
            return view

        tools = mcp._local_provider._components
        tool: Tool = next(
            v for v in tools.values() if isinstance(v, Tool) and v.name == "my_tool"
        )
        assert tool.output_schema is None

    def test_optional_component_no_output_schema(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> Column | None:
            return None

        tools = mcp._local_provider._components
        tool: Tool = next(
            v for v in tools.values() if isinstance(v, Tool) and v.name == "my_tool"
        )
        assert tool.output_schema is None

    def test_annotated_prefab_app_no_output_schema(self):
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> Annotated[PrefabApp | None, "metadata"]:
            return None

        tools = mcp._local_provider._components
        tool: Tool = next(
            v for v in tools.values() if isinstance(v, Tool) and v.name == "my_tool"
        )
        assert tool.output_schema is None


# ---------------------------------------------------------------------------
# Integration — client-server round trip
# ---------------------------------------------------------------------------


class TestIntegration:
    async def test_tool_call_returns_prefab_structured_content(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def greet(name: str) -> PrefabApp:
            with Column() as view:
                Heading("Hello")
                Text(f"Welcome, {name}!")
            return PrefabApp(view=view, state={"name": name})

        async with Client(mcp) as client:
            result = await client.call_tool("greet", {"name": "Alice"})

        assert result.structured_content is not None
        assert result.structured_content["version"] == "0.2"
        assert result.structured_content["state"] == {"name": "Alice"}

    async def test_tool_call_with_custom_text(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def greet(name: str) -> ToolResult:
            app = PrefabApp(view=Heading(f"Hello {name}"))
            return ToolResult(
                content=f"Greeting for {name}",
                structured_content=app,
            )

        async with Client(mcp) as client:
            result = await client.call_tool("greet", {"name": "Alice"})

        assert any(
            "Greeting for Alice" in c.text for c in result.content if hasattr(c, "text")
        )
        assert result.structured_content is not None
        assert result.structured_content["version"] == "0.2"

    async def test_tools_list_includes_app_meta(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def my_tool() -> PrefabApp:
            return PrefabApp(view=Heading("hi"))

        async with Client(mcp) as client:
            tools = await client.list_tools()

        tool = next(t for t in tools if t.name == "my_tool")
        meta = tool.meta or {}
        assert "ui" in meta
        assert meta["ui"]["resourceUri"] == PREFAB_RENDERER_URI

    async def test_renderer_resource_readable(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def my_tool() -> str:
            return "hello"

        async with Client(mcp) as client:
            contents = await client.read_resource(PREFAB_RENDERER_URI)

        assert len(contents) > 0
        text = contents[0].text if hasattr(contents[0], "text") else ""
        assert "<html" in text.lower() or "<!doctype" in text.lower()
