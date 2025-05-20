from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from fastmcp import FastMCP
from fastmcp.server.openapi import OpenAPITool
from fastmcp.utilities.openapi import HTTPRoute, ParameterInfo


@pytest.fixture
def array_path_spec():
    """Load a minimal OpenAPI spec with an array path parameter."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/select/{days}": {
                "put": {
                    "operationId": "test-operation",
                    "parameters": [
                        {
                            "name": "days",
                            "in": "path",
                            "required": True,
                            "style": "simple",
                            "explode": False,
                            "schema": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": [
                                        "monday",
                                        "tuesday",
                                        "wednesday",
                                        "thursday",
                                        "friday",
                                        "saturday",
                                        "sunday",
                                    ],
                                },
                            },
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"result": {"type": "string"}},
                                        "required": ["result"],
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


@pytest.fixture
def mock_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    # Set up a mock response
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": "success"}
    mock_response.raise_for_status.return_value = None
    client.request.return_value = mock_response
    return client


async def test_fastmcp_from_openapi(array_path_spec, mock_client):
    """Test creating FastMCP from OpenAPI spec with array path parameter."""
    # Create FastMCP from the spec
    mcp = FastMCP.from_openapi(array_path_spec, client=mock_client)

    # Verify the tool was created using the MCP protocol method
    tools_result = await mcp.get_tools()
    tool_names = [tool.name for tool in tools_result.values()]
    assert "test-operation" in tool_names


@pytest.mark.asyncio
async def test_array_path_parameter_handling(mock_client):
    """Test how array path parameters are handled."""
    # Create a simple route with array path parameter
    route = HTTPRoute(
        path="/select/{days}",
        method="PUT",
        operation_id="test-operation",
        parameters=[
            ParameterInfo(
                name="days",
                location="path",
                required=True,
                schema={
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "monday",
                            "tuesday",
                            "wednesday",
                            "thursday",
                            "friday",
                            "saturday",
                            "sunday",
                        ],
                    },
                },
            )
        ],
    )

    # Create the tool
    tool = OpenAPITool(
        client=mock_client,
        route=route,
        name="test-operation",
        description="Test operation",
        parameters={},
    )

    # Test with a single value
    await tool._execute_request(days=["monday"])

    # Check that the path parameter is formatted correctly
    # This is where the bug is: it should be '/select/monday' not '/select/[\'monday\']'
    mock_client.request.assert_called_with(
        method="PUT",
        url="/select/monday",  # This is the expected format
        params={},
        headers={},
        json=None,
        timeout=None,
    )
    mock_client.request.reset_mock()

    # Test with multiple values
    await tool._execute_request(days=["monday", "tuesday"])

    # Check that the path parameter is formatted correctly
    # It should be '/select/monday,tuesday' not '/select/[\'monday\', \'tuesday\']'
    mock_client.request.assert_called_with(
        method="PUT",
        url="/select/monday,tuesday",  # This is the expected format
        params={},
        headers={},
        json=None,
        timeout=None,
    )


@pytest.mark.asyncio
async def test_integration_array_path_parameter(array_path_spec, mock_client):
    """Integration test for array path parameters."""
    # Create FastMCP from the spec
    mcp = FastMCP.from_openapi(array_path_spec, client=mock_client)

    # Call the tool with a single value
    await mcp._mcp_call_tool("test-operation", {"days": ["monday"]})

    # Check the request was made correctly
    mock_client.request.assert_called_with(
        method="PUT",
        url="/select/monday",
        params={},
        headers={},
        json=None,
        timeout=None,
    )
    mock_client.request.reset_mock()

    # Call the tool with multiple values
    await mcp._mcp_call_tool("test-operation", {"days": ["monday", "tuesday"]})

    # Check the request was made correctly
    mock_client.request.assert_called_with(
        method="PUT",
        url="/select/monday,tuesday",
        params={},
        headers={},
        json=None,
        timeout=None,
    )


@pytest.mark.asyncio
async def test_complex_nested_array_path_parameter(mock_client):
    """Test handling of complex nested array path parameters."""
    # Create a route with a path parameter that contains nested objects in an array
    route = HTTPRoute(
        path="/report/{filters}",
        method="GET",
        operation_id="test-complex-filters",
        parameters=[
            ParameterInfo(
                name="filters",
                location="path",
                required=True,
                schema={
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "value": {"type": "string"},
                        },
                    },
                },
            )
        ],
    )

    # Create the tool
    tool = OpenAPITool(
        client=mock_client,
        route=route,
        name="test-complex-filters",
        description="Test operation with complex filters",
        parameters={},
    )

    # Test with a more complex path parameter
    # This would typically be serialized as JSON or a more complex format
    # But for path parameters with style=simple, it should be comma-separated
    complex_filters = [
        {"field": "status", "value": "active"},
        {"field": "type", "value": "user"},
    ]

    # Execute the request with complex filters
    await tool._execute_request(filters=complex_filters)

    # The complex object should be properly serialized in the URL
    # For path parameters, this would typically need a custom serialization strategy
    # but our implementation should handle it safely
    call_args = mock_client.request.call_args

    # Verify the request was made
    assert call_args is not None, "The request was not made"

    # Get the called URL and verify it contains the serialized path parameter
    called_url = call_args[1].get("url")

    # Check that the path parameter is handled (we don't expect perfect serialization,
    # but it should not cause errors and should maintain the array structure)
    assert "/report/" in called_url, "The URL should contain the path prefix"

    # Check that it didn't just convert the objects to string representations
    # that include the Python object syntax
    assert "status" in called_url, "The URL should contain filter field names"
    assert "active" in called_url, "The URL should contain filter values"
    assert "}" not in called_url, "The URL should not contain Python object syntax"
    assert "{" not in called_url, "The URL should not contain Python object syntax"
