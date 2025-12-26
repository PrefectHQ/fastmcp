"""Tests for FastMCP configuration file support with nested structure."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from fastmcp.utilities.mcp_server_config import (
    Deployment,
    MCPServerConfig,
)
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource


class TestFileSystemSource:
    """Test FileSystemSource class."""

    def test_dict_source_minimal(self):
        """Test that dict source is converted to FileSystemSource."""
        config = MCPServerConfig(source={"path": "server.py"})
        # Dict is converted to FileSystemSource
        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "server.py"
        assert config.source.entrypoint is None
        assert config.source.type == "filesystem"

    def test_dict_source_with_entrypoint(self):
        """Test dict source with entrypoint field."""
        config = MCPServerConfig(source={"path": "server.py", "entrypoint": "app"})
        # Dict with entrypoint is converted to FileSystemSource
        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "server.py"
        assert config.source.entrypoint == "app"
        assert config.source.type == "filesystem"

    def test_filesystem_source_entrypoint(self):
        """Test FileSystemSource entrypoint format."""
        config = MCPServerConfig(
            source=FileSystemSource(path="src/server.py", entrypoint="mcp")
        )
        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "src/server.py"
        assert config.source.entrypoint == "mcp"
        assert config.source.type == "filesystem"


class TestEnvironment:
    """Test Environment class."""

    def test_environment_config_fields(self):
        """Test all Environment fields."""
        config = MCPServerConfig(
            source={"path": "server.py"},
            environment={
                "python": "3.12",
                "dependencies": ["requests", "numpy>=2.0"],
                "requirements": "requirements.txt",
                "project": ".",
                "editable": ["../my-package"],
            },
        )

        env = config.environment
        assert env.python == "3.12"
        assert env.dependencies == ["requests", "numpy>=2.0"]
        # Paths are stored as Path objects
        assert env.requirements == Path("requirements.txt")
        assert env.project == Path(".")
        assert env.editable == [Path("../my-package")]

    def test_needs_uv(self):
        """Test needs_uv() method."""
        # No environment config - doesn't need UV
        config = MCPServerConfig(source={"path": "server.py"})
        assert not config.environment._must_run_with_uv()

        # Empty environment - doesn't need UV
        config = MCPServerConfig(source={"path": "server.py"}, environment={})
        assert not config.environment._must_run_with_uv()

        # With dependencies - needs UV
        config = MCPServerConfig(
            source={"path": "server.py"}, environment={"dependencies": ["requests"]}
        )
        assert config.environment._must_run_with_uv()

        # With Python version - needs UV
        config = MCPServerConfig(
            source={"path": "server.py"}, environment={"python": "3.12"}
        )
        assert config.environment._must_run_with_uv()

    def test_build_uv_run_command(self):
        """Test build_uv_run_command() method."""
        config = MCPServerConfig(
            source={"path": "server.py"},
            environment={
                "python": "3.12",
                "dependencies": ["requests", "numpy"],
                "requirements": "requirements.txt",
                "project": ".",
            },
        )

        cmd = config.environment.build_command(["fastmcp", "run", "server.py"])

        assert cmd[0] == "uv"
        assert cmd[1] == "run"
        # Python version not added when project is specified (project defines its own Python)
        assert "--python" not in cmd
        assert "3.12" not in cmd
        assert "--project" in cmd
        # Project path should be resolved to absolute path
        project_idx = cmd.index("--project")
        assert Path(cmd[project_idx + 1]).is_absolute()
        assert "--with" in cmd
        assert "requests" in cmd
        assert "numpy" in cmd
        assert "--with-requirements" in cmd
        # Requirements path should be resolved to absolute path
        req_idx = cmd.index("--with-requirements")
        assert Path(cmd[req_idx + 1]).is_absolute()
        # Command args should be at the end
        assert "fastmcp" in cmd[-3:]
        assert "run" in cmd[-2:]
        assert "server.py" in cmd[-1:]


class TestDeployment:
    """Test Deployment class."""

    def test_deployment_config_fields(self):
        """Test all Deployment fields."""
        config = MCPServerConfig(
            source={"path": "server.py"},
            deployment={
                "transport": "http",
                "host": "0.0.0.0",
                "port": 8000,
                "path": "/api/",
                "log_level": "DEBUG",
                "env": {"API_KEY": "secret"},
                "cwd": "./work",
                "args": ["--debug"],
            },
        )

        deploy = config.deployment
        assert deploy.transport == "http"
        assert deploy.host == "0.0.0.0"
        assert deploy.port == 8000
        assert deploy.path == "/api/"
        assert deploy.log_level == "DEBUG"
        assert deploy.env == {"API_KEY": "secret"}
        assert deploy.cwd == "./work"
        assert deploy.args == ["--debug"]

    def test_apply_runtime_settings(self, tmp_path):
        """Test apply_runtime_settings() method."""
        import os

        # Create config with env vars and cwd
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        config = MCPServerConfig(
            source={"path": "server.py"},
            deployment={
                "env": {"TEST_VAR": "test_value"},
                "cwd": "work",
            },
        )

        original_cwd = os.getcwd()
        original_env = os.environ.get("TEST_VAR")

        try:
            config.deployment.apply_runtime_settings(tmp_path / "fastmcp.json")

            # Check environment variable was set
            assert os.environ["TEST_VAR"] == "test_value"

            # Check working directory was changed
            assert Path.cwd() == work_dir.resolve()

        finally:
            # Restore original state
            os.chdir(original_cwd)
            if original_env is None:
                os.environ.pop("TEST_VAR", None)
            else:
                os.environ["TEST_VAR"] = original_env

    def test_env_var_interpolation(self, tmp_path):
        """Test environment variable interpolation in deployment env."""
        import os

        # Set up test environment variables
        os.environ["BASE_URL"] = "example.com"
        os.environ["ENV_NAME"] = "production"

        config = MCPServerConfig(
            source={"path": "server.py"},
            deployment={
                "env": {
                    "API_URL": "https://api.${BASE_URL}/v1",
                    "DATABASE": "postgres://${ENV_NAME}.db",
                    "PREFIXED": "MY_${ENV_NAME}_SERVER",
                    "MISSING": "value_${NONEXISTENT}_here",
                    "STATIC": "no_interpolation",
                }
            },
        )

        original_values = {
            key: os.environ.get(key)
            for key in ["API_URL", "DATABASE", "PREFIXED", "MISSING", "STATIC"]
        }

        try:
            config.deployment.apply_runtime_settings()

            # Check interpolated values
            assert os.environ["API_URL"] == "https://api.example.com/v1"
            assert os.environ["DATABASE"] == "postgres://production.db"
            assert os.environ["PREFIXED"] == "MY_production_SERVER"
            # Missing variables should keep the placeholder
            assert os.environ["MISSING"] == "value_${NONEXISTENT}_here"
            # Static values should remain unchanged
            assert os.environ["STATIC"] == "no_interpolation"

        finally:
            # Clean up
            os.environ.pop("BASE_URL", None)
            os.environ.pop("ENV_NAME", None)
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class TestMCPServerConfig:
    """Test MCPServerConfig root configuration."""

    def test_minimal_config(self):
        """Test creating a config with only required fields."""
        config = MCPServerConfig(source={"path": "server.py"})
        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "server.py"
        assert config.source.entrypoint is None
        # Environment and deployment are now always present but empty
        assert isinstance(config.environment, UVEnvironment)
        assert isinstance(config.deployment, Deployment)
        # Check they have no values set
        assert not config.environment._must_run_with_uv()
        assert all(
            getattr(config.deployment, field, None) is None
            for field in Deployment.model_fields
        )

    def test_nested_structure(self):
        """Test the nested configuration structure."""
        config = MCPServerConfig(
            source={"path": "server.py"},
            environment={
                "python": "3.12",
                "dependencies": ["fastmcp"],
            },
            deployment={
                "transport": "stdio",
                "log_level": "INFO",
            },
        )

        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "server.py"
        assert config.source.entrypoint is None
        assert isinstance(config.environment, UVEnvironment)
        assert isinstance(config.deployment, Deployment)

    def test_from_file(self, tmp_path):
        """Test loading config from JSON file with nested structure."""
        config_data = {
            "$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
            "source": {"path": "src/server.py", "entrypoint": "app"},
            "environment": {"python": "3.12", "dependencies": ["requests"]},
            "deployment": {"transport": "http", "port": 8000},
        }

        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps(config_data))

        config = MCPServerConfig.from_file(config_file)

        # When loaded from JSON with entrypoint format, it becomes EntrypointConfig
        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "src/server.py"
        assert config.source.entrypoint == "app"
        assert config.environment.python == "3.12"
        assert config.environment.dependencies == ["requests"]
        assert config.deployment.transport == "http"
        assert config.deployment.port == 8000

    def test_from_file_with_string_entrypoint(self, tmp_path):
        """Test loading config with dict source format."""
        config_data = {
            "source": {"path": "server.py", "entrypoint": "mcp"},
            "environment": {"dependencies": ["fastmcp"]},
        }

        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps(config_data))

        config = MCPServerConfig.from_file(config_file)
        # String entrypoint with : should be converted to EntrypointConfig
        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "server.py"
        assert config.source.entrypoint == "mcp"

    def test_string_entrypoint_with_entrypoint_and_environment(self, tmp_path):
        """Test that file.py:entrypoint syntax works with environment config."""
        config_data = {
            "source": {"path": "src/server.py", "entrypoint": "app"},
            "environment": {"python": "3.12", "dependencies": ["fastmcp", "requests"]},
            "deployment": {"transport": "http", "port": 8000},
        }

        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps(config_data))

        config = MCPServerConfig.from_file(config_file)

        # Should be parsed into EntrypointConfig
        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "src/server.py"
        assert config.source.entrypoint == "app"

        # Environment config should still work
        assert config.environment.python == "3.12"
        assert config.environment.dependencies == ["fastmcp", "requests"]

        # Deployment config should still work
        assert config.deployment.transport == "http"
        assert config.deployment.port == 8000

    def test_find_config_in_current_dir(self, tmp_path):
        """Test finding config in current directory."""
        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps({"source": {"path": "server.py"}}))

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            found = MCPServerConfig.find_config()
            assert found == config_file
        finally:
            os.chdir(original_cwd)

    def test_find_config_not_in_parent_dir(self, tmp_path):
        """Test that config is NOT found in parent directory."""
        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps({"source": {"path": "server.py"}}))

        subdir = tmp_path / "subdir"
        subdir.mkdir()

        # Should NOT find config in parent directory
        found = MCPServerConfig.find_config(subdir)
        assert found is None

    def test_find_config_in_specified_dir(self, tmp_path):
        """Test finding config in the specified directory."""
        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps({"source": {"path": "server.py"}}))

        # Should find config when looking in the directory that contains it
        found = MCPServerConfig.find_config(tmp_path)
        assert found == config_file

    def test_find_config_not_found(self, tmp_path):
        """Test when config is not found."""
        found = MCPServerConfig.find_config(tmp_path)
        assert found is None

    def test_invalid_transport(self, tmp_path):
        """Test loading config with invalid transport value."""
        config_data = {
            "source": {"path": "server.py"},
            "deployment": {"transport": "invalid_transport"},
        }

        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps(config_data))

        with pytest.raises(ValidationError):
            MCPServerConfig.from_file(config_file)

    def test_optional_sections(self):
        """Test that all config sections are optional except source."""
        # Only source is required
        config = MCPServerConfig(source={"path": "server.py"})
        assert isinstance(config.source, FileSystemSource)
        assert config.source.path == "server.py"
        # Environment and deployment are now always present but may be empty
        assert isinstance(config.environment, UVEnvironment)
        assert isinstance(config.deployment, Deployment)

        # Only environment with values
        config = MCPServerConfig(
            source={"path": "server.py"}, environment={"python": "3.12"}
        )
        assert config.environment.python == "3.12"
        assert isinstance(config.deployment, Deployment)
        assert all(
            getattr(config.deployment, field, None) is None
            for field in Deployment.model_fields
        )

        # Only deployment with values
        config = MCPServerConfig(
            source={"path": "server.py"}, deployment={"transport": "http"}
        )
        assert isinstance(config.environment, UVEnvironment)
        # Check all fields except 'type' which has a default value
        assert all(
            getattr(config.environment, field, None) is None
            for field in UVEnvironment.model_fields
            if field != "type"
        )
        assert config.deployment.transport == "http"


class TestMCPServerConfigRoundtrip:
    """Test that MCPServerConfig survives model_dump() -> reconstruct pattern.

    This is used by the CLI to apply overrides immutably.
    """

    def test_roundtrip_preserves_schema(self):
        """Ensure schema_ field survives dump/reconstruct cycle."""
        config = MCPServerConfig(source=FileSystemSource(path="server.py"))
        config_dict = config.model_dump()
        reconstructed = MCPServerConfig(**config_dict)
        assert reconstructed.schema_ == config.schema_

    def test_roundtrip_with_all_fields(self):
        """Full config survives dump/reconstruct."""
        config = MCPServerConfig(
            source=FileSystemSource(path="server.py", entrypoint="app"),
            environment=UVEnvironment(python="3.11"),
            deployment=Deployment(transport="http", port=8080),
        )
        config_dict = config.model_dump()
        reconstructed = MCPServerConfig(**config_dict)
        assert reconstructed.source.path == "server.py"
        assert reconstructed.environment.python == "3.11"
        assert reconstructed.deployment.port == 8080
