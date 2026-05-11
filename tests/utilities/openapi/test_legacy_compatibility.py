"""Tests to ensure OpenAPI schema generation works correctly."""

import pytest

from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    ParameterInfo,
    RequestBodyInfo,
)
from fastmcp.utilities.openapi.schemas import (
    _combine_schemas_and_map_params,
)


class TestSchemaGeneration:
    """Test that OpenAPI schema generation produces correct schemas."""

    def test_optional_parameter_nullable_behavior(self):
        """Test that optional parameters are not made nullable - they can simply be omitted."""
        route = HTTPRoute(
            method="GET",
            path="/test",
            operation_id="test_op",
            parameters=[
                ParameterInfo(
                    name="required_param",
                    location="query",
                    required=True,
                    schema={"type": "string"},
                ),
                ParameterInfo(
                    name="optional_param",
                    location="query",
                    required=False,
                    schema={"type": "string"},
                ),
            ],
        )

        schema, _ = _combine_schemas_and_map_params(route)

        # Required parameter should have simple type
        assert schema["properties"]["required_param"]["type"] == "string"
        assert "anyOf" not in schema["properties"]["required_param"]

        # Optional parameters should preserve original schema without making it nullable
        assert "anyOf" not in schema["properties"]["optional_param"]
        assert schema["properties"]["optional_param"]["type"] == "string"

        # Required list should only include required parameters
        assert "required_param" in schema["required"]
        assert "optional_param" not in schema["required"]

    def test_parameter_collision_handling(self):
        """Test that parameter collisions are handled with suffixes."""
        route = HTTPRoute(
            method="PUT",
            path="/users/{id}",
            operation_id="update_user",
            parameters=[
                ParameterInfo(
                    name="id",
                    location="path",
                    required=True,
                    schema={"type": "integer"},
                )
            ],
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                        "required": ["name"],
                    }
                },
            ),
        )

        schema, param_map = _combine_schemas_and_map_params(route)

        # Should have path parameter with suffix
        assert "id__path" in schema["properties"]

        # Should have body parameter without suffix
        assert "id" in schema["properties"]

        # Should have name parameter from body
        assert "name" in schema["properties"]

        # Required should include path param (suffixed) and required body params
        required = set(schema["required"])
        assert "id__path" in required
        assert "name" in required

        # Parameter map should correctly map suffixed parameter
        assert param_map["id__path"]["location"] == "path"
        assert param_map["id__path"]["openapi_name"] == "id"
        assert param_map["id"]["location"] == "body"
        assert param_map["name"]["location"] == "body"

    @pytest.mark.parametrize(
        "param_type",
        [
            {"type": "integer"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "array", "items": {"type": "string"}},
            {"type": "object", "properties": {"name": {"type": "string"}}},
        ],
    )
    def test_nullable_behavior_different_types(self, param_type):
        """Test nullable behavior works for all parameter types."""
        route = HTTPRoute(
            method="GET",
            path="/test",
            operation_id="test_op",
            parameters=[
                ParameterInfo(
                    name="optional_param",
                    location="query",
                    required=False,
                    schema=param_type,
                )
            ],
        )

        schema, _ = _combine_schemas_and_map_params(route)

        # Should preserve original schema without making it nullable
        param = schema["properties"]["optional_param"]
        assert "anyOf" not in param

        # Should match the original parameter schema
        for key, value in param_type.items():
            assert param[key] == value

    def test_no_parameters_no_body(self):
        """Test schema generation when there are no parameters or body."""
        route = HTTPRoute(
            method="GET",
            path="/health",
            operation_id="health_check",
        )

        schema, param_map = _combine_schemas_and_map_params(route)

        # Should have empty object schema
        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert schema["required"] == []
        assert param_map == {}

    def test_body_only_no_parameters(self):
        """Test schema generation with only request body, no parameters."""
        body_schema = {
            "application/json": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title"],
            }
        }

        route = HTTPRoute(
            method="POST",
            path="/items",
            operation_id="create_item",
            request_body=RequestBodyInfo(
                required=True,
                content_schema=body_schema,
            ),
        )

        schema, param_map = _combine_schemas_and_map_params(route)

        # Should have body properties
        assert "title" in schema["properties"]
        assert "description" in schema["properties"]

        # Required should match body requirements
        assert "title" in schema["required"]
        assert "description" not in schema["required"]

        # Parameter map should map body properties
        assert param_map["title"]["location"] == "body"
        assert param_map["description"]["location"] == "body"
