"""Tests for circular and self-referential schema serialization (Issues #1016, #1206, #3242)."""

import httpx

from fastmcp import FastMCP
from fastmcp.utilities.openapi.models import (
    ResponseInfo,
)
from fastmcp.utilities.openapi.schemas import (
    _replace_ref_with_defs,
    extract_output_schema_from_responses,
)


class TestCircularReferencesSerialization:
    """Tests for circular/self-referential schemas surviving MCP serialization.

    Issues: #1016, #1206, #3242

    The crash occurs when Pydantic's model_dump() encounters the same Python
    dict object at multiple positions in the serialization tree. This happens
    because _replace_ref_with_defs mutates shared list objects (anyOf/allOf/oneOf)
    in place via shallow copy, causing different tools to share internal dict
    references.
    """

    def test_replace_ref_with_defs_does_not_mutate_input(self):
        """_replace_ref_with_defs must not mutate its input dict's lists."""
        schema = {
            "oneOf": [
                {"$ref": "#/components/schemas/Cat"},
                {"$ref": "#/components/schemas/Dog"},
            ]
        }
        original_list = schema["oneOf"]
        original_items = list(original_list)  # snapshot

        _replace_ref_with_defs(schema)

        # The original list object must not have been mutated
        assert original_list is schema["oneOf"]
        assert original_list == original_items

    def test_replace_ref_with_defs_produces_independent_results(self):
        """Calling _replace_ref_with_defs twice on the same input must produce
        independent dict trees with no shared mutable objects."""
        schema = {
            "type": "object",
            "properties": {
                "pet": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/Cat"},
                        {"$ref": "#/components/schemas/Dog"},
                    ]
                }
            },
        }

        result1 = _replace_ref_with_defs(schema)
        result2 = _replace_ref_with_defs(schema)

        # The oneOf lists should be different objects
        list1 = result1["properties"]["pet"]["oneOf"]
        list2 = result2["properties"]["pet"]["oneOf"]
        assert list1 is not list2

        # Items within the lists should also be independent
        assert list1[0] is not list2[0]

    def test_circular_output_schema_serialization(self):
        """Output schemas with self-referential types must survive model_dump().

        This is the exact crash from issue #3242: Pydantic raises
        ValueError('Circular reference detected (id repeated)') when
        serializing MCP Tool objects whose schemas share Python dict references.
        """
        responses = {
            "200": ResponseInfo(
                description="A tree node",
                content_schema={
                    "application/json": {"$ref": "#/components/schemas/Node"}
                },
            )
        }
        schema_definitions = {
            "Node": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "children": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Node"},
                    },
                },
            },
        }

        output_schema = extract_output_schema_from_responses(
            responses, schema_definitions=schema_definitions, openapi_version="3.0.0"
        )
        assert output_schema is not None

        # Build an MCP Tool with this schema and try to serialize it —
        # this is the exact path that crashes in the reported issue.
        from mcp.types import Tool as MCPTool

        tool = MCPTool(
            name="get_node",
            description="Get a node",
            inputSchema={"type": "object", "properties": {}},
            outputSchema=output_schema,
        )
        # This must not raise ValueError: Circular reference detected
        tool.model_dump(by_alias=True, mode="json", exclude_none=True)

    def test_mutual_circular_references_serialization(self):
        """Mutually circular schemas (A→B→A) must survive serialization."""
        responses = {
            "200": ResponseInfo(
                description="A pull request",
                content_schema={
                    "application/json": {"$ref": "#/components/schemas/PullRequest"}
                },
            )
        }
        schema_definitions = {
            "PullRequest": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"$ref": "#/components/schemas/User"},
                },
            },
            "User": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "pull_requests": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/PullRequest"},
                    },
                },
            },
        }

        output_schema = extract_output_schema_from_responses(
            responses, schema_definitions=schema_definitions, openapi_version="3.0.0"
        )
        assert output_schema is not None

        from mcp.types import Tool as MCPTool

        tool = MCPTool(
            name="get_pr",
            description="Get a pull request",
            inputSchema={"type": "object", "properties": {}},
            outputSchema=output_schema,
        )
        tool.model_dump(by_alias=True, mode="json", exclude_none=True)

    async def test_multiple_tools_sharing_circular_schemas(self):
        """Multiple tools from the same spec must not share Python dict objects
        in their schemas, which would cause Pydantic to raise circular reference
        errors when serializing the list_tools response."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/nodes": {
                    "get": {
                        "operationId": "list_nodes",
                        "responses": {
                            "200": {
                                "description": "List of nodes",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Node"
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    },
                    "post": {
                        "operationId": "create_node",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Node"}
                                }
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "Created node",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Node"}
                                    }
                                },
                            }
                        },
                    },
                },
                "/nodes/{id}": {
                    "get": {
                        "operationId": "get_node",
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
                                "description": "A node",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Node"}
                                    }
                                },
                            }
                        },
                    },
                },
            },
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                            "children": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Node"},
                            },
                        },
                    }
                }
            },
        }

        server = FastMCP.from_openapi(spec, httpx.AsyncClient())
        tools = await server.list_tools()
        assert len(tools) >= 3

        # Simulate what the MCP SDK does: serialize all tools together.
        # This is the exact crash path — model_dump on a list of tools
        # whose schemas share Python dict objects.

        mcp_tools = [tool.to_mcp_tool(name=tool.name) for tool in tools]
        for mcp_tool in mcp_tools:
            mcp_tool.model_dump(by_alias=True, mode="json", exclude_none=True)
