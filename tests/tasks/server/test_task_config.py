"""Tests for TaskConfig (SEP-2663, tools only).

Tests for TaskConfig:
- Normalization of boolean task values to TaskConfig
- Sync-function validation
- Tool mode enforcement (forbidden, optional, required)
- Tool execution metadata (task_support in tools/list)
- Poll interval configuration
"""

from datetime import timedelta

import pytest
from fastmcp_tasks.models import (
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    CreateTaskResult,
)
from mcp.shared.exceptions import MCPError
from mcp_types import ToolExecution

from fastmcp import FastMCP
from fastmcp.tools.base import Tool
from fastmcp.utilities.tasks import TaskConfig
from fastmcp.utilities.versions import VersionSpec
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    _opted_in_request,
    auth_scope,
    call_tool_without_optin,
    running_task_server,
    submit_task,
)


async def _opted_in_call(server: FastMCP, name: str, arguments: dict | None = None):
    """Run a `tools/call` WITH the tasks opt-in bound (used to prove sync paths)."""
    with auth_scope(None), _opted_in_request(name, arguments or {}, None):
        return await server.call_tool(name, arguments or {})


def test_docket_settings_load_from_dotenv(tmp_path, monkeypatch):
    """`FASTMCP_DOCKET_*` in a `.env` file configures the backend.

    A distributed deployment that puts its Redis URL in `.env` must not silently
    fall back to `memory://` — DocketSettings loads the same dotenv source as
    core FastMCP settings.
    """
    from fastmcp_tasks.settings import DocketSettings

    (tmp_path / ".env").write_text("FASTMCP_DOCKET_URL=redis://dotenv-host:6379/2\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FASTMCP_DOCKET_URL", raising=False)

    assert DocketSettings().url == "redis://dotenv-host:6379/2"


async def test_interceptor_tasks_the_requested_version_not_the_highest():
    """A versioned tools/call tasks the version the caller asked for.

    Two versions share a name but differ in task mode: v1 is task-forbidden,
    v2 is task-optional. A call targeting v1 (with the tasks opt-in) must run v1
    synchronously — resolving the highest version instead would wrongly task v2.
    """
    mcp = FastMCP("versioned-tasks")
    mcp.add_extension(TasksExtension())

    @mcp.tool(name="calc", version="1.0")
    async def calc_v1() -> str:
        return "v1-sync"

    @mcp.tool(name="calc", version="2.0", task=True)
    async def calc_v2() -> str:
        return "v2"

    async with running_task_server(mcp):
        with auth_scope(None), _opted_in_request("calc", {}, None):
            result = await mcp.call_tool("calc", {}, version=VersionSpec(eq="1.0"))

    assert not isinstance(result, CreateTaskResult)
    assert result.structured_content == {"result": "v1-sync"}


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
    """Test mode enforcement for tools under the SEP-2663 interceptor."""

    def _server(self) -> FastMCP:
        mcp = FastMCP("test", tasks=False)
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=TaskConfig(mode="required"))
        async def required_tool() -> str:
            return "required result"

        @mcp.tool(task=TaskConfig(mode="forbidden"))
        async def forbidden_tool() -> str:
            return "forbidden result"

        @mcp.tool(task=TaskConfig(mode="optional"))
        async def optional_tool() -> str:
            return "optional result"

        return mcp

    async def test_required_mode_without_opt_in_raises(self):
        """Required mode raises -32003 when called without a tasks opt-in."""
        mcp = self._server()
        async with running_task_server(mcp):
            with pytest.raises(MCPError) as exc_info:
                await call_tool_without_optin(mcp, "required_tool")
            assert exc_info.value.error.code == MISSING_REQUIRED_CLIENT_CAPABILITY

    async def test_required_mode_with_opt_in_tasks(self):
        """Required mode tasks when the caller opts in."""
        mcp = self._server()
        async with running_task_server(mcp):
            created = await submit_task(mcp, "required_tool")
            assert isinstance(created, CreateTaskResult)

    async def test_forbidden_mode_never_tasks_even_with_opt_in(self):
        """Forbidden mode runs synchronously even when the caller opts in."""
        mcp = self._server()
        async with running_task_server(mcp):
            result = await _opted_in_call(mcp, "forbidden_tool")
            assert not isinstance(result, CreateTaskResult)
            assert result.structured_content == {"result": "forbidden result"}

    async def test_optional_mode_without_opt_in_runs_sync(self):
        """Optional mode runs synchronously without a tasks opt-in."""
        mcp = self._server()
        async with running_task_server(mcp):
            result = await call_tool_without_optin(mcp, "optional_tool")
            assert not isinstance(result, CreateTaskResult)
            assert result.structured_content == {"result": "optional result"}

    async def test_optional_mode_with_opt_in_tasks(self):
        """Optional mode tasks when the caller opts in."""
        mcp = self._server()
        async with running_task_server(mcp):
            created = await submit_task(mcp, "optional_tool")
            assert isinstance(created, CreateTaskResult)


class TestToolExecutionMetadata:
    """Test that ToolExecution.task_support is set correctly in tool metadata.

    The tools/list payload is produced by ``Tool.to_mcp_tool()``; these tests
    assert on that serialization directly, which is what a server advertises on
    the wire. (The FastMCP client session does not yet surface ``execution`` back
    to callers, so a client round-trip cannot observe it until Phase 4.)
    """

    async def test_optional_tool_exposes_task_support(self):
        """Tools with mode=optional expose task_support='optional'."""
        mcp = FastMCP("test", tasks=False)
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=TaskConfig(mode="optional"))
        async def my_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("my_tool")
        assert tool is not None
        execution = tool.to_mcp_tool().execution
        assert isinstance(execution, ToolExecution)
        assert execution.task_support == "optional"

    async def test_required_tool_exposes_task_support(self):
        """Tools with mode=required expose task_support='required'."""
        mcp = FastMCP("test", tasks=False)
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=TaskConfig(mode="required"))
        async def my_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("my_tool")
        assert tool is not None
        execution = tool.to_mcp_tool().execution
        assert isinstance(execution, ToolExecution)
        assert execution.task_support == "required"

    async def test_forbidden_tool_has_no_execution(self):
        """Tools with mode=forbidden do not expose execution metadata."""
        mcp = FastMCP("test", tasks=False)
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=TaskConfig(mode="forbidden"))
        async def my_tool() -> str:
            return "ok"

        tool = await mcp.get_tool("my_tool")
        assert tool is not None
        assert tool.to_mcp_tool().execution is None


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
