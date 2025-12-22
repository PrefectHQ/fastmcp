"""Unit tests for OpenAPIProvider."""

import httpx
import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers.openapi import OpenAPIProvider


class TestOpenAPIProviderBasicFunctionality:
    """Test basic OpenAPIProvider functionality."""

    @pytest.fixture
    def simple_openapi_spec(self):
        """Simple OpenAPI spec for testing."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
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
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "User retrieved successfully",
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
                "/users": {
                    "post": {
                        "operationId": "create_user",
                        "summary": "Create a new user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string"},
                                        },
                                        "required": ["name", "email"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "User created successfully",
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
            },
        }

    def test_provider_initialization(self, simple_openapi_spec):
        """Test provider initialization with OpenAPI spec."""
        client = httpx.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(openapi_spec=simple_openapi_spec, client=client)

        # Should have initialized RequestDirector successfully
        assert hasattr(provider, "_director")
        assert hasattr(provider, "_spec")

    def test_server_with_provider(self, simple_openapi_spec):
        """Test server initialization with OpenAPIProvider."""
        client = httpx.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(openapi_spec=simple_openapi_spec, client=client)

        mcp = FastMCP("Test Server")
        mcp.add_provider(provider)

        assert mcp.name == "Test Server"

    async def test_provider_creates_tools_from_spec(self, simple_openapi_spec):
        """Test that provider creates tools from OpenAPI spec."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(openapi_spec=simple_openapi_spec, client=client)

            mcp = FastMCP("Test Server")
            mcp.add_provider(provider)

            async with Client(mcp) as mcp_client:
                tools = await mcp_client.list_tools()

                # Should have created tools for both operations
                assert len(tools) == 2

                tool_names = {tool.name for tool in tools}
                assert "get_user" in tool_names
                assert "create_user" in tool_names

    async def test_provider_tool_execution(self, simple_openapi_spec):
        """Test tool execution uses RequestDirector."""
        mock_client = httpx.AsyncClient()
        provider = OpenAPIProvider(openapi_spec=simple_openapi_spec, client=mock_client)

        mcp = FastMCP("Test Server")
        mcp.add_provider(provider)

        async with Client(mcp) as mcp_client:
            tools = await mcp_client.list_tools()

            # Should have tools using RequestDirector
            assert len(tools) == 2

            get_user_tool = next(tool for tool in tools if tool.name == "get_user")
            assert get_user_tool is not None
            assert get_user_tool.description is not None

    def test_provider_with_timeout(self, simple_openapi_spec):
        """Test provider initialization with timeout setting."""
        client = httpx.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(
            openapi_spec=simple_openapi_spec,
            client=client,
            timeout=30.0,
        )

        assert provider._timeout == 30.0

    def test_provider_with_empty_spec(self):
        """Test provider with minimal OpenAPI spec."""
        minimal_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Empty API", "version": "1.0.0"},
            "paths": {},
        }

        client = httpx.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(openapi_spec=minimal_spec, client=client)

        # Should handle empty paths gracefully
        assert hasattr(provider, "_director")
        assert hasattr(provider, "_spec")

    async def test_clean_schema_output_no_unused_defs(self):
        """Test that unused schema definitions are removed from tool schemas."""
        spec_with_unused_defs = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "create_user",
                        "summary": "Create a new user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string", "title": "Name"},
                                            "active": {
                                                "type": "boolean",
                                                "title": "Active",
                                            },
                                        },
                                        "required": ["name", "active"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "User created successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {
                                                    "type": "integer",
                                                    "title": "Id",
                                                },
                                                "name": {
                                                    "type": "string",
                                                    "title": "Name",
                                                },
                                                "active": {
                                                    "type": "boolean",
                                                    "title": "Active",
                                                },
                                            },
                                            "required": ["id", "name", "active"],
                                            "title": "User",
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "HTTPValidationError": {
                        "properties": {
                            "detail": {
                                "items": {
                                    "$ref": "#/components/schemas/ValidationError"
                                },
                                "title": "Detail",
                                "type": "array",
                            }
                        },
                        "title": "HTTPValidationError",
                        "type": "object",
                    },
                    "ValidationError": {
                        "properties": {
                            "loc": {
                                "items": {
                                    "anyOf": [{"type": "string"}, {"type": "integer"}]
                                },
                                "title": "Location",
                                "type": "array",
                            },
                            "msg": {"title": "Message", "type": "string"},
                            "type": {"title": "Error Type", "type": "string"},
                        },
                        "required": ["loc", "msg", "type"],
                        "title": "ValidationError",
                        "type": "object",
                    },
                }
            },
        }

        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=spec_with_unused_defs, client=client
            )
            mcp = FastMCP("Test Server")
            mcp.add_provider(provider)

            async with Client(mcp) as mcp_client:
                tools = await mcp_client.list_tools()

                assert len(tools) == 1
                tool = tools[0]

                assert tool.name == "create_user"

                expected_input_schema = {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "title": "Name"},
                        "active": {"type": "boolean", "title": "Active"},
                    },
                    "required": ["name", "active"],
                }
                assert tool.inputSchema == expected_input_schema

                expected_output_schema = {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "title": "Id"},
                        "name": {"type": "string", "title": "Name"},
                        "active": {"type": "boolean", "title": "Active"},
                    },
                    "required": ["id", "name", "active"],
                    "title": "User",
                }
                assert tool.outputSchema == expected_output_schema
