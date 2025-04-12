import json
import logging

import pytest
from pydantic import BaseModel

from fastmcp.exceptions import ToolError
from fastmcp.settings import DuplicateBehavior
from fastmcp.tools import ToolManager


class TestAddTools:
    def test_basic_function(self):
        """Test registering and running a basic function."""

        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        manager = ToolManager()
        manager.add_tool_from_fn(add)

        tool = manager.get_tool("add")
        assert tool is not None
        assert tool.name == "add"
        assert tool.description == "Add two numbers."
        assert tool.is_async is False
        assert tool.parameters["properties"]["a"]["type"] == "integer"
        assert tool.parameters["properties"]["b"]["type"] == "integer"

    @pytest.mark.anyio
    async def test_async_function(self):
        """Test registering and running an async function."""

        async def fetch_data(url: str) -> str:
            """Fetch data from URL."""
            return f"Data from {url}"

        manager = ToolManager()
        manager.add_tool_from_fn(fetch_data)

        tool = manager.get_tool("fetch_data")
        assert tool is not None
        assert tool.name == "fetch_data"
        assert tool.description == "Fetch data from URL."
        assert tool.is_async is True
        assert tool.parameters["properties"]["url"]["type"] == "string"

    def test_pydantic_model_function(self):
        """Test registering a function that takes a Pydantic model."""

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput, flag: bool) -> dict:
            """Create a new user."""
            return {"id": 1, **user.model_dump()}

        manager = ToolManager()
        manager.add_tool_from_fn(create_user)

        tool = manager.get_tool("create_user")
        assert tool is not None
        assert tool.name == "create_user"
        assert tool.description == "Create a new user."
        assert tool.is_async is False
        assert "name" in tool.parameters["$defs"]["UserInput"]["properties"]
        assert "age" in tool.parameters["$defs"]["UserInput"]["properties"]
        assert "flag" in tool.parameters["properties"]

    def test_add_invalid_tool(self):
        manager = ToolManager()
        with pytest.raises(AttributeError):
            manager.add_tool_from_fn(1)  # type: ignore

    def test_add_lambda(self):
        manager = ToolManager()
        tool = manager.add_tool_from_fn(lambda x: x, name="my_tool")
        assert tool.name == "my_tool"

    def test_add_lambda_with_no_name(self):
        manager = ToolManager()
        with pytest.raises(
            ValueError, match="You must provide a name for lambda functions"
        ):
            manager.add_tool_from_fn(lambda x: x)

    def test_warn_on_duplicate_tools(self, caplog):
        """Test warning on duplicate tools."""

        def f(x: int) -> int:
            return x

        manager = ToolManager(duplicate_behavior=DuplicateBehavior.WARN)
        manager.add_tool_from_fn(f)
        with caplog.at_level(logging.WARNING):
            manager.add_tool_from_fn(f)
            assert "Tool already exists: f" in caplog.text

    def test_disable_warn_on_duplicate_tools(self, caplog):
        """Test disabling warning on duplicate tools."""

        def f(x: int) -> int:
            return x

        manager = ToolManager(duplicate_behavior=DuplicateBehavior.IGNORE)
        manager.add_tool_from_fn(f)
        with caplog.at_level(logging.WARNING):
            manager.add_tool_from_fn(f)
            assert "Tool already exists: f" not in caplog.text

    def test_error_on_duplicate_tools(self):
        """Test error on duplicate tools."""

        def f(x: int) -> int:
            return x

        manager = ToolManager(duplicate_behavior=DuplicateBehavior.ERROR)
        manager.add_tool_from_fn(f)

        with pytest.raises(ValueError, match="Tool already exists"):
            manager.add_tool_from_fn(f)

    def test_replace_duplicate_tools(self):
        """Test replacing duplicate tools."""

        def original_fn(x: int) -> int:
            return x

        def replacement_fn(x: int) -> int:
            return x * 2

        manager = ToolManager(duplicate_behavior=DuplicateBehavior.REPLACE)
        manager.add_tool_from_fn(original_fn, name="test_tool")
        replacement_tool = manager.add_tool_from_fn(replacement_fn, name="test_tool")

        # Should have replaced the first tool with the second
        stored_tool = manager.get_tool("test_tool")
        assert stored_tool == replacement_tool


class TestToolTags:
    """Test functionality related to tool tags."""

    def test_add_tool_with_tags(self):
        """Test adding tags to a tool."""

        def example_tool(x: int) -> int:
            """An example tool with tags."""
            return x * 2

        manager = ToolManager()
        tool = manager.add_tool_from_fn(example_tool, tags={"math", "utility"})

        assert tool.tags == {"math", "utility"}
        tool = manager.get_tool("example_tool")
        assert tool is not None
        assert tool.tags == {"math", "utility"}

    def test_add_tool_with_empty_tags(self):
        """Test adding a tool with empty tags set."""

        def example_tool(x: int) -> int:
            """An example tool with empty tags."""
            return x * 2

        manager = ToolManager()
        tool = manager.add_tool_from_fn(example_tool, tags=set())

        assert tool.tags == set()

    def test_add_tool_with_none_tags(self):
        """Test adding a tool with None tags."""

        def example_tool(x: int) -> int:
            """An example tool with None tags."""
            return x * 2

        manager = ToolManager()
        tool = manager.add_tool_from_fn(example_tool, tags=None)

        assert tool.tags == set()

    def test_list_tools_with_tags(self):
        """Test listing tools with specific tags."""

        def math_tool(x: int) -> int:
            """A math tool."""
            return x * 2

        def string_tool(x: str) -> str:
            """A string tool."""
            return x.upper()

        def mixed_tool(x: int) -> str:
            """A tool with multiple tags."""
            return str(x)

        manager = ToolManager()
        manager.add_tool_from_fn(math_tool, tags={"math"})
        manager.add_tool_from_fn(string_tool, tags={"string", "utility"})
        manager.add_tool_from_fn(mixed_tool, tags={"math", "utility", "string"})

        # Check if we can filter by tags when listing tools
        math_tools = [tool for tool in manager.list_tools() if "math" in tool.tags]
        assert len(math_tools) == 2
        assert {tool.name for tool in math_tools} == {"math_tool", "mixed_tool"}

        utility_tools = [
            tool for tool in manager.list_tools() if "utility" in tool.tags
        ]
        assert len(utility_tools) == 2
        assert {tool.name for tool in utility_tools} == {"string_tool", "mixed_tool"}

    def test_import_tools_preserves_tags(self):
        """Test that importing tools preserves their tags."""

        def tagged_tool(x: int) -> int:
            """A tool with tags."""
            return x

        source_manager = ToolManager()
        source_manager.add_tool_from_fn(tagged_tool, tags={"test", "example"})

        target_manager = ToolManager()
        target_manager.import_tools(source_manager, "source/")

        imported_tool = target_manager.get_tool("source/tagged_tool")
        assert imported_tool is not None
        assert imported_tool.tags == {"test", "example"}


class TestCallTools:
    @pytest.mark.anyio
    async def test_call_tool(self):
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        manager = ToolManager()
        manager.add_tool_from_fn(add)
        result = await manager.call_tool("add", {"a": 1, "b": 2})
        assert result == 3

    @pytest.mark.anyio
    async def test_call_async_tool(self):
        async def double(n: int) -> int:
            """Double a number."""
            return n * 2

        manager = ToolManager()
        manager.add_tool_from_fn(double)
        result = await manager.call_tool("double", {"n": 5})
        assert result == 10

    @pytest.mark.anyio
    async def test_call_tool_with_default_args(self):
        def add(a: int, b: int = 1) -> int:
            """Add two numbers."""
            return a + b

        manager = ToolManager()
        manager.add_tool_from_fn(add)
        result = await manager.call_tool("add", {"a": 1})
        assert result == 2

    @pytest.mark.anyio
    async def test_call_tool_with_missing_args(self):
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        manager = ToolManager()
        manager.add_tool_from_fn(add)
        with pytest.raises(ToolError):
            await manager.call_tool("add", {"a": 1})

    @pytest.mark.anyio
    async def test_call_unknown_tool(self):
        manager = ToolManager()
        with pytest.raises(ToolError):
            await manager.call_tool("unknown", {"a": 1})

    @pytest.mark.anyio
    async def test_call_tool_with_list_int_input(self):
        def sum_vals(vals: list[int]) -> int:
            return sum(vals)

        manager = ToolManager()
        manager.add_tool_from_fn(sum_vals)
        # Try both with plain list and with JSON list
        result = await manager.call_tool("sum_vals", {"vals": "[1, 2, 3]"})
        assert result == 6
        result = await manager.call_tool("sum_vals", {"vals": [1, 2, 3]})
        assert result == 6

    @pytest.mark.anyio
    async def test_call_tool_with_list_str_or_str_input(self):
        def concat_strs(vals: list[str] | str) -> str:
            return vals if isinstance(vals, str) else "".join(vals)

        manager = ToolManager()
        manager.add_tool_from_fn(concat_strs)
        # Try both with plain python object and with JSON list
        result = await manager.call_tool("concat_strs", {"vals": ["a", "b", "c"]})
        assert result == "abc"
        result = await manager.call_tool("concat_strs", {"vals": '["a", "b", "c"]'})
        assert result == "abc"
        result = await manager.call_tool("concat_strs", {"vals": "a"})
        assert result == "a"
        result = await manager.call_tool("concat_strs", {"vals": '"a"'})
        assert result == '"a"'

    @pytest.mark.anyio
    async def test_call_tool_with_complex_model(self):
        from fastmcp import Context

        class MyShrimpTank(BaseModel):
            class Shrimp(BaseModel):
                name: str

            shrimp: list[Shrimp]
            x: None

        def name_shrimp(tank: MyShrimpTank, ctx: Context) -> list[str]:
            return [x.name for x in tank.shrimp]

        manager = ToolManager()
        manager.add_tool_from_fn(name_shrimp)
        result = await manager.call_tool(
            "name_shrimp",
            {"tank": {"x": None, "shrimp": [{"name": "rex"}, {"name": "gertrude"}]}},
        )
        assert result == ["rex", "gertrude"]
        result = await manager.call_tool(
            "name_shrimp",
            {"tank": '{"x": null, "shrimp": [{"name": "rex"}, {"name": "gertrude"}]}'},
        )
        assert result == ["rex", "gertrude"]


class TestToolSchema:
    @pytest.mark.anyio
    async def test_context_arg_excluded_from_schema(self):
        from fastmcp import Context

        def something(a: int, ctx: Context) -> int:
            return a

        manager = ToolManager()
        tool = manager.add_tool_from_fn(something)
        assert "ctx" not in json.dumps(tool.parameters)
        assert "Context" not in json.dumps(tool.parameters)
        assert "ctx" not in tool.fn_metadata.arg_model.model_fields


class TestContextHandling:
    """Test context handling in the tool manager."""

    def test_context_parameter_detection(self):
        """Test that context parameters are properly detected in
        Tool.from_function()."""
        from fastmcp import Context

        def tool_with_context(x: int, ctx: Context) -> str:
            return str(x)

        manager = ToolManager()
        tool = manager.add_tool_from_fn(tool_with_context)
        assert tool.context_kwarg == "ctx"

        def tool_without_context(x: int) -> str:
            return str(x)

        tool = manager.add_tool_from_fn(tool_without_context)
        assert tool.context_kwarg is None

    @pytest.mark.anyio
    async def test_context_injection(self):
        """Test that context is properly injected during tool execution."""
        from fastmcp import Context, FastMCP

        def tool_with_context(x: int, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return str(x)

        manager = ToolManager()
        manager.add_tool_from_fn(tool_with_context)

        mcp = FastMCP()
        ctx = mcp.get_context()
        result = await manager.call_tool("tool_with_context", {"x": 42}, context=ctx)
        assert result == "42"

    @pytest.mark.anyio
    async def test_context_injection_async(self):
        """Test that context is properly injected in async tools."""
        from fastmcp import Context, FastMCP

        async def async_tool(x: int, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return str(x)

        manager = ToolManager()
        manager.add_tool_from_fn(async_tool)

        mcp = FastMCP()
        ctx = mcp.get_context()
        result = await manager.call_tool("async_tool", {"x": 42}, context=ctx)
        assert result == "42"

    @pytest.mark.anyio
    async def test_context_optional(self):
        """Test that context is optional when calling tools."""
        from fastmcp import Context

        def tool_with_context(x: int, ctx: Context | None = None) -> str:
            return str(x)

        manager = ToolManager()
        manager.add_tool_from_fn(tool_with_context)
        # Should not raise an error when context is not provided
        result = await manager.call_tool("tool_with_context", {"x": 42})
        assert result == "42"

    @pytest.mark.anyio
    async def test_context_error_handling(self):
        """Test error handling when context injection fails."""
        from fastmcp import Context, FastMCP

        def tool_with_context(x: int, ctx: Context) -> str:
            raise ValueError("Test error")

        manager = ToolManager()
        manager.add_tool_from_fn(tool_with_context)

        mcp = FastMCP()
        ctx = mcp.get_context()
        with pytest.raises(ToolError, match="Error executing tool tool_with_context"):
            await manager.call_tool("tool_with_context", {"x": 42}, context=ctx)


class TestImportTools:
    def test_import_tools(self):
        """Test importing tools from one manager to another with a prefix."""
        # Setup source manager with tools
        source_manager = ToolManager()

        # Create some test tools
        def tool1_fn():
            return "Tool 1 result"

        def tool2_fn():
            return "Tool 2 result"

        # Add tools to source manager
        source_manager.add_tool_from_fn(
            tool1_fn, name="get_data", description="Get some data"
        )
        source_manager.add_tool_from_fn(
            tool2_fn, name="process_data", description="Process the data"
        )

        # Create target manager
        target_manager = ToolManager()

        # Import tools from source to target
        prefix = "source/"
        target_manager.import_tools(source_manager, prefix)

        # Verify tools were imported with prefixes
        assert "source/get_data" in target_manager._tools
        assert "source/process_data" in target_manager._tools

        # Verify the original tools still exist in source manager
        assert "get_data" in source_manager._tools
        assert "process_data" in source_manager._tools

        # Verify the imported tools have the correct descriptions
        assert target_manager._tools["source/get_data"].description == "Get some data"
        assert (
            target_manager._tools["source/process_data"].description
            == "Process the data"
        )

        # Verify the tool functions were properly copied
        # We can't directly compare functions, so we'll check their __name__ attribute
        assert target_manager._tools["source/get_data"].fn == tool1_fn
        assert target_manager._tools["source/process_data"].fn == tool2_fn

    def test_tool_duplicate_behavior(self):
        """Test the behavior when importing tools with duplicate names."""
        # Setup source and target managers
        source_manager = ToolManager()
        target_manager = ToolManager()

        # Add the same tool name to both managers
        def source_fn():
            return "Source result"

        def target_fn():
            return "Target result"

        source_manager.add_tool_from_fn(source_fn, name="common_tool")
        target_manager.add_tool_from_fn(
            target_fn, name="source/common_tool"
        )  # Pre-create with the prefixed name

        # Import tools from source to target
        target_manager.import_tools(source_manager, "source/")

        # The original tool in the target manager is replaced by the imported one
        assert target_manager._tools["source/common_tool"].fn == source_fn

    def test_import_tools_with_multiple_prefixes(self):
        """Test importing tools from multiple managers with different prefixes."""
        # Setup source managers
        weather_manager = ToolManager()
        news_manager = ToolManager()

        # Add tools to source managers
        def forecast_fn():
            return "Weather forecast"

        def headlines_fn():
            return "News headlines"

        weather_manager.add_tool_from_fn(forecast_fn, name="forecast")
        news_manager.add_tool_from_fn(headlines_fn, name="headlines")

        # Create target manager and import from both sources
        main_manager = ToolManager()
        main_manager.import_tools(weather_manager, "weather/")
        main_manager.import_tools(news_manager, "news/")

        # Verify tools were imported with correct prefixes
        assert "weather/forecast" in main_manager._tools
        assert "news/headlines" in main_manager._tools

        # Verify the tools are accessible and functioning
        assert (
            main_manager._tools["weather/forecast"].fn.__name__ == forecast_fn.__name__
        )
        assert (
            main_manager._tools["news/headlines"].fn.__name__ == headlines_fn.__name__
        )
