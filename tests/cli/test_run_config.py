"""Integration tests for FastMCP configuration with run command."""

import json
import os
from pathlib import Path

import pytest

from fastmcp.cli.run import load_fastmcp_config
from fastmcp.utilities.fastmcp_config import (
    DeploymentConfig,
    EntrypointConfig,
    EnvironmentConfig,
)


@pytest.fixture
def sample_config(tmp_path):
    """Create a sample fastmcp.json configuration file with nested structure."""
    config_data = {
        "$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
        "entrypoint": "server.py",
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


def test_load_fastmcp_config(sample_config, monkeypatch):
    """Test loading configuration and returning config subsets."""

    # Capture environment changes
    original_env = dict(os.environ)

    try:
        entrypoint, deployment, environment = load_fastmcp_config(sample_config)

        # Check that we got the right types
        assert isinstance(entrypoint, EntrypointConfig)
        assert isinstance(deployment, DeploymentConfig)
        assert isinstance(environment, EnvironmentConfig)

        # Check entrypoint
        assert entrypoint.file.endswith("server.py")
        assert Path(entrypoint.file).is_absolute()
        assert entrypoint.object is None

        # Check environment config
        assert environment.python == "3.11"
        assert environment.dependencies == ["requests"]

        # Check deployment config
        assert deployment.transport == "stdio"
        assert deployment.env == {"TEST_VAR": "test_value"}

        # Check that environment variables were applied
        assert os.environ.get("TEST_VAR") == "test_value"

    finally:
        # Restore original environment
        os.environ.clear()
        os.environ.update(original_env)


def test_load_config_with_object_entrypoint(tmp_path):
    """Test loading config with object-format entrypoint."""
    config_data = {
        "entrypoint": {"file": "src/server.py", "object": "app"},
        "deployment": {"transport": "http", "port": 8000},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create the server file in subdirectory
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    server_file = src_dir / "server.py"
    server_file.write_text("# Server")

    entrypoint, deployment, environment = load_fastmcp_config(config_file)

    # Check entrypoint resolution
    assert entrypoint.file == str(server_file.resolve())
    assert entrypoint.object == "app"

    # Check deployment
    assert deployment is not None
    assert deployment.transport == "http"
    assert deployment.port == 8000

    # No environment config
    assert environment is None


def test_load_config_with_cwd(tmp_path):
    """Test that DeploymentConfig applies working directory change."""

    # Create a subdirectory
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    config_data = {"entrypoint": "server.py", "deployment": {"cwd": "subdir"}}

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file in subdirectory
    server_file = subdir / "server.py"
    server_file.write_text("# Test server")

    original_cwd = os.getcwd()

    try:
        entrypoint, deployment, environment = load_fastmcp_config(config_file)

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
        "entrypoint": "server.py",
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
        entrypoint, deployment, environment = load_fastmcp_config(config_file)

        # Should change to parent directory of config file
        assert Path.cwd() == subdir1.resolve()

    finally:
        os.chdir(original_cwd)


def test_load_minimal_config(tmp_path):
    """Test loading minimal configuration with only entrypoint."""
    config_data = {"entrypoint": "server.py"}

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    entrypoint, deployment, environment = load_fastmcp_config(config_file)

    # Check we got entrypoint
    assert isinstance(entrypoint, EntrypointConfig)
    assert entrypoint.file == str(server_file.resolve())

    # No deployment or environment
    assert deployment is None
    assert environment is None


def test_load_config_with_server_args(tmp_path):
    """Test configuration with server arguments."""
    config_data = {
        "entrypoint": "server.py",
        "deployment": {"args": ["--debug", "--config", "custom.json"]},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    entrypoint, deployment, environment = load_fastmcp_config(config_file)

    assert deployment is not None
    assert deployment.args == ["--debug", "--config", "custom.json"]


def test_config_subset_independence(tmp_path):
    """Test that config subsets can be used independently."""
    config_data = {
        "entrypoint": "server.py",
        "environment": {"python": "3.12", "dependencies": ["pandas"]},
        "deployment": {"transport": "http", "host": "0.0.0.0", "port": 3000},
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    entrypoint, deployment, environment = load_fastmcp_config(config_file)

    # Each subset should be independently usable
    assert entrypoint.file == str(server_file.resolve())
    assert entrypoint.object is None

    assert environment is not None
    assert environment.python == "3.12"
    assert environment.dependencies == ["pandas"]
    assert environment.needs_uv()  # Has dependencies

    assert deployment is not None
    assert deployment.transport == "http"
    assert deployment.host == "0.0.0.0"
    assert deployment.port == 3000

    # Can merge deployment config with CLI args
    merged = deployment.merge_with_cli_args(
        transport=None,  # Keep config value
        host="localhost",  # Override
        port=8080,  # Override
        path="/api",  # New value
        log_level=None,
        server_args=None,
    )

    assert merged["transport"] == "http"  # Kept from config
    assert merged["host"] == "localhost"  # CLI override
    assert merged["port"] == 8080  # CLI override
    assert merged["path"] == "/api"  # CLI value


def test_environment_config_path_resolution(tmp_path):
    """Test that paths in environment config are resolved correctly."""
    # Create requirements file
    reqs_file = tmp_path / "requirements.txt"
    reqs_file.write_text("fastmcp>=2.0")

    config_data = {
        "entrypoint": "server.py",
        "environment": {
            "requirements": "requirements.txt",
            "project": ".",
            "editable": "../other-project",
        },
    }

    config_file = tmp_path / "fastmcp.json"
    config_file.write_text(json.dumps(config_data))

    # Create server file
    server_file = tmp_path / "server.py"
    server_file.write_text("# Server")

    entrypoint, deployment, environment = load_fastmcp_config(config_file)

    # Check that UV args are built with resolved paths
    assert environment is not None
    uv_args = environment.build_uv_args(["fastmcp", "run", "server.py"])

    assert "--with-requirements" in uv_args
    assert "--project" in uv_args
    # Path should be resolved relative to config file
    req_idx = uv_args.index("--with-requirements") + 1
    assert (
        Path(uv_args[req_idx]).is_absolute() or uv_args[req_idx] == "requirements.txt"
    )
