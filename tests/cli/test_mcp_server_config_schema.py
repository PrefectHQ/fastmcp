"""Test that the generated JSON schema has the correct structure."""

import pytest

from fastmcp.utilities.mcp_server_config.v1.mcp_server_config import (
    Deployment,
    generate_schema,
)


def test_schema_has_correct_id():
    """Test that the schema has the correct $id field."""
    generated_schema = generate_schema()

    assert generated_schema is not None
    assert "$id" in generated_schema
    assert (
        generated_schema["$id"]
        == "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json"
    )


def test_schema_has_required_fields():
    """Test that the schema specifies the required fields correctly."""
    generated_schema = generate_schema()

    assert generated_schema is not None
    # Check that source is required
    assert "required" in generated_schema
    assert "source" in generated_schema["required"]

    # Check that source is in properties
    assert "properties" in generated_schema
    assert "source" in generated_schema["properties"]


def test_schema_nested_structure():
    """Test that the schema has the correct nested structure."""
    generated_schema = generate_schema()

    assert generated_schema is not None
    properties = generated_schema["properties"]

    # Check environment section
    assert "environment" in properties
    env_schema = properties["environment"]
    # Environment can be in anyOf or direct properties
    if "anyOf" in env_schema:
        # Find the UVEnvironment in anyOf
        for option in env_schema["anyOf"]:
            if option.get("type") == "object" and "properties" in option:
                env_props = option["properties"]
                assert "type" in env_props  # New type field
                assert "python" in env_props
                assert "dependencies" in env_props
                assert "requirements" in env_props
                assert "project" in env_props
                assert "editable" in env_props
                break
    elif "properties" in env_schema:
        env_props = env_schema["properties"]
        assert "type" in env_props  # New type field
        assert "python" in env_props
        assert "dependencies" in env_props
        assert "requirements" in env_props
        assert "project" in env_props
        assert "editable" in env_props

    # Check deployment section
    assert "deployment" in properties
    deploy_schema = properties["deployment"]
    if "properties" in deploy_schema:
        deploy_props = deploy_schema["properties"]
        assert "transport" in deploy_props
        assert "host" in deploy_props
        assert "port" in deploy_props
        assert "log_level" in deploy_props
        assert "env" in deploy_props
        assert "cwd" in deploy_props
        assert "args" in deploy_props


def test_schema_transport_enum():
    """Test that transport field has correct enum values."""
    generated_schema = generate_schema()

    assert generated_schema is not None
    # Navigate to transport field
    deploy_schema = generated_schema["properties"]["deployment"]

    # Handle both direct properties and anyOf cases
    if "anyOf" in deploy_schema:
        # Find the object type in anyOf
        for option in deploy_schema["anyOf"]:
            if option.get("type") == "object" and "properties" in option:
                transport_schema = option["properties"].get("transport", {})
                if "anyOf" in transport_schema:
                    # Look for enum in anyOf options
                    for trans_option in transport_schema["anyOf"]:
                        if "enum" in trans_option:
                            valid_transports = trans_option["enum"]
                            assert "stdio" in valid_transports
                            assert "http" in valid_transports
                            assert "sse" in valid_transports
                            assert "streamable-http" in valid_transports
                            break
    elif "properties" in deploy_schema:
        transport_schema = deploy_schema["properties"].get("transport", {})
        if "anyOf" in transport_schema:
            for option in transport_schema["anyOf"]:
                if "enum" in option:
                    valid_transports = option["enum"]
                    assert "stdio" in valid_transports
                    assert "http" in valid_transports
                    assert "sse" in valid_transports
                    assert "streamable-http" in valid_transports
                    break


def test_schema_log_level_enum():
    """Test that log_level field has correct enum values."""
    generated_schema = generate_schema()

    assert generated_schema is not None
    # Navigate to log_level field
    deploy_schema = generated_schema["properties"]["deployment"]

    # Handle both direct properties and anyOf cases
    if "anyOf" in deploy_schema:
        # Find the object type in anyOf
        for option in deploy_schema["anyOf"]:
            if option.get("type") == "object" and "properties" in option:
                log_level_schema = option["properties"].get("log_level", {})
                if "anyOf" in log_level_schema:
                    # Look for enum in anyOf options
                    for level_option in log_level_schema["anyOf"]:
                        if "enum" in level_option:
                            valid_levels = level_option["enum"]
                            assert "DEBUG" in valid_levels
                            assert "INFO" in valid_levels
                            assert "WARNING" in valid_levels
                            assert "ERROR" in valid_levels
                            assert "CRITICAL" in valid_levels
                            break
    elif "properties" in deploy_schema:
        log_level_schema = deploy_schema["properties"].get("log_level", {})
        if "anyOf" in log_level_schema:
            for option in log_level_schema["anyOf"]:
                if "enum" in option:
                    valid_levels = option["enum"]
                    assert "DEBUG" in valid_levels
                    assert "INFO" in valid_levels
                    assert "WARNING" in valid_levels
                    assert "ERROR" in valid_levels
                    assert "CRITICAL" in valid_levels
                    break


@pytest.mark.parametrize(
    "transport",
    [
        "streamable-http",
        "http",
        "stdio",
        "sse",
        None,
    ],
)
def test_transport_values_accepted(transport):
    """Test that all valid transport values are accepted."""
    deployment = Deployment(transport=transport)
    assert deployment.transport == transport
