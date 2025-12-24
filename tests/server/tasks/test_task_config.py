"""Tests for TaskConfig (SEP-1686).

Tests for TaskConfig:
- Mode enforcement (forbidden, optional, required)
- Poll interval configuration
"""

from datetime import timedelta

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import TextContent, ToolExecution
from mcp.types import Tool as MCPTool

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.tasks import TaskConfig
from fastmcp.tools.tool import Tool


class TestTaskConfigNormalization:
    """Test that boolean task values normalize correctly to TaskConfig."""

    async def test_task_true_normalizes_to_optional(self):
        """task=True should normalize to TaskConfig(mode='optional')."""
        mcp = FastMCP("test", tasks=False)  # Disable default task support

        @mcp.tool(task=True)
        async def my_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("my_tool")
        assert isinstance(tool, Tool)
        assert tool.task_config.mode == "optional"

    async def test_task_false_normalizes_to_forbidden(self):
        """task=False should normalize to TaskConfig(mode='forbidden')."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=False)
        async def my_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("my_tool")
        assert isinstance(tool, Tool)
        assert tool.task_config.mode == "forbidden"

    async def test_task_config_passed_directly(self):
        """TaskConfig should be preserved when passed directly."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=TaskConfig(mode="required"))
        async def my_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("my_tool")
        assert isinstance(tool, Tool)
        assert tool.task_config.mode == "required"

    async def test_default_task_inherits_server_default(self):
        """Default task value should inherit from server default."""
        # Server with tasks disabled
        mcp_no_tasks = FastMCP("test", tasks=False)

        @mcp_no_tasks.tool()
        def my_tool_sync() -> str:
            return "ok"

        tool = await mcp_no_tasks.get_tool("my_tool_sync")
        assert isinstance(tool, Tool)
        assert tool.task_config.mode == "forbidden"

        # Server with tasks enabled
        mcp_tasks = FastMCP("test", tasks=True)

        @mcp_tasks.tool()
        async def my_tool_async() -> str:
            return "ok"

        tool2 = await mcp_tasks.get_tool("my_tool_async")
        assert isinstance(tool2, Tool)
        assert tool2.task_config.mode == "optional"


class TestToolModeEnforcement:
    """Test mode enforcement for tools."""

    @pytest.fixture
    def server(self):
        """Create server with tools in different modes."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=TaskConfig(mode="required"))
        async def required_tool() -> str:
            """Tool that requires task execution."""
            return "required result"

        @mcp.tool(task=TaskConfig(mode="forbidden"))
        async def forbidden_tool() -> str:
            """Tool that forbids task execution."""
            return "forbidden result"

        @mcp.tool(task=TaskConfig(mode="optional"))
        async def optional_tool() -> str:
            """Tool that supports both modes."""
            return "optional result"

        return mcp

    async def test_required_mode_without_task_returns_error(self, server):
        """Required mode raises error when called without task metadata."""
        async with Client(server) as client:
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("required_tool", {})

            assert "requires task-augmented execution" in str(exc_info.value)

    async def test_required_mode_with_task_succeeds(self, server):
        """Required mode succeeds when called with task metadata."""
        async with Client(server) as client:
            task = await client.call_tool("required_tool", {}, task=True)
            assert task is not None
            result = await task.result()
            assert result.data == "required result"

    async def test_forbidden_mode_with_task_returns_error(self, server):
        """Forbidden mode returns error when called with task metadata."""
        async with Client(server) as client:
            # Call with task=True should fail
            task = await client.call_tool("forbidden_tool", {}, task=True)
            assert task is not None
            # The task should have returned immediately with an error
            assert task.returned_immediately
            result = await task.result()
            # Check for error in the result
            assert result.is_error

    async def test_forbidden_mode_without_task_succeeds(self, server):
        """Forbidden mode succeeds when called without task metadata."""
        async with Client(server) as client:
            result = await client.call_tool("forbidden_tool", {})
            assert "forbidden result" in str(result)

    async def test_optional_mode_without_task_succeeds(self, server):
        """Optional mode succeeds when called without task metadata."""
        async with Client(server) as client:
            result = await client.call_tool("optional_tool", {})
            assert "optional result" in str(result)

    async def test_optional_mode_with_task_succeeds(self, server):
        """Optional mode succeeds when called with task metadata."""
        async with Client(server) as client:
            task = await client.call_tool("optional_tool", {}, task=True)
            assert task is not None
            result = await task.result()
            assert result.data == "optional result"


class TestResourceModeEnforcement:
    """Test mode enforcement for resources."""

    @pytest.fixture
    def server(self):
        """Create server with resources in different modes."""
        mcp = FastMCP("test", tasks=False)

        @mcp.resource("resource://required", task=TaskConfig(mode="required"))
        async def required_resource() -> str:
            """Resource that requires task execution."""
            return "required content"

        @mcp.resource("resource://forbidden", task=TaskConfig(mode="forbidden"))
        async def forbidden_resource() -> str:
            """Resource that forbids task execution."""
            return "forbidden content"

        @mcp.resource("resource://optional", task=TaskConfig(mode="optional"))
        async def optional_resource() -> str:
            """Resource that supports both modes."""
            return "optional content"

        return mcp

    async def test_required_resource_without_task_returns_error(self, server):
        """Required mode returns error when read without task metadata."""
        from mcp.types import METHOD_NOT_FOUND

        async with Client(server) as client:
            with pytest.raises(McpError) as exc_info:
                await client.read_resource("resource://required")

            assert exc_info.value.error.code == METHOD_NOT_FOUND
            assert "requires task-augmented execution" in exc_info.value.error.message

    async def test_required_resource_with_task_succeeds(self, server):
        """Required mode succeeds when read with task metadata."""
        async with Client(server) as client:
            task = await client.read_resource("resource://required", task=True)
            assert task is not None
            result = await task.result()
            # Result is a list of resource contents
            assert "required content" in str(result)

    async def test_forbidden_resource_without_task_succeeds(self, server):
        """Forbidden mode succeeds when read without task metadata."""
        async with Client(server) as client:
            result = await client.read_resource("resource://forbidden")
            assert "forbidden content" in str(result)


class TestPromptModeEnforcement:
    """Test mode enforcement for prompts."""

    @pytest.fixture
    def server(self):
        """Create server with prompts in different modes."""
        mcp = FastMCP("test", tasks=False)

        @mcp.prompt(task=TaskConfig(mode="required"))
        async def required_prompt() -> str:
            """Prompt that requires task execution."""
            return "required message"

        @mcp.prompt(task=TaskConfig(mode="forbidden"))
        async def forbidden_prompt() -> str:
            """Prompt that forbids task execution."""
            return "forbidden message"

        @mcp.prompt(task=TaskConfig(mode="optional"))
        async def optional_prompt() -> str:
            """Prompt that supports both modes."""
            return "optional message"

        return mcp

    async def test_required_prompt_without_task_returns_error(self, server):
        """Required mode returns error when called without task metadata."""
        from mcp.types import METHOD_NOT_FOUND

        async with Client(server) as client:
            with pytest.raises(McpError) as exc_info:
                await client.get_prompt("required_prompt")

            assert exc_info.value.error.code == METHOD_NOT_FOUND
            assert "requires task-augmented execution" in exc_info.value.error.message

    async def test_required_prompt_with_task_succeeds(self, server):
        """Required mode succeeds when called with task metadata."""
        async with Client(server) as client:
            task = await client.get_prompt("required_prompt", task=True)
            assert task is not None
            result = await task.result()
            # Result contains the prompt messages
            assert "required message" in str(result)

    async def test_forbidden_prompt_without_task_succeeds(self, server):
        """Forbidden mode succeeds when called without task metadata."""
        async with Client(server) as client:
            result = await client.get_prompt("forbidden_prompt")
            assert isinstance(result.messages[0].content, TextContent)
            assert "forbidden message" in str(result.messages[0].content)


class TestToolExecutionMetadata:
    """Test that ToolExecution.taskSupport is set correctly in tool metadata."""

    async def test_optional_tool_exposes_task_support(self):
        """Tools with task enabled should expose taskSupport in metadata."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=TaskConfig(mode="optional"))
        async def my_tool() -> str:
            return "ok"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "my_tool")
            assert isinstance(tool, MCPTool)
            assert isinstance(tool.execution, ToolExecution)
            assert tool.execution.taskSupport == "optional"

    async def test_required_tool_exposes_task_support(self):
        """Tools with mode=required should expose taskSupport='required'."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=TaskConfig(mode="required"))
        async def my_tool() -> str:
            return "ok"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "my_tool")
            assert isinstance(tool, MCPTool)
            assert isinstance(tool.execution, ToolExecution)
            assert tool.execution.taskSupport == "required"

    async def test_forbidden_tool_has_no_execution(self):
        """Tools with mode=forbidden should not expose execution metadata."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=TaskConfig(mode="forbidden"))
        async def my_tool() -> str:
            return "ok"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "my_tool")
            assert tool.execution is None


class TestSyncFunctionValidation:
    """Test that sync functions cannot have task execution enabled."""

    def test_sync_function_with_task_true_raises(self):
        """Sync functions should raise ValueError when task=True."""
        mcp = FastMCP("test", tasks=False)

        with pytest.raises(ValueError, match="sync function"):

            @mcp.tool(task=True)
            def sync_tool() -> str:
                return "ok"

    def test_sync_function_with_required_mode_raises(self):
        """Sync functions should raise ValueError with mode='required'."""
        mcp = FastMCP("test", tasks=False)

        with pytest.raises(ValueError, match="sync function"):

            @mcp.tool(task=TaskConfig(mode="required"))
            def sync_tool() -> str:
                return "ok"

    def test_sync_function_with_optional_mode_raises(self):
        """Sync functions should raise ValueError with mode='optional'."""
        mcp = FastMCP("test", tasks=False)

        with pytest.raises(ValueError, match="sync function"):

            @mcp.tool(task=TaskConfig(mode="optional"))
            def sync_tool() -> str:
                return "ok"

    async def test_sync_function_with_forbidden_mode_ok(self):
        """Sync functions should work fine with mode='forbidden'."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=TaskConfig(mode="forbidden"))
        def sync_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("sync_tool")
        assert isinstance(tool, Tool)
        assert tool.task_config.mode == "forbidden"


class TestPollIntervalConfiguration:
    """Test poll_interval configuration in TaskConfig."""

    async def test_default_poll_interval_is_5_seconds(self):
        """Default poll_interval should be 5 seconds."""
        config = TaskConfig()
        assert config.poll_interval == timedelta(seconds=5)

    async def test_custom_poll_interval_preserved(self):
        """Custom poll_interval should be preserved in TaskConfig."""
        config = TaskConfig(poll_interval=timedelta(seconds=10))
        assert config.poll_interval == timedelta(seconds=10)

    async def test_tool_inherits_poll_interval(self):
        """Tool should inherit poll_interval from TaskConfig."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=TaskConfig(mode="optional", poll_interval=timedelta(seconds=2)))
        async def my_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("my_tool")
        assert isinstance(tool, Tool)
        assert tool.task_config.poll_interval == timedelta(seconds=2)

    async def test_task_true_uses_default_poll_interval(self):
        """task=True should use default 5 second poll_interval."""
        mcp = FastMCP("test", tasks=False)

        @mcp.tool(task=True)
        async def my_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("my_tool")
        assert isinstance(tool, Tool)
        assert tool.task_config.poll_interval == timedelta(seconds=5)
