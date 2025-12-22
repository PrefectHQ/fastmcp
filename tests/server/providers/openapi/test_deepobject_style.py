"""Tests for deepObject style parameter handling in OpenAPIProvider."""

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


class TestDeepObjectStyle:
    """Test deepObject style parameter handling in openapi_new."""

    @pytest.fixture
    def deepobject_spec(self):
        """OpenAPI spec with deepObject style parameters."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "DeepObject Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/surveys": {
                    "get": {
                        "operationId": "get_surveys",
                        "summary": "Get surveys with deepObject filtering",
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
                                            "description": "Target ID",
                                        },
                                        "type": {
                                            "type": "string",
                                            "enum": ["location", "organisation"],
                                            "description": "Target type",
                                        },
                                    },
                                    "required": ["type", "id"],
                                },
                                "description": "Target object for filtering",
                            },
                            {
                                "name": "filters",
                                "in": "query",
                                "required": False,
                                "style": "deepObject",
                                "explode": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string"},
                                        "category": {"type": "string"},
                                        "priority": {"type": "integer"},
                                    },
                                },
                                "description": "Additional filters",
                            },
                            {
                                "name": "compact",
                                "in": "query",
                                "required": False,
                                "style": "deepObject",
                                "explode": False,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "format": {"type": "string"},
                                        "level": {"type": "integer"},
                                    },
                                },
                                "description": "Compact format options (explode=false)",
                            },
                        ],
                        "responses": {
                            "200": {
                                "description": "Survey list",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "surveys": {
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
                "/users/{id}/preferences": {
                    "patch": {
                        "operationId": "update_preferences",
                        "summary": "Update user preferences with deepObject in body",
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
                                            "preferences": {
                                                "type": "object",
                                                "properties": {
                                                    "theme": {"type": "string"},
                                                    "notifications": {
                                                        "type": "object",
                                                        "properties": {
                                                            "email": {
                                                                "type": "boolean"
                                                            },
                                                            "push": {"type": "boolean"},
                                                            "frequency": {
                                                                "type": "string"
                                                            },
                                                        },
                                                    },
                                                    "privacy": {
                                                        "type": "object",
                                                        "properties": {
                                                            "profile_visible": {
                                                                "type": "boolean"
                                                            },
                                                            "analytics": {
                                                                "type": "boolean"
                                                            },
                                                        },
                                                    },
                                                },
                                                "description": "Nested preference object",
                                            }
                                        },
                                        "required": ["preferences"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "Preferences updated",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "success": {"type": "boolean"}
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

    async def test_deepobject_style_parsing_from_spec(self, deepobject_spec):
        """Test that deepObject style parameters are correctly parsed from OpenAPI spec."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=deepobject_spec,
                client=client,
                name="DeepObject Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find the surveys tool
                surveys_tool = next(
                    tool for tool in tools if tool.name == "get_surveys"
                )
                assert surveys_tool is not None

                # Check that deepObject parameters are included in schema
                params = surveys_tool.inputSchema
                properties = params["properties"]

                # Should have the deepObject parameters
                assert "target" in properties
                assert "filters" in properties
                assert "compact" in properties

                # Check that target parameter is present
                # (Exact schema structure may vary based on implementation)
                target_param = properties["target"]
                # Should have some structure, exact format may vary
                assert target_param is not None

    async def test_deepobject_explode_true_handling(self, deepobject_spec):
        """Test deepObject with explode=true parameter handling."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=deepobject_spec,
                client=client,
                name="DeepObject Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()
                surveys_tool = next(
                    tool for tool in tools if tool.name == "get_surveys"
                )

                # Check that explode=true parameters are properly structured
                params = surveys_tool.inputSchema
                properties = params["properties"]

                # Target parameter with explode=true should allow individual property access
                target_properties = properties["target"]["properties"]
                assert "id" in target_properties
                assert "type" in target_properties
                assert target_properties["type"]["enum"] == ["location", "organisation"]

    async def test_deepobject_explode_false_handling(self, deepobject_spec):
        """Test deepObject with explode=false parameter handling."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=deepobject_spec,
                client=client,
                name="DeepObject Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()
                surveys_tool = next(
                    tool for tool in tools if tool.name == "get_surveys"
                )

                # Check that explode=false parameters are handled
                params = surveys_tool.inputSchema
                properties = params["properties"]

                # Compact parameter with explode=false should still be present and valid
                assert "compact" in properties
                compact_param = properties["compact"]
                # Check that it's a valid parameter (exact structure may vary)
                assert compact_param is not None
                # If it has a type, it should be object
                if "type" in compact_param:
                    assert compact_param["type"] == "object"

    async def test_nested_object_structure_in_request_body(self, deepobject_spec):
        """Test nested object structures in request body are preserved."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=deepobject_spec,
                client=client,
                name="DeepObject Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Find the preferences tool
                prefs_tool = next(
                    tool for tool in tools if tool.name == "update_preferences"
                )
                assert prefs_tool is not None

                # Check that nested object structure is preserved
                params = prefs_tool.inputSchema
                properties = params["properties"]

                # Should have path parameter
                assert "id" in properties

                # Should have preferences object
                assert "preferences" in properties
                prefs_param = properties["preferences"]
                assert prefs_param["type"] == "object"

                # Check nested structure
                prefs_props = prefs_param["properties"]
                assert "theme" in prefs_props
                assert "notifications" in prefs_props
                assert "privacy" in prefs_props

                # Check deeply nested objects
                notifications = prefs_props["notifications"]
                assert notifications["type"] == "object"
                notif_props = notifications["properties"]
                assert "email" in notif_props
                assert "push" in notif_props
                assert "frequency" in notif_props

    async def test_deepobject_tool_functionality(self, deepobject_spec):
        """Test that tools with deepObject parameters maintain basic functionality."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            server = create_openapi_server(
                openapi_spec=deepobject_spec,
                client=client,
                name="DeepObject Test Server",
            )

            async with Client(server) as mcp_client:
                tools = await mcp_client.list_tools()

                # Should successfully create tools with deepObject parameters
                assert len(tools) == 2

                tool_names = {tool.name for tool in tools}
                assert "get_surveys" in tool_names
                assert "update_preferences" in tool_names

                # All tools should have valid schemas
                for tool in tools:
                    assert tool.inputSchema is not None
                    assert tool.inputSchema["type"] == "object"
                    assert "properties" in tool.inputSchema

                    # Should have some properties
                    assert len(tool.inputSchema["properties"]) > 0
