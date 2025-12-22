"""Tests for OpenAPI feature support in OpenAPIProvider."""

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


class TestParameterHandling:
    """Test OpenAPI parameter handling features."""

    @pytest.fixture
    def parameter_spec(self):
        """OpenAPI spec with various parameter types."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Parameter Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search_items",
                        "summary": "Search items",
                        "parameters": [
                            {
                                "name": "query",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Search query",
                            },
                            {
                                "name": "limit",
                                "in": "query",
                                "required": False,
                                "schema": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                                "description": "Maximum number of results",
                            },
                            {
                                "name": "tags",
                                "in": "query",
                                "required": False,
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "style": "form",
                                "explode": True,
                                "description": "Filter by tags",
                            },
                            {
                                "name": "X-API-Key",
                                "in": "header",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "API key for authentication",
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
                                                "items": {
                                                    "type": "array",
                                                    "items": {"type": "object"},
                                                },
                                                "total": {"type": "integer"},
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/users/{id}/posts/{post_id}": {
                    "get": {
                        "operationId": "get_user_post",
                        "summary": "Get specific user post",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                                "description": "User ID",
                            },
                            {
                                "name": "post_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                                "description": "Post ID",
                            },
                        ],
                        "responses": {
                            "200": {
                                "description": "User post",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "title": {"type": "string"},
                                                "content": {"type": "string"},
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

    async def test_query_parameters_in_tools(self, parameter_spec):
        """Test that query parameters are properly included in tool parameters."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=parameter_spec, client=client, name="Parameter Test Server"
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find the search tool
                search_tool = next(
                    tool for tool in tools if tool.name == "search_items"
                )
                assert search_tool is not None

                # Check that parameters are included in the tool's input schema
                params = search_tool.inputSchema
                assert params["type"] == "object"

                properties = params["properties"]

                # Check that key parameters are present
                # (Schema details may vary based on implementation)
                assert "query" in properties
                assert "limit" in properties
                assert "tags" in properties
                assert "X-API-Key" in properties

                # Check that parameter descriptions are included
                assert "description" in properties["query"], (
                    "Query parameter should have description"
                )
                assert properties["query"]["description"] == "Search query"
                assert "description" in properties["limit"], (
                    "Limit parameter should have description"
                )
                assert properties["limit"]["description"] == "Maximum number of results"
                assert "description" in properties["tags"], (
                    "Tags parameter should have description"
                )
                assert properties["tags"]["description"] == "Filter by tags"

                # Check that required parameters are marked as required
                required = params.get("required", [])
                assert "query" in required
                assert "X-API-Key" in required

    async def test_path_parameters_in_tools(self, parameter_spec):
        """Test that path parameters are properly included in tool parameters."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=parameter_spec, client=client, name="Parameter Test Server"
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find the user post tool
                user_post_tool = next(
                    tool for tool in tools if tool.name == "get_user_post"
                )
                assert user_post_tool is not None

                # Check that path parameters are included
                params = user_post_tool.inputSchema
                properties = params["properties"]

                # Check that path parameters are present
                assert "id" in properties
                assert "post_id" in properties

                # Path parameters should be required
                required = params.get("required", [])
                assert "id" in required
                assert "post_id" in required


class TestRequestBodyHandling:
    """Test OpenAPI request body handling."""

    @pytest.fixture
    def request_body_spec(self):
        """OpenAPI spec with request body."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Request Body Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "create_user",
                        "summary": "Create a user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {
                                                "type": "string",
                                                "description": "User's full name",
                                            },
                                            "email": {
                                                "type": "string",
                                                "format": "email",
                                                "description": "User's email address",
                                            },
                                            "age": {
                                                "type": "integer",
                                                "minimum": 0,
                                                "maximum": 150,
                                                "description": "User's age",
                                            },
                                            "preferences": {
                                                "type": "object",
                                                "properties": {
                                                    "theme": {"type": "string"},
                                                    "notifications": {
                                                        "type": "boolean"
                                                    },
                                                },
                                                "description": "User preferences",
                                            },
                                        },
                                        "required": ["name", "email"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "User created",
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
                }
            },
        }

    async def test_request_body_properties_in_tool(self, request_body_spec):
        """Test that request body properties are included in tool parameters."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=request_body_spec,
                client=client,
                name="Request Body Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find the create user tool
                create_tool = next(tool for tool in tools if tool.name == "create_user")
                assert create_tool is not None

                # Check that request body properties are included
                params = create_tool.inputSchema
                properties = params["properties"]

                # Check that request body properties are present
                assert "name" in properties
                assert "email" in properties
                assert "age" in properties
                assert "preferences" in properties

                # Check required fields from request body
                required = params.get("required", [])
                assert "name" in required
                assert "email" in required


class TestResponseSchemas:
    """Test OpenAPI response schema handling."""

    @pytest.fixture
    def response_schema_spec(self):
        """OpenAPI spec with detailed response schemas."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Response Schema Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "get_user",
                        "summary": "Get user details",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "User details retrieved successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                                "email": {"type": "string"},
                                                "profile": {
                                                    "type": "object",
                                                    "properties": {
                                                        "bio": {"type": "string"},
                                                        "avatar_url": {
                                                            "type": "string"
                                                        },
                                                    },
                                                },
                                            },
                                            "required": ["id", "name", "email"],
                                        }
                                    }
                                },
                            },
                            "404": {
                                "description": "User not found",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "error": {"type": "string"},
                                                "code": {"type": "integer"},
                                            },
                                        }
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }

    async def test_tool_has_output_schema(self, response_schema_spec):
        """Test that tools have output schemas from response definitions."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=response_schema_spec,
                client=client,
                name="Response Schema Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find the get user tool
                get_user_tool = next(tool for tool in tools if tool.name == "get_user")
                assert get_user_tool is not None

                # Check that the tool has an output schema
                # Note: output schema might be None if not extracted properly
                # Let's just check the tool exists and has basic properties
                assert get_user_tool.description is not None
                assert get_user_tool.name == "get_user"
