"""Integration tests for FastMCP configuration with run command."""

import json
import os
from pathlib import Path

import pytest

from fastmcp.cli.run import load_mcp_server_config
from fastmcp.utilities.mcp_server_config import (
    Deployment,
    MCPServerConfig,
)
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource


@pytest.fixture
def sample_config(tmp_path):
    """Create a sample fastmcp.json configuration file with nested structure."""
    config_data = {
        "$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
        "source": {"path": "server.py"},
        "environment": {"python": "3.11", "dependencies": ["requests"]},
        "deployment": {"transport": "stdio", "env": {"TEST_VAR": "test_value"}},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data, indent=2))

    # Create a simple server file
    server_file = tmp_path / "server.py"
    server_file.write_text("""
from fastmcp import FastMCP

mcp = FastMCP("Test Server")

@mcp.tool
def test_tool(message: str) -> str:
    return f"Echo: {message}"
""")

    return config_file


def test_load_mcp_server_config(sample_config, monkeypatch):
    """Test loading configuration and returning config subsets."""

    # Capture environment changes
    original_env = dict(os.environ)

    try:
        config = load_mcp_server_config(sample_config)

        # Check that we got the right types
        assert isinstance(config, MCPServerConfig)
        assert isinstance(config.source, FileSystemSource)
        assert isinstance(config.deployment, Deployment)
        assert isinstance(config.environment, UVEnvironment)

        # Check source - path is not resolved yet, only during load_server
        assert config.source.path == "server.py"
        assert config.source.entrypoint is None

        # Check environment config
        assert config.environment.python == "3.11"
        assert config.environment.dependencies == ["requests"]

        # Check deployment config
        assert config.deployment.transport == "stdio"
        assert config.deployment.env == {"TEST_VAR": "test_value"}

        # Check that environment variables were applied
        assert os.environ.get("TEST_VAR") == "test_value"

    finally:
        # Restore original environment
        os.environ.clear()
        os.environ.update(original_env)


def test_load_config_with_entrypoint_source(tmp_path):
    """Test loading config with entrypoint-format source."""
    config_data = {
        "source": {"path": "src/server.py", "entrypoint": "app"},
        "deployment": {"transport": "http", "port": 8000},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create the server file in subdirectory
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    server_file = src_dir / "server.py"
    server_file.write_text("# Server")

    config = load_mcp_server_config(config_file)

    # Check source - path is not resolved yet, only during load_server
    assert config.source.path == "src/server.py"
    assert config.source.entrypoint == "app"

    # Check deployment
    assert config.deployment.transport == "http"
    assert config.deployment.port == 8000


def test_load_config_with_cwd(tmp_path):
    """Test that Deployment applies working directory change."""

    # Create a subdirectory
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    config_data = {"source": {"path": "server.py"}, "deployment": {"cwd": "subdir"}}

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file in subdirectory
    server_file = subdir / "server.py"
    server_file.write_text("# Test server")

    original_cwd = os.getcwd()

    try:
        config = load_mcp_server_config(config_file)  # noqa: F841

        # Check that working directory was changed
        assert Path.cwd() == subdir.resolve()

    finally:
        # Restore original working directory
        os.chdir(original_cwd)


def test_load_config_with_relative_cwd(tmp_path):
    """Test configuration with relative working directory."""

    # Create nested subdirectories
    subdir1 = tmp_path / "dir1"
    subdir2 = subdir1 / "dir2"
    subdir2.mkdir(parents=True)

    config_data = {
        "source": {"path": "server.py"},
        "deployment": {
            "cwd": "../"  # Relative to config file location
        },
    }

    config_file = subdir2 / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file in parent directory
    server_file = subdir1 / "server.py"
    server_file.write_text("# Server")

    original_cwd = os.getcwd()

    try:
        config = load_mcp_server_config(config_file)  # noqa: F841

        # Should change to parent directory of config file
        assert Path.cwd() == subdir1.resolve()

    finally:
        os.chdir(original_cwd)


def test_load_minimal_config(tmp_path):
    """Test loading minimal configuration with only source."""
    config_data = {"source": {"path": "server.py"}}

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    config = load_mcp_server_config(config_file)

    # Check we got source - path is not resolved yet, only during load_server
    assert isinstance(config.source, FileSystemSource)
    assert config.source.path == "server.py"


def test_load_config_with_server_args(tmp_path):
    """Test configuration with server arguments."""
    config_data = {
        "source": {"path": "server.py"},
        "deployment": {"args": ["--debug", "--config", "custom.json"]},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    config = load_mcp_server_config(config_file)

    assert config.deployment.args == ["--debug", "--config", "custom.json"]


def test_load_config_with_log_level(tmp_path):
    """Test configuration with log_level setting."""
    config_data = {
        "source": {"path": "server.py"},
        "deployment": {"log_level": "DEBUG"},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    config = load_mcp_server_config(config_file)

    assert config.deployment.log_level == "DEBUG"


def test_load_config_with_various_log_levels(tmp_path):
    """Test that all valid log levels are accepted."""
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

    for level in valid_levels:
        config_data = {
            "source": {"path": "server.py"},
            "deployment": {"log_level": level},
        }

        config_file = tmp_path / f"fastmcp_{level}.json"
        config_file.write_text(json.dumps(config_data))

        # Create server file
        server_file = tmp_path / "server.py"
        server_file.write_text("# Server")

        config = load_mcp_server_config(config_file)

        assert config.deployment.log_level == level


def test_config_subset_independence(tmp_path):
    """Test that config subsets can be used independently."""
    config_data = {
        "source": {"path": "server.py"},
        "environment": {"python": "3.12", "dependencies": ["pandas"]},
        "deployment": {"transport": "http", "host": "0.0.0.0", "port": 3000},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    config = load_mcp_server_config(config_file)

    # Each subset should be independently usable
    # Path is not resolved yet, only during load_server
    assert config.source.path == "server.py"
    assert config.source.entrypoint is None

    assert config.environment.python == "3.12"
    assert config.environment.dependencies == ["pandas"]
    assert config.environment._must_run_with_uv()  # Has dependencies

    assert config.deployment.transport == "http"
    assert config.deployment.host == "0.0.0.0"
    assert config.deployment.port == 3000


def test_environment_config_path_resolution(tmp_path):
    """Test that paths in environment config are resolved correctly."""
    # Create requirements file
    reqs_file = tmp_path / "requirements.txt"
    reqs_file.write_text("fastmcp>=2.0")

    config_data = {
        "source": {"path": "server.py"},
        "environment": {
            "requirements": "requirements.txt",
            "project": ".",
            "editable": ["../other-project"],
        },
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    config = load_mcp_server_config(config_file)

    # Check that UV command is built with resolved paths
    uv_cmd = config.environment.build_command(["fastmcp", "run", "server.py"])

    assert "--with-requirements" in uv_cmd
    assert "--project" in uv_cmd
    # Path should be resolved relative to config file
    req_idx = uv_cmd.index("--with-requirements") + 1
    assert Path(uv_cmd[req_idx]).is_absolute() or uv_cmd[req_idx] == "requirements.txt"
