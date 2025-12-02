"""Unit tests for FastMCPOpenAPI server."""

import httpx
import pytest

from fastmcp.client import Client
from fastmcp.server.openapi import FastMCPOpenAPI


class TestFastMCPOpenAPIBasicFunctionality:
    """Test basic FastMCPOpenAPI server functionality."""

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

    def test_server_initialization(self, simple_openapi_spec):
        """Test server initialization with OpenAPI spec."""
        client = httpx.AsyncClient(base_url="https://api.example.com")

        server = FastMCPOpenAPI(
            openapi_spec=simple_openapi_spec, client=client, name="Test Server"
        )

        assert server.name == "Test Server"
        # Should have initialized RequestDirector successfully
        assert hasattr(server, "_director")
        assert hasattr(server, "_spec")

    def test_server_initialization_with_custom_name(self, simple_openapi_spec):
        """Test server initialization with custom name."""
        client = httpx.AsyncClient(base_url="https://api.example.com")

        server = FastMCPOpenAPI(openapi_spec=simple_openapi_spec, client=client)

        # Should use default name
        assert server.name == "OpenAPI FastMCP"

    async def test_server_creates_tools_from_spec(self, simple_openapi_spec):
        """Test that server creates tools from OpenAPI spec."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = FastMCPOpenAPI(
                openapi_spec=simple_openapi_spec, client=client, name="Test Server"
            )

            # Test with in-memory client
            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Should have created tools for both operations
                assert len(tools) == 2

                tool_names = {tool.name for tool in tools}
                assert "get_user" in tool_names
                assert "create_user" in tool_names

    async def test_server_tool_execution_fallback_to_http(self, simple_openapi_spec):
        """Test tool execution falls back to HTTP when callables aren't available."""
        # Use a mock client that will be used for HTTP fallback
        mock_client = httpx.AsyncClient()

        server = FastMCPOpenAPI(
            openapi_spec=simple_openapi_spec, client=mock_client, name="Test Server"
        )

        # With new architecture, tools are always created using RequestDirector

        async with Client(server) as mcp_client:
            tools = await mcp_client.list_tools()

            # Should still have tools even without callables
            assert len(tools) == 2

            # Tools should be OpenAPITool instances using RequestDirector
            # We'll just verify they exist and are callable
            get_user_tool = next(tool for tool in tools if tool.name == "get_user")
            assert get_user_tool is not None
            assert get_user_tool.description is not None

    def test_server_request_director_initialization(self, simple_openapi_spec):
        """Test that server initializes RequestDirector successfully."""
        client = httpx.AsyncClient(base_url="https://api.example.com")

        # This should not raise an exception
        server = FastMCPOpenAPI(
            openapi_spec=simple_openapi_spec, client=client, name="Test Server"
        )

        # Server should be created successfully
        assert server is not None
        assert server.name == "Test Server"
        # RequestDirector and Spec should be initialized
        assert hasattr(server, "_director")
        assert hasattr(server, "_spec")

    def test_server_with_timeout(self, simple_openapi_spec):
        """Test server initialization with timeout setting."""
        client = httpx.AsyncClient(base_url="https://api.example.com")

        server = FastMCPOpenAPI(
            openapi_spec=simple_openapi_spec,
            client=client,
            name="Test Server",
            timeout=30.0,
        )

        assert server._timeout == 30.0

    def test_server_with_empty_spec(self):
        """Test server with minimal OpenAPI spec."""
        minimal_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Empty API", "version": "1.0.0"},
            "paths": {},
        }

        client = httpx.AsyncClient(base_url="https://api.example.com")

        server = FastMCPOpenAPI(
            openapi_spec=minimal_spec, client=client, name="Empty Server"
        )

        assert server.name == "Empty Server"
        # Should handle empty paths gracefully
        assert hasattr(server, "_director")
        assert hasattr(server, "_spec")

    async def test_clean_schema_output_no_unused_defs(self):
        """Test that unused schema definitions are removed from tool schemas."""
        # Create a spec with unused HTTPValidationError-like definitions
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
                    # This should be removed since it's not referenced
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
            server = FastMCPOpenAPI(
                openapi_spec=spec_with_unused_defs, client=client, name="Test Server"
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                assert len(tools) == 1  # Only the POST operation
                tool = tools[0]

                # Verify tool has clean schemas without unused $defs
                assert tool.name == "create_user"

                # Input schema should not have $defs since no references are used
                expected_input_schema = {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "title": "Name"},
                        "active": {"type": "boolean", "title": "Active"},
                    },
                    "required": ["name", "active"],
                }
                assert tool.inputSchema == expected_input_schema

                # Output schema should not have $defs since no references are used
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
