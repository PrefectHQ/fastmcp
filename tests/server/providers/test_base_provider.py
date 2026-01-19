"""Tests for base Provider class behavior."""

from typing import Any

from fastmcp.server.providers.base import Provider
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.server.transforms import Namespace
from fastmcp.tools.tool import Tool, ToolResult


class CustomTool(Tool):
    """A custom Tool subclass (not FunctionTool) with task support."""

    task_config: TaskConfig = TaskConfig(mode="optional")
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content="custom result")


class SimpleProvider(Provider):
    """Minimal provider that returns custom components from list methods."""

    def __init__(self, tools: list[Tool] | None = None):
        super().__init__()
        self._tools = tools or []

    async def _list_tools(self) -> list[Tool]:
        return self._tools


class TestBaseProviderGetTasks:
    """Tests for Provider.get_tasks() base implementation."""

    async def test_get_tasks_includes_custom_tool_subclasses(self):
        """Base Provider.get_tasks() should include custom Tool subclasses."""
        custom_tool = CustomTool(name="custom", description="A custom tool")
        provider = SimpleProvider(tools=[custom_tool])

        tasks = await provider.get_tasks()

        assert len(tasks) == 1
        assert tasks[0].name == "custom"
        assert tasks[0] is custom_tool

    async def test_get_tasks_filters_forbidden_custom_tools(self):
        """Base Provider.get_tasks() should exclude tools with forbidden task mode."""

        class ForbiddenTool(Tool):
            task_config: TaskConfig = TaskConfig(mode="forbidden")
            parameters: dict[str, Any] = {"type": "object", "properties": {}}

            async def run(self, arguments: dict[str, Any]) -> ToolResult:
                return ToolResult(content="forbidden")

        forbidden_tool = ForbiddenTool(name="forbidden", description="Forbidden tool")
        provider = SimpleProvider(tools=[forbidden_tool])

        tasks = await provider.get_tasks()

        assert len(tasks) == 0

    async def test_get_tasks_mixed_custom_and_forbidden(self):
        """Base Provider.get_tasks() filters correctly with mixed task modes."""

        class ForbiddenTool(Tool):
            task_config: TaskConfig = TaskConfig(mode="forbidden")
            parameters: dict[str, Any] = {"type": "object", "properties": {}}

            async def run(self, arguments: dict[str, Any]) -> ToolResult:
                return ToolResult(content="forbidden")

        enabled_tool = CustomTool(name="enabled", description="Task enabled")
        forbidden_tool = ForbiddenTool(name="forbidden", description="Task forbidden")
        provider = SimpleProvider(tools=[enabled_tool, forbidden_tool])

        tasks = await provider.get_tasks()

        assert len(tasks) == 1
        assert tasks[0].name == "enabled"

    async def test_get_tasks_applies_transforms(self):
        """get_tasks should apply provider transforms to component names."""
        tool = CustomTool(name="my_tool", description="A tool")
        provider = SimpleProvider(tools=[tool])
        provider.add_transform(Namespace("api"))

        tasks = await provider.get_tasks()

        assert len(tasks) == 1
        assert tasks[0].name == "api_my_tool"
