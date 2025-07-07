"""Integration test for OpenAPI explode property handling.

This test verifies that the explode property is correctly parsed from OpenAPI
specifications and properly applied during HTTP request serialization.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx

from fastmcp.server.openapi import OpenAPITool
from fastmcp.utilities.openapi import parse_openapi_to_http_routes


class TestExplodeIntegration:
    """Test the complete pipeline from OpenAPI spec to HTTP request parameters."""

    def test_explode_false_parsing_from_openapi_spec(self):
        """Test that explode=false is correctly parsed from OpenAPI specification."""
        # Real OpenAPI spec with explode: false
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search_items",
                        "parameters": [
                            {
                                "name": "tags",
                                "in": "query",
                                "required": False,
                                "style": "form",
                                "explode": False,  # This should be respected
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Success",
                                "content": {
                                    "application/json": {"schema": {"type": "object"}}
                                },
                            }
                        },
                    }
                }
            },
        }

        # Parse the spec
        routes = parse_openapi_to_http_routes(openapi_spec)
        route = routes[0]
        parameter = route.parameters[0]

        # Verify explode property was captured correctly
        assert parameter.name == "tags"
        assert parameter.location == "query"
        assert parameter.explode is False, (
            f"Expected explode=False, got {parameter.explode}"
        )

    def test_explode_true_parsing_from_openapi_spec(self):
        """Test that explode=true is correctly parsed from OpenAPI specification."""
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search_items",
                        "parameters": [
                            {
                                "name": "tags",
                                "in": "query",
                                "explode": True,  # Explicitly set to true
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            }
                        ],
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            },
        }

        routes = parse_openapi_to_http_routes(openapi_spec)
        parameter = routes[0].parameters[0]

        assert parameter.explode is True, (
            f"Expected explode=True, got {parameter.explode}"
        )

    def test_explode_default_parsing_from_openapi_spec(self):
        """Test that missing explode defaults to None during parsing."""
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search_items",
                        "parameters": [
                            {
                                "name": "tags",
                                "in": "query",
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                # No explode property specified
                            }
                        ],
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            },
        }

        routes = parse_openapi_to_http_routes(openapi_spec)
        parameter = routes[0].parameters[0]

        assert parameter.explode is None, (
            f"Expected explode=None, got {parameter.explode}"
        )

    async def test_explode_false_request_serialization(self):
        """Test that explode=false results in comma-separated query parameters in HTTP requests.

        This is the critical integration test that would have failed before the fix.
        """
        # OpenAPI spec with explode: false
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search_items",
                        "parameters": [
                            {
                                "name": "tags",
                                "in": "query",
                                "explode": False,
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            }
                        ],
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            },
        }

        # Parse and create tool
        routes = parse_openapi_to_http_routes(openapi_spec)
        route = routes[0]

        # Mock HTTP client
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response

        # Create tool
        tool = OpenAPITool(
            client=mock_client,
            route=route,
            name="search_items",
            description="Search items",
            parameters={},
        )

        # Execute tool with array parameter
        await tool.run({"tags": ["red", "blue", "green"]})

        # Verify the HTTP request was made with comma-separated parameters
        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs

        # Check that params contains comma-separated values, not an array
        params = call_kwargs.get("params", {})
        assert "tags" in params, "tags parameter should be present"

        tags_value = params["tags"]
        assert isinstance(tags_value, str), (
            f"Expected string for explode=false, got {type(tags_value)}"
        )
        assert tags_value == "red,blue,green", (
            f"Expected 'red,blue,green', got '{tags_value}'"
        )

    async def test_explode_true_request_serialization(self):
        """Test that explode=true results in separate query parameters in HTTP requests."""
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search_items",
                        "parameters": [
                            {
                                "name": "tags",
                                "in": "query",
                                "explode": True,
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            }
                        ],
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            },
        }

        routes = parse_openapi_to_http_routes(openapi_spec)
        route = routes[0]

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response

        tool = OpenAPITool(
            client=mock_client,
            route=route,
            name="search_items",
            description="Search items",
            parameters={},
        )

        await tool.run({"tags": ["red", "blue", "green"]})

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs

        params = call_kwargs.get("params", {})
        assert "tags" in params, "tags parameter should be present"

        tags_value = params["tags"]
        assert isinstance(tags_value, list), (
            f"Expected list for explode=true, got {type(tags_value)}"
        )
        assert tags_value == ["red", "blue", "green"], (
            f"Expected ['red', 'blue', 'green'], got {tags_value}"
        )

    async def test_explode_default_request_serialization(self):
        """Test that default behavior (no explode) uses explode=true for query parameters."""
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search_items",
                        "parameters": [
                            {
                                "name": "tags",
                                "in": "query",
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                # No explode specified - should default to true for query params
                            }
                        ],
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            },
        }

        routes = parse_openapi_to_http_routes(openapi_spec)
        route = routes[0]

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response

        tool = OpenAPITool(
            client=mock_client,
            route=route,
            name="search_items",
            description="Search items",
            parameters={},
        )

        await tool.run({"tags": ["red", "blue", "green"]})

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs

        params = call_kwargs.get("params", {})
        tags_value = params["tags"]

        # Default behavior should be explode=true (separate parameters)
        assert isinstance(tags_value, list), (
            f"Expected list for default behavior, got {type(tags_value)}"
        )
        assert tags_value == ["red", "blue", "green"], (
            f"Expected ['red', 'blue', 'green'], got {tags_value}"
        )
