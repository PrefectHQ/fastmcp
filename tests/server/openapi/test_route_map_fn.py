"""Tests for the route_map_fn and component_fn functionality in FastMCPOpenAPI."""

import httpx
import pytest

from fastmcp.server.openapi import FastMCPOpenAPI, MCPType, RouteMap, RouteMapFn


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

    def admin_routes_to_tools(route, mcp_type):
        """Convert all admin routes to tools."""
        if "/admin/" in route.path:
            return MCPType.TOOL
        return None

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_map_fn=admin_routes_to_tools,
    )

    # Admin GET route should be converted to tool instead of resource
    tools = server._tool_manager._tools
    assert "getAdminSettings" in tools

    # Admin POST route should still be a tool (was already)
    assert "updateAdminSettings" in tools


def test_component_fn_customization(sample_openapi_spec, http_client):
    """Test that component_fn can customize components."""

    def customize_components(route, component):
        """Customize components based on route."""
        from fastmcp.server.openapi import OpenAPIResource, OpenAPITool

        # Add custom tags to all components
        component.tags.add("custom")

        # Modify tool descriptions
        if isinstance(component, OpenAPITool):
            component.description = (component.description or "") + " [CUSTOMIZED TOOL]"

        # Modify resource descriptions
        if isinstance(component, OpenAPIResource):
            component.description = (
                component.description or ""
            ) + " [CUSTOMIZED RESOURCE]"

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        mcp_component_fn=customize_components,
    )

    # Check that components were customized
    tools = server._tool_manager._tools
    resources = server._resource_manager._resources

    # Tools should have custom tags and modified descriptions
    for tool in tools.values():
        assert "custom" in tool.tags
        assert "[CUSTOMIZED TOOL]" in (tool.description or "")

    # Resources should have custom tags and modified descriptions
    for resource in resources.values():
        assert "custom" in resource.tags
        assert "[CUSTOMIZED RESOURCE]" in (resource.description or "")


def test_route_map_fn_returns_none(sample_openapi_spec, http_client):
    """Test that route_map_fn returning None uses defaults."""

    def always_return_none(route, mcp_type):
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
    # Check that components were created with default mapping
    tools = server._tool_manager._tools
    resources = server._resource_manager._resources
    templates = server._resource_manager._templates

    # Should have tools, resources, and templates based on default mapping
    assert len(tools) > 0
    assert len(resources) == 0
    assert len(templates) == 0


def test_route_map_fn_called_for_excluded_routes(sample_openapi_spec, http_client):
    """Test that route_map_fn is called for excluded routes and can rescue them."""

    # Exclude all admin routes
    route_maps = [
        RouteMap(
            methods=["GET", "POST"], pattern=r".*/admin/.*", mcp_type=MCPType.EXCLUDE
        )
    ]

    called_routes = []

    def track_calls_and_rescue(route, mcp_type):
        """Track which routes the function is called for and rescue some excluded routes."""
        called_routes.append((route.method, route.path))

        # Rescue the admin GET route by converting it to a tool
        if route.path == "/admin/settings" and route.method == "GET":
            return MCPType.TOOL

        return None  # Accept the assignment for other routes

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_maps=route_maps,
        route_map_fn=track_calls_and_rescue,
    )

    # route_map_fn should now be called for all routes, including excluded admin routes
    assert ("GET", "/admin/settings") in called_routes
    assert ("GET", "/users") in called_routes
    assert ("GET", "/users/{id}") in called_routes
    assert ("GET", "/api/data") in called_routes
    assert ("POST", "/admin/settings") in called_routes

    # The rescued admin GET route should now be a tool
    tools = server._tool_manager._tools
    assert "getAdminSettings" in tools

    # The admin POST route should still be excluded (not rescued)
    assert "updateAdminSettings" not in tools


def test_route_map_fn_error_handling(sample_openapi_spec, http_client):
    """Test that errors in route_map_fn are handled gracefully."""

    def error_function(route, mcp_type):
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


def test_component_fn_error_handling(sample_openapi_spec, http_client):
    """Test that errors in component_fn are handled gracefully."""

    def error_function(route, component):
        """Function that raises an error."""
        if route.path == "/users":
            raise ValueError("Test error in component_fn")

    # Should not raise an error, but log a warning
    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        mcp_component_fn=error_function,
    )

    # Server should still be created successfully
    assert server.name == "Test Server"


def test_combined_route_map_fn_and_component_fn(sample_openapi_spec, http_client):
    """Test using both route_map_fn and component_fn together."""

    def route_mapper(route, mcp_type):
        """Convert admin routes to tools."""
        if "/admin/" in route.path:
            return MCPType.TOOL
        return None

    def component_customizer(route, component):
        """Add admin tag to admin components."""
        if "/admin/" in route.path:
            component.tags.add("admin")

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_map_fn=route_mapper,
        mcp_component_fn=component_customizer,
    )

    # Check that both functions worked
    tools = server._tool_manager._tools

    # Admin GET route should be converted to tool
    assert "getAdminSettings" in tools
    admin_tool = tools["getAdminSettings"]
    assert "admin" in admin_tool.tags

    # Admin POST route should have admin tag
    admin_post_tool = tools["updateAdminSettings"]
    assert "admin" in admin_post_tool.tags


def test_route_map_fn_signature_validation():
    """Test that route_map_fn has the correct signature."""

    from fastmcp.utilities import openapi

    # This is more of a type checking test
    def valid_route_map_fn(
        route: openapi.HTTPRoute, mcp_type: MCPType
    ) -> MCPType | None:
        return None

    # Should be assignable to RouteMapFn type
    fn: RouteMapFn = valid_route_map_fn
    assert callable(fn)


def test_component_fn_signature_validation():
    """Test that component_fn has the correct signature."""
    from fastmcp.server.openapi import (
        ComponentFn,
        OpenAPIResource,
        OpenAPIResourceTemplate,
        OpenAPITool,
    )
    from fastmcp.utilities import openapi

    # This is more of a type checking test
    def valid_component_fn(
        route: openapi.HTTPRoute,
        component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
    ) -> None:
        pass

    # Should be assignable to ComponentFn type
    fn: ComponentFn = valid_component_fn
    assert callable(fn)


def test_route_map_fn_can_rescue_excluded_routes(sample_openapi_spec, http_client):
    """Test that route_map_fn can rescue routes that were excluded by RouteMap."""

    # Exclude ALL routes by default
    route_maps = [
        RouteMap(mcp_type=MCPType.EXCLUDE)  # Catch-all exclusion
    ]

    def rescue_users_routes(route, mcp_type):
        """Rescue only user-related routes."""
        if "/users" in route.path:
            # Rescue user routes as tools
            return MCPType.TOOL
        # Let everything else stay excluded
        return None

    server = FastMCPOpenAPI(
        openapi_spec=sample_openapi_spec,
        client=http_client,
        name="Test Server",
        route_maps=route_maps,
        route_map_fn=rescue_users_routes,
    )

    # Only user routes should be rescued as tools
    tools = server._tool_manager._tools
    resources = server._resource_manager._resources
    templates = server._resource_manager._templates

    # Should have user-related tools
    assert "listUsers" in tools
    assert "getUserById" in tools

    # Should have no resources or templates (everything excluded except rescued tools)
    assert len(resources) == 0
    assert len(templates) == 0

    # Admin and API routes should still be excluded
    assert "getAdminSettings" not in tools
    assert "updateAdminSettings" not in tools
    assert "getData" not in tools
