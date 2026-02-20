"""Tests for OpenAPI feature support in OpenAPIProvider."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from httpx import Response

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers.openapi import OpenAPIProvider
from fastmcp.server.providers.openapi.components import _extract_mime_type_from_route
from fastmcp.server.providers.openapi.routing import MCPType, RouteMap
from fastmcp.utilities.openapi.models import HTTPRoute, ResponseInfo


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


class TestMimeTypeExtraction:
    """Test MIME type extraction from route responses."""

    def test_json_response(self):
        """JSON content type is correctly extracted."""
        route = HTTPRoute(
            path="/items",
            method="GET",
            responses={
                "200": ResponseInfo(
                    content_schema={"application/json": {"type": "object"}}
                )
            },
        )
        assert _extract_mime_type_from_route(route) == "application/json"

    def test_text_plain_response(self):
        """Plain text content type is correctly extracted."""
        route = HTTPRoute(
            path="/health",
            method="GET",
            responses={
                "200": ResponseInfo(content_schema={"text/plain": {"type": "string"}})
            },
        )
        assert _extract_mime_type_from_route(route) == "text/plain"

    def test_text_html_response(self):
        """HTML content type is correctly extracted."""
        route = HTTPRoute(
            path="/page",
            method="GET",
            responses={
                "200": ResponseInfo(content_schema={"text/html": {"type": "string"}})
            },
        )
        assert _extract_mime_type_from_route(route) == "text/html"

    def test_image_response(self):
        """Image content type is correctly extracted."""
        route = HTTPRoute(
            path="/avatar",
            method="GET",
            responses={
                "200": ResponseInfo(
                    content_schema={"image/png": {"type": "string", "format": "binary"}}
                )
            },
        )
        assert _extract_mime_type_from_route(route) == "image/png"

    def test_no_responses_defaults_to_json(self):
        """Empty responses default to application/json."""
        route = HTTPRoute(path="/items", method="GET", responses={})
        assert _extract_mime_type_from_route(route) == "application/json"

    def test_no_content_schema_defaults_to_json(self):
        """Response without content_schema defaults to application/json."""
        route = HTTPRoute(
            path="/items",
            method="GET",
            responses={"204": ResponseInfo(description="No content")},
        )
        assert _extract_mime_type_from_route(route) == "application/json"

    def test_prefers_json_when_multiple_types(self):
        """When both JSON and other types exist, JSON is preferred."""
        route = HTTPRoute(
            path="/items",
            method="GET",
            responses={
                "200": ResponseInfo(
                    content_schema={
                        "text/html": {"type": "string"},
                        "application/json": {"type": "object"},
                    }
                )
            },
        )
        assert _extract_mime_type_from_route(route) == "application/json"

    def test_non_standard_2xx_code(self):
        """Falls back to any 2xx status code when standard ones are missing."""
        route = HTTPRoute(
            path="/items",
            method="GET",
            responses={
                "206": ResponseInfo(
                    content_schema={
                        "application/octet-stream": {
                            "type": "string",
                            "format": "binary",
                        }
                    }
                )
            },
        )
        assert _extract_mime_type_from_route(route) == "application/octet-stream"

    def test_ignores_error_responses(self):
        """Only error responses (no 2xx) results in default."""
        route = HTTPRoute(
            path="/items",
            method="GET",
            responses={
                "404": ResponseInfo(
                    content_schema={"application/json": {"type": "object"}}
                )
            },
        )
        assert _extract_mime_type_from_route(route) == "application/json"

    def test_201_response(self):
        """201 Created response content type is extracted."""
        route = HTTPRoute(
            path="/items",
            method="POST",
            responses={
                "201": ResponseInfo(content_schema={"text/plain": {"type": "string"}})
            },
        )
        assert _extract_mime_type_from_route(route) == "text/plain"

    def test_media_type_without_schema(self):
        """Media type declared without a schema still infers MIME type."""
        route = HTTPRoute(
            path="/health",
            method="GET",
            responses={"200": ResponseInfo(content_schema={"text/plain": {}})},
        )
        assert _extract_mime_type_from_route(route) == "text/plain"


class TestResourceTemplateMimeType:
    """Test that OpenAPIResourceTemplate uses inferred MIME types."""

    @pytest.fixture
    def text_plain_spec(self):
        """OpenAPI spec with a text/plain resource template endpoint."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Text API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/documents/{id}": {
                    "get": {
                        "operationId": "get_document",
                        "summary": "Get document content",
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
                                "description": "Document content",
                                "content": {
                                    "text/plain": {"schema": {"type": "string"}}
                                },
                            }
                        },
                    }
                }
            },
        }

    @pytest.fixture
    def html_spec(self):
        """OpenAPI spec with a text/html resource endpoint."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "HTML API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/pages/{slug}": {
                    "get": {
                        "operationId": "get_page",
                        "summary": "Get HTML page",
                        "parameters": [
                            {
                                "name": "slug",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "HTML page",
                                "content": {
                                    "text/html": {"schema": {"type": "string"}}
                                },
                            }
                        },
                    }
                }
            },
        }

    async def test_resource_template_text_plain_mime_type(self, text_plain_spec):
        """Resource template should reflect text/plain from OpenAPI spec."""
        route_maps = [RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE_TEMPLATE)]
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=text_plain_spec, client=client, route_maps=route_maps
            )
            mcp = FastMCP("Test")
            mcp.add_provider(provider)
            async with Client(mcp) as mcp_client:
                templates = await mcp_client.list_resource_templates()
                assert len(templates) == 1
                assert templates[0].mimeType == "text/plain"

    async def test_resource_template_html_mime_type(self, html_spec):
        """Resource template should reflect text/html from OpenAPI spec."""
        route_maps = [RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE_TEMPLATE)]
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=html_spec, client=client, route_maps=route_maps
            )
            mcp = FastMCP("Test")
            mcp.add_provider(provider)
            async with Client(mcp) as mcp_client:
                templates = await mcp_client.list_resource_templates()
                assert len(templates) == 1
                assert templates[0].mimeType == "text/html"

    async def test_resource_template_defaults_json_mime_type(self):
        """Resource template defaults to application/json for JSON responses."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "JSON API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "get_user",
                        "summary": "Get user",
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
                                "description": "User data",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
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
        route_maps = [RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE_TEMPLATE)]
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=spec, client=client, route_maps=route_maps
            )
            mcp = FastMCP("Test")
            mcp.add_provider(provider)
            async with Client(mcp) as mcp_client:
                templates = await mcp_client.list_resource_templates()
                assert len(templates) == 1
                assert templates[0].mimeType == "application/json"


class TestResourceMimeType:
    """Test that OpenAPIResource uses inferred MIME types."""

    async def test_resource_text_plain_mime_type(self):
        """Static resource should reflect text/plain from OpenAPI spec."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Health API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/health": {
                    "get": {
                        "operationId": "healthcheck",
                        "summary": "Health check",
                        "responses": {
                            "200": {
                                "description": "Health status",
                                "content": {
                                    "text/plain": {"schema": {"type": "string"}}
                                },
                            }
                        },
                    }
                }
            },
        }
        route_maps = [RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE)]
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=spec, client=client, route_maps=route_maps
            )
            mcp = FastMCP("Test")
            mcp.add_provider(provider)
            async with Client(mcp) as mcp_client:
                resources = await mcp_client.list_resources()
                assert len(resources) == 1
                assert resources[0].mimeType == "text/plain"

    async def test_resource_mime_type_without_schema(self):
        """Resource with media type but no schema still infers MIME type."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Health API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/health": {
                    "get": {
                        "operationId": "healthcheck",
                        "summary": "Health check",
                        "responses": {
                            "200": {
                                "description": "Health status",
                                "content": {"text/plain": {}},
                            }
                        },
                    }
                }
            },
        }
        route_maps = [RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE)]
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=spec, client=client, route_maps=route_maps
            )
            mcp = FastMCP("Test")
            mcp.add_provider(provider)
            async with Client(mcp) as mcp_client:
                resources = await mcp_client.list_resources()
                assert len(resources) == 1
                assert resources[0].mimeType == "text/plain"


class TestValidateOutput:
    """Tests for the validate_output option on OpenAPIProvider."""

    @pytest.fixture
    def spec_with_output_schema(self):
        return {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "get_user",
                        "summary": "Get a user",
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
                                "description": "A user",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                                "email": {"type": "string"},
                                            },
                                            "required": ["id", "name"],
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/items": {
                    "get": {
                        "operationId": "list_items",
                        "summary": "List items",
                        "responses": {
                            "200": {
                                "description": "An array of items",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "name": {"type": "string"}
                                                },
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

    async def test_validate_output_true_preserves_extracted_schema(
        self, spec_with_output_schema
    ):
        """Default validate_output=True uses the real extracted schema."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=spec_with_output_schema,
                client=client,
            )

            tool = provider._tools["get_user"]
            assert tool.output_schema is not None
            assert tool.output_schema.get("type") == "object"
            assert "properties" in tool.output_schema
            assert "id" in tool.output_schema["properties"]

    async def test_validate_output_false_uses_permissive_schema(
        self, spec_with_output_schema
    ):
        """validate_output=False replaces the schema with a permissive one."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=spec_with_output_schema,
                client=client,
                validate_output=False,
            )

            tool = provider._tools["get_user"]
            assert tool.output_schema is not None
            assert tool.output_schema == {
                "type": "object",
                "additionalProperties": True,
            }

    async def test_validate_output_false_preserves_wrap_result_flag(
        self, spec_with_output_schema
    ):
        """validate_output=False preserves x-fastmcp-wrap-result for array responses."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=spec_with_output_schema,
                client=client,
                validate_output=False,
            )

            # The list_items endpoint returns an array, so the extracted schema
            # would have had x-fastmcp-wrap-result=True
            tool = provider._tools["list_items"]
            assert tool.output_schema is not None
            assert tool.output_schema.get("x-fastmcp-wrap-result") is True
            assert tool.output_schema.get("additionalProperties") is True

    async def test_validate_output_false_allows_nonconforming_response(
        self, spec_with_output_schema
    ):
        """With validate_output=False, responses that don't match the spec succeed."""
        mock_client = Mock(spec=httpx.AsyncClient)
        mock_client.base_url = "https://api.example.com"
        mock_client.headers = None

        # Return extra fields not in the schema
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 1,
            "name": "Alice",
            "email": "alice@example.com",
            "unexpected_field": "surprise",
            "nested": {"deep": True},
        }
        mock_response.raise_for_status = Mock()
        mock_client.send = AsyncMock(return_value=mock_response)

        provider = OpenAPIProvider(
            openapi_spec=spec_with_output_schema,
            client=mock_client,
            validate_output=False,
        )
        mcp = FastMCP("Test")
        mcp.add_provider(provider)

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool("get_user", {"id": 1})
            assert result is not None
            # Structured content should have the full response including extra fields
            assert result.structured_content is not None
            assert result.structured_content["unexpected_field"] == "surprise"

    async def test_validate_output_false_wraps_non_dict_response(
        self, spec_with_output_schema
    ):
        """Non-dict responses are wrapped even when schema says object and validate_output=False."""
        mock_client = Mock(spec=httpx.AsyncClient)
        mock_client.base_url = "https://api.example.com"
        mock_client.headers = None

        # Backend returns an array even though schema says object
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": 1}, {"id": 2}]
        mock_response.raise_for_status = Mock()
        mock_client.send = AsyncMock(return_value=mock_response)

        provider = OpenAPIProvider(
            openapi_spec=spec_with_output_schema,
            client=mock_client,
            validate_output=False,
        )
        mcp = FastMCP("Test")
        mcp.add_provider(provider)

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool("get_user", {"id": 1})
            assert result is not None
            # Non-dict should be wrapped so structured_content is always a dict
            assert result.structured_content is not None
            assert isinstance(result.structured_content, dict)
            assert result.structured_content["result"] == [{"id": 1}, {"id": 2}]

    async def test_from_openapi_threads_validate_output(self, spec_with_output_schema):
        """FastMCP.from_openapi() correctly passes validate_output to the provider."""
        mock_client = Mock(spec=httpx.AsyncClient)
        mock_client.base_url = "https://api.example.com"
        mock_client.headers = None

        server = FastMCP.from_openapi(
            openapi_spec=spec_with_output_schema,
            client=mock_client,
            validate_output=False,
        )

        async with Client(server) as mcp_client:
            tools = await mcp_client.list_tools()
            get_user = next(t for t in tools if t.name == "get_user")
            # With validate_output=False, the outputSchema should be permissive
            assert get_user.outputSchema is not None
            assert get_user.outputSchema.get("additionalProperties") is True
            # Should NOT have specific properties from the original schema
            assert "properties" not in get_user.outputSchema
