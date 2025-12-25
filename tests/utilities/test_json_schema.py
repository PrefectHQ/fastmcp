from fastmcp.utilities.json_schema import (
    _prune_param,
    compress_schema,
    resolve_root_ref,
)

# Wrapper for backward compatibility with tests


def _prune_additional_properties(schema):
    """Wrapper for compress_schema that only prunes additionalProperties: false."""
    return compress_schema(
        schema, prune_defs=False, prune_additional_properties=True, prune_titles=False
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


class TestPruneUnusedDefs:
    """Tests for unused definition pruning (via compress_schema)."""

    def test_removes_unreferenced_defs(self):
        """Test that unreferenced definitions are removed."""
        schema = {
            "properties": {
                "foo": {"$ref": "#/$defs/foo_def"},
            },
            "$defs": {
                "foo_def": {"type": "string"},
                "unused_def": {"type": "integer"},
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )

        assert "foo_def" in result["$defs"]
        assert "unused_def" not in result["$defs"]

    def test_nested_references_kept(self):
        """Test that definitions referenced via nesting are kept."""
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
                "unused_def": {"type": "integer"},
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )
        assert "foo_def" in result["$defs"]
        assert "nested_def" in result["$defs"]
        assert "unused_def" not in result["$defs"]

    def test_nested_references_removed(self):
        """Test that definitions referenced via nesting in unused defs are removed."""
        schema = {
            "properties": {},
            "$defs": {
                "foo_def": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/nested_def"}},
                },
                "nested_def": {"type": "string"},
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )
        assert "$defs" not in result

    def test_nested_references_with_recursion_kept(self):
        """Test that definitions with recursion referenced via nesting are kept."""
        schema = {
            "properties": {
                "foo": {"$ref": "#/$defs/foo_def"},
            },
            "$defs": {
                "foo_def": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/foo_def"}},
                },
                "unused_def": {"type": "integer"},
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )
        assert "foo_def" in result["$defs"]
        assert "unused_def" not in result["$defs"]

    def test_nested_references_with_recursion_removed(self):
        """Test that definitions with recursion referenced via nesting in unused defs are removed."""
        schema = {
            "properties": {},
            "$defs": {
                "foo_def": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/foo_def"}},
                },
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )
        assert "$defs" not in result

    def test_multiple_nested_references_with_recursion_kept(self):
        """Test that definitions with multiple levels of recursion referenced via nesting are kept."""
        schema = {
            "properties": {
                "foo": {"$ref": "#/$defs/foo_def"},
            },
            "$defs": {
                "foo_def": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/nested_def"}},
                },
                "nested_def": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/foo_def"}},
                },
                "unused_def": {"type": "integer"},
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )
        assert "foo_def" in result["$defs"]
        assert "nested_def" in result["$defs"]
        assert "unused_def" not in result["$defs"]

    def test_multiple_nested_references_with_recursion_removed(self):
        """Test that definitions with multiple levels of recursion referenced via nesting in unused defs are removed."""
        schema = {
            "properties": {},
            "$defs": {
                "foo_def": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/nested_def"}},
                },
                "nested_def": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/foo_def"}},
                },
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )
        assert "$defs" not in result

    def test_array_references_kept(self):
        """Test that definitions referenced in array items are kept."""
        schema = {
            "properties": {
                "items": {"type": "array", "items": {"$ref": "#/$defs/item_def"}},
            },
            "$defs": {
                "item_def": {"type": "string"},
                "unused_def": {"type": "integer"},
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )
        assert "item_def" in result["$defs"]
        assert "unused_def" not in result["$defs"]

    def test_removes_defs_field_when_empty(self):
        """Test that $defs field is removed when all definitions are unused."""
        schema = {
            "properties": {
                "foo": {"type": "string"},
            },
            "$defs": {
                "unused_def": {"type": "integer"},
            },
        }
        result = compress_schema(
            schema,
            prune_defs=True,
            prune_additional_properties=False,
            prune_titles=False,
        )
        assert "$defs" not in result


class TestPruneAdditionalProperties:
    """Tests for the _prune_additional_properties function."""

    def test_removes_when_false(self):
        """Test that additionalProperties is removed when it's false."""
        schema = {
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": False,
        }
        result = _prune_additional_properties(schema)
        assert "additionalProperties" not in result

    def test_keeps_when_true(self):
        """Test that additionalProperties is kept when it's true."""
        schema = {
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": True,
        }
        result = _prune_additional_properties(schema)
        assert "additionalProperties" in result
        assert result["additionalProperties"] is True

    def test_keeps_when_object(self):
        """Test that additionalProperties is kept when it's an object schema."""
        schema = {
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": {"type": "string"},
        }
        result = _prune_additional_properties(schema)
        assert "additionalProperties" in result
        assert result["additionalProperties"] == {"type": "string"}


class TestCompressSchema:
    """Tests for the compress_schema function."""

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

    def test_prune_defs(self):
        """Test pruning unused definitions with compress_schema."""
        schema = {
            "properties": {
                "foo": {"$ref": "#/$defs/foo_def"},
                "bar": {"type": "integer"},
            },
            "$defs": {
                "foo_def": {"type": "string"},
                "unused_def": {"type": "number"},
            },
        }
        result = compress_schema(schema)
        assert "foo_def" in result["$defs"]
        assert "unused_def" not in result["$defs"]

    def test_disable_prune_defs(self):
        """Test disabling pruning of unused definitions."""
        schema = {
            "properties": {
                "foo": {"$ref": "#/$defs/foo_def"},
                "bar": {"type": "integer"},
            },
            "$defs": {
                "foo_def": {"type": "string"},
                "unused_def": {"type": "number"},
            },
        }
        result = compress_schema(schema, prune_defs=False)
        assert "foo_def" in result["$defs"]
        assert "unused_def" in result["$defs"]

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
        # Check that unused definitions were removed
        assert "$defs" not in result  # Both defs should be gone
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
