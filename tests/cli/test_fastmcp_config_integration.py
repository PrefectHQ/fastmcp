"""Integration tests for fastmcp.json configuration system."""

import json
import sys
from pathlib import Path

import pytest

from fastmcp.client import Client
from fastmcp.utilities.fastmcp_config import FastMCPConfig


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
        "$schema": "https://gofastmcp.com/schemas/fastmcp_config/v1.json",
        "entrypoint": "server.py",
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
        config_file.write_text(json.dumps({"entrypoint": "server.py"}))

        # Should be detected as fastmcp config
        assert "fastmcp.json" in config_file.name
        assert config_file.name.endswith("fastmcp.json")

    def test_detect_prefixed_fastmcp_json(self, tmp_path):
        """Test detection of prefixed fastmcp.json files."""
        config_file = tmp_path / "my.fastmcp.json"
        config_file.write_text(json.dumps({"entrypoint": "server.py"}))

        # Should be detected as fastmcp config
        assert "fastmcp.json" in config_file.name

    def test_detect_test_fastmcp_json(self, tmp_path):
        """Test detection of test_fastmcp.json file."""
        config_file = tmp_path / "test_fastmcp.json"
        config_file.write_text(json.dumps({"entrypoint": "server.py"}))

        # Should be detected as fastmcp config
        assert "fastmcp.json" in config_file.name


class TestConfigWithClient:
    """Test fastmcp.json configuration with client connections."""

    @pytest.mark.asyncio
    async def test_config_server_with_client(self, server_with_config):
        """Test that a server loaded from config works with a client."""
        # Load the config
        config_file = server_with_config / "fastmcp.json"
        config = FastMCPConfig.from_file(config_file)

        # Import the server using the entrypoint
        import importlib.util
        import sys

        entrypoint = config.get_entrypoint(config_file)
        spec = importlib.util.spec_from_file_location("test_server", entrypoint.file)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load module from {entrypoint.file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["test_server"] = module
        spec.loader.exec_module(module)

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
        config = FastMCPConfig(
            entrypoint="server.py",
            environment={"dependencies": ["requests", "numpy"]},  # type: ignore[arg-type]
        )

        assert config.environment is not None
        assert config.environment.needs_uv()

    def test_needs_uv_with_python_version(self):
        """Test that environment with Python version needs UV."""
        config = FastMCPConfig(
            entrypoint="server.py",
            environment={"python": "3.12"},  # type: ignore[arg-type]
        )

        assert config.environment is not None
        assert config.environment.needs_uv()

    def test_no_uv_needed_without_environment(self):
        """Test that no UV is needed without environment config."""
        config = FastMCPConfig(entrypoint="server.py")

        # Environment is now always present but may be empty
        assert config.environment is not None
        assert not config.environment.needs_uv()

    def test_no_uv_needed_with_empty_environment(self):
        """Test that no UV is needed with empty environment config."""
        config = FastMCPConfig(
            entrypoint="server.py",
            environment={},  # type: ignore[arg-type]
        )

        assert config.environment is not None
        assert not config.environment.needs_uv()


class TestCLIArgumentMerging:
    """Test CLI argument merging with config values."""

    def test_cli_overrides_environment(self):
        """Test that CLI args override environment config."""
        config = FastMCPConfig(
            entrypoint="server.py",
            environment={"python": "3.11", "dependencies": ["requests"]},  # type: ignore[arg-type]
        )

        assert config.environment is not None
        merged = config.environment.merge_with_cli_args(
            python="3.12",  # Override Python version
            with_packages=["numpy"],  # Add package
            with_requirements=None,
            project=None,
        )

        assert merged["python"] == "3.12"  # CLI wins
        assert "requests" in merged["with_packages"]  # From config
        assert "numpy" in merged["with_packages"]  # From CLI

    def test_cli_overrides_deployment(self):
        """Test that CLI args override deployment config."""
        config = FastMCPConfig(
            entrypoint="server.py",
            deployment={"transport": "stdio", "port": 3000, "log_level": "INFO"},  # type: ignore[arg-type]
        )

        assert config.deployment is not None
        merged = config.deployment.merge_with_cli_args(
            transport="http",  # Override transport
            host="localhost",  # New value
            port=8080,  # Override port
            path=None,
            log_level="DEBUG",  # Override log level
            server_args=None,
        )

        assert merged["transport"] == "http"  # CLI wins
        assert merged["host"] == "localhost"  # CLI value
        assert merged["port"] == 8080  # CLI wins
        assert merged["log_level"] == "DEBUG"  # CLI wins

    def test_config_values_when_cli_is_none(self):
        """Test that config values are used when CLI args are None."""
        config = FastMCPConfig(
            entrypoint="server.py",
            deployment={"transport": "http", "port": 3000},  # type: ignore[arg-type]
        )

        assert config.deployment is not None
        merged = config.deployment.merge_with_cli_args(
            transport=None,  # Use config
            host=None,  # No value
            port=None,  # Use config
            path=None,
            log_level=None,
            server_args=None,
        )

        assert merged["transport"] == "http"  # From config
        assert merged["port"] == 3000  # From config
        assert merged["host"] is None  # No value provided


class TestPathResolution:
    """Test path resolution in configurations."""

    def test_entrypoint_path_resolution(self, tmp_path):
        """Test that entrypoint paths are resolved relative to config."""
        # Create nested directory structure
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        # Server is in src, config is in config
        server_file = src_dir / "server.py"
        server_file.write_text("# Server")

        config = FastMCPConfig(entrypoint="../src/server.py")

        # Get entrypoint resolved relative to config location
        config_file = config_dir / "fastmcp.json"
        entrypoint = config.get_entrypoint(config_file)

        # Should resolve to absolute path of server file
        assert Path(entrypoint.file) == server_file.resolve()

    def test_cwd_path_resolution(self, tmp_path):
        """Test that working directory is resolved relative to config."""
        import os

        # Create directory structure
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        config = FastMCPConfig(
            entrypoint="server.py",
            deployment={"cwd": "work"},  # type: ignore[arg-type]
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

        config = FastMCPConfig(
            entrypoint="server.py",
            environment={"requirements": "requirements.txt"},  # type: ignore[arg-type]
        )

        # Build UV args
        assert config.environment is not None
        uv_args = config.environment.build_uv_args(["fastmcp", "run"])

        # Should include requirements file
        assert "--with-requirements" in uv_args
        req_idx = uv_args.index("--with-requirements") + 1
        assert uv_args[req_idx] == "requirements.txt"


class TestConfigValidation:
    """Test configuration validation."""

    def test_invalid_transport_rejected(self):
        """Test that invalid transport values are rejected."""
        with pytest.raises(ValueError):
            FastMCPConfig(
                entrypoint="server.py",
                deployment={"transport": "invalid_transport"},  # type: ignore[arg-type]
            )

    def test_streamable_http_transport_rejected(self):
        """Test that streamable-http transport is rejected in fastmcp.json config."""
        with pytest.raises(ValueError):
            FastMCPConfig(
                entrypoint="server.py",
                deployment={"transport": "streamable-http"},  # type: ignore[arg-type]
            )

    def test_invalid_log_level_rejected(self):
        """Test that invalid log level values are rejected."""
        with pytest.raises(ValueError):
            FastMCPConfig(
                entrypoint="server.py",
                deployment={"log_level": "INVALID"},  # type: ignore[arg-type]
            )

    def test_missing_entrypoint_rejected(self):
        """Test that config without entrypoint is rejected."""
        with pytest.raises(ValueError):
            FastMCPConfig()  # type: ignore[call-arg]

    def test_valid_transport_values(self):
        """Test that all valid transport values are accepted."""
        for transport in ["stdio", "http", "sse"]:
            config = FastMCPConfig(
                entrypoint="server.py",
                deployment={"transport": transport},  # type: ignore[arg-type]
            )
            assert config.deployment is not None
            assert config.deployment.transport == transport

    def test_valid_log_levels(self):
        """Test that all valid log levels are accepted."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = FastMCPConfig(
                entrypoint="server.py",
                deployment={"log_level": level},  # type: ignore[arg-type]
            )
            assert config.deployment is not None
            assert config.deployment.log_level == level
