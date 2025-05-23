"""Tests for FastAPI integration with the OpenAPI utilities."""

from typing import Any

import pytest
from fastapi import FastAPI

from fastmcp.utilities.openapi import parse_openapi_to_http_routes


@pytest.fixture
def fastapi_app() -> FastAPI:
    """Fixture that returns a FastAPI app for live OpenAPI schema testing."""
    from enum import Enum

    from fastapi import Body, Depends, Header, HTTPException, Path, Query
    from pydantic import BaseModel, Field

    class ItemStatus(str, Enum):
        available = "available"
        pending = "pending"
        sold = "sold"

    class Tag(BaseModel):
        id: int
        name: str

    class Item(BaseModel):
        """Example pydantic model for testing OpenAPI schema generation."""

        name: str
        description: str | None = None
        price: float
        tax: float | None = None
        tags: list[str] = Field(default_factory=list)
        status: ItemStatus = ItemStatus.available
        dimensions: dict[str, float] | None = None

    # Create a FastAPI app with comprehensive features
    app = FastAPI(
        title="Comprehensive Test API",
        description="A test API with various OpenAPI features",
        version="1.0.0",
    )

    def get_token_header(
        x_token: str = Header(..., description="Authentication token"),
    ):
        """Example dependency function for header validation."""
        if x_token != "fake-super-secret-token":
            raise HTTPException(status_code=400, detail="X-Token header invalid")
        return x_token

    TokenDep = Depends(get_token_header)

    @app.get(
        "/items/",
        operation_id="list_items",
        summary="List all items",
        description="Get a list of all items with optional filtering",
        tags=["items"],
    )
    async def list_items(
        skip: int = Query(0, description="Number of items to skip"),
        limit: int = Query(10, description="Max number of items to return"),
        status: ItemStatus | None = Query(None, description="Filter items by status"),
    ):
        """List all items with pagination and optional status filtering."""
        fake_items = [
            {"name": f"Item {i}", "price": float(i)} for i in range(skip, skip + limit)
        ]
        if status:
            fake_items = [item for item in fake_items if item.get("status") == status]
        return fake_items

    @app.post(
        "/items/",
        operation_id="create_item",
        summary="Create a new item",
        tags=["items"],
        status_code=201,
    )
    async def create_item(
        item: Item = Body(..., description="Item to create"),
        x_token: str = TokenDep,
    ):
        """Create a new item (requires authentication)."""
        return item

    @app.get(
        "/items/{item_id}",
        operation_id="get_item",
        summary="Get a specific item by ID",
        tags=["items"],
    )
    async def get_item(
        item_id: int = Path(..., description="The ID of the item to retrieve"),
        include_tax: bool = Query(
            False, description="Whether to include tax information"
        ),
    ):
        """Get details about a specific item."""
        item = {
            "id": item_id,
            "name": f"Item {item_id}",
            "price": float(item_id) * 10.0,
        }
        if include_tax:
            item["tax"] = item["price"] * 0.2
        return item

    @app.put(
        "/items/{item_id}",
        operation_id="update_item",
        summary="Update an existing item",
        tags=["items"],
    )
    async def update_item(
        item_id: int = Path(..., description="The ID of the item to update"),
        item: Item = Body(..., description="Updated item data"),
        x_token: str = TokenDep,
    ):
        """Update an existing item (requires authentication)."""
        return {"item_id": item_id, **item.model_dump()}

    @app.delete(
        "/items/{item_id}",
        operation_id="delete_item",
        summary="Delete an item",
        tags=["items"],
    )
    async def delete_item(
        item_id: int = Path(..., description="The ID of the item to delete"),
        x_token: str = TokenDep,
    ):
        """Delete an item (requires authentication)."""
        return {"item_id": item_id, "deleted": True}

    @app.patch(
        "/items/{item_id}/tags",
        operation_id="update_item_tags",
        summary="Update item tags",
        tags=["items", "tags"],
    )
    async def update_item_tags(
        item_id: int = Path(..., description="The ID of the item"),
        tags: list[str] = Body(..., description="Updated tags"),
    ):
        """Update just the tags of an item."""
        return {"item_id": item_id, "tags": tags}

    @app.get(
        "/items/{item_id}/tags/{tag_id}",
        operation_id="get_item_tag",
        summary="Get a specific tag for an item",
        tags=["items", "tags"],
    )
    async def get_item_tag(
        item_id: int = Path(..., description="The ID of the item"),
        tag_id: str = Path(..., description="The ID of the tag"),
    ):
        """Get a specific tag for an item."""
        return {"item_id": item_id, "tag_id": tag_id}

    @app.post(
        "/upload/",
        operation_id="upload_file",
        summary="Upload a file",
        tags=["files"],
    )
    async def upload_file(
        file_name: str = Query(..., description="Name of the file"),
        content_type: str = Query(..., description="Content type of the file"),
    ):
        """Upload a file (dummy endpoint for testing query params)."""
        return {
            "file_name": file_name,
            "content_type": content_type,
            "status": "uploaded",
        }

    # Add a callback route for testing complex documentation
    @app.post(
        "/webhook",
        operation_id="register_webhook",
        summary="Register a webhook",
        tags=["webhooks"],
        callbacks={  # type: ignore
            "itemProcessed": {
                "{$request.body.callbackUrl}": {
                    "post": {
                        "summary": "Callback for when an item is processed",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "item_id": {"type": "integer"},
                                            "status": {"type": "string"},
                                            "timestamp": {
                                                "type": "string",
                                                "format": "date-time",
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {
                            "200": {"description": "Webhook processed successfully"}
                        },
                    }
                }
            }
        },
    )
    async def register_webhook(
        callback_url: str = Body(
            ..., embed=True, description="URL to call when processing completes"
        ),
    ):
        """Register a webhook for processing notifications."""
        return {"registered": True, "callback_url": callback_url}

    return app


@pytest.fixture
def fastapi_openapi_schema(fastapi_app) -> dict[str, Any]:
    """Fixture that returns the OpenAPI schema from a live FastAPI server."""
    return fastapi_app.openapi()


@pytest.fixture
def parsed_routes(fastapi_openapi_schema):
    """Return parsed routes from a FastAPI OpenAPI schema."""
    return parse_openapi_to_http_routes(fastapi_openapi_schema)


@pytest.fixture
def route_map(parsed_routes):
    """Return a dictionary of routes by operation ID."""
    return {r.operation_id: r for r in parsed_routes if r.operation_id is not None}


def test_parse_fastapi_schema_route_count(parsed_routes):
    """Test that all routes are parsed from the FastAPI schema."""
    assert len(parsed_routes) == 9  # 8 endpoints + 1 callback


def test_parse_fastapi_schema_operation_ids(route_map):
    """Test that all expected operation IDs are present in the parsed schema."""
    expected_operations = [
        "list_items",
        "create_item",
        "get_item",
        "update_item",
        "delete_item",
        "update_item_tags",
        "get_item_tag",
        "upload_file",
        "register_webhook",
    ]

    for op_id in expected_operations:
        assert op_id in route_map, f"Operation ID '{op_id}' not found in parsed routes"


def test_path_parameter_parsing(route_map):
    """Test that path parameters are correctly parsed."""
    get_item = route_map["get_item"]
    path_params = [p for p in get_item.parameters if p.location == "path"]

    assert len(path_params) == 1
    assert path_params[0].name == "item_id"
    assert path_params[0].required is True


def test_query_parameter_parsing(route_map):
    """Test that query parameters are correctly parsed."""
    list_items = route_map["list_items"]
    query_params = [p for p in list_items.parameters if p.location == "query"]

    assert len(query_params) == 3  # skip, limit, status
    param_names = [p.name for p in query_params]
    assert "skip" in param_names
    assert "limit" in param_names
    assert "status" in param_names


def test_header_parameter_parsing(route_map):
    """Test that header parameters from dependencies are correctly parsed."""
    create_item = route_map["create_item"]
    header_params = [p for p in create_item.parameters if p.location == "header"]

    assert len(header_params) == 1
    assert header_params[0].name == "x-token"
    assert header_params[0].required is True


def test_request_body_content_type(route_map):
    """Test that request body content types are correctly parsed."""
    create_item = route_map["create_item"]

    assert create_item.request_body is not None
    assert "application/json" in create_item.request_body.content_schema


def test_request_body_properties(route_map):
    """Test that request body properties are correctly parsed."""
    create_item = route_map["create_item"]
    json_schema = create_item.request_body.content_schema["application/json"]
    properties = json_schema.get("properties", {})

    assert "name" in properties
    assert "price" in properties
    assert "description" in properties
    assert "tags" in properties
    assert "status" in properties


def test_request_body_status_schema(route_map):
    """Test that the status schema in request body is correctly handled."""
    create_item = route_map["create_item"]
    json_schema = create_item.request_body.content_schema["application/json"]
    properties = json_schema.get("properties", {})
    status_schema = properties.get("status", {})

    # FastAPI may represent enums as references or directly include enum values
    assert "$ref" in status_schema or "enum" in status_schema


def test_route_with_items_tag(parsed_routes):
    """Test that routes with 'items' tag are correctly parsed."""
    item_routes = [r for r in parsed_routes if "items" in r.tags]

    assert len(item_routes) >= 6  # At least 6 endpoints with "items" tag


def test_routes_with_multiple_tags(parsed_routes):
    """Test that routes with multiple tags are correctly parsed."""
    multi_tag_routes = [r for r in parsed_routes if len(r.tags) > 1]

    assert len(multi_tag_routes) >= 2  # At least 2 endpoints with multiple tags


def test_specific_route_tags(route_map):
    """Test that specific routes have the expected tags."""
    assert "items" in route_map["list_items"].tags
    assert "items" in route_map["update_item_tags"].tags
    assert "tags" in route_map["update_item_tags"].tags
    assert "webhooks" in route_map["register_webhook"].tags


def test_operation_summary(route_map):
    """Test that operation summary is correctly parsed."""
    list_items = route_map["list_items"]

    assert list_items.summary == "List all items"


def test_operation_description(route_map):
    """Test that operation description is correctly parsed."""
    list_items = route_map["list_items"]

    assert list_items.description is not None
    assert "optional filtering" in list_items.description


def test_path_with_route_parameters(route_map):
    """Test that paths with route parameters are correctly parsed."""
    get_item = route_map["get_item"]

    assert get_item.path == "/items/{item_id}"


def test_complex_nested_paths(route_map):
    """Test that complex nested paths are correctly parsed."""
    get_item_tag = route_map["get_item_tag"]

    assert get_item_tag.path == "/items/{item_id}/tags/{tag_id}"


def test_http_methods(route_map):
    """Test that HTTP methods are correctly parsed."""
    assert route_map["list_items"].method == "GET"
    assert route_map["create_item"].method == "POST"
    assert route_map["update_item"].method == "PUT"
    assert route_map["delete_item"].method == "DELETE"
    assert route_map["update_item_tags"].method == "PATCH"


def test_item_schema_properties(route_map):
    """Test that Item schema properties are correctly resolved."""
    create_item = route_map["create_item"]
    json_schema = create_item.request_body.content_schema["application/json"]
    properties = json_schema.get("properties", {})

    assert "name" in properties
    assert properties["name"]["type"] == "string"
    assert "price" in properties
    assert properties["price"]["type"] == "number"


def test_webhook_endpoint(route_map):
    """Test parsing of webhook registration endpoint."""
    webhook = route_map["register_webhook"]

    assert webhook.method == "POST"
    assert webhook.path == "/webhook"


def test_webhook_request_body(route_map):
    """Test that webhook request body is correctly parsed."""
    webhook = route_map["register_webhook"]

    assert webhook.request_body is not None
    assert "application/json" in webhook.request_body.content_schema
    json_schema = webhook.request_body.content_schema["application/json"]
    assert "callback_url" in json_schema.get("properties", {})


def test_token_dependency_handling(route_map):
    """Test that token dependencies are correctly handled in parsed endpoints."""
    token_endpoints = ["create_item", "update_item", "delete_item"]

    for op_id in token_endpoints:
        route = route_map[op_id]
        header_params = [p for p in route.parameters if p.location == "header"]
        token_headers = [p for p in header_params if p.name == "x-token"]
        assert len(token_headers) == 1, f"Expected x-token header in {op_id}"
        assert token_headers[0].required is True


# --- Additional Tag-related Tests --- #


def test_all_routes_have_tags(parsed_routes):
    """Test that all routes have a non-empty tags list."""
    for route in parsed_routes:
        assert hasattr(route, "tags"), f"Route {route.path} should have tags attribute"
        assert route.tags is not None, f"Route {route.path} tags should not be None"
        # FastAPI adds tags to all routes in our test fixture
        assert len(route.tags) > 0, f"Route {route.path} should have at least one tag"


def test_tag_consistency_across_related_endpoints(route_map):
    """Test that related endpoints have consistent tags."""
    # All item endpoints should have the "items" tag
    item_endpoints = [
        "list_items",
        "create_item",
        "get_item",
        "update_item",
        "delete_item",
    ]
    for endpoint in item_endpoints:
        assert "items" in route_map[endpoint].tags, (
            f"Endpoint {endpoint} should have 'items' tag"
        )

    # Tag-related endpoints should have both "items" and "tags" tags
    tag_endpoints = ["update_item_tags", "get_item_tag"]
    for endpoint in tag_endpoints:
        assert "items" in route_map[endpoint].tags, (
            f"Endpoint {endpoint} should have 'items' tag"
        )
        assert "tags" in route_map[endpoint].tags, (
            f"Endpoint {endpoint} should have 'tags' tag"
        )


def test_tag_order_preservation(fastapi_app):
    """Test that tag order is preserved in the parsed routes."""

    # Add a new endpoint with specifically ordered tags
    @fastapi_app.get(
        "/test-tag-order",
        tags=["first", "second", "third"],
        operation_id="test_tag_order",
    )
    async def test_tag_order():
        return {"result": "testing tag order"}

    # Get the updated schema and parse routes
    routes = parse_openapi_to_http_routes(fastapi_app.openapi())

    # Find our test route
    test_route = next((r for r in routes if r.path == "/test-tag-order"), None)
    assert test_route is not None

    # Check tag order is preserved
    assert test_route.tags == ["first", "second", "third"], (
        "Tag order should be preserved"
    )


def test_duplicate_tags_handling(fastapi_app):
    """Test handling of duplicate tags in the OpenAPI schema."""

    # Add an endpoint with duplicate tags
    @fastapi_app.get(
        "/test-duplicate-tags",
        tags=["duplicate", "items", "duplicate"],
        operation_id="test_duplicate_tags",
    )
    async def test_duplicate_tags():
        return {"result": "testing duplicate tags"}

    # Get the updated schema and parse routes
    routes = parse_openapi_to_http_routes(fastapi_app.openapi())

    # Find our test route
    test_route = next((r for r in routes if r.path == "/test-duplicate-tags"), None)
    assert test_route is not None

    # Check that duplicate tags are preserved (FastAPI might deduplicate)
    # We'll test both possibilities to be safe
    assert "duplicate" in test_route.tags, "Tag 'duplicate' should be present"
    assert "items" in test_route.tags, "Tag 'items' should be present"


def test_repr_http_routes(parsed_routes):
    """Test that HTTPRoute objects can be represented without recursion errors."""
    # Test repr on all parsed routes
    for route in parsed_routes:
        route_repr = repr(route)

        # Verify repr contains essential information
        assert route.method in route_repr, f"Method {route.method} missing from repr"
        assert route.path in route_repr, f"Path {route.path} missing from repr"

        # If operation_id exists, it should be in the repr
        if route.operation_id:
            assert route.operation_id in route_repr, (
                f"Operation ID {route.operation_id} missing from repr"
            )
