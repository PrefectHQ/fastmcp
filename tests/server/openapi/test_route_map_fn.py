"""Tests for the route_map_fn functionality in FastMCPOpenAPI."""

import httpx
import pytest

from fastmcp.server.openapi import FastMCPOpenAPI, MCPType


@pytest.fixture
def sample_openapi_spec():
    """Sample OpenAPI spec for testing."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "summary": "List users",
                    "operationId": "listUsers",
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/users/{id}": {
                "get": {
                    "summary": "Get user by ID",
                    "operationId": "getUserById",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/admin/settings": {
                "get": {
                    "summary": "Get admin settings",
                    "operationId": "getAdminSettings",
                    "responses": {"200": {"description": "Success"}},
                },
                "post": {
                    "summary": "Update admin settings",
                    "operationId": "updateAdminSettings",
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    },
                    "responses": {"200": {"description": "Success"}},
                },
            },
            "/api/data": {
                "get": {
                    "summary": "Get data",
                    "operationId": "getData",
                    "responses": {"200": {"description": "Success"}},
                }
            },
        },
    }


@pytest.fixture
def http_client():
    """HTTP client for testing."""
    return httpx.AsyncClient()


def test_route_map_fn_none(sample_openapi_spec, http_client):
    """Test that server works correctly when route_map_fn is None."""
    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_map_fn=None,  # Explicitly set to None
    )

    assert server.name == "Test Server"


def test_route_map_fn_custom_type_conversion(sample_openapi_spec, http_client):
    """Test that route_map_fn can convert route types."""

    def admin_routes_to_tools(route, mcp_type, name):
        """Convert all admin routes to tools."""
        if "/admin/" in route.path:
            return MCPType.TOOL, f"admin_{name}"
        return None

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_map_fn=admin_routes_to_tools,
    )

    # Admin GET route should be converted to tool instead of resource
    tools = server._tool_manager._tools
    assert "admin_getAdminSettings" in tools

    # Admin POST route should be renamed
    assert "admin_updateAdminSettings" in tools


def test_route_map_fn_custom_naming(sample_openapi_spec, http_client):
    """Test that route_map_fn can customize naming."""

    def prefix_user_routes(route, mcp_type, name):
        """Add user_ prefix to user-related routes."""
        if "/users/" in route.path:
            return mcp_type, f"user_{name}"
        return None

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_map_fn=prefix_user_routes,
    )

    # Check that user routes got renamed
    templates = server._resource_manager._templates
    template_names = list(templates.keys())

    # The getUserById template should be renamed to user_getUserById
    found_user_template = False
    for uri in template_names:
        if "user_getUserById" in uri:
            found_user_template = True
            break
    assert found_user_template


def test_route_map_fn_returns_none(sample_openapi_spec, http_client):
    """Test that route_map_fn returning None uses defaults."""

    def always_return_none(route, mcp_type, name):
        """Always return None to use defaults."""
        return None

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_map_fn=always_return_none,
    )

    # Should have default behavior
    assert server.name == "Test Server"
    # Check that components were created with default names
    tools = server._tool_manager._tools
    resources = server._resource_manager._resources
    templates = server._resource_manager._templates

    # Should have tools, resources, and templates based on default mapping
    assert len(tools) > 0
    assert len(resources) > 0
    assert len(templates) > 0


def test_route_map_fn_not_called_for_excluded_routes(sample_openapi_spec, http_client):
    """Test that route_map_fn is not called for excluded routes."""

    from fastmcp.server.openapi import RouteMap

    # Exclude all admin routes
    route_maps = [
        RouteMap(
            methods=["GET", "POST"], pattern=r".*/admin/.*", mcp_type=MCPType.EXCLUDE
        )
    ]

    called_routes = []

    def track_calls(route, mcp_type, name):
        """Track which routes the function is called for."""
        called_routes.append(route.path)
        return None

    FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_maps=route_maps,
        route_map_fn=track_calls,
    )

    # route_map_fn should not be called for excluded admin routes
    assert "/admin/settings" not in called_routes
    # But should be called for other routes
    assert "/users" in called_routes
    assert "/users/{id}" in called_routes
    assert "/api/data" in called_routes


def test_route_map_fn_error_handling(sample_openapi_spec, http_client):
    """Test that errors in route_map_fn are handled gracefully."""

    def error_function(route, mcp_type, name):
        """Function that raises an error."""
        if route.path == "/users":
            raise ValueError("Test error")
        return None

    # Should not raise an error, but log a warning
    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_map_fn=error_function,
    )

    # Server should still be created successfully
    assert server.name == "Test Server"


def test_route_map_fn_with_complex_logic(sample_openapi_spec, http_client):
    """Test route_map_fn with complex conditional logic."""

    def complex_mapper(route, mcp_type, name):
        """Complex mapping logic."""
        # Convert admin routes to tools
        if "/admin/" in route.path:
            return MCPType.TOOL, f"admin_{name}"

        # Convert user parameter routes to templates with custom naming
        if "/users/{" in route.path:
            return MCPType.RESOURCE_TEMPLATE, f"user_template_{name}"

        # Convert list routes to resources with custom naming
        if route.path.endswith("/users") or route.path.endswith("/data"):
            return MCPType.RESOURCE, f"list_{name}"

        # Use defaults for everything else
        return None

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_map_fn=complex_mapper,
    )

    # Check that the complex logic was applied correctly
    tools = server._tool_manager._tools
    resources = server._resource_manager._resources
    templates = server._resource_manager._templates

    # Admin routes should be tools
    assert "admin_getAdminSettings" in tools
    assert "admin_updateAdminSettings" in tools

    # List routes should be resources with custom names
    found_list_resource = False
    for uri in resources.keys():
        if "list_listUsers" in uri or "list_getData" in uri:
            found_list_resource = True
            break
    assert found_list_resource

    # User parameter route should be template with custom name
    found_user_template = False
    for uri in templates.keys():
        if "user_template_getUserById" in uri:
            found_user_template = True
            break
    assert found_user_template


def test_route_map_fn_signature_validation():
    """Test that route_map_fn has the correct signature."""
    from fastmcp.server.openapi import RouteMapFn
    from fastmcp.utilities import openapi

    # This is more of a type checking test
    def valid_route_map_fn(
        route: openapi.HTTPRoute, mcp_type: MCPType, name: str
    ) -> tuple[MCPType, str] | None:
        return None

    # Should be assignable to RouteMapFn type
    fn: RouteMapFn = valid_route_map_fn
    assert callable(fn)
