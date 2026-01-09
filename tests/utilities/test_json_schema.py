from fastmcp.utilities.json_schema import (
    _prune_param,
    compress_schema,
    dereference_refs,
    resolve_root_ref,
)


class TestPruneParam:
    """Tests for the _prune_param function."""

    def test_nonexistent(self):
        """Test pruning a parameter that doesn't exist."""
        schema = {"properties": {"foo": {"type": "string"}}}
        result = _prune_param(schema, "bar")
        assert result == schema  # Schema should be unchanged

    def test_exists(self):
        """Test pruning a parameter that exists."""
        schema = {"properties": {"foo": {"type": "string"}, "bar": {"type": "integer"}}}
        result = _prune_param(schema, "bar")
        assert result["properties"] == {"foo": {"type": "string"}}

    def test_last_property(self):
        """Test pruning the only/last parameter, should leave empty properties object."""
        schema = {"properties": {"foo": {"type": "string"}}}
        result = _prune_param(schema, "foo")
        assert "properties" in result
        assert result["properties"] == {}

    def test_from_required(self):
        """Test pruning a parameter that's in the required list."""
        schema = {
            "properties": {"foo": {"type": "string"}, "bar": {"type": "integer"}},
            "required": ["foo", "bar"],
        }
        result = _prune_param(schema, "bar")
        assert result["required"] == ["foo"]

    def test_last_required(self):
        """Test pruning the last required parameter, should remove required field."""
        schema = {
            "properties": {"foo": {"type": "string"}, "bar": {"type": "integer"}},
            "required": ["foo"],
        }
        result = _prune_param(schema, "foo")
        assert "required" not in result


class TestDereferenceRefs:
    """Tests for the dereference_refs function."""

    def test_dereferences_simple_ref(self):
        """Test that simple $ref is dereferenced."""
        schema = {
            "properties": {
                "foo": {"$ref": "#/$defs/foo_def"},
            },
            "$defs": {
                "foo_def": {"type": "string"},
            },
        }
        result = dereference_refs(schema)

        # $ref should be inlined
        assert result["properties"]["foo"] == {"type": "string"}
        # $defs should be removed
        assert "$defs" not in result

    def test_dereferences_nested_refs(self):
        """Test that nested $refs are dereferenced."""
        schema = {
            "properties": {
                "foo": {"$ref": "#/$defs/foo_def"},
            },
            "$defs": {
                "foo_def": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/nested_def"}},
                },
                "nested_def": {"type": "string"},
            },
        }
        result = dereference_refs(schema)

        # All refs should be inlined
        assert result["properties"]["foo"]["properties"]["nested"] == {"type": "string"}
        # $defs should be removed
        assert "$defs" not in result

    def test_falls_back_for_circular_refs(self):
        """Test that circular references fall back to resolve_root_ref."""
        schema = {
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/Node"},
                        }
                    },
                }
            },
            "$ref": "#/$defs/Node",
        }
        result = dereference_refs(schema)

        # Should fall back to resolve_root_ref behavior
        # Root should be resolved but nested refs preserved
        assert result.get("type") == "object"
        assert "$defs" in result  # $defs preserved for circular refs

    def test_preserves_sibling_keywords(self):
        """Test that sibling keywords (default, description) are preserved.

        Pydantic places description, default, examples as siblings to $ref.
        These should not be lost during dereferencing.
        """
        schema = {
            "$defs": {
                "Status": {"type": "string", "enum": ["active", "inactive"]},
            },
            "properties": {
                "status": {
                    "$ref": "#/$defs/Status",
                    "default": "active",
                    "description": "The user status",
                },
            },
            "type": "object",
        }
        result = dereference_refs(schema)

        # $ref should be inlined with siblings preserved
        status = result["properties"]["status"]
        assert status["type"] == "string"
        assert status["enum"] == ["active", "inactive"]
        assert status["default"] == "active"
        assert status["description"] == "The user status"
        # $defs should be removed
        assert "$defs" not in result

    def test_preserves_siblings_in_lists(self):
        """Test that siblings are preserved for $refs inside lists (allOf, anyOf, etc)."""
        schema = {
            "$defs": {
                "StringType": {"type": "string"},
                "IntType": {"type": "integer"},
            },
            "properties": {
                "field": {
                    "anyOf": [
                        {"$ref": "#/$defs/StringType", "description": "As string"},
                        {"$ref": "#/$defs/IntType", "description": "As integer"},
                    ]
                },
            },
        }
        result = dereference_refs(schema)

        # Both items in anyOf should have their siblings preserved
        any_of = result["properties"]["field"]["anyOf"]
        assert any_of[0]["type"] == "string"
        assert any_of[0]["description"] == "As string"
        assert any_of[1]["type"] == "integer"
        assert any_of[1]["description"] == "As integer"
        assert "$defs" not in result

    def test_preserves_nested_siblings(self):
        """Test that siblings on nested $refs are preserved."""
        schema = {
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {
                        "country": {"$ref": "#/$defs/Country", "default": "US"},
                    },
                },
                "Country": {"type": "string", "enum": ["US", "UK", "CA"]},
            },
            "properties": {
                "home_address": {"$ref": "#/$defs/Address"},
            },
        }
        result = dereference_refs(schema)

        # The nested $ref's sibling (default) should be preserved
        country = result["properties"]["home_address"]["properties"]["country"]
        assert country["type"] == "string"
        assert country["enum"] == ["US", "UK", "CA"]
        assert country["default"] == "US"
        assert "$defs" not in result


class TestCompressSchema:
    """Tests for the compress_schema function."""

    def test_dereferences_by_default(self):
        """Test that compress_schema dereferences $refs by default."""
        schema = {
            "properties": {
                "foo": {"$ref": "#/$defs/foo_def"},
            },
            "$defs": {
                "foo_def": {"type": "string"},
            },
        }
        result = compress_schema(schema)

        # $ref should be inlined
        assert result["properties"]["foo"] == {"type": "string"}
        # $defs should be removed
        assert "$defs" not in result

    def test_prune_params(self):
        """Test pruning parameters with compress_schema."""
        schema = {
            "properties": {
                "foo": {"type": "string"},
                "bar": {"type": "integer"},
                "baz": {"type": "boolean"},
            },
            "required": ["foo", "bar"],
        }
        result = compress_schema(schema, prune_params=["foo", "baz"])
        assert result["properties"] == {"bar": {"type": "integer"}}
        assert result["required"] == ["bar"]

    def test_pruning_additional_properties(self):
        """Test pruning additionalProperties when False."""
        schema = {
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": False,
        }
        result = compress_schema(schema)
        assert "additionalProperties" not in result

    def test_disable_pruning_additional_properties(self):
        """Test disabling pruning of additionalProperties."""
        schema = {
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": False,
        }
        result = compress_schema(schema, prune_additional_properties=False)
        assert "additionalProperties" in result
        assert result["additionalProperties"] is False

    def test_combined_operations(self):
        """Test all pruning operations together."""
        schema = {
            "type": "object",
            "properties": {
                "keep": {"type": "string"},
                "remove": {"$ref": "#/$defs/remove_def"},
            },
            "required": ["keep", "remove"],
            "additionalProperties": False,
            "$defs": {
                "remove_def": {"type": "string"},
                "unused_def": {"type": "number"},
            },
        }
        result = compress_schema(schema, prune_params=["remove"])
        # Check that parameter was removed
        assert "remove" not in result["properties"]
        # Check that required list was updated
        assert result["required"] == ["keep"]
        # Check that $defs was removed (dereferenced)
        assert "$defs" not in result
        # Check that additionalProperties was removed
        assert "additionalProperties" not in result

    def test_prune_titles(self):
        """Test pruning title fields."""
        schema = {
            "title": "Root Schema",
            "type": "object",
            "properties": {
                "foo": {"title": "Foo Property", "type": "string"},
                "bar": {
                    "title": "Bar Property",
                    "type": "object",
                    "properties": {
                        "nested": {"title": "Nested Property", "type": "string"}
                    },
                },
            },
        }
        result = compress_schema(schema, prune_titles=True)
        assert "title" not in result
        assert "title" not in result["properties"]["foo"]
        assert "title" not in result["properties"]["bar"]
        assert "title" not in result["properties"]["bar"]["properties"]["nested"]

    def test_prune_nested_additional_properties(self):
        """Test pruning additionalProperties: false at all levels."""
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "foo": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "nested": {
                            "type": "object",
                            "additionalProperties": False,
                        }
                    },
                },
            },
        }
        result = compress_schema(schema)
        assert "additionalProperties" not in result
        assert "additionalProperties" not in result["properties"]["foo"]
        assert (
            "additionalProperties"
            not in result["properties"]["foo"]["properties"]["nested"]
        )

    def test_title_pruning_preserves_parameter_named_title(self):
        """Test that a parameter named 'title' is not removed during title pruning.

        This is a critical edge case - we want to remove title metadata but preserve
        actual parameters that happen to be named 'title'.
        """
        from typing import Annotated

        from pydantic import Field, TypeAdapter

        def greet(
            name: Annotated[str, Field(description="The name to greet")],
            title: Annotated[str, Field(description="Optional title", default="")],
        ) -> str:
            """A greeting function."""
            return f"Hello {title} {name}"

        adapter = TypeAdapter(greet)
        schema = adapter.json_schema()

        # Compress with title pruning
        compressed = compress_schema(schema, prune_titles=True)

        # The 'title' parameter should be preserved
        assert "title" in compressed["properties"]
        assert compressed["properties"]["title"]["description"] == "Optional title"
        assert compressed["properties"]["title"]["default"] == ""

        # But title metadata should be removed
        assert "title" not in compressed["properties"]["name"]
        assert "title" not in compressed["properties"]["title"]

    def test_title_pruning_with_nested_properties(self):
        """Test that nested property structures are handled correctly."""
        schema = {
            "type": "object",
            "title": "OuterObject",
            "properties": {
                "title": {  # This is a property named "title", not metadata
                    "type": "object",
                    "title": "TitleObject",  # This is metadata
                    "properties": {
                        "subtitle": {
                            "type": "string",
                            "title": "SubTitle",  # This is metadata
                        }
                    },
                },
                "normal_field": {
                    "type": "string",
                    "title": "NormalField",  # This is metadata
                },
            },
        }

        compressed = compress_schema(schema, prune_titles=True)

        # Root title should be removed
        assert "title" not in compressed

        # The property named "title" should be preserved
        assert "title" in compressed["properties"]

        # But its metadata title should be removed
        assert "title" not in compressed["properties"]["title"]

        # Nested metadata titles should be removed
        assert (
            "title" not in compressed["properties"]["title"]["properties"]["subtitle"]
        )
        assert "title" not in compressed["properties"]["normal_field"]


class TestResolveRootRef:
    """Tests for the resolve_root_ref function.

    This function resolves $ref at root level to meet MCP spec requirements.
    MCP specification requires outputSchema to have "type": "object" at root.
    """

    def test_resolves_simple_root_ref(self):
        """Test that simple $ref at root is resolved."""
        schema = {
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["id"],
                }
            },
            "$ref": "#/$defs/Node",
        }
        result = resolve_root_ref(schema)

        # Should have type: object at root now
        assert result.get("type") == "object"
        assert "properties" in result
        assert "id" in result["properties"]
        assert "name" in result["properties"]
        # Should still have $defs for nested references
        assert "$defs" in result
        # Should NOT have $ref at root
        assert "$ref" not in result

    def test_resolves_self_referential_model(self):
        """Test resolving schema for self-referential models like Issue."""
        # This is the exact schema Pydantic generates for self-referential models
        schema = {
            "$defs": {
                "Issue": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "dependencies": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/Issue"},
                        },
                        "dependents": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/Issue"},
                        },
                    },
                    "required": ["id", "title"],
                }
            },
            "$ref": "#/$defs/Issue",
        }
        result = resolve_root_ref(schema)

        # Should have type: object at root
        assert result.get("type") == "object"
        assert "properties" in result
        assert "id" in result["properties"]
        assert "dependencies" in result["properties"]
        # Nested $refs should still point to $defs
        assert result["properties"]["dependencies"]["items"]["$ref"] == "#/$defs/Issue"
        # Should have $defs preserved for nested references
        assert "$defs" in result
        assert "Issue" in result["$defs"]

    def test_does_not_modify_schema_with_type_at_root(self):
        """Test that schemas already having type at root are not modified."""
        schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "$defs": {"SomeType": {"type": "string"}},
            "$ref": "#/$defs/SomeType",  # This would be unusual but possible
        }
        result = resolve_root_ref(schema)

        # Schema should be unchanged (returned as-is)
        assert result is schema

    def test_does_not_modify_schema_without_ref(self):
        """Test that schemas without $ref are not modified."""
        schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }
        result = resolve_root_ref(schema)

        assert result is schema

    def test_does_not_modify_schema_without_defs(self):
        """Test that schemas with $ref but without $defs are not modified."""
        schema = {
            "$ref": "#/$defs/Missing",
        }
        result = resolve_root_ref(schema)

        assert result is schema

    def test_does_not_modify_external_ref(self):
        """Test that external $refs (not pointing to $defs) are not resolved."""
        schema = {
            "$defs": {"Node": {"type": "object"}},
            "$ref": "https://example.com/schema.json#/definitions/Node",
        }
        result = resolve_root_ref(schema)

        assert result is schema

    def test_preserves_all_defs_for_nested_references(self):
        """Test that $defs are preserved even if multiple definitions exist."""
        schema = {
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "child": {"$ref": "#/$defs/ChildNode"},
                    },
                },
                "ChildNode": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            },
            "$ref": "#/$defs/Node",
        }
        result = resolve_root_ref(schema)

        # Both defs should be preserved
        assert "$defs" in result
        assert "Node" in result["$defs"]
        assert "ChildNode" in result["$defs"]

    def test_handles_missing_def_gracefully(self):
        """Test that missing definition in $defs doesn't cause error."""
        schema = {
            "$defs": {"OtherType": {"type": "string"}},
            "$ref": "#/$defs/Missing",
        }
        result = resolve_root_ref(schema)

        # Should return original schema unchanged
        assert result is schema
