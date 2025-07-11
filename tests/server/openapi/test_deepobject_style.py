"""Integration test for OpenAPI deepObject style parameter handling.

This test verifies that the deepObject style and explode properties are correctly
parsed from OpenAPI specifications and properly applied during HTTP request serialization.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx

from fastmcp.server.openapi import OpenAPITool
from fastmcp.utilities.openapi import parse_openapi_to_http_routes


class TestDeepObjectStyle:
    """Test the complete pipeline from OpenAPI spec to HTTP request parameters for deepObject style."""

    def test_deepobject_style_parsing_from_openapi_spec(self):
        """Test that deepObject style is correctly parsed from OpenAPI specification."""
        # Real OpenAPI spec with style: deepObject and explode: true
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/api/surveys": {
                    "get": {
                        "operationId": "getSurveys",
                        "parameters": [
                            {
                                "name": "target",
                                "in": "query",
                                "required": False,
                                "style": "deepObject",
                                "explode": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {
                                            "type": "string",
                                            "description": "Valid ID for an object",
                                        },
                                        "type": {
                                            "type": "string",
                                            "enum": ["location", "organisation"],
                                            "description": "The type of object for given id",
                                        },
                                    },
                                    "required": ["type", "id"],
                                },
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Success",
                                "content": {
                                    "application/json": {"schema": {"type": "integer"}}
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

        # Verify style and explode properties were captured correctly
        assert parameter.name == "target"
        assert parameter.location == "query"
        assert parameter.style == "deepObject", (
            f"Expected style='deepObject', got {parameter.style}"
        )
        assert parameter.explode is True, (
            f"Expected explode=True, got {parameter.explode}"
        )

    async def test_deepobject_style_request_serialization(self):
        """Test that deepObject style results in bracketed query parameters in HTTP requests.

        This is the critical integration test that reproduces the GitHub issue.
        """
        # OpenAPI spec matching the GitHub issue example
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/api/surveys": {
                    "get": {
                        "operationId": "getSurveys",
                        "parameters": [
                            {
                                "name": "target",
                                "in": "query",
                                "required": False,
                                "style": "deepObject",
                                "explode": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "type": {"type": "string"},
                                    },
                                    "required": ["type", "id"],
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
            name="getSurveys",
            description="Get surveys",
            parameters={},
        )

        # Execute tool with object parameter (as it would come from user input)
        await tool.run(
            {"target": {"id": "57dc372a81b610496e8b465e", "type": "organisation"}}
        )

        # Verify the HTTP request was made with deepObject-style parameters
        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs

        # Check that params contains bracketed parameters, not JSON string
        params = call_kwargs.get("params", {})

        # Should have target[id] and target[type] parameters
        assert "target[id]" in params, "target[id] parameter should be present"
        assert "target[type]" in params, "target[type] parameter should be present"

        # Values should be correctly set
        assert params["target[id]"] == "57dc372a81b610496e8b465e", (
            f"Expected target[id]=57dc372a81b610496e8b465e, got {params.get('target[id]')}"
        )
        assert params["target[type]"] == "organisation", (
            f"Expected target[type]=organisation, got {params.get('target[type]')}"
        )

        # Should NOT have the original parameter name as JSON
        assert "target" not in params, (
            "Original 'target' parameter should not be present when using deepObject style"
        )

    async def test_deepobject_style_with_explode_false(self):
        """Test that deepObject style with explode=false falls back to JSON serialization."""
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/api/surveys": {
                    "get": {
                        "operationId": "getSurveys",
                        "parameters": [
                            {
                                "name": "target",
                                "in": "query",
                                "style": "deepObject",
                                "explode": False,  # Non-standard combination
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "type": {"type": "string"},
                                    },
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
            name="getSurveys",
            description="Get surveys",
            parameters={},
        )

        await tool.run({"target": {"id": "123", "type": "test"}})

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs

        params = call_kwargs.get("params", {})

        # Should fall back to JSON serialization
        assert "target" in params, "target parameter should be present"
        assert params["target"] == '{"id": "123", "type": "test"}', (
            f"Expected JSON string fallback, got {params.get('target')}"
        )

    async def test_non_object_with_deepobject_style(self):
        """Test that non-object parameters with deepObject style are handled gracefully."""
        openapi_spec = {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/api/test": {
                    "get": {
                        "operationId": "testEndpoint",
                        "parameters": [
                            {
                                "name": "param",
                                "in": "query",
                                "style": "deepObject",
                                "explode": True,
                                "schema": {"type": "string"},  # Not an object
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
            name="testEndpoint",
            description="Test endpoint",
            parameters={},
        )

        # Pass a string value instead of an object
        await tool.run({"param": "test_value"})

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs

        params = call_kwargs.get("params", {})

        # Should use the parameter as-is since it's not an object
        assert "param" in params, "param parameter should be present"
        assert params["param"] == "test_value", (
            f"Expected 'test_value', got {params.get('param')}"
        )
