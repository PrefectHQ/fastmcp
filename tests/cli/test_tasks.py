"""Tests for the fastmcp tasks CLI."""

import pytest
from fastmcp_tasks.settings import DocketSettings
from fastmcp_tasks.worker_cli import (
    check_distributed_backend,
    resolve_docket_settings,
    tasks_app,
)

from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension


class TestResolveDocketSettings:
    """`resolve_docket_settings` reads the server's *registered* extension."""

    def test_reads_the_registered_extensions_settings(self):
        """The constructor-configured URL is visible without any env var."""
        mcp = FastMCP("t")
        mcp.add_extension(TasksExtension(url="redis://example:6379/0"))
        settings = resolve_docket_settings(mcp)
        assert settings.url == "redis://example:6379/0"

    def test_exits_when_no_tasks_extension_registered(self):
        """A server with no TasksExtension has nothing for the CLI to serve."""
        mcp = FastMCP("t")
        with pytest.raises(SystemExit) as exc_info:
            resolve_docket_settings(mcp)
        assert exc_info.value.code == 1


class TestCheckDistributedBackend:
    """Test the distributed backend checker function."""

    def test_succeeds_with_redis_url(self):
        """Test that it succeeds with Redis URL."""
        settings = DocketSettings(url="redis://localhost:6379/0")
        check_distributed_backend(settings)

    def test_exits_with_helpful_error_for_memory_url(self):
        """Test that it exits with helpful error for memory:// URLs."""
        settings = DocketSettings(url="memory://test-123")
        with pytest.raises(SystemExit) as exc_info:
            check_distributed_backend(settings)

        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1


class TestWorkerCommand:
    """Test the worker command."""

    def test_worker_command_parsing(self):
        """Test that worker command parses arguments correctly."""
        command, bound, _ = tasks_app.parse_args(["worker", "server.py"])
        assert callable(command)
        assert command.__name__ == "worker"  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        assert bound.arguments["server_spec"] == "server.py"


class TestTasksAppIntegration:
    """Test the tasks app integration."""

    def test_tasks_app_exists(self):
        """Test that the tasks app is properly configured."""
        assert "tasks" in tasks_app.name
        assert "Docket" in tasks_app.help

    def test_tasks_app_has_commands(self):
        """Test that all expected commands are registered."""
        # Just verify the app exists and has the right metadata
        # Detailed command testing is done in individual test classes
        assert "tasks" in tasks_app.name
        assert tasks_app.help
