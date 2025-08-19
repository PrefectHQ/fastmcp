"""Test that the JSON schema file matches the Pydantic model."""

import json
from pathlib import Path

from fastmcp.utilities.fastmcp_config.v1.fastmcp_config import generate_schema


def test_schema_file_matches_pydantic_model():
    """Test that the schema.json file matches what the Pydantic model generates."""
    # Path to the schema file
    schema_file = (
        Path(__file__).parent.parent.parent
        / "src"
        / "fastmcp"
        / "utilities"
        / "fastmcp_config"
        / "v1"
        / "schema.json"
    )

    # Load the schema file
    with open(schema_file) as f:
        file_schema = json.load(f)

    # Generate schema from Pydantic model
    generated_schema = generate_schema()

    # They should be identical
    assert file_schema == generated_schema, (
        "The schema.json file does not match the Pydantic model schema. "
        "Please regenerate the schema file by running:\n"
        'uv run python -c "from fastmcp.utilities.fastmcp_config.v1.fastmcp_config import generate_schema; '
        'import json; print(json.dumps(generate_schema(), indent=2))" > '
        f"{schema_file}"
    )


def test_schema_has_correct_id():
    """Test that the schema has the correct $id field."""
    generated_schema = generate_schema()

    assert "$id" in generated_schema
    assert (
        generated_schema["$id"]
        == "https://gofastmcp.com/schemas/fastmcp_config/v1.json"
    )


def test_schema_has_required_fields():
    """Test that the schema specifies the required fields correctly."""
    generated_schema = generate_schema()

    # Check that entrypoint is required
    assert "required" in generated_schema
    assert "entrypoint" in generated_schema["required"]

    # Check that entrypoint is in properties
    assert "properties" in generated_schema
    assert "entrypoint" in generated_schema["properties"]


def test_schema_nested_structure():
    """Test that the schema has the correct nested structure."""
    generated_schema = generate_schema()

    properties = generated_schema["properties"]

    # Check environment section
    assert "environment" in properties
    env_schema = properties["environment"]
    if "properties" in env_schema:
        env_props = env_schema["properties"]
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
                    break


def test_schema_log_level_enum():
    """Test that log_level field has correct enum values."""
    generated_schema = generate_schema()

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
