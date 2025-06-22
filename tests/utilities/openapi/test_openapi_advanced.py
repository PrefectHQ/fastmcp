"""Tests for advanced features of the OpenAPI utilities."""

from typing import Any

import pytest

from fastmcp.utilities.openapi import parse_openapi_to_http_routes


@pytest.fixture
def complex_schema() -> dict[str, Any]:
    """Fixture that returns a complex OpenAPI schema with nested references."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Complex API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "summary": "List all users",
                    "operationId": "listUsers",
                    "parameters": [
                        {"$ref": "#/components/parameters/PageLimit"},
                        {"$ref": "#/components/parameters/PageOffset"},
                    ],
                    "responses": {"200": {"description": "A list of users"}},
                }
            },
            "/users/{userId}": {
                "get": {
                    "summary": "Get user by ID",
                    "operationId": "getUser",
                    "parameters": [
                        {"$ref": "#/components/parameters/UserId"},
                        {"$ref": "#/components/parameters/IncludeInactive"},
                    ],
                    "responses": {"200": {"description": "User details"}},
                }
            },
            "/users/{userId}/orders": {
                "post": {
                    "summary": "Create order for user",
                    "operationId": "createOrder",
                    "parameters": [{"$ref": "#/components/parameters/UserId"}],
                    "requestBody": {"$ref": "#/components/requestBodies/OrderRequest"},
                    "responses": {"201": {"description": "Order created"}},
                }
            },
        },
        "components": {
            "parameters": {
                "UserId": {
                    "name": "userId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                },
                "PageLimit": {
                    "name": "limit",
                    "in": "query",
                    "schema": {"type": "integer", "default": 20, "maximum": 100},
                },
                "PageOffset": {
                    "name": "offset",
                    "in": "query",
                    "schema": {"type": "integer", "default": 0},
                },
                "IncludeInactive": {
                    "name": "include_inactive",
                    "in": "query",
                    "schema": {"type": "boolean", "default": False},
                },
            },
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "name": {"type": "string"},
                        "email": {"type": "string", "format": "email"},
                        "role": {"$ref": "#/components/schemas/Role"},
                        "address": {"$ref": "#/components/schemas/Address"},
                    },
                },
                "Role": {
                    "type": "string",
                    "enum": ["admin", "user", "guest"],
                },
                "Address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                        "zip": {"type": "string"},
                        "country": {"type": "string"},
                    },
                },
                "Order": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/OrderItem"},
                        },
                        "total": {"type": "number"},
                        "status": {"$ref": "#/components/schemas/OrderStatus"},
                    },
                },
                "OrderItem": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "format": "uuid"},
                        "quantity": {"type": "integer"},
                        "price": {"type": "number"},
                    },
                },
                "OrderStatus": {
                    "type": "string",
                    "enum": [
                        "pending",
                        "processing",
                        "shipped",
                        "delivered",
                        "cancelled",
                    ],
                },
            },
            "requestBodies": {
                "OrderRequest": {
                    "description": "Order to create",
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["items"],
                                "properties": {
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/OrderItem"
                                        },
                                    },
                                    "notes": {"type": "string"},
                                },
                            }
                        }
                    },
                }
            },
        },
    }


@pytest.fixture
def parsed_complex_routes(complex_schema):
    """Return parsed routes from the complex schema."""
    return parse_openapi_to_http_routes(complex_schema)


@pytest.fixture
def complex_route_map(parsed_complex_routes):
    """Return a dictionary of routes by operation ID."""
    return {
        r.operation_id: r for r in parsed_complex_routes if r.operation_id is not None
    }


@pytest.fixture
def schema_with_invalid_reference() -> dict[str, Any]:
    """Fixture that returns a schema with an invalid reference."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Invalid Reference API", "version": "1.0.0"},
        "paths": {
            "/broken-ref": {
                "get": {
                    "summary": "Endpoint with broken reference",
                    "operationId": "brokenRef",
                    "parameters": [
                        {"$ref": "#/components/parameters/NonExistentParam"}
                    ],
                    "responses": {"200": {"description": "Something"}},
                }
            }
        },
        "components": {
            "parameters": {}  # Empty parameters object to ensure the reference is broken
        },
    }


@pytest.fixture
def schema_with_content_params() -> dict[str, Any]:
    """Fixture that returns a schema with content-based parameters (complex parameters)."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Content Params API", "version": "1.0.0"},
        "paths": {
            "/complex-params": {
                "post": {
                    "summary": "Endpoint with complex parameter",
                    "operationId": "complexParams",
                    "parameters": [
                        {
                            "name": "filter",
                            "in": "query",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "field": {"type": "string"},
                                            "operator": {
                                                "type": "string",
                                                "enum": ["eq", "gt", "lt"],
                                            },
                                            "value": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    ],
                    "responses": {"200": {"description": "Results"}},
                }
            },
        },
    }


@pytest.fixture
def parsed_content_param_routes(schema_with_content_params):
    """Return parsed routes from the schema with content parameters."""
    return parse_openapi_to_http_routes(schema_with_content_params)


@pytest.fixture
def schema_all_http_methods() -> dict[str, Any]:
    """Fixture that returns a schema with all HTTP methods."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "All Methods API", "version": "1.0.0"},
        "paths": {
            "/resource": {
                "get": {
                    "operationId": "getResource",
                    "responses": {"200": {"description": "Success"}},
                },
                "post": {
                    "operationId": "createResource",
                    "responses": {"201": {"description": "Created"}},
                },
                "put": {
                    "operationId": "updateResource",
                    "responses": {"200": {"description": "Updated"}},
                },
                "delete": {
                    "operationId": "deleteResource",
                    "responses": {"204": {"description": "Deleted"}},
                },
                "patch": {
                    "operationId": "patchResource",
                    "responses": {"200": {"description": "Patched"}},
                },
                "head": {
                    "operationId": "headResource",
                    "responses": {"200": {"description": "Headers only"}},
                },
                "options": {
                    "operationId": "optionsResource",
                    "responses": {"200": {"description": "Options"}},
                },
                "trace": {
                    "operationId": "traceResource",
                    "responses": {"200": {"description": "Trace"}},
                },
            },
        },
    }


@pytest.fixture
def parsed_http_methods_routes(schema_all_http_methods):
    """Return parsed routes from the schema with all HTTP methods."""
    return parse_openapi_to_http_routes(schema_all_http_methods)


# --- Tests for complex schemas with references --- #


def test_complex_schema_route_count(parsed_complex_routes):
    """Test that parsing a schema with references successfully extracts all routes."""
    assert len(parsed_complex_routes) == 3


def test_complex_schema_ref_rewriting(parsed_complex_routes):
    """Test that all #/components references have been rewritten."""

    def no_components(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "$ref":
                    assert not v.startswith("#/components/"), (
                        f"reference '{v}' was not rewritten"
                    )
                else:
                    no_components(v)
        elif isinstance(value, list):
            for v in value:
                no_components(v)

    for route in parsed_complex_routes:
        no_components(route.schema_definitions)
        for param in route.parameters:
            no_components(param.schema_)


def test_complex_schema_list_users_query_param_limit(complex_route_map):
    """Test that a reference to a limit query parameter is correctly resolved."""
    list_users = complex_route_map["listUsers"]

    limit_param = next((p for p in list_users.parameters if p.name == "limit"), None)
    assert limit_param is not None
    assert limit_param.location == "query"
    assert limit_param.schema_.get("default") == 20


def test_complex_schema_list_users_query_param_limit_maximum(complex_route_map):
    """Test that a limit parameter's maximum value is correctly resolved."""
    list_users = complex_route_map["listUsers"]

    limit_param = next((p for p in list_users.parameters if p.name == "limit"), None)
    assert limit_param is not None
    assert limit_param.schema_.get("maximum") == 100


def test_complex_schema_get_user_path_param_existence(complex_route_map):
    """Test that a reference to a path parameter exists."""
    get_user = complex_route_map["getUser"]

    user_id_param = next((p for p in get_user.parameters if p.name == "userId"), None)
    assert user_id_param is not None
    assert user_id_param.location == "path"


def test_complex_schema_get_user_path_param_required(complex_route_map):
    """Test that a path parameter is correctly marked as required."""
    get_user = complex_route_map["getUser"]

    user_id_param = next((p for p in get_user.parameters if p.name == "userId"), None)
    assert user_id_param is not None
    assert user_id_param.required is True


def test_complex_schema_get_user_path_param_format(complex_route_map):
    """Test that a path parameter format is correctly resolved."""
    get_user = complex_route_map["getUser"]

    user_id_param = next((p for p in get_user.parameters if p.name == "userId"), None)
    assert user_id_param is not None
    assert user_id_param.schema_.get("format") == "uuid"


def test_complex_schema_create_order_request_body_presence(complex_route_map):
    """Test that a reference to a request body is resolved correctly."""
    create_order = complex_route_map["createOrder"]

    assert create_order.request_body is not None
    assert create_order.request_body.required is True


def test_complex_schema_create_order_request_body_content_type(complex_route_map):
    """Test that request body content type is correctly resolved."""
    create_order = complex_route_map["createOrder"]

    assert create_order.request_body is not None
    assert "application/json" in create_order.request_body.content_schema


def test_complex_schema_create_order_request_body_properties(complex_route_map):
    """Test that request body properties are correctly resolved."""
    create_order = complex_route_map["createOrder"]

    assert create_order.request_body is not None
    json_schema = create_order.request_body.content_schema["application/json"]
    assert "items" in json_schema.get("properties", {})


def test_complex_schema_create_order_request_body_required_fields(complex_route_map):
    """Test that request body required fields are correctly resolved."""
    create_order = complex_route_map["createOrder"]

    assert create_order.request_body is not None
    json_schema = create_order.request_body.content_schema["application/json"]
    assert json_schema.get("required") == ["items"]


# --- Tests for schema reference resolution errors --- #


def test_parser_handles_broken_references(schema_with_invalid_reference):
    """Test that parser handles broken references gracefully."""
    # We're just checking that the function doesn't throw an exception
    routes = parse_openapi_to_http_routes(schema_with_invalid_reference)

    # Should still return routes list (may be empty)
    assert isinstance(routes, list)

    # Verify that the route with broken parameter reference is still included
    # though it may not have the parameter properly
    broken_route = next(
        (r for r in routes if r.path == "/broken-ref" and r.method == "GET"), None
    )

    # The route should still be present
    assert broken_route is not None
    assert broken_route.operation_id == "brokenRef"


# --- Tests for content-based parameters --- #


def test_content_param_parameter_name(parsed_content_param_routes):
    """Test that parser correctly extracts name for content-based parameters."""
    complex_params = parsed_content_param_routes[0]

    assert len(complex_params.parameters) == 1
    param = complex_params.parameters[0]
    assert param.name == "filter"


def test_content_param_parameter_location(parsed_content_param_routes):
    """Test that parser correctly extracts location for content-based parameters."""
    complex_params = parsed_content_param_routes[0]

    assert len(complex_params.parameters) == 1
    param = complex_params.parameters[0]
    assert param.location == "query"


def test_content_param_schema_properties_presence(parsed_content_param_routes):
    """Test that parser extracts schema properties from content-based parameter."""
    complex_params = parsed_content_param_routes[0]

    param = complex_params.parameters[0]
    properties = param.schema_.get("properties", {})

    assert "field" in properties
    assert "operator" in properties
    assert "value" in properties


def test_content_param_schema_enum_presence(parsed_content_param_routes):
    """Test that parser extracts enum values from content-based parameter."""
    complex_params = parsed_content_param_routes[0]

    param = complex_params.parameters[0]
    properties = param.schema_.get("properties", {})

    assert "enum" in properties.get("operator", {})


# --- Tests for HTTP methods --- #


def test_http_get_method_presence(parsed_http_methods_routes):
    """Test that GET method is correctly extracted."""
    get_route = next((r for r in parsed_http_methods_routes if r.method == "GET"), None)

    assert get_route is not None
    assert get_route.operation_id == "getResource"


def test_http_get_method_path(parsed_http_methods_routes):
    """Test that GET method path is correctly extracted."""
    get_route = next((r for r in parsed_http_methods_routes if r.method == "GET"), None)

    assert get_route is not None
    assert get_route.path == "/resource"


def test_http_post_method_presence(parsed_http_methods_routes):
    """Test that POST method is correctly extracted."""
    post_route = next(
        (r for r in parsed_http_methods_routes if r.method == "POST"), None
    )

    assert post_route is not None
    assert post_route.operation_id == "createResource"


def test_http_post_method_path(parsed_http_methods_routes):
    """Test that POST method path is correctly extracted."""
    post_route = next(
        (r for r in parsed_http_methods_routes if r.method == "POST"), None
    )

    assert post_route is not None
    assert post_route.path == "/resource"


def test_http_put_method_presence(parsed_http_methods_routes):
    """Test that PUT method is correctly extracted."""
    put_route = next((r for r in parsed_http_methods_routes if r.method == "PUT"), None)

    assert put_route is not None
    assert put_route.operation_id == "updateResource"


def test_http_put_method_path(parsed_http_methods_routes):
    """Test that PUT method path is correctly extracted."""
    put_route = next((r for r in parsed_http_methods_routes if r.method == "PUT"), None)

    assert put_route is not None
    assert put_route.path == "/resource"


def test_http_delete_method_presence(parsed_http_methods_routes):
    """Test that DELETE method is correctly extracted."""
    delete_route = next(
        (r for r in parsed_http_methods_routes if r.method == "DELETE"), None
    )

    assert delete_route is not None
    assert delete_route.operation_id == "deleteResource"


def test_http_delete_method_path(parsed_http_methods_routes):
    """Test that DELETE method path is correctly extracted."""
    delete_route = next(
        (r for r in parsed_http_methods_routes if r.method == "DELETE"), None
    )

    assert delete_route is not None
    assert delete_route.path == "/resource"


def test_http_patch_method_presence(parsed_http_methods_routes):
    """Test that PATCH method is correctly extracted."""
    patch_route = next(
        (r for r in parsed_http_methods_routes if r.method == "PATCH"), None
    )

    assert patch_route is not None
    assert patch_route.operation_id == "patchResource"


def test_http_patch_method_path(parsed_http_methods_routes):
    """Test that PATCH method path is correctly extracted."""
    patch_route = next(
        (r for r in parsed_http_methods_routes if r.method == "PATCH"), None
    )

    assert patch_route is not None
    assert patch_route.path == "/resource"


def test_http_head_method_presence(parsed_http_methods_routes):
    """Test that HEAD method is correctly extracted."""
    head_route = next(
        (r for r in parsed_http_methods_routes if r.method == "HEAD"), None
    )

    assert head_route is not None
    assert head_route.operation_id == "headResource"


def test_http_head_method_path(parsed_http_methods_routes):
    """Test that HEAD method path is correctly extracted."""
    head_route = next(
        (r for r in parsed_http_methods_routes if r.method == "HEAD"), None
    )

    assert head_route is not None
    assert head_route.path == "/resource"


def test_http_options_method_presence(parsed_http_methods_routes):
    """Test that OPTIONS method is correctly extracted."""
    options_route = next(
        (r for r in parsed_http_methods_routes if r.method == "OPTIONS"), None
    )

    assert options_route is not None
    assert options_route.operation_id == "optionsResource"


def test_http_options_method_path(parsed_http_methods_routes):
    """Test that OPTIONS method path is correctly extracted."""
    options_route = next(
        (r for r in parsed_http_methods_routes if r.method == "OPTIONS"), None
    )

    assert options_route is not None
    assert options_route.path == "/resource"


def test_http_trace_method_presence(parsed_http_methods_routes):
    """Test that TRACE method is correctly extracted."""
    trace_route = next(
        (r for r in parsed_http_methods_routes if r.method == "TRACE"), None
    )

    assert trace_route is not None
    assert trace_route.operation_id == "traceResource"


def test_http_trace_method_path(parsed_http_methods_routes):
    """Test that TRACE method path is correctly extracted."""
    trace_route = next(
        (r for r in parsed_http_methods_routes if r.method == "TRACE"), None
    )

    assert trace_route is not None
    assert trace_route.path == "/resource"
