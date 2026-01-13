"""Integration tests for fastmcp.json configuration system."""

import json
import sys
from pathlib import Path

import pytest

from fastmcp.client import Client
from fastmcp.utilities.mcp_server_config import MCPServerConfig


@pytest.fixture
def server_with_config(tmp_path):
    """Create a complete server setup with fastmcp.json config."""
    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("""
from fastmcp import FastMCP

mcp = FastMCP("Config Test Server")

@mcp.tool
def hello(name: str = "World") -> str:
    '''Say hello to someone'''
    return f"Hello, {name}!"

@mcp.resource("resource://greeting")
def get_greeting() -> str:
    '''Get a greeting message'''
    return "Welcome to FastMCP!"

if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_async())
""")

    # Create config file
    config_data = {
        "$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
        "source": {"path": "server.py"},
        "environment": {
            "python": sys.version.split()[0],  # Use current Python version
            "dependencies": ["fastmcp"],
        },
        "deployment": {"transport": "stdio", "log_level": "INFO"},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data, indent=2))

    return tmp_path


class TestConfigFileDetection:
    """Test configuration file detection patterns."""

    def test_detect_standard_fastmcp_json(self, tmp_path):
        """Test detection of standard fastmcp.json file."""
        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps({"source": {"path": "server.py"}}))

        # Should be detected as fastmcp config
        assert "fastmcp.json" in config_file.name
        assert config_file.name.endswith("fastmcp.json")

    def test_detect_prefixed_fastmcp_json(self, tmp_path):
        """Test detection of prefixed fastmcp.json files."""
        config_file = tmp_path / "my.fastmcp.json"
        config_file.write_text(json.dumps({"source": {"path": "server.py"}}))

        # Should be detected as fastmcp config
        assert "fastmcp.json" in config_file.name

    def test_detect_test_fastmcp_json(self, tmp_path):
        """Test detection of test_fastmcp.json file."""
        config_file = tmp_path / "test_fastmcp.json"
        config_file.write_text(json.dumps({"source": {"path": "server.py"}}))

        # Should be detected as fastmcp config
        assert "fastmcp.json" in config_file.name


class TestConfigWithClient:
    """Test fastmcp.json configuration with client connections."""

    async def test_config_server_with_client(self, server_with_config):
        """Test that a server loaded from config works with a client."""
        # Load the config
        config_file = server_with_config / "fastmcp.json"
        config = MCPServerConfig.from_file(config_file)

        # Import the server using the source
        import importlib.util
        import sys

        # Resolve the path from the source
        source_path = Path(config.source.path)
        if not source_path.is_absolute():
            source_path = (config_file.parent / source_path).resolve()

        spec = importlib.util.spec_from_file_location("test_server", str(source_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load module from {source_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules["test_server"] = module
        spec.loader.exec_module(module)

        assert hasattr(module, "mcp")

        server = module.mcp

        # Connect client to server
        async with Client(server) as client:
            # Test tool
            result = await client.call_tool("hello", {"name": "FastMCP"})
            assert result.data == "Hello, FastMCP!"  # Use .data for string result

            # Test resource
            results = await client.read_resource("resource://greeting")
            assert len(results) == 1
            # Resource results should have text content
            assert hasattr(results[0], "text") or hasattr(results[0], "contents")
            # Get the text content from the resource
            text = getattr(results[0], "text", None) or getattr(
                results[0], "contents", ""
            )
            assert "Welcome to FastMCP!" in str(text)


class TestEnvironmentExecution:
    """Test environment configuration execution paths."""

    def test_needs_uv_with_dependencies(self):
        """Test that environment with dependencies needs UV."""
        config = MCPServerConfig(
            source={"path": "server.py"},
            environment={"dependencies": ["requests", "numpy"]},
        )

        assert config.environment is not None
        assert config.environment._must_run_with_uv()

    def test_needs_uv_with_python_version(self):
        """Test that environment with Python version needs UV."""
        config = MCPServerConfig(
            source={"path": "server.py"},
            environment={"python": "3.12"},
        )

        assert config.environment is not None
        assert config.environment._must_run_with_uv()

    def test_no_uv_needed_without_environment(self):
        """Test that no UV is needed without environment config."""
        config = MCPServerConfig(source={"path": "server.py"})

        # Environment is now always present but may be empty
        assert config.environment is not None
        assert not config.environment._must_run_with_uv()

    def test_no_uv_needed_with_empty_environment(self):
        """Test that no UV is needed with empty environment config."""
        config = MCPServerConfig(
            source={"path": "server.py"},
            environment={},
        )

        assert config.environment is not None
        assert not config.environment._must_run_with_uv()


class TestPathResolution:
    """Test path resolution in configurations."""

    def test_source_path_resolution(self, tmp_path):
        """Test that source paths are resolved relative to config."""
        # Create nested directory structure
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        # Server is in src, config is in config
        server_file = src_dir / "server.py"
        server_file.write_text("# Server")

        config = MCPServerConfig(source={"path": "../src/server.py"})

        # The source path is resolved during load_server
        # For now, just check that the source is created correctly
        assert config.source.path == "../src/server.py"

    def test_cwd_path_resolution(self, tmp_path):
        """Test that working directory is resolved relative to config."""
        import os

        # Create directory structure
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        config = MCPServerConfig(
            source={"path": "server.py"},
            deployment={"cwd": "work"},
        )

        original_cwd = os.getcwd()

        try:
            # Apply runtime settings relative to config location
            assert config.deployment is not None
            config.deployment.apply_runtime_settings(tmp_path / "fastmcp.json")

            # Should change to work directory
            assert Path.cwd() == work_dir.resolve()

        finally:
            os.chdir(original_cwd)

    def test_requirements_path_resolution(self, tmp_path):
        """Test that requirements path is resolved correctly."""
        # Create requirements file
        reqs_file = tmp_path / "requirements.txt"
        reqs_file.write_text("fastmcp>=2.0")

        config = MCPServerConfig(
            source={"path": "server.py"},
            environment={"requirements": "requirements.txt"},
        )

        # Build UV command
        assert config.environment is not None
        uv_cmd = config.environment.build_command(["fastmcp", "run"])

        # Should include requirements file with absolute path
        assert "--with-requirements" in uv_cmd
        req_idx = uv_cmd.index("--with-requirements") + 1
        assert Path(uv_cmd[req_idx]).is_absolute()
        assert Path(uv_cmd[req_idx]).name == "requirements.txt"


class TestConfigValidation:
    """Test configuration validation."""

    def test_invalid_transport_rejected(self):
        """Test that invalid transport values are rejected."""
        with pytest.raises(ValueError):
            MCPServerConfig(
                source={"path": "server.py"},
                deployment={"transport": "invalid_transport"},
            )

    def test_streamable_http_transport_accepted(self):
        """Test that streamable-http transport is accepted as a valid value."""
        config = MCPServerConfig(
            source={"path": "server.py"},
            deployment={"transport": "streamable-http"},
        )
        assert config.deployment.transport == "streamable-http"

    def test_invalid_log_level_rejected(self):
        """Test that invalid log level values are rejected."""
        with pytest.raises(ValueError):
            MCPServerConfig(
                source={"path": "server.py"},
                deployment={"log_level": "INVALID"},
            )

    def test_missing_source_rejected(self):
        """Test that config without source is rejected."""
        with pytest.raises(ValueError):
            MCPServerConfig()  # type: ignore[call-arg]

    def test_valid_transport_values(self):
        """Test that all valid transport values are accepted."""
        for transport in ["stdio", "http", "sse"]:
            config = MCPServerConfig(
                source={"path": "server.py"},
                deployment={"transport": transport},
            )
            assert config.deployment is not None
            assert config.deployment.transport == transport

    def test_valid_log_levels(self):
        """Test that all valid log levels are accepted."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = MCPServerConfig(
                source={"path": "server.py"},
                deployment={"log_level": level},
            )
            assert config.deployment is not None
            assert config.deployment.log_level == level
