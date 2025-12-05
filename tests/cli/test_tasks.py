"""Tests for the fastmcp tasks CLI."""

import pytest

from fastmcp.cli.tasks import check_docket_enabled, tasks_app
from fastmcp.utilities.tests import temporary_settings


class TestCheckDocketEnabled:
    """Test the Docket enabled checker function."""

    def test_succeeds_when_docket_enabled_with_redis(self):
        """Test that it succeeds when Docket is enabled with Redis."""
        with temporary_settings(
            enable_docket=True,
            docket__url="redis://localhost:6379/0",
        ):
            check_docket_enabled()

    def test_exits_when_docket_not_enabled(self):
        """Test that it exits with helpful error when Docket not enabled."""
        with temporary_settings(enable_docket=False):
            with pytest.raises(SystemExit) as exc_info:
                check_docket_enabled()

            assert isinstance(exc_info.value, SystemExit)
            assert exc_info.value.code == 1

    def test_exits_with_helpful_error_for_memory_url(self):
        """Test that it exits with helpful error for memory:// URLs."""
        with temporary_settings(
            enable_docket=True,
            docket__url="memory://test-123",
        ):
            with pytest.raises(SystemExit) as exc_info:
                check_docket_enabled()

            assert isinstance(exc_info.value, SystemExit)
            assert exc_info.value.code == 1


class TestWorkerCommand:
    """Test the worker command."""

    def test_worker_command_parsing(self):
        """Test that worker command parses arguments correctly."""
        command, bound, _ = tasks_app.parse_args(["worker", "server.py"])
        assert command.__name__ == "worker"  # type: ignore[attr-defined]
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
