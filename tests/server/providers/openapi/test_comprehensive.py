"""Comprehensive tests for OpenAPIProvider implementation."""

import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from httpx import Response

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


class TestOpenAPIComprehensive:
    """Comprehensive tests ensuring no functionality is lost."""

    @pytest.fixture
    def comprehensive_openapi_spec(self):
        """Comprehensive OpenAPI spec covering all major features."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Comprehensive API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                            "email": {"type": "string", "format": "email"},
                            "age": {"type": "integer", "minimum": 0},
                        },
                        "required": ["name", "email"],
                    },
                    "Error": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "message": {"type": "string"},
                        },
                    },
                },
                "parameters": {
                    "UserId": {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "User identifier",
                    }
                },
            },
            "paths": {
                # Basic CRUD operations
                "/users": {
                    "get": {
                        "operationId": "list_users",
                        "summary": "List all users",
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "schema": {
                                    "type": "integer",
                                    "default": 10,
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                                "description": "Number of users to return",
                            },
                            {
                                "name": "offset",
                                "in": "query",
                                "schema": {
                                    "type": "integer",
                                    "default": 0,
                                    "minimum": 0,
                                },
                                "description": "Number of users to skip",
                            },
                            {
                                "name": "sort",
                                "in": "query",
                                "schema": {
                                    "type": "string",
                                    "enum": ["name", "email", "age"],
                                },
                                "description": "Sort field",
                            },
                        ],
                        "responses": {
                            "200": {
                                "description": "List of users",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/User"
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    },
                    "post": {
                        "operationId": "create_user",
                        "summary": "Create a new user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/User"}
                                }
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "User created",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/User"}
                                    }
                                },
                            },
                            "400": {
                                "description": "Invalid input",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Error"}
                                    }
                                },
                            },
                        },
                    },
                },
                "/users/{id}": {
                    "parameters": [{"$ref": "#/components/parameters/UserId"}],
                    "get": {
                        "operationId": "get_user",
                        "summary": "Get user by ID",
                        "responses": {
                            "200": {
                                "description": "User details",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/User"}
                                    }
                                },
                            },
                            "404": {
                                "description": "User not found",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Error"}
                                    }
                                },
                            },
                        },
                    },
                    "put": {
                        "operationId": "update_user",
                        "summary": "Update user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/User"}
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "User updated",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/User"}
                                    }
                                },
                            },
                        },
                    },
                    "delete": {
                        "operationId": "delete_user",
                        "summary": "Delete user",
                        "responses": {
                            "204": {"description": "User deleted"},
                            "404": {
                                "description": "User not found",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Error"}
                                    }
                                },
                            },
                        },
                    },
                },
                # Complex parameter scenarios
                "/search": {
                    "get": {
                        "operationId": "search_users",
                        "summary": "Search users with complex filters",
                        "parameters": [
                            {
                                "name": "q",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Search query",
                            },
                            {
                                "name": "filter",
                                "in": "query",
                                "style": "deepObject",
                                "explode": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "age": {
                                            "type": "object",
                                            "properties": {
                                                "min": {"type": "integer"},
                                                "max": {"type": "integer"},
                                            },
                                        },
                                        "name": {"type": "string"},
                                        "active": {"type": "boolean"},
                                    },
                                },
                            },
                            {
                                "name": "X-Request-ID",
                                "in": "header",
                                "schema": {"type": "string"},
                                "description": "Request identifier for tracing",
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
                                                    "items": {
                                                        "$ref": "#/components/schemas/User"
                                                    },
                                                },
                                                "total": {"type": "integer"},
                                                "page": {"type": "integer"},
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                # Parameter collision scenario
                "/collision/{id}": {
                    "patch": {
                        "operationId": "collision_test",
                        "summary": "Test parameter collision handling",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Resource ID",
                            },
                            {
                                "name": "version",
                                "in": "query",
                                "schema": {"type": "integer", "default": 1},
                            },
                            {
                                "name": "version",
                                "in": "header",
                                "schema": {"type": "string"},
                            },
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
                                                "description": "Internal ID",
                                            },
                                            "version": {
                                                "type": "string",
                                                "description": "Data version",
                                            },
                                            "data": {"type": "object"},
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "Updated"}},
                    }
                },
            },
        }

    @pytest.fixture
    def openapi_31_spec(self):
        """OpenAPI 3.1 spec to test compatibility."""
        return {
            "openapi": "3.1.0",
            "info": {"title": "OpenAPI 3.1 Test", "version": "1.0.0"},
            "paths": {
                "/items/{id}": {
                    "get": {
                        "operationId": "get_item_31",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Item details",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
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

    async def test_comprehensive_server_initialization(
        self, comprehensive_openapi_spec
    ):
        """Test server initialization with comprehensive spec."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=comprehensive_openapi_spec,
                client=client,
            )
            server = FastMCP("Comprehensive Test Server")
            server.add_provider(provider)

            # Should initialize successfully
            assert server.name == "Comprehensive Test Server"
            assert hasattr(provider, "_director")
            assert hasattr(provider, "_spec")

            # Test with in-memory client
            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Should have created tools for all operations
                tool_names = {tool.name for tool in tools}
                expected_operations = {
                    "list_users",
                    "create_user",
                    "get_user",
                    "update_user",
                    "delete_user",
                    "search_users",
                    "collision_test",
                }

                assert tool_names == expected_operations

    async def test_openapi_31_compatibility(self, openapi_31_spec):
        """Test that OpenAPI 3.1 specs work correctly."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=openapi_31_spec,
                client=client,
                name="OpenAPI 3.1 Test",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                assert len(tools) == 1
                tool = tools[0]
                assert tool.name == "get_item_31"

    async def test_parameter_collision_handling(self, comprehensive_openapi_spec):
        """Test that parameter collisions are handled correctly."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=comprehensive_openapi_spec,
                client=client,
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                collision_tool = next(
                    tool for tool in tools if tool.name == "collision_test"
                )
                schema = collision_tool.inputSchema
                properties = schema["properties"]

                # Should have unique parameter names for colliding parameters
                param_names = list(properties.keys())

                # Should have some form of id parameters (path and body)
                id_params = [name for name in param_names if "id" in name]
                assert len(id_params) >= 2

                # Should have some form of version parameters (query, header, body)
                version_params = [name for name in param_names if "version" in name]
                assert len(version_params) >= 3

                # Should have other parameters
                assert "data" in param_names

    async def test_deep_object_parameters(self, comprehensive_openapi_spec):
        """Test deepObject parameter handling."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=comprehensive_openapi_spec,
                client=client,
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                search_tool = next(
                    tool for tool in tools if tool.name == "search_users"
                )
                schema = search_tool.inputSchema
                properties = schema["properties"]

                # Should have flattened deepObject parameters
                # The exact flattening depends on implementation
                assert "q" in properties  # Regular query parameter

                # Should have some form of filter parameters
                filter_params = [name for name in properties.keys() if "filter" in name]
                assert len(filter_params) > 0

    async def test_request_building_and_execution(self, comprehensive_openapi_spec):
        """Test that requests are built and executed correctly."""
        # Create a mock client that tracks requests
        mock_client = Mock(spec=httpx.AsyncClient)
        mock_client.base_url = "https://api.example.com"
        mock_client.headers = None

        # Mock successful response
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 123,
            "name": "Test User",
            "email": "test@example.com",
        }
        mock_response.text = json.dumps(
            {"id": 123, "name": "Test User", "email": "test@example.com"}
        )
        mock_response.raise_for_status = Mock()

        mock_client.send = AsyncMock(return_value=mock_response)

        server = create_openapi_server(
            openapi_spec=comprehensive_openapi_spec,
            client=mock_client,
        )

        async with Client(server) as mcp_client:
            # Test GET request with path parameter
            await mcp_client.call_tool("get_user", {"id": 123})

            # Should have made a request
            mock_client.send.assert_called_once()
            request = mock_client.send.call_args[0][0]

            # Verify request details
            assert request.method == "GET"
            assert "123" in str(request.url)
            assert "users/123" in str(request.url)

    async def test_request_uses_localhost_fallback_when_no_base_url(
        self, comprehensive_openapi_spec
    ):
        """Test that tool uses localhost fallback when client has no base_url."""
        mock_client = Mock(spec=httpx.AsyncClient)
        mock_client.base_url = httpx.URL("")  # Empty URL, same as httpx default
        mock_client.headers = None

        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 123,
            "name": "Test User",
            "email": "test@example.com",
        }
        mock_response.raise_for_status = Mock()

        mock_client.send = AsyncMock(return_value=mock_response)

        server = create_openapi_server(
            openapi_spec=comprehensive_openapi_spec,
            client=mock_client,
        )

        async with Client(server) as mcp_client:
            await mcp_client.call_tool("get_user", {"id": 123})

        # Verify request was made to localhost fallback
        mock_client.send.assert_called_once()
        request = mock_client.send.call_args[0][0]
        assert str(request.url).startswith("http://localhost")

    async def test_complex_request_with_body_and_parameters(
        self, comprehensive_openapi_spec
    ):
        """Test complex request with both parameters and body."""
        mock_client = Mock(spec=httpx.AsyncClient)
        mock_client.base_url = "https://api.example.com"
        mock_client.headers = None

        mock_response = Mock(spec=Response)
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": 456,
            "name": "New User",
            "email": "new@example.com",
        }
        mock_response.raise_for_status = Mock()

        mock_client.send = AsyncMock(return_value=mock_response)

        server = create_openapi_server(
            openapi_spec=comprehensive_openapi_spec,
            client=mock_client,
        )

        async with Client(server) as mcp_client:
            # Test POST request with body
            await mcp_client.call_tool(
                "create_user",
                {
                    "name": "New User",
                    "email": "new@example.com",
                    "age": 25,
                },
            )

            # Should have made a request
            mock_client.send.assert_called_once()
            request = mock_client.send.call_args[0][0]

            # Verify request details
            assert request.method == "POST"
            assert "users" in str(request.url)

            # Should have JSON body
            assert request.content is not None
            body_data = json.loads(request.content)
            assert body_data["name"] == "New User"
            assert body_data["email"] == "new@example.com"
            assert body_data["age"] == 25

    async def test_query_parameters(self, comprehensive_openapi_spec):
        """Test query parameter handling."""
        mock_client = Mock(spec=httpx.AsyncClient)
        mock_client.base_url = "https://api.example.com"
        mock_client.headers = None

        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()

        mock_client.send = AsyncMock(return_value=mock_response)

        server = create_openapi_server(
            openapi_spec=comprehensive_openapi_spec,
            client=mock_client,
        )

        async with Client(server) as mcp_client:
            # Test GET request with query parameters
            await mcp_client.call_tool(
                "list_users",
                {
                    "limit": 20,
                    "offset": 10,
                    "sort": "name",
                },
            )

            mock_client.send.assert_called_once()
            request = mock_client.send.call_args[0][0]

            # Verify query parameters in URL
            url_str = str(request.url)
            assert "limit=20" in url_str
            assert "offset=10" in url_str
            assert "sort=name" in url_str

    async def test_error_handling(self, comprehensive_openapi_spec):
        """Test error handling for HTTP errors."""
        mock_client = Mock(spec=httpx.AsyncClient)
        mock_client.base_url = "https://api.example.com"
        mock_client.headers = None

        # Mock HTTP error response
        mock_response = Mock(spec=Response)
        mock_response.status_code = 404
        mock_response.reason_phrase = "Not Found"
        mock_response.json.return_value = {"code": 404, "message": "User not found"}
        mock_response.text = json.dumps({"code": 404, "message": "User not found"})

        # Configure raise_for_status to raise HTTPStatusError
        def raise_for_status():
            raise httpx.HTTPStatusError(
                "404 Not Found", request=Mock(), response=mock_response
            )

        mock_response.raise_for_status = raise_for_status
        mock_client.send = AsyncMock(return_value=mock_response)

        server = create_openapi_server(
            openapi_spec=comprehensive_openapi_spec,
            client=mock_client,
        )

        async with Client(server) as mcp_client:
            # Should handle HTTP errors gracefully
            with pytest.raises(Exception) as exc_info:
                await mcp_client.call_tool("get_user", {"id": 999})

            # Error should be wrapped appropriately
            error_message = str(exc_info.value)
            assert "404" in error_message

    async def test_schema_refs_resolution(self, comprehensive_openapi_spec):
        """Test that schema references are resolved correctly."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=comprehensive_openapi_spec,
                client=client,
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find create_user tool which uses schema refs
                create_tool = next(tool for tool in tools if tool.name == "create_user")
                schema = create_tool.inputSchema
                properties = schema["properties"]

                # Should have resolved User schema properties
                assert "name" in properties
                assert "email" in properties
                # May also have id and age depending on implementation

    async def test_optional_vs_required_parameters(self, comprehensive_openapi_spec):
        """Test handling of optional vs required parameters."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=comprehensive_openapi_spec,
                client=client,
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Check list_users tool - has optional query parameters
                list_tool = next(tool for tool in tools if tool.name == "list_users")
                schema = list_tool.inputSchema
                # Query parameters should be optional
                # (may not appear in required list)
                # This test just ensures the schema is well-formed
                assert "properties" in schema

                # Check search_users tool - has required query parameter
                search_tool = next(
                    tool for tool in tools if tool.name == "search_users"
                )
                search_schema = search_tool.inputSchema
                # Should have some required parameters
                assert len(search_schema["properties"]) > 0

    async def test_server_performance_no_latency(self, comprehensive_openapi_spec):
        """Test that server initialization is fast (no code generation latency)."""
        import time

        # Time the provider creation
        start_time = time.time()

        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=comprehensive_openapi_spec,
                client=client,
            )

        end_time = time.time()

        # Should be very fast (no code generation)
        initialization_time = end_time - start_time
        assert initialization_time < 0.1  # Should be under 100ms

        # Verify provider was created correctly
        assert provider is not None
        assert hasattr(provider, "_director")
        assert hasattr(provider, "_spec")
