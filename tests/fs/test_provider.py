"""Tests for FileSystemProvider."""

import time
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers import FileSystemProvider


class TestFileSystemProvider:
    """Tests for FileSystemProvider."""

    def test_provider_empty_directory(self, tmp_path: Path):
        """Provider should work with empty directory."""
        provider = FileSystemProvider(tmp_path)
        assert repr(provider).startswith("FileSystemProvider")

    def test_provider_discovers_tools(self, tmp_path: Path):
        """Provider should discover @tool decorated functions."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "greet.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def greet(name: str) -> str:
    '''Greet someone by name.'''
    return f"Hello, {name}!"
"""
        )

        provider = FileSystemProvider(tmp_path)

        # Check tool was registered
        assert len(provider._components) == 1

    def test_provider_discovers_resources(self, tmp_path: Path):
        """Provider should discover @resource decorated functions."""
        (tmp_path / "config.py").write_text(
            """\
from fastmcp.resources import resource

@resource("config://app")
def get_config() -> dict:
    '''Get app config.'''
    return {"setting": "value"}
"""
        )

        provider = FileSystemProvider(tmp_path)
        assert len(provider._components) == 1

    def test_provider_discovers_resource_templates(self, tmp_path: Path):
        """Provider should discover resource templates."""
        (tmp_path / "users.py").write_text(
            """\
from fastmcp.resources import resource

@resource("users://{user_id}/profile")
def get_profile(user_id: str) -> dict:
    '''Get user profile.'''
    return {"id": user_id}
"""
        )

        provider = FileSystemProvider(tmp_path)
        assert len(provider._components) == 1

    def test_provider_discovers_prompts(self, tmp_path: Path):
        """Provider should discover @prompt decorated functions."""
        (tmp_path / "analyze.py").write_text(
            """\
from fastmcp.prompts import prompt

@prompt
def analyze(topic: str) -> list:
    '''Analyze a topic.'''
    return [{"role": "user", "content": f"Analyze: {topic}"}]
"""
        )

        provider = FileSystemProvider(tmp_path)
        assert len(provider._components) == 1

    def test_provider_discovers_multiple_in_one_file(self, tmp_path: Path):
        """Provider should discover multiple components in one file."""
        (tmp_path / "multi.py").write_text(
            """\
from fastmcp.tools import tool
from fastmcp.resources import resource

@tool
def tool1() -> str:
    return "tool1"

@tool
def tool2() -> str:
    return "tool2"

@resource("config://app")
def get_config() -> dict:
    return {}
"""
        )

        provider = FileSystemProvider(tmp_path)
        assert len(provider._components) == 3

    def test_provider_skips_undecorated_files(self, tmp_path: Path):
        """Provider should skip files with no decorated functions."""
        (tmp_path / "utils.py").write_text(
            """\
def helper_function():
    return "helper"

SOME_CONSTANT = 42
"""
        )
        (tmp_path / "tool.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def my_tool() -> str:
    return "tool"
"""
        )

        provider = FileSystemProvider(tmp_path)
        # Only the tool should be registered
        assert len(provider._components) == 1


class TestFileSystemProviderReloadMode:
    """Tests for FileSystemProvider reload mode."""

    def test_reload_false_caches_at_init(self, tmp_path: Path):
        """With reload=False, components are cached at init."""
        (tmp_path / "tool.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def original() -> str:
    return "original"
"""
        )

        provider = FileSystemProvider(tmp_path, reload=False)
        assert len(provider._components) == 1

        # Add another file - should NOT be picked up
        (tmp_path / "tool2.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def added() -> str:
    return "added"
"""
        )

        # Still only one component
        assert len(provider._components) == 1

    async def test_reload_true_rescans(self, tmp_path: Path):
        """With reload=True, components are rescanned on each request."""
        (tmp_path / "tool.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def original() -> str:
    return "original"
"""
        )

        provider = FileSystemProvider(tmp_path, reload=True)

        # Always loaded once at init (to catch errors early)
        assert provider._loaded
        assert len(provider._components) == 1

        # Add another file - should be picked up on next _ensure_loaded
        (tmp_path / "tool2.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def added() -> str:
    return "added"
"""
        )

        # With reload=True, _ensure_loaded re-scans
        await provider._ensure_loaded()
        assert len(provider._components) == 2

    async def test_warning_deduplication_same_file(self, tmp_path: Path, capsys):
        """Warnings for the same broken file should not repeat."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("1/0  # division by zero")

        provider = FileSystemProvider(tmp_path, reload=True)

        # First load - should warn
        captured = capsys.readouterr()
        # Check for warning indicator (rich may truncate long paths)
        assert "WARNING" in captured.err and "Failed to import" in captured.err

        # Second load (same file, unchanged) - should NOT warn again
        await provider._ensure_loaded()
        captured = capsys.readouterr()
        assert "Failed to import" not in captured.err

    async def test_warning_on_file_change(self, tmp_path: Path, capsys):
        """Warnings should reappear when a broken file changes."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("1/0  # division by zero")

        provider = FileSystemProvider(tmp_path, reload=True)

        # First load - should warn
        captured = capsys.readouterr()
        # Check for warning indicator (rich may truncate long paths)
        assert "WARNING" in captured.err and "Failed to import" in captured.err

        # Modify the file (different error) - need to ensure mtime changes
        time.sleep(0.01)  # Ensure mtime differs
        bad_file.write_text("syntax error here !!!")

        # Next load - should warn again (file changed)
        await provider._ensure_loaded()
        captured = capsys.readouterr()
        # Check for warning indicator (rich may truncate long paths)
        assert "WARNING" in captured.err and "Failed to import" in captured.err

    async def test_warning_cleared_when_fixed(self, tmp_path: Path, capsys):
        """Warnings should clear when a file is fixed, and reappear if broken again."""
        bad_file = tmp_path / "tool.py"
        bad_file.write_text("1/0  # broken")

        provider = FileSystemProvider(tmp_path, reload=True)

        # First load - should warn
        captured = capsys.readouterr()
        # Check for warning indicator (rich may truncate long paths)
        assert "WARNING" in captured.err and "Failed to import" in captured.err

        # Fix the file
        time.sleep(0.01)
        bad_file.write_text(
            """\
from fastmcp.tools import tool

@tool
def my_tool() -> str:
    return "fixed"
"""
        )

        # Load again - should NOT warn, file is fixed
        await provider._ensure_loaded()
        captured = capsys.readouterr()
        assert "Failed to import" not in captured.err
        assert len(provider._components) == 1

        # Break it again
        time.sleep(0.01)
        bad_file.write_text("1/0  # broken again")

        # Should warn again
        await provider._ensure_loaded()
        captured = capsys.readouterr()
        # Check for warning indicator (rich may truncate long paths)
        assert "WARNING" in captured.err and "Failed to import" in captured.err


class TestFileSystemProviderIntegration:
    """Integration tests with FastMCP server."""

    async def test_provider_with_fastmcp_server(self, tmp_path: Path):
        """FileSystemProvider should work with FastMCP server."""
        (tmp_path / "greet.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def greet(name: str) -> str:
    '''Greet someone.'''
    return f"Hello, {name}!"
"""
        )

        provider = FileSystemProvider(tmp_path)
        mcp = FastMCP("TestServer", providers=[provider])

        async with Client(mcp) as client:
            # List tools
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "greet"

            # Call tool
            result = await client.call_tool("greet", {"name": "World"})
            assert "Hello, World!" in str(result)

    async def test_provider_with_resources(self, tmp_path: Path):
        """FileSystemProvider should work with resources."""
        (tmp_path / "config.py").write_text(
            """\
from fastmcp.resources import resource

@resource("config://app")
def get_config() -> str:
    '''Get app config.'''
    return '{"version": "1.0"}'
"""
        )

        provider = FileSystemProvider(tmp_path)
        mcp = FastMCP("TestServer", providers=[provider])

        async with Client(mcp) as client:
            # List resources
            resources = await client.list_resources()
            assert len(resources) == 1
            assert str(resources[0].uri) == "config://app"

            # Read resource
            result = await client.read_resource("config://app")
            assert "1.0" in str(result)

    async def test_provider_with_resource_templates(self, tmp_path: Path):
        """FileSystemProvider should work with resource templates."""
        (tmp_path / "users.py").write_text(
            """\
from fastmcp.resources import resource

@resource("users://{user_id}/profile")
def get_profile(user_id: str) -> str:
    '''Get user profile.'''
    return f'{{"id": "{user_id}", "name": "User {user_id}"}}'
"""
        )

        provider = FileSystemProvider(tmp_path)
        mcp = FastMCP("TestServer", providers=[provider])

        async with Client(mcp) as client:
            # List templates
            templates = await client.list_resource_templates()
            assert len(templates) == 1

            # Read with parameter
            result = await client.read_resource("users://123/profile")
            assert "123" in str(result)

    async def test_provider_with_prompts(self, tmp_path: Path):
        """FileSystemProvider should work with prompts."""
        (tmp_path / "analyze.py").write_text(
            """\
from fastmcp.prompts import prompt

@prompt
def analyze(topic: str) -> str:
    '''Analyze a topic.'''
    return f"Please analyze: {topic}"
"""
        )

        provider = FileSystemProvider(tmp_path)
        mcp = FastMCP("TestServer", providers=[provider])

        async with Client(mcp) as client:
            # List prompts
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            assert prompts[0].name == "analyze"

            # Get prompt
            result = await client.get_prompt("analyze", {"topic": "Python"})
            assert "Python" in str(result)

    async def test_nested_directory_structure(self, tmp_path: Path):
        """FileSystemProvider should work with nested directories."""
        # Create nested structure
        tools = tmp_path / "tools"
        tools.mkdir()
        (tools / "greet.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def greet(name: str) -> str:
    return f"Hello, {name}!"
"""
        )

        payments = tools / "payments"
        payments.mkdir()
        (payments / "charge.py").write_text(
            """\
from fastmcp.tools import tool

@tool
def charge(amount: float) -> str:
    return f"Charged ${amount}"
"""
        )

        provider = FileSystemProvider(tmp_path)
        mcp = FastMCP("TestServer", providers=[provider])

        async with Client(mcp) as client:
            tools_list = await client.list_tools()
            assert len(tools_list) == 2
            names = {t.name for t in tools_list}
            assert names == {"greet", "charge"}
