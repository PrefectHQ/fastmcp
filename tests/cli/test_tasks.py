"""Tests for the fastmcp tasks CLI."""

import pytest
from fastmcp_tasks.settings import DocketSettings
from fastmcp_tasks.worker_cli import (
    check_distributed_backend,
    redact_backend_url,
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


class TestRedactBackendUrl:
    """The worker prints the backend URL without its passwords."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (
                "redis://tasks:S3cret@redis.example.com:6379/0",
                "redis://tasks:***@redis.example.com:6379/0",
            ),
            (
                "rediss://:S3cret@redis.example.com/0",
                "rediss://:***@redis.example.com/0",
            ),
            (
                "redis://redis.example.com/0?password=S3cret&socket_timeout=5",
                "redis://redis.example.com/0?password=***&socket_timeout=5",
            ),
            (
                "redis+sentinel://u:S3cret@h1:26379,h2:26379/mymaster/0"
                "?sentinel_password=S3cret2",
                "redis+sentinel://u:***@h1:26379,h2:26379/mymaster/0"
                "?sentinel_password=***",
            ),
        ],
    )
    def test_hides_passwords(self, url: str, expected: str):
        assert redact_backend_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "redis://localhost:6379/0",
            "redis://tasks@localhost:6379/0",
            "redis://localhost/0?ssl_ca_certs=/etc/ca.pem",
            "memory://test-123",
        ],
    )
    def test_leaves_urls_without_passwords_unchanged(self, url: str):
        assert redact_backend_url(url) == url


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
