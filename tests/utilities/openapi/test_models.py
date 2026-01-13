"""Unit tests for OpenAPI models."""

import pytest
from inline_snapshot import snapshot

from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    ParameterInfo,
    RequestBodyInfo,
    ResponseInfo,
)


class TestParameterInfo:
    """Test ParameterInfo model."""

    def test_basic_parameter_creation(self):
        """Test creating a basic parameter."""
        param = ParameterInfo(
            name="id",
            location="path",
            required=True,
            schema={"type": "integer"},
        )

        assert param.name == "id"
        assert param.location == "path"
        assert param.required is True
        assert param.schema_ == {"type": "integer"}
        assert param.description is None
        assert param.explode is None
        assert param.style is None

    def test_parameter_with_all_fields(self):
        """Test creating parameter with all optional fields."""
        param = ParameterInfo(
            name="filter",
            location="query",
            required=False,
            schema={"type": "object", "properties": {"name": {"type": "string"}}},
            description="Filter criteria",
            explode=True,
            style="deepObject",
        )

        assert param.name == "filter"
        assert param.location == "query"
        assert param.required is False
        assert param.description == "Filter criteria"
        assert param.explode is True
        assert param.style == "deepObject"

    @pytest.mark.parametrize("location", ["path", "query", "header", "cookie"])
    def test_valid_parameter_locations(self, location):
        """Test that all valid parameter locations are accepted."""
        param = ParameterInfo(
            name="test",
            location=location,
            required=False,
            schema={"type": "string"},
        )
        assert param.location == location

    def test_parameter_defaults(self):
        """Test parameter default values."""
        param = ParameterInfo(
            name="test",
            location="query",
            schema={"type": "string"},
        )

        # required should default to False for non-path parameters
        assert param.required is False
        assert param.description is None
        assert param.explode is None
        assert param.style is None

    def test_parameter_with_empty_schema(self):
        """Test parameter with empty schema."""
        param = ParameterInfo(
            name="test",
            location="query",
            schema={},
        )

        assert param.schema_ == {}


class TestRequestBodyInfo:
    """Test RequestBodyInfo model."""

    def test_basic_request_body(self):
        """Test creating a basic request body."""
        request_body = RequestBodyInfo(
            required=True,
            description="User data",
        )

        assert request_body.required is True
        assert request_body.description == "User data"
        assert request_body.content_schema == {}

    def test_request_body_with_content_schema(self):
        """Test request body with content schema."""
        content_schema = {
            "application/json": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["name"],
            }
        }

        request_body = RequestBodyInfo(
            required=True,
            content_schema=content_schema,
        )

        assert request_body.content_schema == content_schema

    def test_request_body_defaults(self):
        """Test request body default values."""
        request_body = RequestBodyInfo()

        assert request_body.required is False
        assert request_body.description is None
        assert request_body.content_schema == {}

    def test_request_body_multiple_content_types(self):
        """Test request body with multiple content types."""
        content_schema = {
            "application/json": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
            "application/xml": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        }

        request_body = RequestBodyInfo(content_schema=content_schema)

        assert len(request_body.content_schema) == 2
        assert "application/json" in request_body.content_schema
        assert "application/xml" in request_body.content_schema


class TestResponseInfo:
    """Test ResponseInfo model."""

    def test_basic_response(self):
        """Test creating a basic response."""
        response = ResponseInfo(description="Success response")

        assert response.description == "Success response"
        assert response.content_schema == {}

    def test_response_with_content_schema(self):
        """Test response with content schema."""
        content_schema = {
            "application/json": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "message": {"type": "string"},
                },
            }
        }

        response = ResponseInfo(
            description="User created",
            content_schema=content_schema,
        )

        assert response.description == "User created"
        assert response.content_schema == content_schema

    def test_response_required_description(self):
        """Test that response description is required."""
        # Should not raise an error - description has a default
        response = ResponseInfo()
        assert response.description is None


class TestHTTPRoute:
    """Test HTTPRoute model."""

    def test_basic_route_creation(self):
        """Test creating a basic HTTP route."""
        route = HTTPRoute(
            path="/users/{id}",
            method="GET",
            operation_id="get_user",
        )

        assert route.path == "/users/{id}"
        assert route.method == "GET"
        assert route.operation_id == "get_user"
        assert route.summary is None
        assert route.description is None
        assert route.tags == []
        assert route.parameters == []
        assert route.request_body is None
        assert route.responses == {}

    def test_route_with_all_fields(self):
        """Test creating route with all fields."""
        parameters = [
            ParameterInfo(
                name="id",
                location="path",
                required=True,
                schema={"type": "integer"},
            )
        ]

        request_body = RequestBodyInfo(
            required=True,
            content_schema={
                "application/json": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                }
            },
        )

        responses = {
            "200": ResponseInfo(
                description="Success",
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    }
                },
            )
        }

        route = HTTPRoute(
            path="/users/{id}",
            method="PUT",
            operation_id="update_user",
            summary="Update user",
            description="Update user by ID",
            tags=["users"],
            parameters=parameters,
            request_body=request_body,
            responses=responses,
            request_schemas={"User": {"type": "object"}},
            extensions={"x-custom": "value"},
        )

        assert route.path == "/users/{id}"
        assert route.method == "PUT"
        assert route.operation_id == "update_user"
        assert route.summary == "Update user"
        assert route.description == "Update user by ID"
        assert route.tags == ["users"]
        assert len(route.parameters) == 1
        assert route.request_body is not None
        assert "200" in route.responses
        assert "User" in route.request_schemas
        assert route.extensions["x-custom"] == "value"

    def test_route_pre_calculated_fields(self):
        """Test route with pre-calculated fields."""
        route = HTTPRoute(
            path="/test",
            method="GET",
            operation_id="test",
            flat_param_schema={
                "type": "object",
                "properties": {"id": {"type": "integer"}},
            },
            parameter_map={"id": {"location": "path", "openapi_name": "id"}},
        )

        assert route.flat_param_schema["type"] == "object"
        assert "id" in route.flat_param_schema["properties"]
        assert "id" in route.parameter_map
        assert route.parameter_map["id"]["location"] == "path"

    @pytest.mark.parametrize(
        "method", ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
    )
    def test_valid_http_methods(self, method):
        """Test that all valid HTTP methods are accepted."""
        route = HTTPRoute(
            path="/test",
            method=method,
            operation_id="test",
        )
        assert route.method == method

    def test_route_with_empty_collections(self):
        """Test route with empty collections."""
        route = HTTPRoute(
            path="/test",
            method="GET",
            operation_id="test",
            tags=[],
            parameters=[],
            responses={},
            request_schemas={},
            extensions={},
        )

        assert route.tags == []
        assert route.parameters == []
        assert route.responses == {}
        assert route.request_schemas == {}
        assert route.response_schemas == {}
        assert route.extensions == {}

    def test_route_defaults(self):
        """Test route default values."""
        route = HTTPRoute(
            path="/test",
            method="GET",
            operation_id="test",
        )

        assert route.summary is None
        assert route.description is None
        assert route.tags == []
        assert route.parameters == []
        assert route.request_body is None
        assert route.responses == {}
        assert route.request_schemas == {}
        assert route.response_schemas == {}
        assert route.extensions == {}
        assert route.flat_param_schema == {}
        assert route.parameter_map == {}


class TestModelValidation:
    """Test model validation and error cases."""

    def test_parameter_info_validation(self):
        """Test ParameterInfo validation."""
        # Valid parameter
        param = ParameterInfo(
            name="test",
            location="query",
            schema={"type": "string"},
        )
        assert param.name == "test"

    def test_route_validation(self):
        """Test HTTPRoute validation."""
        # Valid route
        route = HTTPRoute(
            path="/test",
            method="GET",
            operation_id="test",
        )
        assert route.path == "/test"

    def test_nested_model_validation(self):
        """Test validation of nested models."""
        # Create route with nested models
        param = ParameterInfo(
            name="id",
            location="path",
            required=True,
            schema={"type": "integer"},
        )

        request_body = RequestBodyInfo(required=True)

        route = HTTPRoute(
            path="/test/{id}",
            method="POST",
            operation_id="test",
            parameters=[param],
            request_body=request_body,
        )

        assert len(route.parameters) == 1
        assert route.parameters[0].name == "id"
        assert route.request_body is not None
        assert route.request_body.required is True


class TestModelSerialization:
    """Test model serialization and deserialization."""

    def test_parameter_info_serialization(self):
        """Test ParameterInfo serialization."""
        param = ParameterInfo(
            name="filter",
            location="query",
            required=False,
            schema={"type": "object"},
            description="Filter criteria",
            explode=True,
            style="deepObject",
        )

        # Test model_dump with alias
        data = param.model_dump(by_alias=True)

        assert data["name"] == "filter"
        assert data["location"] == "query"
        assert data["required"] is False
        assert data["schema"] == {"type": "object"}  # Using alias
        assert data["description"] == "Filter criteria"
        assert data["explode"] is True
        assert data["style"] == "deepObject"

    def test_route_serialization(self):
        """Test HTTPRoute serialization."""
        param = ParameterInfo(
            name="id",
            location="path",
            required=True,
            schema={"type": "integer"},
        )

        route = HTTPRoute(
            path="/users/{id}",
            method="GET",
            operation_id="get_user",
            parameters=[param],
        )

        # Test model_dump
        data = route.model_dump()

        assert data["path"] == "/users/{id}"
        assert data["method"] == "GET"
        assert data["operation_id"] == "get_user"
        assert len(data["parameters"]) == 1
        assert data["parameters"][0]["name"] == "id"

    def test_model_reconstruction(self):
        """Test reconstructing models from serialized data."""
        # Create original parameter
        original_param = ParameterInfo(
            name="test",
            location="query",
            schema={"type": "string"},
            description="Test parameter",
        )

        # Serialize and reconstruct using by_alias
        data = original_param.model_dump(by_alias=True)

        data == snapshot(
            {
                "name": "test",
                "location": "query",
                "required": False,
                "schema": {"type": "string"},
                "description": "Test parameter",
                "explode": None,
                "style": None,
            }
        )

        reconstructed_param = ParameterInfo(
            name=data["name"],
            location=data["location"],
            schema=data["schema"],
            description=data["description"],
            explode=data["explode"],
            style=data["style"],
        )

        assert reconstructed_param.name == original_param.name
        assert reconstructed_param.location == original_param.location
        assert reconstructed_param.schema_ == original_param.schema_
        assert reconstructed_param.description == original_param.description
