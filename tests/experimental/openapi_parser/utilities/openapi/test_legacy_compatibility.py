"""Tests to ensure new OpenAPI implementation matches legacy behavior exactly."""

import pytest

from fastmcp.experimental.utilities.openapi.models import (
    HTTPRoute,
    ParameterInfo,
    RequestBodyInfo,
)
from fastmcp.experimental.utilities.openapi.schemas import (
    _combine_schemas_and_map_params,
)
from fastmcp.utilities.openapi import HTTPRoute as LegacyHTTPRoute
from fastmcp.utilities.openapi import ParameterInfo as LegacyParameterInfo
from fastmcp.utilities.openapi import RequestBodyInfo as LegacyRequestBodyInfo
from fastmcp.utilities.openapi import _combine_schemas as legacy_combine_schemas


class TestLegacyCompatibility:
    """Test that new implementation produces identical schemas to legacy."""

    def test_optional_parameter_nullable_behavior(self):
        """Test that optional parameters get anyOf with null, required don't."""
        # Legacy route
        legacy_route = LegacyHTTPRoute(
            method="GET",
            path="/test",
            parameters=[
                LegacyParameterInfo(
                    name="required_param",
                    location="query",
                    required=True,
                    schema={"type": "string"},
                ),
                LegacyParameterInfo(
                    name="optional_param",
                    location="query",
                    required=False,
                    schema={"type": "string"},
                ),
            ],
            request_body=None,
            responses={},
            summary="Test endpoint",
            schema_definitions={},
        )

        # New route (equivalent)
        new_route = HTTPRoute(
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

        # Generate schemas
        legacy_schema = legacy_combine_schemas(legacy_route)
        new_schema, _ = _combine_schemas_and_map_params(new_route)

        # Required parameter should have simple type
        assert legacy_schema["properties"]["required_param"]["type"] == "string"
        assert new_schema["properties"]["required_param"]["type"] == "string"
        assert "anyOf" not in legacy_schema["properties"]["required_param"]
        assert "anyOf" not in new_schema["properties"]["required_param"]

        # Both implementations now correctly preserve original schema
        # Neither should make optional parameters nullable - they can simply be omitted
        assert "anyOf" not in legacy_schema["properties"]["optional_param"]
        assert "anyOf" not in new_schema["properties"]["optional_param"]
        assert legacy_schema["properties"]["optional_param"]["type"] == "string"
        assert new_schema["properties"]["optional_param"]["type"] == "string"

        # Required lists should match
        assert set(legacy_schema["required"]) == set(new_schema["required"])
        assert "required_param" in legacy_schema["required"]
        assert "optional_param" not in legacy_schema["required"]

    def test_parameter_collision_handling(self):
        """Test that parameter collisions are handled identically."""
        # Legacy route with collision (path param 'id' and body property 'id')
        legacy_route = LegacyHTTPRoute(
            method="PUT",
            path="/users/{id}",
            parameters=[
                LegacyParameterInfo(
                    name="id",
                    location="path",
                    required=True,
                    schema={"type": "integer"},
                )
            ],
            request_body=LegacyRequestBodyInfo(
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
            responses={},
            summary="Update user",
            schema_definitions={},
        )

        # New route (equivalent)
        new_route = HTTPRoute(
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

        # Generate schemas
        legacy_schema = legacy_combine_schemas(legacy_route)
        new_schema, param_map = _combine_schemas_and_map_params(new_route)

        # Should have path parameter with suffix
        assert "id__path" in legacy_schema["properties"]
        assert "id__path" in new_schema["properties"]

        # Should have body parameter without suffix
        assert "id" in legacy_schema["properties"]
        assert "id" in new_schema["properties"]

        # Should have name parameter from body
        assert "name" in legacy_schema["properties"]
        assert "name" in new_schema["properties"]

        # Required should include path param (suffixed) and required body params
        legacy_required = set(legacy_schema["required"])
        new_required = set(new_schema["required"])

        assert "id__path" in legacy_required
        assert "id__path" in new_required
        assert "name" in legacy_required  # required in body
        assert "name" in new_required

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
        # Legacy route
        legacy_route = LegacyHTTPRoute(
            method="GET",
            path="/test",
            parameters=[
                LegacyParameterInfo(
                    name="optional_param",
                    location="query",
                    required=False,
                    schema=param_type,
                )
            ],
            request_body=None,
            responses={},
            summary="Test endpoint",
            schema_definitions={},
        )

        # New route
        new_route = HTTPRoute(
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

        # Generate schemas
        legacy_schema = legacy_combine_schemas(legacy_route)
        new_schema, _ = _combine_schemas_and_map_params(new_route)

        # Both implementations now correctly preserve original schema
        legacy_param = legacy_schema["properties"]["optional_param"]
        new_param = new_schema["properties"]["optional_param"]

        # Both should preserve original schema without making it nullable
        assert "anyOf" not in legacy_param
        assert "anyOf" not in new_param

        # Both should match the original parameter schema (plus description in legacy)
        for key, value in param_type.items():
            assert legacy_param[key] == value
            assert new_param[key] == value

    def test_no_parameters_no_body(self):
        """Test schema generation when there are no parameters or body."""
        # Legacy route
        legacy_route = LegacyHTTPRoute(
            method="GET",
            path="/health",
            parameters=[],
            request_body=None,
            responses={},
            summary="Health check",
            schema_definitions={},
        )

        # New route
        new_route = HTTPRoute(
            method="GET",
            path="/health",
            operation_id="health_check",
        )

        # Generate schemas
        legacy_schema = legacy_combine_schemas(legacy_route)
        new_schema, param_map = _combine_schemas_and_map_params(new_route)

        # Both should have empty object schemas
        assert legacy_schema["type"] == "object"
        assert new_schema["type"] == "object"
        assert legacy_schema["properties"] == {}
        assert new_schema["properties"] == {}
        assert legacy_schema["required"] == []
        assert new_schema["required"] == []
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

        # Legacy route
        legacy_route = LegacyHTTPRoute(
            method="POST",
            path="/items",
            parameters=[],
            request_body=LegacyRequestBodyInfo(
                required=True,
                content_schema=body_schema,
            ),
            responses={},
            summary="Create item",
            schema_definitions={},
        )

        # New route
        new_route = HTTPRoute(
            method="POST",
            path="/items",
            operation_id="create_item",
            request_body=RequestBodyInfo(
                required=True,
                content_schema=body_schema,
            ),
        )

        # Generate schemas
        legacy_schema = legacy_combine_schemas(legacy_route)
        new_schema, param_map = _combine_schemas_and_map_params(new_route)

        # Should have body properties
        assert "title" in legacy_schema["properties"]
        assert "description" in legacy_schema["properties"]
        assert "title" in new_schema["properties"]
        assert "description" in new_schema["properties"]

        # Required should match body requirements
        assert "title" in legacy_schema["required"]
        assert "title" in new_schema["required"]
        assert "description" not in legacy_schema["required"]
        assert "description" not in new_schema["required"]

        # Parameter map should map body properties
        assert param_map["title"]["location"] == "body"
        assert param_map["description"]["location"] == "body"
