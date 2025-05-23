"""Tests for the deprecated RouteType.IGNORE."""

import warnings

import httpx
import pytest

from fastmcp.server.openapi import (
    FastMCPOpenAPI,
    MCPType,
    RouteMap,
    RouteType,
)


def test_route_type_ignore_deprecation_warning():
    """Test that using RouteType.IGNORE emits a deprecation warning."""
    # Let's manually capture the warnings

    # Record all warnings
    with warnings.catch_warnings(record=True) as recorded:
        # Make sure warnings are always triggered
        warnings.simplefilter("always")

        # Create a RouteMap with RouteType.IGNORE
        route_map = RouteMap(
            methods=["GET"], pattern=r"^/analytics$", route_type=RouteType.IGNORE
        )

    # Check for the expected warnings in the recorded warnings
    route_type_warning = False
    ignore_warning = False

    for w in recorded:
        if issubclass(w.category, DeprecationWarning):
            message = str(w.message)
            if "route_type' parameter is deprecated" in message:
                route_type_warning = True
            if "RouteType.IGNORE is deprecated" in message:
                ignore_warning = True

    # Make sure both warnings were triggered
    assert route_type_warning, "Missing 'route_type' deprecation warning"
    assert ignore_warning, "Missing 'RouteType.IGNORE' deprecation warning"

    # Verify that RouteType.IGNORE was converted to MCPType.EXCLUDE
    assert route_map.mcp_type == MCPType.EXCLUDE


class TestRouteTypeIgnoreDeprecation:
    """Test class for the deprecated RouteType.IGNORE."""

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
                    }
                },
                "/analytics": {
                    "get": {
                        "operationId": "get_analytics",
                        "summary": "Get analytics data",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
        }

    @pytest.fixture
    async def mock_client(self) -> httpx.AsyncClient:
        """Create a mock client for testing."""

        async def _responder(request):
            return httpx.Response(200, json={"success": True})

        return httpx.AsyncClient(transport=httpx.MockTransport(_responder))

    async def test_route_type_ignore_conversion(self, basic_openapi_spec, mock_client):
        """Test that routes with RouteType.IGNORE are properly excluded."""
        # Capture the deprecation warning without checking the exact message
        with pytest.warns(DeprecationWarning):
            server = FastMCPOpenAPI(
                openapi_spec=basic_openapi_spec,
                client=mock_client,
                route_maps=[
                    # Use the deprecated RouteType.IGNORE
                    RouteMap(
                        methods=["GET"],
                        pattern=r"^/analytics$",
                        route_type=RouteType.IGNORE,
                    ),
                    # Make everything else a resource
                    RouteMap(
                        methods=["GET"], pattern=r".*", route_type=RouteType.RESOURCE
                    ),
                ],
            )

        # Check that the analytics route was excluded (converted from IGNORE to EXCLUDE)
        resources = await server.get_resources()
        resource_uris = [str(r.uri) for r in resources.values()]

        # Analytics should be excluded
        assert "resource://get_items" in resource_uris
        assert "resource://get_analytics" not in resource_uris
