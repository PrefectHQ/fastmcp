"""Test for optional parameter handling in FastMCP OpenAPI integration."""

import pytest

from fastmcp.utilities.openapi import HTTPRoute, ParameterInfo, _combine_schemas


async def test_optional_parameter_schema_preserves_original_type():
    """Test that optional parameters preserve their original schema without forcing nullable behavior."""
    # Create a minimal HTTPRoute with optional parameter
    optional_param = ParameterInfo(
        name="optional_param",
        location="query",
        required=False,
        schema={"type": "string"},
        description="Optional parameter",
    )

    required_param = ParameterInfo(
        name="required_param",
        location="query",
        required=True,
        schema={"type": "string"},
        description="Required parameter",
    )

    route = HTTPRoute(
        method="GET",
        path="/test",
        parameters=[required_param, optional_param],
        request_body=None,
        responses={},
        summary="Test endpoint",
        description=None,
        schema_definitions={},
    )

    # Generate combined schema
    schema = _combine_schemas(route)

    # Verify that optional parameter preserves original schema
    optional_param_schema = schema["properties"]["optional_param"]

    # Should preserve the original type without making it nullable
    assert optional_param_schema["type"] == "string"
    assert "anyOf" not in optional_param_schema

    # Required parameter should not allow null
    required_param_schema = schema["properties"]["required_param"]
    assert required_param_schema["type"] == "string"
    assert "anyOf" not in required_param_schema

    # Required list should only contain required param
    assert "required_param" in schema["required"]
    assert "optional_param" not in schema["required"]


@pytest.mark.parametrize(
    "param_schema",
    [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "array", "items": {"type": "string"}},
        {"type": "object", "properties": {"name": {"type": "string"}}},
    ],
)
async def test_optional_parameter_preserves_schema_for_all_types(param_schema):
    """Test that optional parameters of any type preserve their original schema without nullable behavior."""
    optional_param = ParameterInfo(
        name="optional_param",
        location="query",
        required=False,
        schema=param_schema,
        description="Optional parameter",
    )

    route = HTTPRoute(
        method="GET",
        path="/test",
        parameters=[optional_param],
        request_body=None,
        responses={},
        summary="Test endpoint",
        description=None,
        schema_definitions={},
    )

    # Generate combined schema
    schema = _combine_schemas(route)
    optional_param_schema = schema["properties"]["optional_param"]

    # Should preserve the original schema exactly without making it nullable
    assert "anyOf" not in optional_param_schema

    # The schema should include the original type and fields, plus the description
    for key, value in param_schema.items():
        assert optional_param_schema[key] == value
    assert optional_param_schema.get("description") == "Optional parameter"
