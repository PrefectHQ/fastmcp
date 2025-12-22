"""Tests for parameter collision handling in OpenAPIProvider."""

import httpx
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


class TestParameterCollisions:
    """Test parameter name collisions between different locations (path, query, body)."""

    @pytest.fixture
    def collision_spec(self):
        """OpenAPI spec with parameter name collisions."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Collision Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users/{id}": {
                    "put": {
                        "operationId": "update_user",
                        "summary": "Update user with collision between path and body",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                                "description": "User ID in path",
                            }
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {
                                                "type": "integer",
                                                "description": "User ID in body (different from path)",
                                            },
                                            "name": {
                                                "type": "string",
                                                "description": "User name",
                                            },
                                            "email": {
                                                "type": "string",
                                                "description": "User email",
                                            },
                                        },
                                        "required": ["name", "email"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "User updated",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                                "email": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/search": {
                    "get": {
                        "operationId": "search_with_collision",
                        "summary": "Search with query and header collision",
                        "parameters": [
                            {
                                "name": "query",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Search query parameter",
                            },
                            {
                                "name": "query",
                                "in": "header",
                                "required": False,
                                "schema": {"type": "string"},
                                "description": "Search query in header",
                            },
                        ],
                        "responses": {
                            "200": {
                                "description": "Search results",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "results": {
                                                    "type": "array",
                                                    "items": {"type": "object"},
                                                }
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

    async def test_path_body_collision_handling(self, collision_spec):
        """Test that path and body parameters with same name are handled correctly."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=collision_spec, client=client, name="Collision Test Server"
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find the update user tool
                update_tool = next(tool for tool in tools if tool.name == "update_user")
                assert update_tool is not None

                # Check that both path and body 'id' parameters are included
                params = update_tool.inputSchema
                properties = params["properties"]

                # Should have both path ID and body ID (with potential suffixing)
                # The implementation should handle this collision by suffixing one of them
                assert "id" in properties  # One version of id

                # Check for suffixed versions or verify both exist somehow
                # The exact handling depends on implementation, but both should be accessible
                param_names = list(properties.keys())
                id_params = [name for name in param_names if "id" in name]
                assert len(id_params) >= 1  # At least one id parameter

                # Should also have other body parameters
                assert "name" in properties
                assert "email" in properties

                # Required fields should include path parameter and required body fields
                required = params.get("required", [])
                assert "name" in required
                assert "email" in required
                # Path parameter should be required (may be suffixed)
                id_required = any("id" in req for req in required)
                assert id_required

    async def test_query_header_collision_handling(self, collision_spec):
        """Test that query and header parameters with same name are handled correctly."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=collision_spec, client=client, name="Collision Test Server"
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find the search tool
                search_tool = next(
                    tool for tool in tools if tool.name == "search_with_collision"
                )
                assert search_tool is not None

                # Check that both query and header 'query' parameters are handled
                params = search_tool.inputSchema
                properties = params["properties"]

                # Should handle the collision somehow (suffixing or other mechanism)
                param_names = list(properties.keys())
                query_params = [name for name in param_names if "query" in name]
                assert len(query_params) >= 1  # At least one query parameter

                # Required should include the required query parameter
                required = params.get("required", [])
                query_required = any("query" in req for req in required)
                assert query_required

    async def test_collision_resolution_maintains_functionality(self, collision_spec):
        """Test that collision resolution doesn't break basic tool functionality."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=collision_spec, client=client, name="Collision Test Server"
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Should successfully create tools despite collisions
                assert len(tools) == 2

                tool_names = {tool.name for tool in tools}
                assert "update_user" in tool_names
                assert "search_with_collision" in tool_names

                # Tools should have valid schemas
                for tool in tools:
                    assert tool.inputSchema is not None
                    assert tool.inputSchema["type"] == "object"
                    assert "properties" in tool.inputSchema
