"""Tests for nullable field handling in OpenAPI schemas."""

from fastmcp.experimental.utilities.openapi.json_schema_converter import (
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
        assert convert_openapi_schema_to_json_schema("string", "3.0.0") == "string"  # type: ignore[arg-type]
        assert convert_openapi_schema_to_json_schema(123, "3.0.0") == 123  # type: ignore[arg-type]
        assert convert_openapi_schema_to_json_schema(None, "3.0.0") is None  # type: ignore[arg-type]
        assert convert_openapi_schema_to_json_schema([1, 2, 3], "3.0.0") == [1, 2, 3]  # type: ignore[arg-type]

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
