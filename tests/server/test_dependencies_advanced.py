"""Advanced tests for dependency injection in FastMCP: context annotations, vendored DI, and auth dependencies."""

import inspect

import mcp.types as mcp_types
import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context


class Connection:
    """Test connection that tracks whether it's currently open."""

    def __init__(self):
        self.is_open = False

    async def __aenter__(self):
        self.is_open = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.is_open = False


@pytest.fixture
def mcp():
    """Create a FastMCP server for testing."""
    return FastMCP("test-server")


class TestTransformContextAnnotations:
    """Tests for the transform_context_annotations function."""

    async def test_optional_context_degrades_to_none_without_active_context(self):
        """Optional Context should resolve to None when no context is active."""
        from fastmcp.server.dependencies import transform_context_annotations

        async def fn_with_optional_ctx(name: str, ctx: Context | None = None) -> str:
            return name

        transform_context_annotations(fn_with_optional_ctx)
        sig = inspect.signature(fn_with_optional_ctx)
        ctx_dependency = sig.parameters["ctx"].default

        resolved_ctx = await ctx_dependency.__aenter__()
        try:
            assert resolved_ctx is None
        finally:
            await ctx_dependency.__aexit__(None, None, None)

    async def test_optional_context_still_injected_in_foreground_requests(
        self, mcp: FastMCP
    ):
        """Optional Context should still be injected for normal MCP requests."""

        @mcp.tool()
        async def tool_with_optional_context(
            name: str, ctx: Context | None = None
        ) -> str:
            if ctx is None:
                return f"missing:{name}"
            return f"present:{ctx.session_id}:{name}"

        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_optional_context", {"name": "x"})
            assert result.content[0].text.startswith("present:")

    async def test_basic_context_transformation(self, mcp: FastMCP):
        """Test basic Context type annotation is transformed."""

        @mcp.tool()
        async def tool_with_context(name: str, ctx: Context) -> str:
            return f"session={ctx.session_id}, name={name}"

        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_context", {"name": "test"})
            assert "session=" in result.content[0].text
            assert "name=test" in result.content[0].text

    async def test_transform_with_var_params(self):
        """Test transform_context_annotations handles *args and **kwargs correctly."""
        from fastmcp.server.dependencies import transform_context_annotations

        # This function can't be a tool (FastMCP doesn't support *args/**kwargs),
        # but transform should handle it gracefully for signature inspection
        async def fn_with_var_params(
            first: str, ctx: Context, *args: str, **kwargs: str
        ) -> str:
            return f"first={first}"

        transform_context_annotations(fn_with_var_params)
        sig = inspect.signature(fn_with_var_params)

        # Verify structure is preserved
        param_kinds = {p.name: p.kind for p in sig.parameters.values()}
        assert param_kinds["first"] == inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert param_kinds["ctx"] == inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert param_kinds["args"] == inspect.Parameter.VAR_POSITIONAL
        assert param_kinds["kwargs"] == inspect.Parameter.VAR_KEYWORD

        # ctx should now have a default
        assert sig.parameters["ctx"].default is not inspect.Parameter.empty

    async def test_context_keyword_only(self, mcp: FastMCP):
        """Test Context transformation preserves keyword-only parameter semantics."""
        from fastmcp.server.dependencies import transform_context_annotations

        # Define function with keyword-only Context param
        async def fn_with_kw_only(a: str, *, ctx: Context, b: str = "default") -> str:
            return f"a={a}, b={b}"

        # Transform and check signature structure
        transform_context_annotations(fn_with_kw_only)
        sig = inspect.signature(fn_with_kw_only)
        params = list(sig.parameters.values())

        # 'a' should be POSITIONAL_OR_KEYWORD
        assert params[0].name == "a"
        assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD

        # 'ctx' should still be KEYWORD_ONLY (after transformation)
        ctx_param = sig.parameters["ctx"]
        assert ctx_param.kind == inspect.Parameter.KEYWORD_ONLY

        # 'b' should still be KEYWORD_ONLY
        b_param = sig.parameters["b"]
        assert b_param.kind == inspect.Parameter.KEYWORD_ONLY

    async def test_context_with_annotated(self, mcp: FastMCP):
        """Test Context with Annotated type is transformed."""
        from typing import Annotated

        @mcp.tool()
        async def tool_with_annotated_ctx(
            name: str, ctx: Annotated[Context, "custom annotation"]
        ) -> str:
            return f"session={ctx.session_id}"

        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_annotated_ctx", {"name": "test"})
            assert "session=" in result.content[0].text

    async def test_context_already_has_dependency_default(self, mcp: FastMCP):
        """Test that Context with existing Depends default is not re-transformed."""

        @mcp.tool()
        async def tool_with_explicit_context(
            name: str, ctx: Context = CurrentContext()
        ) -> str:
            return f"session={ctx.session_id}"

        async with Client(mcp) as client:
            result = await client.call_tool(
                "tool_with_explicit_context", {"name": "test"}
            )
            assert "session=" in result.content[0].text

    async def test_multiple_context_params(self, mcp: FastMCP):
        """Test multiple Context-typed parameters are all transformed."""

        @mcp.tool()
        async def tool_with_multiple_ctx(
            name: str, ctx1: Context, ctx2: Context
        ) -> str:
            # Both should refer to same context
            assert ctx1.session_id == ctx2.session_id
            return f"same={ctx1 is ctx2}"

        # Both ctx params should be excluded from schema
        result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
        tool = next(t for t in result.tools if t.name == "tool_with_multiple_ctx")
        assert "name" in tool.inputSchema["properties"]
        assert "ctx1" not in tool.inputSchema["properties"]
        assert "ctx2" not in tool.inputSchema["properties"]

    async def test_context_in_class_method(self, mcp: FastMCP):
        """Test Context transformation works with bound methods."""

        class MyTools:
            def __init__(self, prefix: str):
                self.prefix = prefix

            async def greet(self, name: str, ctx: Context) -> str:
                return f"{self.prefix} {name}, session={ctx.session_id}"

        tools = MyTools("Hello")
        mcp.tool()(tools.greet)

        async with Client(mcp) as client:
            result = await client.call_tool("greet", {"name": "World"})
            assert "Hello World" in result.content[0].text
            assert "session=" in result.content[0].text

    async def test_context_in_static_method(self, mcp: FastMCP):
        """Test Context transformation works with static methods."""

        class MyTools:
            @staticmethod
            async def static_tool(name: str, ctx: Context) -> str:
                return f"name={name}, session={ctx.session_id}"

        mcp.tool()(MyTools.static_tool)

        async with Client(mcp) as client:
            result = await client.call_tool("static_tool", {"name": "test"})
            assert "name=test" in result.content[0].text
            assert "session=" in result.content[0].text

    async def test_context_in_callable_class(self, mcp: FastMCP):
        """Test Context transformation works with callable class instances."""
        from fastmcp.tools import Tool

        class CallableTool:
            def __init__(self, multiplier: int):
                self.multiplier = multiplier

            async def __call__(self, x: int, ctx: Context) -> str:
                return f"result={x * self.multiplier}, session={ctx.session_id}"

        # Use Tool.from_function directly (mcp.tool() decorator doesn't support callable instances)
        tool = Tool.from_function(CallableTool(3))
        mcp.add_tool(tool)

        async with Client(mcp) as client:
            result = await client.call_tool("CallableTool", {"x": 5})
            assert "result=15" in result.content[0].text
            assert "session=" in result.content[0].text

    async def test_context_param_reordering(self, mcp: FastMCP):
        """Test that Context params are reordered correctly to maintain valid signature."""
        from fastmcp.server.dependencies import transform_context_annotations

        # Context in middle without default - should be moved after non-default params
        async def fn_with_middle_ctx(a: str, ctx: Context, b: str) -> str:
            return f"{a},{b}"

        transform_context_annotations(fn_with_middle_ctx)
        sig = inspect.signature(fn_with_middle_ctx)
        params = list(sig.parameters.values())

        # After transform: a, b should come before ctx (which now has default)
        param_names = [p.name for p in params]
        assert param_names == ["a", "b", "ctx"]

        # ctx should have a default now
        assert sig.parameters["ctx"].default is not inspect.Parameter.empty

    async def test_context_resource(self, mcp: FastMCP):
        """Test Context transformation works with resources."""

        @mcp.resource("data://test")
        async def resource_with_ctx(ctx: Context) -> str:
            return f"session={ctx.session_id}"

        async with Client(mcp) as client:
            result = await client.read_resource("data://test")
            assert len(result) == 1
            assert "session=" in result[0].text

    async def test_context_resource_template(self, mcp: FastMCP):
        """Test Context transformation works with resource templates."""

        @mcp.resource("item://{item_id}")
        async def template_with_ctx(item_id: str, ctx: Context) -> str:
            return f"item={item_id}, session={ctx.session_id}"

        async with Client(mcp) as client:
            result = await client.read_resource("item://123")
            assert len(result) == 1
            assert "item=123" in result[0].text
            assert "session=" in result[0].text

    async def test_context_prompt(self, mcp: FastMCP):
        """Test Context transformation works with prompts."""

        @mcp.prompt()
        async def prompt_with_ctx(topic: str, ctx: Context) -> str:
            return f"Write about {topic} (session: {ctx.session_id})"

        async with Client(mcp) as client:
            result = await client.get_prompt("prompt_with_ctx", {"topic": "AI"})
            assert "Write about AI" in result.messages[0].content.text
            assert "session:" in result.messages[0].content.text
