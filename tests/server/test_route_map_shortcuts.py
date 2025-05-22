"""Tests for the route map shortcut functions."""

import httpx
import pytest

from fastmcp.server.openapi import (
    ALL_TOOLS,
    EXCLUDE_ALL,
    EXCLUDE_PATTERN,
    PATTERN_AS_TOOLS,
    FastMCPOpenAPI,
    MCPType,
    RouteMap,
    RouteType,
)


class TestRouteMapShortcuts:
    """Tests for the route map shortcut functions."""

    def test_functions_return_correct_route_maps(self):
        """Test that each shortcut function returns a RouteMap with the expected properties."""
        # Test EXCLUDE_ALL
        exclude_all = EXCLUDE_ALL()
        assert isinstance(exclude_all, RouteMap)
        assert exclude_all.methods == "*"
        assert exclude_all.pattern == ".*"
        assert exclude_all.mcp_type == MCPType.EXCLUDE

        # Test ALL_TOOLS
        all_tools = ALL_TOOLS()
        assert isinstance(all_tools, RouteMap)
        assert all_tools.methods == "*"
        assert all_tools.pattern == ".*"
        assert all_tools.mcp_type == MCPType.TOOL

        # Test PATTERN_AS_TOOLS
        pattern = r"^/api/.*"
        pattern_as_tools = PATTERN_AS_TOOLS(pattern)
        assert isinstance(pattern_as_tools, RouteMap)
        assert pattern_as_tools.methods == "*"
        assert pattern_as_tools.pattern == pattern
        assert pattern_as_tools.mcp_type == MCPType.TOOL

        # Test EXCLUDE_PATTERN
        pattern = r"^/admin/.*"
        exclude_pattern = EXCLUDE_PATTERN(pattern)
        assert isinstance(exclude_pattern, RouteMap)
        assert exclude_pattern.methods == "*"
        assert exclude_pattern.pattern == pattern
        assert exclude_pattern.mcp_type == MCPType.EXCLUDE

    def test_backward_compatibility(self):
        """Test that backward compatibility with RouteType and route_type works."""
        # Test creating a RouteMap with route_type
        with pytest.warns(DeprecationWarning):
            route_map = RouteMap(
                methods=["GET"], pattern=r".*", route_type=RouteType.TOOL
            )
            assert route_map.mcp_type == MCPType.TOOL

        # Test accessing fields on RouteType directly
        # Note: importing RouteType already causes the deprecation warning,
        # so we don't need to check for it again here
        rt = RouteType.RESOURCE
        assert rt.value == "RESOURCE"
        assert rt.name == "RESOURCE"


class TestRouteMapShortcutsIntegration:
    """Integration tests for the route map shortcut functions with FastMCPOpenAPI."""

    @pytest.fixture
    def basic_openapi_spec(self) -> dict:
        """Create a simple OpenAPI spec for testing."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "get_items",
                        "summary": "Get all items",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "create_item",
                        "summary": "Create an item",
                        "responses": {"201": {"description": "Created"}},
                    },
                },
                "/users": {
                    "get": {
                        "operationId": "get_users",
                        "summary": "Get all users",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
                "/admin": {
                    "get": {
                        "operationId": "get_admin",
                        "summary": "Admin endpoint",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
                "/items/{item_id}": {
                    "get": {
                        "operationId": "get_item",
                        "summary": "Get an item by ID",
                        "parameters": [
                            {
                                "name": "item_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "Success"}},
                    },
                },
            },
        }

    @pytest.fixture
    async def mock_client(self) -> httpx.AsyncClient:
        """Create a mock client for testing."""

        async def _responder(request):
            return httpx.Response(200, json={"success": True})

        return httpx.AsyncClient(transport=httpx.MockTransport(_responder))

    async def test_all_tools(self, basic_openapi_spec, mock_client):
        """Test using ALL_TOOLS() to convert all routes to tools."""
        server = FastMCPOpenAPI(
            openapi_spec=basic_openapi_spec,
            client=mock_client,
            route_maps=[ALL_TOOLS()],
        )

        # Check that all routes are tools
        tools = await server.get_tools()
        resources = await server.get_resources()
        templates = await server.get_resource_templates()

        # All 5 routes should be tools
        assert len(tools) == 5
        assert len(resources) == 0
        assert len(templates) == 0

        # Check that all expected tools exist
        tool_names = [t.name for t in tools.values()]
        assert "get_items" in tool_names
        assert "create_item" in tool_names
        assert "get_users" in tool_names
        assert "get_admin" in tool_names
        assert "get_item" in tool_names

    async def test_exclude_pattern(self, basic_openapi_spec, mock_client):
        """Test using EXCLUDE_PATTERN() to exclude specific routes."""
        server = FastMCPOpenAPI(
            openapi_spec=basic_openapi_spec,
            client=mock_client,
            route_maps=[
                # Exclude admin endpoints
                EXCLUDE_PATTERN(r"^/admin"),
                # Make everything else a tool
                ALL_TOOLS(),
            ],
        )

        # Check that admin route is excluded
        tools = await server.get_tools()
        tool_names = [t.name for t in tools.values()]

        # All routes except admin should be tools
        assert "get_items" in tool_names
        assert "create_item" in tool_names
        assert "get_users" in tool_names
        assert "get_item" in tool_names
        assert "get_admin" not in tool_names  # This should be excluded

    async def test_pattern_as_tools(self, basic_openapi_spec, mock_client):
        """Test using PATTERN_AS_TOOLS() to convert routes matching a pattern to tools."""
        server = FastMCPOpenAPI(
            openapi_spec=basic_openapi_spec,
            client=mock_client,
            route_maps=[
                # Make /items routes tools regardless of method
                PATTERN_AS_TOOLS(r"^/items"),
                # Make everything else a resource
                RouteMap(methods="*", pattern=r".*", mcp_type=MCPType.RESOURCE),
            ],
        )

        # Check that /items routes are tools
        tools = await server.get_tools()
        tool_names = [t.name for t in tools.values()]
        assert "get_items" in tool_names
        assert "create_item" in tool_names
        assert "get_item" in tool_names

        # Check that other routes are resources
        resources = await server.get_resources()
        resource_names = [r.name for r in resources.values()]
        assert "get_users" in resource_names
        assert "get_admin" in resource_names
