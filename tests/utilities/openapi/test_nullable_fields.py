"""Tests for nullable field handling in OpenAPI schemas."""

import httpx
import pytest
from jsonschema import ValidationError, validate

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers.openapi import OpenAPIProvider
from fastmcp.utilities.openapi.json_schema_converter import (
    convert_openapi_schema_to_json_schema,
)


class TestHandleNullableFields:
    """Test conversion of OpenAPI nullable fields to JSON Schema format."""

    def test_root_level_nullable_string(self):
        """Test nullable string at root level."""
        input_schema = {"type": "string", "nullable": True}
        expected = {"type": ["string", "null"]}
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_root_level_nullable_integer(self):
        """Test nullable integer at root level."""
        input_schema = {"type": "integer", "nullable": True}
        expected = {"type": ["integer", "null"]}
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_root_level_nullable_boolean(self):
        """Test nullable boolean at root level."""
        input_schema = {"type": "boolean", "nullable": True}
        expected = {"type": ["boolean", "null"]}
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_property_level_nullable_fields(self):
        """Test nullable fields in properties."""
        input_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "company": {"type": "string", "nullable": True},
                "age": {"type": "integer", "nullable": True},
                "active": {"type": "boolean", "nullable": True},
            },
        }
        expected = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "company": {"type": ["string", "null"]},
                "age": {"type": ["integer", "null"]},
                "active": {"type": ["boolean", "null"]},
            },
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_mixed_nullable_and_non_nullable(self):
        """Test mix of nullable and non-nullable fields."""
        input_schema = {
            "type": "object",
            "properties": {
                "required_field": {"type": "string"},
                "optional_nullable": {"type": "string", "nullable": True},
                "optional_non_nullable": {"type": "string"},
            },
            "required": ["required_field"],
        }
        expected = {
            "type": "object",
            "properties": {
                "required_field": {"type": "string"},
                "optional_nullable": {"type": ["string", "null"]},
                "optional_non_nullable": {"type": "string"},
            },
            "required": ["required_field"],
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_nullable_false_ignored(self):
        """Test that nullable: false is ignored (removed but no type change)."""
        input_schema = {"type": "string", "nullable": False}
        expected = {"type": "string"}
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_no_nullable_field_unchanged(self):
        """Test that schemas without nullable field are unchanged."""
        input_schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        expected = input_schema.copy()
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_nullable_without_type_removes_nullable(self):
        """Test that nullable field is removed even without type."""
        input_schema = {"nullable": True, "description": "Some field"}
        expected = {"description": "Some field"}
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_preserves_other_fields(self):
        """Test that other fields are preserved during conversion."""
        input_schema = {
            "type": "string",
            "nullable": True,
            "description": "A nullable string",
            "example": "test",
            "format": "email",
        }
        expected = {
            "type": ["string", "null"],
            "description": "A nullable string",
            "example": "test",
            "format": "email",
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_non_dict_input_unchanged(self):
        """Test that non-dict inputs are returned unchanged."""
        # These tests intentionally pass invalid types to check edge case handling
        from typing import Any, cast

        assert (
            convert_openapi_schema_to_json_schema(cast(Any, "string"), "3.0.0")
            == "string"
        )
        assert convert_openapi_schema_to_json_schema(cast(Any, 123), "3.0.0") == 123
        assert convert_openapi_schema_to_json_schema(cast(Any, None), "3.0.0") is None
        assert convert_openapi_schema_to_json_schema(cast(Any, [1, 2, 3]), "3.0.0") == [
            1,
            2,
            3,
        ]

    def test_performance_optimization_no_copy_when_unchanged(self):
        """Test that schemas without nullable fields return the same object (no copy)."""
        input_schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        # Should return the exact same object, not a copy
        assert result is input_schema

    def test_union_types_with_nullable(self):
        """Test nullable handling with existing union types (type as array)."""
        input_schema = {"type": ["string", "integer"], "nullable": True}
        expected = {"type": ["string", "integer", "null"]}
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_already_nullable_union_unchanged(self):
        """Test that union types already containing null are not modified."""
        input_schema = {"type": ["string", "null"], "nullable": True}
        expected = {"type": ["string", "null"]}
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_property_level_union_with_nullable(self):
        """Test nullable handling with union types in properties."""
        input_schema = {
            "type": "object",
            "properties": {"value": {"type": ["string", "integer"], "nullable": True}},
        }
        expected = {
            "type": "object",
            "properties": {"value": {"type": ["string", "integer", "null"]}},
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_complex_union_nullable_scenarios(self):
        """Test various complex union type scenarios."""
        # Already has null in different position
        input1 = {"type": ["null", "string", "integer"], "nullable": True}
        result1 = convert_openapi_schema_to_json_schema(input1, "3.0.0")
        assert result1 == {"type": ["null", "string", "integer"]}

        # Single item array
        input2 = {"type": ["string"], "nullable": True}
        result2 = convert_openapi_schema_to_json_schema(input2, "3.0.0")
        assert result2 == {"type": ["string", "null"]}

    def test_oneof_with_nullable(self):
        """Test nullable handling with oneOf constructs."""
        input_schema = {
            "oneOf": [{"type": "string"}, {"type": "integer"}],
            "nullable": True,
        }
        expected = {
            "anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_anyof_with_nullable(self):
        """Test nullable handling with anyOf constructs."""
        input_schema = {
            "anyOf": [{"type": "string"}, {"type": "integer"}],
            "nullable": True,
        }
        expected = {
            "anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_anyof_already_nullable(self):
        """Test anyOf that already contains null type."""
        input_schema = {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "nullable": True,
        }
        expected = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_allof_with_nullable(self):
        """Test nullable handling with allOf constructs."""
        input_schema = {
            "allOf": [{"type": "string"}, {"minLength": 1}],
            "nullable": True,
        }
        expected = {
            "anyOf": [
                {"allOf": [{"type": "string"}, {"minLength": 1}]},
                {"type": "null"},
            ]
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_property_level_oneof_with_nullable(self):
        """Test nullable handling with oneOf in properties."""
        input_schema = {
            "type": "object",
            "properties": {
                "value": {
                    "oneOf": [{"type": "string"}, {"type": "integer"}],
                    "nullable": True,
                }
            },
        }
        expected = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]
                }
            },
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_nullable_enum_field(self):
        """Test nullable enum field - issue #2082."""
        input_schema = {
            "type": "string",
            "nullable": True,
            "enum": ["VALUE1", "VALUE2", "VALUE3"],
        }
        expected = {
            "type": ["string", "null"],
            "enum": ["VALUE1", "VALUE2", "VALUE3", None],
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_nullable_enum_already_contains_null(self):
        """Test nullable enum that already contains None."""
        input_schema = {
            "type": "string",
            "nullable": True,
            "enum": ["VALUE1", "VALUE2", None],
        }
        expected = {
            "type": ["string", "null"],
            "enum": ["VALUE1", "VALUE2", None],
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_nullable_enum_without_type(self):
        """Test nullable enum without explicit type field."""
        input_schema = {
            "nullable": True,
            "enum": ["VALUE1", "VALUE2", "VALUE3"],
        }
        expected = {
            "enum": ["VALUE1", "VALUE2", "VALUE3", None],
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_non_nullable_enum_unchanged(self):
        """Test that enum without nullable is unchanged."""
        input_schema = {
            "type": "string",
            "enum": ["VALUE1", "VALUE2", "VALUE3"],
        }
        expected = {
            "type": "string",
            "enum": ["VALUE1", "VALUE2", "VALUE3"],
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_property_level_nullable_enum(self):
        """Test nullable enum in object properties."""
        input_schema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "nullable": True,
                    "enum": ["ACTIVE", "INACTIVE", "PENDING"],
                },
                "name": {"type": "string"},
            },
        }
        expected = {
            "type": "object",
            "properties": {
                "status": {
                    "type": ["string", "null"],
                    "enum": ["ACTIVE", "INACTIVE", "PENDING", None],
                },
                "name": {"type": "string"},
            },
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_nullable_integer_enum(self):
        """Test nullable enum with integer values."""
        input_schema = {
            "type": "integer",
            "nullable": True,
            "enum": [1, 2, 3],
        }
        expected = {
            "type": ["integer", "null"],
            "enum": [1, 2, 3, None],
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_nullable_in_definitions(self):
        """Test nullable field inside $defs."""
        input_schema = {
            "type": "object",
            "properties": {"user": {"$ref": "#/$defs/User"}},
            "$defs": {
                "User": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "bio": {"type": "string", "nullable": True},
                    },
                }
            },
        }
        expected = {
            "type": "object",
            "properties": {"user": {"$ref": "#/$defs/User"}},
            "$defs": {
                "User": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "bio": {"type": ["string", "null"]},
                    },
                }
            },
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected

    def test_nullable_in_nested_properties(self):
        """Test nullable field in deeply nested properties."""
        input_schema = {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {
                        "b": {
                            "type": "object",
                            "properties": {"c": {"type": "string", "nullable": True}},
                        }
                    },
                }
            },
        }
        expected = {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {
                        "b": {
                            "type": "object",
                            "properties": {"c": {"type": ["string", "null"]}},
                        }
                    },
                }
            },
        }
        result = convert_openapi_schema_to_json_schema(input_schema, "3.0.0")
        assert result == expected


class TestNullableFieldValidation:
    """Test that converted schemas validate correctly with jsonschema."""

    def test_nullable_string_validates(self):
        """Test that nullable string validates both null and string values."""
        openapi_schema = {"type": "string", "nullable": True}
        json_schema = convert_openapi_schema_to_json_schema(openapi_schema, "3.0.0")

        # Both null and string should validate
        validate(instance=None, schema=json_schema)
        validate(instance="test", schema=json_schema)

        # Other types should fail
        with pytest.raises(ValidationError):
            validate(instance=123, schema=json_schema)

    def test_nullable_enum_validates(self):
        """Test that nullable enum validates null, enum values, and rejects invalid values."""
        openapi_schema = {
            "type": "string",
            "nullable": True,
            "enum": ["VALUE1", "VALUE2", "VALUE3"],
        }
        json_schema = convert_openapi_schema_to_json_schema(openapi_schema, "3.0.0")

        # Null and enum values should validate
        validate(instance=None, schema=json_schema)
        validate(instance="VALUE1", schema=json_schema)

        # Invalid values should fail
        with pytest.raises(ValidationError):
            validate(instance="INVALID", schema=json_schema)


class TestNullableInputSchemaIntegration:
    """Test that nullable fields are converted in tool input schemas end-to-end.

    These tests exercise the full pipeline: OpenAPI spec -> OpenAPIProvider ->
    tool.inputSchema, verifying that `nullable: true` doesn't leak through.
    """

    async def test_nullable_query_param_converted_in_tool_input_schema(self):
        """Nullable query parameter should produce type union in tool input schema."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search",
                        "parameters": [
                            {
                                "name": "query",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "category",
                                "in": "query",
                                "schema": {"type": "string", "nullable": True},
                            },
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(openapi_spec=spec, client=client)
            mcp = FastMCP("test")
            mcp.add_provider(provider)

            async with Client(mcp) as mcp_client:
                tools = await mcp_client.list_tools()
                assert len(tools) == 1
                schema = tools[0].inputSchema
                category_prop = schema["properties"]["category"]
                assert "nullable" not in category_prop
                assert category_prop["type"] == ["string", "null"]

    async def test_nullable_in_request_body_defs_converted(self):
        """Nullable field inside $defs referenced by request body should be converted."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "create_user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/CreateUser"
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "Created"}},
                    }
                }
            },
            "components": {
                "schemas": {
                    "CreateUser": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "bio": {"type": "string", "nullable": True},
                        },
                    }
                }
            },
        }
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(openapi_spec=spec, client=client)
            mcp = FastMCP("test")
            mcp.add_provider(provider)

            async with Client(mcp) as mcp_client:
                tools = await mcp_client.list_tools()
                assert len(tools) == 1
                schema = tools[0].inputSchema

                # Find the bio property — it may be inline or in $defs
                if "$defs" in schema:
                    # Resolve through $defs
                    user_schema = next(iter(schema["$defs"].values()))
                    bio_prop = user_schema["properties"]["bio"]
                else:
                    bio_prop = schema["properties"]["bio"]

                assert "nullable" not in bio_prop
                assert bio_prop["type"] == ["string", "null"]
