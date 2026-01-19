from datetime import timedelta

import pytest
from dirty_equals import HasName
from inline_snapshot import snapshot
from mcp.types import (
    AudioContent,
    ImageContent,
    ToolExecution,
)
from pydantic import BaseModel

from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.types import Audio, File, Image


class TestToolFromFunction:
    def test_basic_function(self):
        """Test registering and running a basic function."""

        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        tool = Tool.from_function(add)

        assert tool.model_dump(exclude_none=True) == snapshot(
            {
                "name": "add",
                "description": "Add two numbers.",
                "tags": set(),
                "parameters": {
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "fn": HasName("add"),
                "task_config": {
                    "mode": "forbidden",
                    "poll_interval": timedelta(seconds=5),
                },
            }
        )

    def test_meta_parameter(self):
        """Test that meta parameter is properly handled."""

        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        meta_data = {"version": "1.0", "author": "test"}
        tool = Tool.from_function(multiply, meta=meta_data)

        assert tool.meta == meta_data
        mcp_tool = tool.to_mcp_tool()

        # MCP tool includes fastmcp meta, so check that our meta is included
        assert mcp_tool.meta is not None
        assert meta_data.items() <= mcp_tool.meta.items()

    async def test_async_function(self):
        """Test registering and running an async function."""

        async def fetch_data(url: str) -> str:
            """Fetch data from URL."""
            return f"Data from {url}"

        tool = Tool.from_function(fetch_data)

        assert tool.model_dump(exclude_none=True) == snapshot(
            {
                "name": "fetch_data",
                "description": "Fetch data from URL.",
                "tags": set(),
                "parameters": {
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "string"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "fn": HasName("fetch_data"),
                "task_config": {
                    "mode": "forbidden",
                    "poll_interval": timedelta(seconds=5),
                },
            }
        )

    def test_callable_object(self):
        class Adder:
            """Adds two numbers."""

            def __call__(self, x: int, y: int) -> int:
                """ignore this"""
                return x + y

        tool = Tool.from_function(Adder())

        assert tool.model_dump(exclude_none=True, exclude={"fn"}) == snapshot(
            {
                "name": "Adder",
                "description": "Adds two numbers.",
                "tags": set(),
                "parameters": {
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "task_config": {
                    "mode": "forbidden",
                    "poll_interval": timedelta(seconds=5),
                },
            }
        )

    def test_async_callable_object(self):
        class Adder:
            """Adds two numbers."""

            async def __call__(self, x: int, y: int) -> int:
                """ignore this"""
                return x + y

        tool = Tool.from_function(Adder())

        assert tool.model_dump(exclude_none=True, exclude={"fn"}) == snapshot(
            {
                "name": "Adder",
                "description": "Adds two numbers.",
                "tags": set(),
                "parameters": {
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "task_config": {
                    "mode": "forbidden",
                    "poll_interval": timedelta(seconds=5),
                },
            }
        )

    def test_pydantic_model_function(self):
        """Test registering a function that takes a Pydantic model."""

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput, flag: bool) -> dict:
            """Create a new user."""
            return {"id": 1, **user.model_dump()}

        tool = Tool.from_function(create_user)

        assert tool.model_dump(exclude_none=True) == snapshot(
            {
                "name": "create_user",
                "description": "Create a new user.",
                "tags": set(),
                "parameters": {
                    "properties": {
                        "user": {
                            "properties": {
                                "name": {"type": "string"},
                                "age": {"type": "integer"},
                            },
                            "required": ["name", "age"],
                            "type": "object",
                        },
                        "flag": {"type": "boolean"},
                    },
                    "required": ["user", "flag"],
                    "type": "object",
                },
                "output_schema": {"additionalProperties": True, "type": "object"},
                "fn": HasName("create_user"),
                "task_config": {
                    "mode": "forbidden",
                    "poll_interval": timedelta(seconds=5),
                },
            }
        )

    async def test_tool_with_image_return(self):
        def image_tool(data: bytes) -> Image:
            return Image(data=data)

        tool = Tool.from_function(image_tool)
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert tool.output_schema is None

        result = await tool.run({"data": "test.png"})
        assert isinstance(result.content[0], ImageContent)

    async def test_tool_with_audio_return(self):
        def audio_tool(data: bytes) -> Audio:
            return Audio(data=data)

        tool = Tool.from_function(audio_tool)
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert tool.output_schema is None

        result = await tool.run({"data": "test.wav"})
        assert isinstance(result.content[0], AudioContent)

    async def test_tool_with_file_return(self):
        from pydantic import AnyUrl

        def file_tool(data: bytes) -> File:
            return File(data=data, format="octet-stream")

        tool = Tool.from_function(file_tool)
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert tool.output_schema is None

        result: ToolResult = await tool.run({"data": "test.bin"})
        assert result.content[0].model_dump(exclude_none=True) == snapshot(
            {
                "type": "resource",
                "resource": {
                    "uri": AnyUrl("file:///resource.octet-stream"),
                    "mimeType": "application/octet-stream",
                    "blob": "dGVzdC5iaW4=",
                },
            }
        )

    def test_non_callable_fn(self):
        with pytest.raises(TypeError, match="not a callable object"):
            Tool.from_function(1)  # type: ignore

    def test_lambda(self):
        tool = Tool.from_function(lambda x: x, name="my_tool")
        assert tool.model_dump(exclude_none=True, exclude={"fn"}) == snapshot(
            {
                "name": "my_tool",
                "tags": set(),
                "parameters": {
                    "properties": {"x": {"title": "X"}},
                    "required": ["x"],
                    "type": "object",
                },
                "task_config": {
                    "mode": "forbidden",
                    "poll_interval": timedelta(seconds=5),
                },
            }
        )

    def test_lambda_with_no_name(self):
        with pytest.raises(
            ValueError, match="You must provide a name for lambda functions"
        ):
            Tool.from_function(lambda x: x)

    def test_private_arguments(self):
        def add(_a: int, _b: int) -> int:
            """Add two numbers."""
            return _a + _b

        tool = Tool.from_function(add)

        assert tool.model_dump(
            exclude_none=True, exclude={"output_schema", "fn"}
        ) == snapshot(
            {
                "name": "add",
                "description": "Add two numbers.",
                "tags": set(),
                "parameters": {
                    "properties": {
                        "_a": {"type": "integer"},
                        "_b": {"type": "integer"},
                    },
                    "required": ["_a", "_b"],
                    "type": "object",
                },
                "task_config": {
                    "mode": "forbidden",
                    "poll_interval": timedelta(seconds=5),
                },
            }
        )

    def test_tool_with_varargs_not_allowed(self):
        def func(a: int, b: int, *args: int) -> int:
            """Add two numbers."""
            return a + b

        with pytest.raises(
            ValueError, match=r"Functions with \*args are not supported as tools"
        ):
            Tool.from_function(func)

    def test_tool_with_varkwargs_not_allowed(self):
        def func(a: int, b: int, **kwargs: int) -> int:
            """Add two numbers."""
            return a + b

        with pytest.raises(
            ValueError, match=r"Functions with \*\*kwargs are not supported as tools"
        ):
            Tool.from_function(func)

    async def test_instance_method(self):
        class MyClass:
            def add(self, x: int, y: int) -> int:
                """Add two numbers."""
                return x + y

        obj = MyClass()

        tool = Tool.from_function(obj.add)
        assert "self" not in tool.parameters["properties"]

        assert tool.model_dump(exclude_none=True, exclude={"fn"}) == snapshot(
            {
                "name": "add",
                "description": "Add two numbers.",
                "tags": set(),
                "parameters": {
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "task_config": {
                    "mode": "forbidden",
                    "poll_interval": timedelta(seconds=5),
                },
            }
        )

    async def test_instance_method_with_varargs_not_allowed(self):
        class MyClass:
            def add(self, x: int, y: int, *args: int) -> int:
                """Add two numbers."""
                return x + y

        obj = MyClass()

        with pytest.raises(
            ValueError, match=r"Functions with \*args are not supported as tools"
        ):
            Tool.from_function(obj.add)

    async def test_instance_method_with_varkwargs_not_allowed(self):
        class MyClass:
            def add(self, x: int, y: int, **kwargs: int) -> int:
                """Add two numbers."""
                return x + y

        obj = MyClass()

        with pytest.raises(
            ValueError, match=r"Functions with \*\*kwargs are not supported as tools"
        ):
            Tool.from_function(obj.add)

    async def test_classmethod(self):
        class MyClass:
            x: int = 10

            @classmethod
            def call(cls, x: int, y: int) -> int:
                """Add two numbers."""
                return x + y

        tool = Tool.from_function(MyClass.call)
        assert tool.name == "call"
        assert tool.description == "Add two numbers."
        assert "x" in tool.parameters["properties"]
        assert "y" in tool.parameters["properties"]


class TestToolNameValidation:
    """Tests for tool name validation per MCP specification (SEP-986)."""

    @pytest.fixture
    def caplog_for_mcp_validation(self, caplog):
        """Capture logs from the MCP SDK's tool name validation logger."""
        import logging

        caplog.set_level(logging.WARNING)
        logger = logging.getLogger("mcp.shared.tool_name_validation")
        original_level = logger.level
        logger.setLevel(logging.WARNING)
        logger.addHandler(caplog.handler)
        try:
            yield caplog
        finally:
            logger.removeHandler(caplog.handler)
            logger.setLevel(original_level)

    @pytest.mark.parametrize(
        "name",
        [
            "valid_tool",
            "valid-tool",
            "valid.tool",
            "ValidTool",
            "tool123",
            "a",
            "a" * 128,
        ],
    )
    def test_valid_tool_names_no_warnings(self, name, caplog_for_mcp_validation):
        """Valid tool names should not produce warnings."""

        def fn() -> str:
            return "test"

        tool = Tool.from_function(fn, name=name)
        assert tool.name == name
        assert "Tool name validation warning" not in caplog_for_mcp_validation.text

    def test_tool_name_with_spaces_warns(self, caplog_for_mcp_validation):
        """Tool names with spaces should produce a warning."""

        def fn() -> str:
            return "test"

        tool = Tool.from_function(fn, name="my tool")
        assert tool.name == "my tool"
        assert "Tool name validation warning" in caplog_for_mcp_validation.text
        assert "contains spaces" in caplog_for_mcp_validation.text

    def test_tool_name_with_invalid_chars_warns(self, caplog_for_mcp_validation):
        """Tool names with invalid characters should produce a warning."""

        def fn() -> str:
            return "test"

        tool = Tool.from_function(fn, name="tool@name!")
        assert tool.name == "tool@name!"
        assert "Tool name validation warning" in caplog_for_mcp_validation.text
        assert "invalid characters" in caplog_for_mcp_validation.text

    def test_tool_name_too_long_warns(self, caplog_for_mcp_validation):
        """Tool names exceeding 128 characters should produce a warning."""

        def fn() -> str:
            return "test"

        long_name = "a" * 129
        tool = Tool.from_function(fn, name=long_name)
        assert tool.name == long_name
        assert "Tool name validation warning" in caplog_for_mcp_validation.text
        assert "exceeds maximum length" in caplog_for_mcp_validation.text

    def test_tool_name_with_leading_dash_warns(self, caplog_for_mcp_validation):
        """Tool names starting with dash should produce a warning."""

        def fn() -> str:
            return "test"

        tool = Tool.from_function(fn, name="-tool")
        assert tool.name == "-tool"
        assert "Tool name validation warning" in caplog_for_mcp_validation.text
        assert "starts or ends with a dash" in caplog_for_mcp_validation.text

    def test_tool_still_created_despite_warnings(self, caplog_for_mcp_validation):
        """Tools with invalid names should still be created (SHOULD not MUST)."""

        def add(a: int, b: int) -> int:
            return a + b

        tool = Tool.from_function(add, name="invalid tool name!")
        assert tool.name == "invalid tool name!"
        assert tool.parameters is not None
        assert "a" in tool.parameters["properties"]
        assert "b" in tool.parameters["properties"]


class TestToolExecutionField:
    """Tests for the execution field on the base Tool class."""

    def test_tool_with_execution_field(self):
        """Test that Tool can store and return execution metadata."""
        tool = Tool(
            name="my_tool",
            description="A tool with execution",
            parameters={"type": "object", "properties": {}},
            execution=ToolExecution(taskSupport="optional"),
        )

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "optional"

    def test_tool_without_execution_field(self):
        """Test that Tool without execution returns None."""
        tool = Tool(
            name="my_tool",
            description="A tool without execution",
            parameters={"type": "object", "properties": {}},
        )

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is None

    def test_execution_override_takes_precedence(self):
        """Test that explicit override takes precedence over field value."""
        tool = Tool(
            name="my_tool",
            description="A tool",
            parameters={"type": "object", "properties": {}},
            execution=ToolExecution(taskSupport="optional"),
        )

        override_execution = ToolExecution(taskSupport="required")
        mcp_tool = tool.to_mcp_tool(execution=override_execution)
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "required"

    async def test_function_tool_task_config_still_works(self):
        """FunctionTool should still derive execution from task_config."""

        async def my_fn() -> str:
            return "hello"

        tool = Tool.from_function(my_fn, task=True)
        mcp_tool = tool.to_mcp_tool()

        # FunctionTool sets execution from task_config
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "optional"

    def test_tool_execution_required_mode(self):
        """Test that Tool can store required execution mode."""
        tool = Tool(
            name="my_tool",
            description="A tool with required execution",
            parameters={"type": "object", "properties": {}},
            execution=ToolExecution(taskSupport="required"),
        )

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "required"

    def test_tool_execution_forbidden_mode(self):
        """Test that Tool can store forbidden execution mode."""
        tool = Tool(
            name="my_tool",
            description="A tool with forbidden execution",
            parameters={"type": "object", "properties": {}},
            execution=ToolExecution(taskSupport="forbidden"),
        )

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "forbidden"
