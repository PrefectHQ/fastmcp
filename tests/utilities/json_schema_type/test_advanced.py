"""Advanced JSON schema type conversion features."""

from dataclasses import Field
from typing import Union

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import (
    _hash_schema,
    _merge_defaults,
    json_schema_to_type,
)


def get_dataclass_field(type: type, field_name: str) -> Field:
    return type.__dataclass_fields__[field_name]  # ty: ignore[unresolved-attribute]


class TestDefaultValues:
    """Test suite for default value handling."""

    @pytest.fixture
    def simple_defaults(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "default": "anonymous"},
                    "age": {"type": "integer", "default": 0},
                },
            }
        )

    @pytest.fixture
    def nested_defaults(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "default": "anonymous"},
                            "settings": {
                                "type": "object",
                                "properties": {
                                    "theme": {"type": "string", "default": "light"}
                                },
                                "default": {"theme": "dark"},
                            },
                        },
                        "default": {"name": "guest", "settings": {"theme": "system"}},
                    }
                },
            }
        )

    def test_simple_defaults_empty_object(self, simple_defaults):
        validator = TypeAdapter(simple_defaults)
        result = validator.validate_python({})
        assert result.name == "anonymous"
        assert result.age == 0

    def test_simple_defaults_partial_override(self, simple_defaults):
        validator = TypeAdapter(simple_defaults)
        result = validator.validate_python({"name": "test"})
        assert result.name == "test"
        assert result.age == 0

    def test_nested_defaults_empty_object(self, nested_defaults):
        validator = TypeAdapter(nested_defaults)
        result = validator.validate_python({})
        assert result.user.name == "guest"
        assert result.user.settings.theme == "system"

    def test_nested_defaults_partial_override(self, nested_defaults):
        validator = TypeAdapter(nested_defaults)
        result = validator.validate_python({"user": {"name": "test"}})
        assert result.user.name == "test"
        assert result.user.settings.theme == "system"


class TestCircularReferences:
    """Test suite for circular reference handling."""

    @pytest.fixture
    def self_referential(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "child": {"$ref": "#"}},
            }
        )

    @pytest.fixture
    def mutually_recursive(self):
        return json_schema_to_type(
            {
                "type": "object",
                "definitions": {
                    "Person": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "friend": {"$ref": "#/definitions/Pet"},
                        },
                    },
                    "Pet": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "owner": {"$ref": "#/definitions/Person"},
                        },
                    },
                },
                "properties": {"person": {"$ref": "#/definitions/Person"}},
            }
        )

    def test_self_ref_single_level(self, self_referential):
        validator = TypeAdapter(self_referential)
        result = validator.validate_python(
            {"name": "parent", "child": {"name": "child"}}
        )
        assert result.name == "parent"
        assert result.child.name == "child"
        assert result.child.child is None

    def test_self_ref_multiple_levels(self, self_referential):
        validator = TypeAdapter(self_referential)
        result = validator.validate_python(
            {
                "name": "grandparent",
                "child": {"name": "parent", "child": {"name": "child"}},
            }
        )
        assert result.name == "grandparent"
        assert result.child.name == "parent"
        assert result.child.child.name == "child"

    def test_mutual_recursion_single_level(self, mutually_recursive):
        validator = TypeAdapter(mutually_recursive)
        result = validator.validate_python(
            {"person": {"name": "Alice", "friend": {"name": "Spot"}}}
        )
        assert result.person.name == "Alice"
        assert result.person.friend.name == "Spot"
        assert result.person.friend.owner is None

    def test_mutual_recursion_multiple_levels(self, mutually_recursive):
        validator = TypeAdapter(mutually_recursive)
        result = validator.validate_python(
            {
                "person": {
                    "name": "Alice",
                    "friend": {"name": "Spot", "owner": {"name": "Bob"}},
                }
            }
        )
        assert result.person.name == "Alice"
        assert result.person.friend.name == "Spot"
        assert result.person.friend.owner.name == "Bob"


class TestIdentifierNormalization:
    """Test suite for handling non-standard property names."""

    @pytest.fixture
    def special_chars(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "@type": {"type": "string"},
                    "first-name": {"type": "string"},
                    "last.name": {"type": "string"},
                    "2nd_address": {"type": "string"},
                    "$ref": {"type": "string"},
                },
            }
        )

    def test_normalizes_special_chars(self, special_chars):
        validator = TypeAdapter(special_chars)
        result = validator.validate_python(
            {
                "@type": "person",
                "first-name": "Alice",
                "last.name": "Smith",
                "2nd_address": "456 Oak St",
                "$ref": "12345",
            }
        )
        assert result.field_type == "person"  # @type -> field_type
        assert result.first_name == "Alice"  # first-name -> first_name
        assert result.last_name == "Smith"  # last.name -> last_name
        assert (
            result.field_2nd_address == "456 Oak St"
        )  # 2nd_address -> field_2nd_address
        assert result.field_ref == "12345"  # $ref -> field_ref


class TestConstantValues:
    """Test suite for constant value validation."""

    @pytest.fixture
    def string_const(self):
        return json_schema_to_type({"type": "string", "const": "production"})

    @pytest.fixture
    def number_const(self):
        return json_schema_to_type({"type": "number", "const": 42.5})

    @pytest.fixture
    def boolean_const(self):
        return json_schema_to_type({"type": "boolean", "const": True})

    @pytest.fixture
    def null_const(self):
        return json_schema_to_type({"type": "null", "const": None})

    @pytest.fixture
    def object_with_consts(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "env": {"const": "production"},
                    "version": {"const": 1},
                    "enabled": {"const": True},
                },
            }
        )

    def test_string_const_valid(self, string_const):
        validator = TypeAdapter(string_const)
        assert validator.validate_python("production") == "production"

    def test_string_const_invalid(self, string_const):
        validator = TypeAdapter(string_const)
        with pytest.raises(ValidationError):
            validator.validate_python("development")

    def test_number_const_valid(self, number_const):
        validator = TypeAdapter(number_const)
        assert validator.validate_python(42.5) == 42.5

    def test_number_const_invalid(self, number_const):
        validator = TypeAdapter(number_const)
        with pytest.raises(ValidationError):
            validator.validate_python(42)

    def test_boolean_const_valid(self, boolean_const):
        validator = TypeAdapter(boolean_const)
        assert validator.validate_python(True) is True

    def test_boolean_const_invalid(self, boolean_const):
        validator = TypeAdapter(boolean_const)
        with pytest.raises(ValidationError):
            validator.validate_python(False)

    def test_null_const_valid(self, null_const):
        validator = TypeAdapter(null_const)
        assert validator.validate_python(None) is None

    def test_null_const_invalid(self, null_const):
        validator = TypeAdapter(null_const)
        with pytest.raises(ValidationError):
            validator.validate_python(False)

    def test_object_consts_valid(self, object_with_consts):
        validator = TypeAdapter(object_with_consts)
        result = validator.validate_python(
            {"env": "production", "version": 1, "enabled": True}
        )
        assert result.env == "production"
        assert result.version == 1
        assert result.enabled is True

    def test_object_consts_invalid(self, object_with_consts):
        validator = TypeAdapter(object_with_consts)
        with pytest.raises(ValidationError):
            validator.validate_python(
                {
                    "env": "production",
                    "version": 2,  # Wrong constant
                    "enabled": True,
                }
            )


class TestSchemaCaching:
    """Test suite for schema caching behavior."""

    def test_identical_schemas_reuse_class(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        class1 = json_schema_to_type(schema)
        class2 = json_schema_to_type(schema)
        assert class1 is class2

    def test_different_names_different_classes(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        class1 = json_schema_to_type(schema, name="Class1")
        class2 = json_schema_to_type(schema, name="Class2")
        assert class1 is not class2
        assert class1.__name__ == "Class1"
        assert class2.__name__ == "Class2"

    def test_nested_schema_caching(self):
        schema = {
            "type": "object",
            "properties": {
                "nested": {"type": "object", "properties": {"name": {"type": "string"}}}
            },
        }

        class1 = json_schema_to_type(schema)
        class2 = json_schema_to_type(schema)

        # Both main classes and their nested classes should be identical
        assert class1 is class2
        assert (
            get_dataclass_field(class1, "nested").type
            is get_dataclass_field(class2, "nested").type
        )


class TestSchemaHashing:
    """Test suite for schema hashing utility."""

    def test_deterministic_hash(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        hash1 = _hash_schema(schema)
        hash2 = _hash_schema(schema)
        assert hash1 == hash2
        assert isinstance(hash1, str)
        assert len(hash1) == 64  # SHA-256 hash length

    def test_different_schemas_different_hashes(self):
        schema1 = {"type": "object", "properties": {"name": {"type": "string"}}}
        schema2 = {"type": "object", "properties": {"age": {"type": "integer"}}}
        assert _hash_schema(schema1) != _hash_schema(schema2)

    def test_order_independent_hash(self):
        schema1 = {"properties": {"name": {"type": "string"}}, "type": "object"}
        schema2 = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert _hash_schema(schema1) == _hash_schema(schema2)

    def test_nested_schema_hash(self):
        schema = {
            "type": "object",
            "properties": {
                "nested": {"type": "object", "properties": {"name": {"type": "string"}}}
            },
        }
        hash1 = _hash_schema(schema)
        assert isinstance(hash1, str)
        assert len(hash1) == 64


class TestDefaultMerging:
    """Test suite for default value merging behavior."""

    def test_simple_merge(self):
        defaults = {"name": "anonymous", "age": 0}
        data = {"name": "test"}
        result = _merge_defaults(data, {"properties": {}}, defaults)
        assert result["name"] == "test"
        assert result["age"] == 0

    def test_nested_merge(self):
        defaults = {"user": {"name": "anonymous", "settings": {"theme": "light"}}}
        data = {"user": {"name": "test"}}
        result = _merge_defaults(data, {"properties": {}}, defaults)
        assert result["user"]["name"] == "test"
        assert result["user"]["settings"]["theme"] == "light"

    def test_array_merge(self):
        defaults = {
            "items": [
                {"name": "item1", "done": False},
                {"name": "item2", "done": False},
            ]
        }
        data = {"items": [{"name": "custom", "done": True}]}
        result = _merge_defaults(data, {"properties": {}}, defaults)
        assert len(result["items"]) == 1
        assert result["items"][0]["name"] == "custom"
        assert result["items"][0]["done"] is True

    def test_empty_data_uses_defaults(self):
        schema = {
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "default": "anonymous"},
                        "settings": {"type": "object", "default": {"theme": "light"}},
                    },
                    "default": {"name": "guest", "settings": {"theme": "dark"}},
                }
            }
        }
        result = _merge_defaults({}, schema)
        assert result["user"]["name"] == "guest"
        assert result["user"]["settings"]["theme"] == "dark"

    def test_property_level_defaults(self):
        schema = {
            "properties": {
                "name": {"type": "string", "default": "anonymous"},
                "age": {"type": "integer", "default": 0},
            }
        }
        result = _merge_defaults({}, schema)
        assert result["name"] == "anonymous"
        assert result["age"] == 0

    def test_nested_property_defaults(self):
        schema = {
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "default": "anonymous"},
                        "settings": {
                            "type": "object",
                            "properties": {
                                "theme": {"type": "string", "default": "light"}
                            },
                        },
                    },
                }
            }
        }
        result = _merge_defaults({"user": {"settings": {}}}, schema)
        assert result["user"]["name"] == "anonymous"
        assert result["user"]["settings"]["theme"] == "light"

    def test_default_priority(self):
        schema = {
            "properties": {
                "settings": {
                    "type": "object",
                    "properties": {"theme": {"type": "string", "default": "light"}},
                    "default": {"theme": "dark"},
                }
            },
            "default": {"settings": {"theme": "system"}},
        }

        # Test priority: data > parent default > object default > property default
        result1 = _merge_defaults({}, schema)  # Uses schema default
        assert result1["settings"]["theme"] == "system"

        result2 = _merge_defaults({"settings": {}}, schema)  # Uses object default
        assert result2["settings"]["theme"] == "dark"

        result3 = _merge_defaults(
            {"settings": {"theme": "custom"}}, schema
        )  # Uses provided data
        assert result3["settings"]["theme"] == "custom"


class TestEdgeCases:
    """Test suite for edge cases and corner scenarios."""

    def test_empty_schema(self):
        schema = {}
        result = json_schema_to_type(schema)
        assert result is object

    def test_schema_without_type(self):
        schema = {"properties": {"name": {"type": "string"}}}
        Type = json_schema_to_type(schema)
        validator = TypeAdapter(Type)
        result = validator.validate_python({"name": "test"})
        assert result.name == "test"  # type: ignore[attr-defined]

    def test_recursive_defaults(self):
        schema = {
            "type": "object",
            "properties": {
                "node": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}, "next": {"$ref": "#"}},
                    "default": {"value": "default", "next": None},
                }
            },
        }
        Type = json_schema_to_type(schema)
        validator = TypeAdapter(Type)
        result = validator.validate_python({})
        assert result.node.value == "default"  # type: ignore[attr-defined]
        assert result.node.next is None  # type: ignore[attr-defined]

    def test_mixed_type_array(self):
        schema = {
            "type": "array",
            "items": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}],
        }
        Type = json_schema_to_type(schema)
        validator = TypeAdapter(Type)
        result = validator.validate_python(["test", 123, True])
        assert result == ["test", 123, True]


class TestNameHandling:
    """Test suite for schema name handling."""

    def test_name_from_title(self):
        schema = {
            "type": "object",
            "title": "Person",
            "properties": {"name": {"type": "string"}},
        }
        Type = json_schema_to_type(schema)
        assert Type.__name__ == "Person"

    def test_explicit_name_overrides_title(self):
        schema = {
            "type": "object",
            "title": "Person",
            "properties": {"name": {"type": "string"}},
        }
        Type = json_schema_to_type(schema, name="CustomPerson")
        assert Type.__name__ == "CustomPerson"

    def test_default_name_without_title(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        Type = json_schema_to_type(schema)
        assert Type.__name__ == "Root"

    def test_name_only_allowed_for_objects(self):
        schema = {"type": "string"}
        with pytest.raises(ValueError, match="Can not apply name to non-object schema"):
            json_schema_to_type(schema, name="StringType")

    def test_nested_object_names(self):
        schema = {
            "type": "object",
            "title": "Parent",
            "properties": {
                "child": {
                    "type": "object",
                    "title": "Child",
                    "properties": {"name": {"type": "string"}},
                }
            },
        }
        Type = json_schema_to_type(schema)
        assert Type.__name__ == "Parent"
        child_field_type = get_dataclass_field(Type, "child").type
        assert child_field_type.__origin__ is Union  # ty: ignore[possibly-missing-attribute]
        assert child_field_type.__args__[0].__name__ == "Child"  # ty: ignore[possibly-missing-attribute]
        assert child_field_type.__args__[1] is type(None)  # ty: ignore[possibly-missing-attribute]

    def test_recursive_schema_naming(self):
        schema = {
            "type": "object",
            "title": "Node",
            "properties": {"next": {"$ref": "#"}},
        }
        Type = json_schema_to_type(schema)
        assert Type.__name__ == "Node"

        next_field_type = get_dataclass_field(Type, "next").type

        assert next_field_type.__origin__ is Union  # ty: ignore[possibly-missing-attribute]
        assert next_field_type.__args__[0].__forward_arg__ == "Node"  # ty: ignore[possibly-missing-attribute]
        assert next_field_type.__args__[1] is type(None)  # ty: ignore[possibly-missing-attribute]

    def test_name_caching_with_different_titles(self):
        """Ensure schemas with different titles create different cached classes"""
        schema1 = {
            "type": "object",
            "title": "Type1",
            "properties": {"name": {"type": "string"}},
        }
        schema2 = {
            "type": "object",
            "title": "Type2",
            "properties": {"name": {"type": "string"}},
        }
        Type1 = json_schema_to_type(schema1)
        Type2 = json_schema_to_type(schema2)
        assert Type1 is not Type2
        assert Type1.__name__ == "Type1"
        assert Type2.__name__ == "Type2"

    def test_recursive_schema_with_invalid_python_name(self):
        """Test that recursive schemas work with titles that aren't valid Python identifiers"""
        schema = {
            "type": "object",
            "title": "My Complex Type!",
            "properties": {"name": {"type": "string"}, "child": {"$ref": "#"}},
        }
        Type = json_schema_to_type(schema)
        # The class should get a sanitized name
        assert Type.__name__ == "My_Complex_Type"
        # Create an instance to verify the recursive reference works
        validator = TypeAdapter(Type)
        result = validator.validate_python(
            {"name": "parent", "child": {"name": "child", "child": None}}
        )
        assert result.name == "parent"  # type: ignore[attr-defined]
        assert result.child.name == "child"  # type: ignore[attr-defined]
        assert result.child.child is None  # type: ignore[attr-defined]


class TestAdditionalProperties:
    """Test suite for additionalProperties handling."""

    @pytest.fixture
    def dict_only_schema(self):
        """Schema with no properties but additionalProperties=True -> dict[str, Any]"""
        return json_schema_to_type({"type": "object", "additionalProperties": True})

    @pytest.fixture
    def properties_with_additional(self):
        """Schema with properties AND additionalProperties=True -> BaseModel"""
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "additionalProperties": True,
            }
        )

    @pytest.fixture
    def properties_without_additional(self):
        """Schema with properties but no additionalProperties -> dataclass"""
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
            }
        )

    @pytest.fixture
    def required_properties_with_additional(self):
        """Schema with required properties AND additionalProperties=True -> BaseModel"""
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name"],
                "additionalProperties": True,
            }
        )

    def test_dict_only_returns_dict_type(self, dict_only_schema):
        """Test that schema with no properties + additionalProperties=True returns dict[str, Any]"""
        import typing

        assert dict_only_schema == dict[str, typing.Any]

    def test_dict_only_accepts_any_data(self, dict_only_schema):
        """Test that pure dict accepts arbitrary key-value pairs"""
        validator = TypeAdapter(dict_only_schema)
        data = {"anything": "works", "numbers": 123, "nested": {"key": "value"}}
        result = validator.validate_python(data)
        assert result == data
        assert isinstance(result, dict)

    def test_properties_with_additional_returns_basemodel(
        self, properties_with_additional
    ):
        """Test that schema with properties + additionalProperties=True returns BaseModel"""
        assert issubclass(properties_with_additional, BaseModel)

    def test_properties_with_additional_accepts_extra_fields(
        self, properties_with_additional
    ):
        """Test that BaseModel with extra='allow' accepts additional properties"""
        validator = TypeAdapter(properties_with_additional)
        data = {
            "name": "Alice",
            "age": 30,
            "extra": "field",
            "another": {"nested": "data"},
        }
        result = validator.validate_python(data)

        # Check standard properties
        assert result.name == "Alice"
        assert result.age == 30

        # Check extra properties are preserved with dot access
        assert hasattr(result, "extra")
        assert result.extra == "field"
        assert hasattr(result, "another")
        assert result.another == {"nested": "data"}

    def test_properties_with_additional_validates_known_fields(
        self, properties_with_additional
    ):
        """Test that BaseModel still validates known fields"""
        validator = TypeAdapter(properties_with_additional)

        # Should accept valid data
        result = validator.validate_python({"name": "Alice", "age": 30, "extra": "ok"})
        assert result.name == "Alice"
        assert result.age == 30
        assert result.extra == "ok"

        # Should reject invalid types for known fields
        with pytest.raises(ValidationError):
            validator.validate_python({"name": "Alice", "age": "not_a_number"})

    def test_properties_without_additional_is_dataclass(
        self, properties_without_additional
    ):
        """Test that schema with properties but no additionalProperties returns dataclass"""
        assert not issubclass(properties_without_additional, BaseModel)
        assert hasattr(properties_without_additional, "__dataclass_fields__")

    def test_properties_without_additional_ignores_extra_fields(
        self, properties_without_additional
    ):
        """Test that dataclass ignores extra properties (current behavior)"""
        validator = TypeAdapter(properties_without_additional)
        data = {"name": "Alice", "age": 30, "extra": "ignored"}
        result = validator.validate_python(data)

        # Check standard properties
        assert result.name == "Alice"
        assert result.age == 30

        # Check extra property is ignored
        assert not hasattr(result, "extra")

    def test_required_properties_with_additional(
        self, required_properties_with_additional
    ):
        """Test BaseModel with required fields and additional properties"""
        validator = TypeAdapter(required_properties_with_additional)

        # Should accept valid data with required field
        result = validator.validate_python({"name": "Alice", "extra": "field"})
        assert result.name == "Alice"
        assert result.age is None  # Optional field
        assert result.extra == "field"

        # Should reject missing required field
        with pytest.raises(ValidationError):
            validator.validate_python({"age": 30, "extra": "field"})

    def test_nested_additional_properties(self):
        """Test nested objects with additionalProperties"""
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": True,
                },
                "settings": {
                    "type": "object",
                    "properties": {"theme": {"type": "string"}},
                },
            },
            "additionalProperties": True,
        }

        Type = json_schema_to_type(schema)
        validator = TypeAdapter(Type)

        data = {
            "user": {"name": "Alice", "extra_user_field": "value"},
            "settings": {"theme": "dark", "extra_settings_field": "ignored"},
            "top_level_extra": "preserved",
        }

        result = validator.validate_python(data)

        # Check top-level extra field (BaseModel)
        assert result.top_level_extra == "preserved"  # type: ignore[attr-defined]

        # Check nested user extra field (BaseModel)
        assert result.user.name == "Alice"  # type: ignore[attr-defined]
        assert result.user.extra_user_field == "value"  # type: ignore[attr-defined]

        # Check nested settings - should be dataclass
        assert result.settings.theme == "dark"  # type: ignore[attr-defined]
        # Note: When nested in BaseModel with extra='allow', Pydantic may preserve extra fields
        # even on dataclass children. The important thing is that settings is still a dataclass.
        assert not issubclass(type(result.settings), BaseModel)  # type: ignore[attr-defined]

    def test_additional_properties_false_vs_missing(self):
        """Test difference between additionalProperties: false and missing additionalProperties"""
        # Schema with explicit additionalProperties: false
        schema_false = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        }

        # Schema with no additionalProperties key
        schema_missing = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }

        Type_false = json_schema_to_type(schema_false)
        Type_missing = json_schema_to_type(schema_missing)

        # Both should create dataclasses (not BaseModel)
        assert not issubclass(Type_false, BaseModel)
        assert not issubclass(Type_missing, BaseModel)
        assert hasattr(Type_false, "__dataclass_fields__")
        assert hasattr(Type_missing, "__dataclass_fields__")

    def test_additional_properties_with_defaults(self):
        """Test additionalProperties with default values"""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": "anonymous"},
                "age": {"type": "integer", "default": 0},
            },
            "additionalProperties": True,
        }

        Type = json_schema_to_type(schema)
        validator = TypeAdapter(Type)

        # Test with extra fields and defaults
        result = validator.validate_python({"extra": "field"})
        assert result.name == "anonymous"  # type: ignore[attr-defined]
        assert result.age == 0  # type: ignore[attr-defined]
        assert result.extra == "field"  # type: ignore[attr-defined]

    def test_additional_properties_type_consistency(self):
        """Test that the same schema always returns the same type"""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": True,
        }

        Type1 = json_schema_to_type(schema)
        Type2 = json_schema_to_type(schema)

        # Should be the same cached class
        assert Type1 is Type2
        assert issubclass(Type1, BaseModel)


class TestFieldsWithDefaults:
    """Test suite for fields with default values not being made nullable."""

    def test_field_with_default_preserves_type(self):
        """Test that fields with defaults preserve their original type."""
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean", "default": False}},
        }

        generated_type = json_schema_to_type(schema)
        regenerated_schema = TypeAdapter(generated_type).json_schema()

        assert regenerated_schema["properties"]["flag"]["type"] == "boolean"

    def test_field_with_default_not_nullable(self):
        """Test that fields with defaults are not made nullable."""
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean", "default": False}},
        }

        generated_type = json_schema_to_type(schema)
        regenerated_schema = TypeAdapter(generated_type).json_schema()

        flag_prop = regenerated_schema["properties"]["flag"]
        assert "anyOf" not in flag_prop

    def test_field_with_default_uses_default(self):
        """Test that fields with defaults use their default values."""
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean", "default": False}},
        }

        generated_type = json_schema_to_type(schema)
        validator = TypeAdapter(generated_type)
        result = validator.validate_python({})
        assert result.flag is False  # type: ignore[attr-defined]

    def test_field_with_default_accepts_explicit_value(self):
        """Test that fields with defaults accept explicit values."""
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean", "default": False}},
        }

        generated_type = json_schema_to_type(schema)
        validator = TypeAdapter(generated_type)
        result = validator.validate_python({"flag": True})
        assert result.flag is True  # type: ignore[attr-defined]
