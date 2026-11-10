"""Tests for OpenAPI discriminator handling in OpenAPIProvider."""

import json
from typing import Any

import httpx2
import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers.openapi import OpenAPIProvider


def create_openapi_server(openapi_spec: dict, client) -> FastMCP:
    """Helper to create a FastMCP server with OpenAPIProvider."""
    mcp = FastMCP("OpenAPI Server")
    mcp.add_provider(OpenAPIProvider(openapi_spec=openapi_spec, client=client))
    return mcp


def discriminator_spec(
    mapping: dict[str, str] | None = None,
    body_ref: str = "Pet",
) -> dict[str, Any]:
    """A parent schema with a discriminator mapping onto two allOf subtypes."""
    if mapping is None:
        mapping = {
            "cat": "#/components/schemas/Cat",
            "dog": "#/components/schemas/Dog",
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Pet API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "create_pet",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/{body_ref}"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "Created"}},
                }
            }
        },
        "components": {
            "schemas": {
                "Pet": {
                    "type": "object",
                    "properties": {"petType": {"type": "string"}},
                    "required": ["petType"],
                    "discriminator": {
                        "propertyName": "petType",
                        "mapping": mapping,
                    },
                },
                "Cat": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Pet"},
                        {
                            "type": "object",
                            "properties": {"meowVolume": {"type": "integer"}},
                            "required": ["meowVolume"],
                        },
                    ]
                },
                "Dog": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Pet"},
                        {
                            "type": "object",
                            "properties": {"packSize": {"type": "integer"}},
                            "required": ["packSize"],
                        },
                    ]
                },
            }
        },
    }


def colliding_variant_spec() -> dict[str, Any]:
    """Subtypes that disagree about the shape of the discriminator property.

    The parent marks ``kind`` required without declaring it, so each subtype's
    own ``const`` is the only schema available for that field.
    """
    return {
        "openapi": "3.1.0",
        "info": {"title": "Pet API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "create_pet",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Pet"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "Created"}},
                }
            }
        },
        "components": {
            "schemas": {
                "Pet": {
                    "type": "object",
                    "required": ["kind"],
                    "discriminator": {
                        "propertyName": "kind",
                        "mapping": {"cat": "Cat", "dog": "Dog"},
                    },
                },
                "Cat": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Pet"},
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"const": "cat"},
                                "meowVolume": {"type": "integer"},
                            },
                        },
                    ]
                },
                "Dog": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Pet"},
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"const": "dog"},
                                "packSize": {"type": "integer"},
                            },
                        },
                    ]
                },
            }
        },
    }


def propertyless_variant_spec() -> dict[str, Any]:
    """Subtypes that add nothing beyond the parent they compose."""
    spec = discriminator_spec()
    for name in ("Cat", "Dog"):
        spec["components"]["schemas"][name] = {
            "allOf": [{"$ref": "#/components/schemas/Pet"}]
        }
    return spec


async def tool_schema(spec: dict[str, Any]) -> dict[str, Any]:
    """Build the server and return the generated input schema for create_pet."""
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, json={"ok": True})
        ),
        base_url="https://api.example.com",
    ) as client:
        server = create_openapi_server(spec, client)
        async with Client(server) as mcp_client:
            tools = await mcp_client.list_tools()
            return next(t for t in tools if t.name == "create_pet").input_schema


class TestDiscriminatorRequestBodies:
    """Subtypes named by a discriminator mapping are flattened in as optional."""

    async def test_subtype_fields_are_advertised(self):
        """Fields reachable only through discriminator.mapping reach the schema."""
        schema = await tool_schema(discriminator_spec())

        assert schema["properties"].keys() >= {"petType", "meowVolume", "packSize"}

    async def test_subtype_fields_are_optional(self):
        """Only the discriminator is required; variant fields never are."""
        schema = await tool_schema(discriminator_spec())

        assert schema["required"] == ["petType"]

    async def test_discriminator_property_describes_the_variants(self):
        """The discriminator names which fields belong to which variant."""
        schema = await tool_schema(discriminator_spec())

        description = schema["properties"]["petType"]["description"]
        assert "meowVolume" in description
        assert "packSize" in description

    async def test_discriminator_keyword_is_dropped(self):
        """The mapping points at $defs that get pruned, so it cannot survive."""
        schema = await tool_schema(discriminator_spec())

        assert "discriminator" not in schema
        assert "discriminator" not in schema["properties"]["petType"]

    @pytest.mark.parametrize(
        "mapping",
        [
            pytest.param({"cat": "#/components/schemas/Missing"}, id="missing_ref"),
            pytest.param({"cat": "Missing"}, id="missing_name"),
            pytest.param({"cat": "https://example.com/Cat"}, id="remote_target"),
            pytest.param({"cat": "#/definitions/Cat"}, id="unsupported_pointer"),
        ],
    )
    async def test_unresolvable_mapping_is_ignored(self, mapping: dict[str, str]):
        """An unusable mapping leaves the parent schema as it was."""
        schema = await tool_schema(discriminator_spec(mapping=mapping))

        assert set(schema["properties"]) == {"petType"}

    async def test_bare_schema_name_mapping_resolves(self):
        """Mapping values may be schema names, not just references."""
        schema = await tool_schema(
            discriminator_spec(mapping={"cat": "Cat", "dog": "Dog"})
        )

        assert schema["properties"].keys() >= {"petType", "meowVolume", "packSize"}

    async def test_selected_variant_field_reaches_the_request_body(self):
        """The reported failure: meowVolume must reach the upstream API."""
        received: dict[str, object] = {}

        def handler(request):
            received["body"] = json.loads(request.content)
            return httpx2.Response(200, json={"ok": True})

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler),
            base_url="https://api.example.com",
        ) as client:
            server = create_openapi_server(discriminator_spec(), client)
            async with Client(server) as mcp_client:
                result = await mcp_client.call_tool(
                    "create_pet", {"petType": "cat", "meowVolume": 11}
                )

        assert result.structured_content == {"ok": True}
        assert received["body"] == {"petType": "cat", "meowVolume": 11}

    async def test_accepted_values_are_advertised(self):
        """The legal tags are named even when no variant adds a field."""
        schema = await tool_schema(propertyless_variant_spec())

        description = schema["properties"]["petType"]["description"]
        assert "'cat'" in description
        assert "'dog'" in description

    async def test_propertyless_variant_is_still_named(self):
        """A variant adding no fields remains a legal discriminator value."""
        spec = discriminator_spec()
        spec["components"]["schemas"]["Dog"] = {
            "allOf": [{"$ref": "#/components/schemas/Pet"}]
        }

        schema = await tool_schema(spec)

        description = schema["properties"]["petType"]["description"]
        assert "'dog'" in description
        assert "meowVolume" in description

    async def test_conflicting_variant_schemas_are_unioned(self):
        """No variant's constraint may be advertised as if it applied to all."""
        schema = await tool_schema(colliding_variant_spec())

        kind = schema["properties"]["kind"]
        assert [alternative.get("const") for alternative in kind["anyOf"]] == [
            "cat",
            "dog",
        ]

    async def test_subtype_body_is_unaffected(self):
        """A body referencing the child still resolves through allOf only."""
        schema = await tool_schema(discriminator_spec(body_ref="Cat"))

        assert set(schema["properties"]) == {"petType", "meowVolume"}
        assert sorted(schema["required"]) == ["meowVolume", "petType"]
