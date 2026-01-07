"""Unit tests for schema processing and parameter mapping."""

import pytest

from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    ParameterInfo,
    RequestBodyInfo,
)
from fastmcp.utilities.openapi.schemas import (
    _combine_schemas,
    _combine_schemas_and_map_params,
    _replace_ref_with_defs,
)


class TestSchemaProcessing:
    """Test schema processing utilities."""

    @pytest.fixture
    def simple_route(self):
        """Create a simple route for testing."""
        return HTTPRoute(
            path="/users/{id}",
            method="GET",
            operation_id="get_user",
            parameters=[
                ParameterInfo(
                    name="id",
                    location="path",
                    required=True,
                    schema={"type": "integer"},
                )
            ],
        )

    @pytest.fixture
    def collision_route(self):
        """Create a route with parameter name collisions."""
        return HTTPRoute(
            path="/users/{id}",
            method="PUT",
            operation_id="update_user",
            parameters=[
                ParameterInfo(
                    name="id",
                    location="path",
                    required=True,
                    schema={"type": "integer"},
                    description="User ID in path",
                )
            ],
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "description": "User ID in body"},
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                        },
                        "required": ["name"],
                    }
                },
            ),
        )

    @pytest.fixture
    def complex_route(self):
        """Create a complex route with multiple parameter types."""
        return HTTPRoute(
            path="/items/{id}",
            method="PATCH",
            operation_id="update_item",
            parameters=[
                ParameterInfo(
                    name="id",
                    location="path",
                    required=True,
                    schema={"type": "string"},
                ),
                ParameterInfo(
                    name="version",
                    location="query",
                    required=False,
                    schema={"type": "integer", "default": 1},
                ),
                ParameterInfo(
                    name="X-Client-Version",
                    location="header",
                    required=False,
                    schema={"type": "string"},
                ),
            ],
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["title"],
                    }
                },
            ),
        )

    def test_combine_schemas_simple(self, simple_route):
        """Test combining schemas for a simple route."""
        combined_schema = _combine_schemas(simple_route)

        assert combined_schema["type"] == "object"
        assert "properties" in combined_schema

        properties = combined_schema["properties"]
        assert "id" in properties
        assert properties["id"]["type"] == "integer"

        required = combined_schema.get("required", [])
        assert "id" in required

    def test_combine_schemas_with_collisions(self, collision_route):
        """Test combining schemas with parameter name collisions."""
        combined_schema = _combine_schemas(collision_route)

        assert combined_schema["type"] == "object"
        properties = combined_schema["properties"]

        # Should handle collision by suffixing
        id_params = [key for key in properties.keys() if "id" in key]
        assert len(id_params) >= 2  # Should have both path and body id

        # Should have other body parameters
        assert "name" in properties
        assert "email" in properties

    def test_combine_schemas_complex(self, complex_route):
        """Test combining schemas for complex route."""
        combined_schema = _combine_schemas(complex_route)

        properties = combined_schema["properties"]

        # Should have path parameter
        assert "id" in properties

        # Should have query parameter
        assert "version" in properties
        assert properties["version"].get("default") == 1

        # Should have header parameter
        assert "X-Client-Version" in properties

        # Should have body parameters
        assert "title" in properties
        assert "description" in properties
        assert "tags" in properties

        # Check required fields
        required = combined_schema.get("required", [])
        assert "id" in required  # Path parameters are required
        assert "title" in required  # Required body parameter

    def test_combine_schemas_and_map_params_simple(self, simple_route):
        """Test combining schemas and creating parameter map."""
        combined_schema, param_map = _combine_schemas_and_map_params(simple_route)

        # Check schema
        assert combined_schema["type"] == "object"
        assert "id" in combined_schema["properties"]

        # Check parameter map
        assert len(param_map) == 1
        assert "id" in param_map
        assert param_map["id"]["location"] == "path"
        assert param_map["id"]["openapi_name"] == "id"

    def test_combine_schemas_and_map_params_with_collisions(self, collision_route):
        """Test parameter mapping with collisions."""
        combined_schema, param_map = _combine_schemas_and_map_params(collision_route)

        # Check that we have entries for both conflicting parameters
        path_id_key = None
        body_id_key = None

        for key, mapping in param_map.items():
            if mapping["location"] == "path" and mapping["openapi_name"] == "id":
                path_id_key = key
            elif mapping["location"] == "body" and mapping["openapi_name"] == "id":
                body_id_key = key

        assert path_id_key is not None
        assert body_id_key is not None
        assert path_id_key != body_id_key  # Should be different keys

        # Both should exist in schema
        assert path_id_key in combined_schema["properties"]
        assert body_id_key in combined_schema["properties"]

        # Should also have non-conflicting parameters
        assert "name" in param_map
        assert "email" in param_map

    def test_combine_schemas_and_map_params_complex(self, complex_route):
        """Test parameter mapping for complex route."""
        combined_schema, param_map = _combine_schemas_and_map_params(complex_route)

        # Should have all parameters mapped
        actual_locations = {mapping["location"] for mapping in param_map.values()}

        # Should have representatives from each location
        assert "path" in actual_locations
        assert "body" in actual_locations
        # May or may not have query/header depending on whether they're included

        # Check specific mappings
        id_mapping = param_map["id"]
        assert id_mapping["location"] == "path"
        assert id_mapping["openapi_name"] == "id"

        title_mapping = param_map["title"]
        assert title_mapping["location"] == "body"
        assert title_mapping["openapi_name"] == "title"

    def test_replace_ref_with_defs(self):
        """Test replacing $ref with $defs for JSON Schema compatibility."""

        schema_with_ref = {
            "type": "object",
            "properties": {
                "user": {"$ref": "#/components/schemas/User"},
                "items": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/Item"},
                },
            },
        }

        # Use our recursive replacement approach
        result = _replace_ref_with_defs(schema_with_ref)

        assert result["properties"]["user"]["$ref"] == "#/$defs/User"
        assert result["properties"]["items"]["items"]["$ref"] == "#/$defs/Item"

    def test_replace_ref_with_defs_nested(self):
        """Test replacing $ref in deeply nested structures."""

        nested_schema = {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {
                        "nested": {"$ref": "#/components/schemas/Nested"},
                    },
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ref_prop": {"$ref": "#/components/schemas/RefProp"},
                        },
                    },
                },
            },
        }

        # Use our recursive replacement approach
        result = _replace_ref_with_defs(nested_schema)

        # Check nested object property
        nested_prop = result["properties"]["data"]["properties"]["nested"]
        assert nested_prop["$ref"] == "#/$defs/Nested"

        # Check array item property
        array_item_prop = result["properties"]["items"]["items"]["properties"][
            "ref_prop"
        ]
        assert array_item_prop["$ref"] == "#/$defs/RefProp"

    def test_replace_ref_with_defs_in_additional_properties(self):
        """Test replacing $ref deeply in 'additionalProperties'."""

        add_props_schema = {
            "description": "An invoice with a fixed header and a flexible set of line items.",
            "type": "object",
            "properties": {
                "invoice_number": {
                    "type": "string",
                    "description": "The unique identifier for the invoice.",
                },
                "customer_name": {
                    "type": "string",
                    "description": "The name of the customer.",
                },
                "total_amount": {
                    "type": "number",
                    "description": "The total amount of the invoice.",
                },
            },
            "required": ["invoice_number", "customer_name", "total_amount"],
            "additionalProperties": {"$ref": "#/components/schemas/Link"},
        }

        # Use our recursive replacement approach
        result = _replace_ref_with_defs(add_props_schema)

        # Check additional properties
        add_props = result["additionalProperties"]
        assert add_props["$ref"] == "#/$defs/Link"

    def test_replace_ref_with_defs_with_bool_additional_properties(self):
        """Test replacing a bool 'additionalProperties'."""

        add_props_schema = {
            "description": "An invoice with a fixed header and a flexible set of line items.",
            "type": "object",
            "properties": {
                "invoice_number": {
                    "type": "string",
                    "description": "The unique identifier for the invoice.",
                },
                "customer_name": {
                    "type": "string",
                    "description": "The name of the customer.",
                },
                "total_amount": {
                    "type": "number",
                    "description": "The total amount of the invoice.",
                },
            },
            "required": ["invoice_number", "customer_name", "total_amount"],
            "additionalProperties": False,
        }

        # Use our recursive replacement approach
        result = _replace_ref_with_defs(add_props_schema)

        # Check additional properties
        add_props = result["additionalProperties"]
        assert add_props is False

    def test_replace_ref_with_defs_with_inner_schema_additional_properties(self):
        """Test replacing a inner schema 'additionalProperties'."""

        add_props_schema = {
            "description": "An invoice with a fixed header and a flexible set of line items.",
            "type": "object",
            "properties": {
                "invoice_number": {
                    "type": "string",
                    "description": "The unique identifier for the invoice.",
                },
                "customer_name": {
                    "type": "string",
                    "description": "The name of the customer.",
                },
                "total_amount": {
                    "type": "number",
                    "description": "The total amount of the invoice.",
                },
            },
            "required": ["invoice_number", "customer_name", "total_amount"],
            "additionalProperties": {
                "type": "integer",
                "format": "int32",
                "description": "The total amount of the invoice.",
            },
        }

        # Use our recursive replacement approach
        result = _replace_ref_with_defs(add_props_schema)

        # Check additional properties
        add_props = result["additionalProperties"]
        assert add_props == {
            "type": "integer",
            "format": "int32",
            "description": "The total amount of the invoice.",
        }

    def test_parameter_collision_suffixing_logic(self):
        """Test the specific logic for parameter collision suffixing."""
        # Create a route that would definitely cause collisions
        route = HTTPRoute(
            path="/test/{id}",
            method="POST",
            operation_id="test_collision",
            parameters=[
                ParameterInfo(
                    name="id", location="path", required=True, schema={"type": "string"}
                ),
                ParameterInfo(
                    name="name",
                    location="query",
                    required=False,
                    schema={"type": "string"},
                ),
                ParameterInfo(
                    name="name",
                    location="header",
                    required=False,
                    schema={"type": "string"},
                ),
            ],
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    }
                },
            ),
        )

        combined_schema, param_map = _combine_schemas_and_map_params(route)

        # Check that all parameters are included with unique keys
        param_keys = list(param_map.keys())
        assert len(param_keys) == len(set(param_keys))  # All keys should be unique

        # Should have some form of id and name parameters
        id_keys = [key for key in param_keys if "id" in key]
        name_keys = [key for key in param_keys if "name" in key]

        assert len(id_keys) >= 2  # Path id and body id
        assert len(name_keys) >= 3  # Query name, header name, and body name

        # Check that locations are correctly mapped
        path_params = [
            key for key, mapping in param_map.items() if mapping["location"] == "path"
        ]
        query_params = [
            key for key, mapping in param_map.items() if mapping["location"] == "query"
        ]
        header_params = [
            key for key, mapping in param_map.items() if mapping["location"] == "header"
        ]
        body_params = [
            key for key, mapping in param_map.items() if mapping["location"] == "body"
        ]

        assert len(path_params) == 1
        assert len(query_params) == 1
        assert len(header_params) == 1
        assert len(body_params) >= 3  # id, name, description from body


class TestEdgeCases:
    """Test edge cases in schema processing."""

    def test_empty_route(self):
        """Test schema processing with empty route."""
        empty_route = HTTPRoute(
            path="/empty",
            method="GET",
            operation_id="empty_op",
            parameters=[],
        )

        combined_schema = _combine_schemas(empty_route)

        assert combined_schema["type"] == "object"
        assert combined_schema["properties"] == {}
        assert combined_schema.get("required", []) == []

    def test_route_without_request_body(self):
        """Test route with only parameters, no request body."""
        route = HTTPRoute(
            path="/test/{id}",
            method="GET",
            operation_id="test_get",
            parameters=[
                ParameterInfo(
                    name="id", location="path", required=True, schema={"type": "string"}
                ),
                ParameterInfo(
                    name="filter",
                    location="query",
                    required=False,
                    schema={"type": "string"},
                ),
            ],
        )

        combined_schema, param_map = _combine_schemas_and_map_params(route)

        assert "id" in combined_schema["properties"]
        assert "filter" in combined_schema["properties"]
        assert len(param_map) == 2

    def test_route_with_only_request_body(self):
        """Test route with only request body, no parameters."""
        route = HTTPRoute(
            path="/create",
            method="POST",
            operation_id="create_item",
            parameters=[],
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["name"],
                    }
                },
            ),
        )

        combined_schema, param_map = _combine_schemas_and_map_params(route)

        assert "name" in combined_schema["properties"]
        assert "description" in combined_schema["properties"]
        assert "name" in combined_schema["required"]
        assert len(param_map) == 2

    def test_parameter_without_schema(self):
        """Test handling parameters without schema."""
        # Use minimal schema to avoid validation error
        route = HTTPRoute(
            path="/test",
            method="GET",
            operation_id="test_no_schema",
            parameters=[
                ParameterInfo(
                    name="param1", location="query", required=False, schema={}
                ),  # Empty schema
            ],
        )

        combined_schema, param_map = _combine_schemas_and_map_params(route)

        # Should handle gracefully
        assert combined_schema["type"] == "object"
        assert isinstance(param_map, dict)

    def test_request_body_multiple_content_types(self):
        """Test request body with multiple content types."""
        route = HTTPRoute(
            path="/upload",
            method="POST",
            operation_id="upload_file",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {"metadata": {"type": "string"}},
                    },
                    "multipart/form-data": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    },
                },
            ),
        )

        combined_schema, param_map = _combine_schemas_and_map_params(route)

        # Should use the first content type found
        properties = combined_schema["properties"]
        assert (
            len(properties) > 0
        )  # Should have some properties from one of the content types

    def test_oneof_reference_dereferenced(self):
        """Test that schemas referenced in oneOf are dereferenced."""

        schema = {
            "type": "object",
            "properties": {"data": {"oneOf": [{"$ref": "#/$defs/TestSchema"}]}},
            "$defs": {
                "TestSchema": {"type": "string"},
                "UnusedSchema": {"type": "number"},
            },
        }

        result = compress_schema(schema)

        # $defs should be removed (all refs dereferenced)
        assert "$defs" not in result

        # TestSchema should be inlined in oneOf
        assert result["properties"]["data"]["oneOf"] == [{"type": "string"}]

    def test_anyof_reference_dereferenced(self):
        """Test that schemas referenced in anyOf are dereferenced."""

        schema = {
            "type": "object",
            "properties": {"data": {"anyOf": [{"$ref": "#/$defs/TestSchema"}]}},
            "$defs": {
                "TestSchema": {"type": "string"},
                "UnusedSchema": {"type": "number"},
            },
        }

        result = compress_schema(schema)

        # $defs should be removed (all refs dereferenced)
        assert "$defs" not in result

        # TestSchema should be inlined in anyOf
        assert result["properties"]["data"]["anyOf"] == [{"type": "string"}]

    def test_allof_reference_dereferenced(self):
        """Test that schemas referenced in allOf are dereferenced."""

        schema = {
            "type": "object",
            "properties": {"data": {"allOf": [{"$ref": "#/$defs/TestSchema"}]}},
            "$defs": {
                "TestSchema": {"type": "string"},
                "UnusedSchema": {"type": "number"},
            },
        }

        result = compress_schema(schema)

        # $defs should be removed (all refs dereferenced)
        assert "$defs" not in result

        # TestSchema should be inlined in allOf
        assert result["properties"]["data"]["allOf"] == [{"type": "string"}]
