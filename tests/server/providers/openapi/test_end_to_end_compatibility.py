"""End-to-end tests for OpenAPIProvider implementation."""

import json

import httpx2
import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers.openapi import OpenAPIProvider


def create_openapi_server(
    openapi_spec: dict,
    client,
    name: str = "OpenAPI Server",
) -> FastMCP:
    """Helper to create a FastMCP server with OpenAPIProvider."""
    provider = OpenAPIProvider(openapi_spec=openapi_spec, client=client)
    mcp = FastMCP(name)
    mcp.add_provider(provider)
    return mcp


class TestEndToEndFunctionality:
    """Test end-to-end functionality of OpenAPIProvider."""

    @pytest.fixture
    def simple_spec(self):
        """Simple OpenAPI spec for testing."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "get_user",
                        "summary": "Get user by ID",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            },
                            {
                                "name": "include_details",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "boolean"},
                            },
                        ],
                        "responses": {"200": {"description": "User found"}},
                    }
                }
            },
        }

    @pytest.fixture
    def collision_spec(self):
        """OpenAPI spec with parameter collisions."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Collision API", "version": "1.0.0"},
            "paths": {
                "/users/{id}": {
                    "put": {
                        "operationId": "update_user",
                        "summary": "Update user",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                        "required": ["name"],
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "User updated"}},
                    }
                }
            },
        }

    async def test_tool_schema_generation(self, simple_spec):
        """Test that tools have correct input schemas."""
        async with httpx2.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=simple_spec,
                client=client,
                name="Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Should have one tool
                assert len(tools) == 1

                tool = tools[0]
                assert tool.name == "get_user"
                assert tool.description

                # Check schema structure
                schema = tool.input_schema
                assert schema["type"] == "object"

                properties = schema.get("properties", {})
                assert "id" in properties
                assert "include_details" in properties

                # Required fields should include path parameter
                required = schema.get("required", [])
                assert "id" in required

    async def test_collision_handling(self, collision_spec):
        """Test that parameter collision handling works correctly."""
        async with httpx2.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=collision_spec,
                client=client,
                name="Collision Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Should have one tool
                assert len(tools) == 1

                tool = tools[0]
                schema = tool.input_schema

                # Both should have collision-resolved parameters
                properties = schema.get("properties", {})

                # Should have: id__path (path param), id (body param), name (body param)
                expected_props = {"id__path", "id", "name"}
                assert set(properties.keys()) == expected_props

                # Required should include path param and required body params
                required = set(schema.get("required", []))
                assert "id__path" in required
                assert "name" in required

                # Path parameter should have integer type
                assert properties["id__path"]["type"] == "integer"

                # Body parameters should match
                assert properties["id"]["type"] == "integer"
                assert properties["name"]["type"] == "string"

    async def test_tool_execution_parameter_mapping(self, collision_spec):
        """Colliding path and body parameters reach the right parts of the request."""
        requests: list[httpx2.Request] = []

        def capture(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(200, json={"ok": True})

        async with httpx2.AsyncClient(
            base_url="https://api.example.com", transport=httpx2.MockTransport(capture)
        ) as client:
            server = create_openapi_server(
                openapi_spec=collision_spec,
                client=client,
                name="Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()
                await mcp_client.call_tool(
                    tools[0].name,
                    {"id__path": 123, "id": 456, "name": "John Doe"},
                )

        assert len(requests) == 1
        assert requests[0].method == "PUT"
        assert requests[0].url.path == "/users/123"
        assert json.loads(requests[0].content) == {"id": 456, "name": "John Doe"}

    @pytest.mark.parametrize(
        "args,expected_params",
        [
            ({"id": 123}, []),
            ({"id": 123, "include_details": True}, [("include_details", "true")]),
        ],
    )
    async def test_optional_parameter_handling(
        self, simple_spec, args, expected_params
    ):
        """Optional query parameters are sent only when provided."""
        requests: list[httpx2.Request] = []

        def capture(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(200, json={"ok": True})

        async with httpx2.AsyncClient(
            base_url="https://api.example.com", transport=httpx2.MockTransport(capture)
        ) as client:
            server = create_openapi_server(
                openapi_spec=simple_spec,
                client=client,
                name="Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()
                await mcp_client.call_tool(tools[0].name, args)

        assert len(requests) == 1
        assert requests[0].url.path == "/users/123"
        assert requests[0].url.params.multi_items() == expected_params
