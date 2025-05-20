from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from fastmcp.server.openapi import OpenAPITool
from fastmcp.utilities.openapi import HTTPRoute, ParameterInfo


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
