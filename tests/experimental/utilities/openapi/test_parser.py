"""Unit tests for OpenAPI parser."""

import pytest

from fastmcp.experimental.utilities.openapi.parser import parse_openapi_to_http_routes


class TestOpenAPIParser:
    """Test OpenAPI parsing functionality."""

    def test_parse_basic_openapi_30(self, basic_openapi_30_spec):
        """Test parsing a basic OpenAPI 3.0 spec."""
        routes = parse_openapi_to_http_routes(basic_openapi_30_spec)

        assert len(routes) == 1
        route = routes[0]

        assert route.path == "/users/{id}"
        assert route.method == "GET"
        assert route.operation_id == "get_user"
        assert route.summary == "Get user by ID"

        # Check parameters
        assert len(route.parameters) == 1
        param = route.parameters[0]
        assert param.name == "id"
        assert param.location == "path"
        assert param.required is True
        assert param.schema_["type"] == "integer"

        # Check pre-calculated fields
        assert hasattr(route, "flat_param_schema")
        assert hasattr(route, "parameter_map")
        assert route.flat_param_schema is not None
        assert route.parameter_map is not None

    def test_parse_basic_openapi_31(self, basic_openapi_31_spec):
        """Test parsing a basic OpenAPI 3.1 spec."""
        routes = parse_openapi_to_http_routes(basic_openapi_31_spec)

        assert len(routes) == 1
        route = routes[0]

        assert route.path == "/users/{id}"
        assert route.method == "GET"
        assert route.operation_id == "get_user"

        # Same structure should work for both 3.0 and 3.1
        assert len(route.parameters) == 1
        param = route.parameters[0]
        assert param.name == "id"
        assert param.location == "path"

    def test_parse_collision_spec(self, collision_spec):
        """Test parsing spec with parameter collisions."""
        routes = parse_openapi_to_http_routes(collision_spec)

        assert len(routes) == 1
        route = routes[0]

        assert route.operation_id == "update_user"

        # Should have path parameter
        path_params = [p for p in route.parameters if p.location == "path"]
        assert len(path_params) == 1
        assert path_params[0].name == "id"

        # Should have request body
        assert route.request_body is not None
        assert route.request_body.required is True

        # Check that parameter map handles collisions
        assert route.parameter_map is not None
        # Should have entries for both path and body parameters
        assert len(route.parameter_map) >= 2  # At least path id and body fields

    def test_parse_deepobject_spec(self, deepobject_spec):
        """Test parsing spec with deepObject parameters."""
        routes = parse_openapi_to_http_routes(deepobject_spec)

        assert len(routes) == 1
        route = routes[0]

        assert route.operation_id == "search"

        # Should have deepObject parameter
        assert len(route.parameters) == 1
        param = route.parameters[0]
        assert param.name == "filter"
        assert param.location == "query"
        assert param.style == "deepObject"
        assert param.explode is True
        assert param.schema_["type"] == "object"

    def test_parse_complex_spec(self, complex_spec):
        """Test parsing complex spec with multiple parameter types."""
        routes = parse_openapi_to_http_routes(complex_spec)

        assert len(routes) == 1
        route = routes[0]

        assert route.operation_id == "update_item"

        # Should have multiple parameters
        assert len(route.parameters) == 3

        # Check parameter locations
        locations = {p.location for p in route.parameters}
        assert locations == {"path", "query", "header"}

        # Check specific parameters
        path_param = next(p for p in route.parameters if p.location == "path")
        assert path_param.name == "id"
        assert path_param.required is True

        query_param = next(p for p in route.parameters if p.location == "query")
        assert query_param.name == "version"
        assert query_param.required is False
        assert query_param.schema_.get("default") == 1

        header_param = next(p for p in route.parameters if p.location == "header")
        assert header_param.name == "X-Client-Version"
        assert header_param.required is False

        # Check request body
        assert route.request_body is not None
        assert route.request_body.required is True

    def test_parse_empty_spec(self):
        """Test parsing spec with no paths."""
        empty_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Empty API", "version": "1.0.0"},
            "paths": {},
        }

        routes = parse_openapi_to_http_routes(empty_spec)
        assert len(routes) == 0

    def test_parse_invalid_spec(self):
        """Test parsing invalid OpenAPI spec."""
        invalid_spec = {
            "openapi": "3.0.0",
            # Missing required fields
        }

        with pytest.raises(ValueError, match="Invalid OpenAPI schema"):
            parse_openapi_to_http_routes(invalid_spec)

    def test_parse_spec_with_refs(self):
        """Test parsing spec with $ref references."""
        spec_with_refs = {
            "openapi": "3.0.0",
            "info": {"title": "Ref Test API", "version": "1.0.0"},
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    }
                },
                "parameters": {
                    "UserId": {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    }
                },
            },
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "get_user",
                        "parameters": [{"$ref": "#/components/parameters/UserId"}],
                        "responses": {
                            "200": {
                                "description": "User",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/User"}
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }

        routes = parse_openapi_to_http_routes(spec_with_refs)

        assert len(routes) == 1
        route = routes[0]

        # Parameter should be resolved from $ref
        assert len(route.parameters) == 1
        param = route.parameters[0]
        assert param.name == "id"
        assert param.location == "path"
        assert param.required is True

    def test_parameter_schema_extraction(self, complex_spec):
        """Test that parameter schemas are properly extracted."""
        routes = parse_openapi_to_http_routes(complex_spec)
        route = routes[0]

        # Check that flat_param_schema contains all parameters
        flat_schema = route.flat_param_schema
        assert flat_schema["type"] == "object"
        assert "properties" in flat_schema

        properties = flat_schema["properties"]

        # Should contain path, query, and body parameters
        assert "id" in properties or any("id" in key for key in properties.keys())
        assert "title" in properties  # From request body

        # Check parameter mapping
        param_map = route.parameter_map
        assert len(param_map) > 0

        # Each mapped parameter should have location and openapi_name
        for param_name, mapping in param_map.items():
            assert "location" in mapping
            assert "openapi_name" in mapping
            assert mapping["location"] in ["path", "query", "header", "body"]


class TestParameterLocationHandling:
    """Test parameter location conversion and handling."""

    @pytest.mark.parametrize(
        "location_str,expected",
        [
            ("path", "path"),
            ("query", "query"),
            ("header", "header"),
            ("cookie", "cookie"),
            ("unknown", "query"),  # Should default to query
        ],
    )
    def test_parameter_location_conversion(self, location_str, expected):
        """Test parameter location string conversion."""
        # Create a simple spec with the parameter location
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Location Test", "version": "1.0.0"},
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "test_op",
                        "parameters": [
                            {
                                "name": "test_param",
                                "in": location_str,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }

        if location_str == "unknown":
            # Should raise validation error for unknown location
            with pytest.raises(ValueError, match="Invalid OpenAPI schema"):
                parse_openapi_to_http_routes(spec)
        else:
            routes = parse_openapi_to_http_routes(spec)
            route = routes[0]
            param = route.parameters[0]
            assert param.location == expected


class TestErrorHandling:
    """Test error handling in parser."""

    def test_external_ref_error(self):
        """Test that external references are handled gracefully."""
        spec_with_external_ref = {
            "openapi": "3.0.0",
            "info": {"title": "External Ref Test", "version": "1.0.0"},
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "test_op",
                        "parameters": [
                            {
                                "$ref": "external-file.yaml#/components/parameters/ExternalParam"
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }

        # Should not crash but skip the invalid parameter
        routes = parse_openapi_to_http_routes(spec_with_external_ref)
        assert len(routes) == 1
        assert (
            len(routes[0].parameters) == 0
        )  # External ref parameter should be skipped

    def test_broken_ref_error(self):
        """Test that broken internal references are handled gracefully."""
        spec_with_broken_ref = {
            "openapi": "3.0.0",
            "info": {"title": "Broken Ref Test", "version": "1.0.0"},
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "test_op",
                        "parameters": [
                            {"$ref": "#/components/parameters/NonExistentParam"}
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }

        # Should handle broken refs gracefully and continue parsing
        routes = parse_openapi_to_http_routes(spec_with_broken_ref)
        # May have empty routes or skip the broken operation
        assert isinstance(routes, list)
