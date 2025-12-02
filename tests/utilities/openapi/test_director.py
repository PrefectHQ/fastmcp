"""Unit tests for RequestDirector."""

import pytest
from jsonschema_path import SchemaPath

from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    ParameterInfo,
    RequestBodyInfo,
)
from fastmcp.utilities.openapi.parser import parse_openapi_to_http_routes


class TestRequestDirector:
    """Test RequestDirector request building functionality."""

    @pytest.fixture
    def basic_route(self):
        """Create a basic HTTPRoute for testing."""
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
            flat_param_schema={
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
            parameter_map={"id": {"location": "path", "openapi_name": "id"}},
        )

    @pytest.fixture
    def complex_route(self):
        """Create a complex HTTPRoute with multiple parameter types."""
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
                        },
                        "required": ["title"],
                    }
                },
            ),
            flat_param_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "version": {"type": "integer", "default": 1},
                    "X-Client-Version": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["id", "title"],
            },
            parameter_map={
                "id": {"location": "path", "openapi_name": "id"},
                "version": {"location": "query", "openapi_name": "version"},
                "X-Client-Version": {
                    "location": "header",
                    "openapi_name": "X-Client-Version",
                },
                "title": {"location": "body", "openapi_name": "title"},
                "description": {"location": "body", "openapi_name": "description"},
            },
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
            flat_param_schema={
                "type": "object",
                "properties": {
                    "id__path": {"type": "integer"},
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["id__path", "name"],
            },
            parameter_map={
                "id__path": {"location": "path", "openapi_name": "id"},
                "id": {"location": "body", "openapi_name": "id"},
                "name": {"location": "body", "openapi_name": "name"},
            },
        )

    @pytest.fixture
    def director(self, basic_openapi_30_spec):
        """Create a RequestDirector instance."""
        spec = SchemaPath.from_dict(basic_openapi_30_spec)
        return RequestDirector(spec)

    def test_director_initialization(self, basic_openapi_30_spec):
        """Test RequestDirector initialization."""
        spec = SchemaPath.from_dict(basic_openapi_30_spec)
        director = RequestDirector(spec)

        assert director._spec is not None
        assert director._spec == spec

    def test_build_basic_request(self, director, basic_route):
        """Test building a basic GET request with path parameter."""
        flat_args = {"id": 123}

        request = director.build(basic_route, flat_args, "https://api.example.com")

        assert request.method == "GET"
        assert request.url == "https://api.example.com/users/123"
        assert (
            request.content == b""
        )  # httpx.Request sets content to empty bytes for GET

    def test_build_complex_request(self, director, complex_route):
        """Test building a complex request with multiple parameter types."""
        flat_args = {
            "id": "item123",
            "version": 2,
            "X-Client-Version": "1.0.0",
            "title": "Updated Title",
            "description": "Updated description",
        }

        request = director.build(complex_route, flat_args, "https://api.example.com")

        assert request.method == "PATCH"
        assert "item123" in str(request.url)
        assert "version=2" in str(request.url)

        # Check headers
        headers = dict(request.headers) if request.headers else {}
        assert (
            headers.get("x-client-version") == "1.0.0"
        )  # httpx normalizes headers to lowercase

        # Check body
        import json

        assert request.content is not None
        body_data = json.loads(request.content)
        assert body_data["title"] == "Updated Title"
        assert body_data["description"] == "Updated description"

    def test_build_request_with_collisions(self, director, collision_route):
        """Test building request with parameter name collisions."""
        flat_args = {
            "id__path": 123,  # Path parameter
            "id": 456,  # Body parameter
            "name": "John Doe",
        }

        request = director.build(collision_route, flat_args, "https://api.example.com")

        assert request.method == "PUT"
        assert "123" in str(request.url)  # Path ID should be 123

        # Check body
        import json

        body_data = json.loads(request.content)
        assert body_data["id"] == 456  # Body ID should be 456
        assert body_data["name"] == "John Doe"

    def test_build_request_with_none_values(self, director, complex_route):
        """Test that None values are skipped for optional parameters."""
        flat_args = {
            "id": "item123",
            "version": None,  # Optional, should be skipped
            "X-Client-Version": None,  # Optional, should be skipped
            "title": "Required Title",
            "description": None,  # Optional body param, should be skipped
        }

        request = director.build(complex_route, flat_args, "https://api.example.com")

        assert request.method == "PATCH"
        assert "item123" in str(request.url)
        assert "version" not in str(request.url)  # Should not include None version

        headers = dict(request.headers) if request.headers else {}
        assert "X-Client-Version" not in headers

        import json

        body_data = json.loads(request.content)
        assert body_data["title"] == "Required Title"
        assert "description" not in body_data  # Should not include None description

    def test_build_request_fallback_mapping(self, director):
        """Test fallback parameter mapping when parameter_map is not available."""
        # Create route without parameter_map
        route_without_map = HTTPRoute(
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
            # No parameter_map provided
        )

        flat_args = {"id": 123}

        request = director.build(
            route_without_map, flat_args, "https://api.example.com"
        )

        assert request.method == "GET"
        assert "123" in str(request.url)

    def test_build_request_suffixed_parameters(self, director):
        """Test handling of suffixed parameters in fallback mode."""
        route = HTTPRoute(
            path="/users/{id}",
            method="POST",
            operation_id="create_user",
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
                        "properties": {"name": {"type": "string"}},
                    }
                },
            ),
        )

        # Use suffixed parameter names
        flat_args = {
            "id__path": 123,
            "name": "John Doe",
        }

        request = director.build(route, flat_args, "https://api.example.com")

        assert request.method == "POST"
        assert "123" in str(request.url)

        import json

        body_data = json.loads(request.content)
        assert body_data["name"] == "John Doe"

    def test_url_building(self, director, basic_route):
        """Test URL building with different base URLs."""
        flat_args = {"id": 123}

        # Test with trailing slash
        request1 = director.build(basic_route, flat_args, "https://api.example.com/")
        assert request1.url == "https://api.example.com/users/123"

        # Test without trailing slash
        request2 = director.build(basic_route, flat_args, "https://api.example.com")
        assert request2.url == "https://api.example.com/users/123"

        # Test with path in base URL
        request3 = director.build(basic_route, flat_args, "https://api.example.com/v1")
        assert request3.url == "https://api.example.com/v1/users/123"

    def test_body_construction_single_value(self, director):
        """Test body construction when body schema is not an object."""
        route = HTTPRoute(
            path="/upload",
            method="POST",
            operation_id="upload_file",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={"text/plain": {"type": "string"}},
            ),
            parameter_map={
                "content": {"location": "body", "openapi_name": "content"},
            },
        )

        flat_args = {"content": "Hello, World!"}

        request = director.build(route, flat_args, "https://api.example.com")

        assert request.method == "POST"
        # For non-JSON content, httpx uses 'content' parameter which becomes bytes
        assert request.content == b"Hello, World!"

    def test_body_construction_multiple_properties_non_object_schema(self, director):
        """Test body construction with multiple properties but non-object schema."""
        route = HTTPRoute(
            path="/complex",
            method="POST",
            operation_id="complex_op",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {"type": "string"}  # Non-object schema
                },
            ),
            parameter_map={
                "prop1": {"location": "body", "openapi_name": "prop1"},
                "prop2": {"location": "body", "openapi_name": "prop2"},
            },
        )

        flat_args = {"prop1": "value1", "prop2": "value2"}

        request = director.build(route, flat_args, "https://api.example.com")

        assert request.method == "POST"
        # Should wrap in object when multiple properties but schema is not object
        import json

        body_data = json.loads(request.content)
        assert body_data == {"prop1": "value1", "prop2": "value2"}


class TestRequestDirectorIntegration:
    """Test RequestDirector with real parsed routes."""

    def test_with_parsed_routes(self, basic_openapi_30_spec):
        """Test RequestDirector with routes parsed from real spec."""
        routes = parse_openapi_to_http_routes(basic_openapi_30_spec)
        assert len(routes) == 1

        route = routes[0]
        spec = SchemaPath.from_dict(basic_openapi_30_spec)
        director = RequestDirector(spec)

        flat_args = {"id": 42}
        request = director.build(route, flat_args, "https://api.example.com")

        assert request.method == "GET"
        assert request.url == "https://api.example.com/users/42"

    def test_with_collision_spec(self, collision_spec):
        """Test RequestDirector with collision spec."""
        routes = parse_openapi_to_http_routes(collision_spec)
        assert len(routes) == 1

        route = routes[0]
        spec = SchemaPath.from_dict(collision_spec)
        director = RequestDirector(spec)

        # Use the parameter names from the actual parameter map
        param_map = route.parameter_map
        path_param_name = None
        body_param_names = []

        for param_name, mapping in param_map.items():
            if mapping["location"] == "path" and mapping["openapi_name"] == "id":
                path_param_name = param_name
            elif mapping["location"] == "body":
                body_param_names.append(param_name)

        assert path_param_name is not None

        flat_args = {path_param_name: 123, "name": "John Doe"}
        # Add body id if it exists in the parameter map
        for param_name in body_param_names:
            if "id" in param_name:
                flat_args[param_name] = 456

        request = director.build(route, flat_args, "https://api.example.com")

        assert request.method == "PUT"
        assert "123" in str(request.url)

    def test_with_deepobject_spec(self, deepobject_spec):
        """Test RequestDirector with deepObject parameters."""
        routes = parse_openapi_to_http_routes(deepobject_spec)
        assert len(routes) == 1

        route = routes[0]
        spec = SchemaPath.from_dict(deepobject_spec)
        director = RequestDirector(spec)

        # DeepObject parameters should be flattened in the parameter map
        flat_args = {}
        for param_name in route.parameter_map.keys():
            if "filter" in param_name:
                # Set some test values based on parameter name
                if "category" in param_name:
                    flat_args[param_name] = "electronics"
                elif "min" in param_name:
                    flat_args[param_name] = 10.0
                elif "max" in param_name:
                    flat_args[param_name] = 100.0

        if flat_args:  # Only test if we have parameters to test with
            request = director.build(route, flat_args, "https://api.example.com")

            assert request.method == "GET"
            assert str(request.url).startswith("https://api.example.com/search")
