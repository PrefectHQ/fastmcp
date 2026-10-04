"""Schemas from remote servers stay data, and converting them has bounded cost."""

import gc
import json
import weakref
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.tools import ToolResult
from fastmcp.utilities import json_schema_type
from fastmcp.utilities.json_schema_type import (
    json_schema_to_type,
    json_schema_to_type_adapter,
    safe_create_model,
)


def marker_expression(marker: Path) -> str:
    """A Python expression that creates `marker` if anything evaluates it."""
    return f"[__import__('pathlib').Path({str(marker)!r}).write_text('ran'), bool][1]"


def self_ref_title_schema(marker: Path) -> dict[str, Any]:
    return {
        "type": "object",
        "title": "Root",
        "properties": {
            "x": {
                "type": "array",
                "items": {"$ref": "#", "title": marker_expression(marker)},
            }
        },
    }


def annotations_property_schema(marker: Path) -> dict[str, Any]:
    return {
        "type": "object",
        "title": "Result",
        "additionalProperties": True,
        "properties": {
            "ok": {"type": "boolean"},
            "__annotations__": {"default": {"ok": marker_expression(marker)}},
        },
    }


SCHEMAS = {
    "self_ref_title": self_ref_title_schema,
    "annotations_property": annotations_property_schema,
}


def build_adapter(generated: Any) -> None:
    """Build a TypeAdapter the way the client does; inert names may not resolve."""
    try:
        TypeAdapter(generated)
    except (NameError, TypeError, ValueError):
        pass


class TestSchemaStringsAreInert:
    @pytest.mark.parametrize("kind", list(SCHEMAS))
    def test_direct_conversion(self, tmp_path: Path, kind: str):
        marker = tmp_path / "ran"
        build_adapter(json_schema_to_type(SCHEMAS[kind](marker)))
        assert not marker.exists()

    @pytest.mark.parametrize("location", ["array", "union", "map", "type_list"])
    def test_nested_self_reference_title(self, tmp_path: Path, location: str):
        marker = tmp_path / "ran"
        ref = {"$ref": "#", "title": marker_expression(marker)}
        nested = {
            "array": {"type": "array", "items": ref},
            "union": {"anyOf": [ref, {"type": "null"}]},
            "map": {"type": "object", "additionalProperties": ref},
            "type_list": {"type": ["array", "null"], "items": ref},
        }[location]
        schema = {"type": "object", "properties": {"x": nested}}
        build_adapter(json_schema_to_type(schema))
        assert not marker.exists()

    @pytest.mark.parametrize("kind", list(SCHEMAS))
    async def test_tool_output_schema(self, tmp_path: Path, kind: str):
        marker = tmp_path / "ran"
        server = FastMCP("Remote")

        @server.tool(output_schema=SCHEMAS[kind](marker))
        def lookup() -> ToolResult:
            return ToolResult(structured_content={"x": [], "ok": True})

        async with Client(server) as client:
            result = await client.call_tool("lookup", {})

        assert result.structured_content == {"x": [], "ok": True}
        assert not marker.exists()

    @pytest.mark.parametrize("kind", list(SCHEMAS))
    async def test_elicitation_schema(self, tmp_path: Path, kind: str):
        marker = tmp_path / "ran"
        server = FastMCP("Remote")
        handled: list[bool] = []

        @server.tool
        async def ask(ctx: Context) -> str:
            response = await ctx.session.elicit_form(
                message="Provide data",
                requestedSchema=SCHEMAS[kind](marker),
                related_request_id=ctx.request_id,
            )
            return response.action

        async def handler(
            message: str, response_type: Any, params: Any, context: Any
        ) -> ElicitResult[dict[str, Any]]:
            handled.append(True)
            build_adapter(response_type)
            return ElicitResult(action="accept", content={"x": [], "ok": True})

        async with Client(server, elicitation_handler=handler) as client:
            result = await client.call_tool("ask", {})

        assert handled == [True]
        assert result.data == "accept"
        assert not marker.exists()


class TestPropertyNamesAreFields:
    @pytest.mark.parametrize(
        "name",
        [
            "__annotations__",
            "__base__",
            "__config__",
            "__validators__",
            "__slots__",
            "_private",
            "model_config",
            "model_dump",
            "model_fields",
        ],
    )
    def test_reserved_names_become_aliased_fields(self, name: str):
        generated = json_schema_to_type(
            {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    name: {"type": "string", "default": "default"},
                    "ordinary": {"type": "integer"},
                },
                "required": ["ordinary"],
            }
        )

        assert issubclass(generated, BaseModel)
        assert generated.model_config.get("extra") == "allow"
        instance = generated.model_validate({name: "data", "ordinary": 3, "other": 1})
        assert instance.model_dump(by_alias=True) == {
            name: "data",
            "ordinary": 3,
            "other": 1,
        }
        defaulted = generated.model_validate({"ordinary": 3})
        assert defaulted.model_dump(by_alias=True)[name] == "default"

    @pytest.mark.parametrize("name", ["ordinary", "foo-bar", "json", "class"])
    def test_other_names_keep_their_field_name(self, name: str):
        generated = json_schema_to_type(
            {
                "type": "object",
                "additionalProperties": True,
                "properties": {name: {"type": "string"}},
            }
        )

        assert issubclass(generated, BaseModel)
        assert list(generated.model_fields) == [name]
        assert generated.model_validate({name: "v"}).model_dump() == {name: "v"}

    def test_renamed_field_does_not_collide_with_existing_property(self):
        generated = json_schema_to_type(
            {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "model_config": {"type": "string"},
                    "field_model_config": {"type": "integer"},
                },
            }
        )

        assert issubclass(generated, BaseModel)
        instance = generated.model_validate(
            {"model_config": "a", "field_model_config": 1}
        )
        assert instance.model_dump(by_alias=True) == {
            "model_config": "a",
            "field_model_config": 1,
        }

    def test_safe_create_model_keeps_field_metadata(self):
        model = safe_create_model(
            "Form",
            {"__base__": (str, Field(description="Required input"))},
        )

        field_info = next(iter(model.model_fields.values()))
        assert isinstance(field_info, FieldInfo)
        assert field_info.alias == "__base__"
        assert field_info.description == "Required input"
        assert field_info.is_required()
        with pytest.raises(ValidationError):
            model.model_validate({})
        assert model.model_validate({"__base__": "ok"}).model_dump(by_alias=True) == {
            "__base__": "ok"
        }


def fanout_schema(depth: int, fanout: int) -> dict[str, Any]:
    defs: dict[str, Any] = {"L0": {"type": "string"}}
    for i in range(1, depth + 1):
        defs[f"L{i}"] = {"anyOf": [{"$ref": f"#/$defs/L{i - 1}"}] * fanout}
    return {
        "type": "object",
        "title": "SharedGraph",
        "properties": {"x": {"$ref": f"#/$defs/L{depth}"}},
        "$defs": defs,
    }


def flat_chain_schema(length: int) -> dict[str, Any]:
    defs: dict[str, Any] = {
        f"L{i}": {"$ref": f"#/$defs/L{i + 1}"} for i in range(length)
    }
    defs[f"L{length}"] = {"type": "string"}
    return {
        "type": "object",
        "title": "FlatChain",
        "properties": {"x": {"$ref": "#/$defs/L0"}},
        "$defs": defs,
    }


class TestConversionCost:
    def test_shared_references_are_converted_once(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = 0
        original = json_schema_type._resolve_ref

        def counting_resolve(ref: str, schemas: Any) -> Any:
            nonlocal calls
            calls += 1
            return original(ref, schemas)

        monkeypatch.setattr(json_schema_type, "_resolve_ref", counting_resolve)
        generated = json_schema_to_type(fanout_schema(depth=8, fanout=3))

        assert TypeAdapter(generated).validate_python({"x": "ok"}).x == "ok"  # ty: ignore[unresolved-attribute]
        assert calls <= 9

    def test_long_reference_chain_is_rejected(self):
        with pytest.raises(ValueError, match="too deeply nested"):
            json_schema_to_type(flat_chain_schema(400))

    def test_repeated_type_lists_are_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            json_schema_type, "_MAX_CONVERSION_STEPS", 200, raising=False
        )
        schema: dict[str, Any] = {"type": "string"}
        for _ in range(8):
            schema = {"type": ["array", "array"], "items": schema}

        with pytest.raises(ValueError, match="too large"):
            json_schema_to_type(schema)

    def test_deeply_nested_models_still_convert(self):
        depth = 20
        defs: dict[str, Any] = {
            f"M{i}": {
                "type": "object",
                "properties": {
                    "child": {
                        "anyOf": [{"$ref": f"#/$defs/M{i + 1}"}, {"type": "null"}]
                    }
                },
            }
            for i in range(depth)
        }
        defs[f"M{depth}"] = {
            "type": "object",
            "properties": {"leaf": {"type": "string"}},
        }
        schema = {
            "type": "object",
            "title": "Deep",
            "properties": {"child": {"$ref": "#/$defs/M0"}},
            "$defs": defs,
        }
        value: dict[str, Any] = {"leaf": "ok"}
        for _ in range(depth):
            value = {"child": value}

        instance = TypeAdapter(json_schema_to_type(schema)).validate_python(
            {"child": value}
        )

        node: Any = instance.child  # ty: ignore[unresolved-attribute]
        for _ in range(depth):
            node = node.child
        assert node.leaf == "ok"


class TestClassCache:
    def test_failed_conversion_leaves_no_placeholder(self):
        schema = {"type": "object", "title": "RetryInvalid", "properties": {"x": 123}}

        for _ in range(2):
            with pytest.raises(AttributeError):
                json_schema_to_type(schema)

    def test_rejected_conversion_leaves_cache_unchanged(self):
        before = len(json_schema_type._classes)

        with pytest.raises(ValueError):
            json_schema_to_type(flat_chain_schema(400))

        assert len(json_schema_type._classes) == before

    def test_cache_is_bounded(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(json_schema_type._classes, "max_entries", 3)
        json_schema_type._classes.clear()

        for i in range(5):
            json_schema_to_type(
                {
                    "type": "object",
                    "title": f"Unique{i}",
                    "properties": {"x": {"type": "string"}},
                }
            )

        assert len(json_schema_type._classes) == 3

    def test_size_budget_evicts_large_schemas(self, monkeypatch: pytest.MonkeyPatch):
        def schema(i: int) -> dict[str, Any]:
            return {
                "type": "object",
                "title": f"Large{i}",
                "description": "x" * 1000,
                "properties": {"x": {"type": "string"}},
            }

        weight = len(json.dumps(schema(0), sort_keys=True))
        monkeypatch.setattr(json_schema_type._classes, "max_weight", weight * 3 // 2)
        json_schema_type._classes.clear()

        json_schema_to_type(schema(0))
        json_schema_to_type(schema(1))

        assert len(json_schema_type._classes) == 1

    def test_schema_over_size_budget_is_not_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(json_schema_type._classes, "max_weight", 10)
        json_schema_type._classes.clear()

        json_schema_to_type(
            {"type": "object", "title": "Big", "properties": {"x": {"type": "string"}}}
        )

        assert len(json_schema_type._classes) == 0

    def test_evicted_classes_are_released(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(json_schema_type._classes, "max_entries", 2)
        monkeypatch.setattr(json_schema_type._adapters, "max_entries", 2)
        json_schema_type._classes.clear()
        json_schema_type._adapters.clear()
        refs: list[weakref.ref[type]] = []

        for i in range(4):
            schema = {
                "type": "object",
                "title": f"Released{i}",
                "properties": {"x": {"type": "string"}},
            }
            value = json_schema_to_type_adapter(schema).validate_python({"x": "a"})
            refs.append(weakref.ref(type(value)))
            del value
        gc.collect()

        assert [ref() is not None for ref in refs] == [False, False, True, True]

    def test_nested_classes_depend_on_root_definitions(self):
        def schema(value_type: str) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "nested": {
                        "type": "object",
                        "properties": {"value": {"$ref": "#/$defs/Value"}},
                    }
                },
                "$defs": {"Value": {"type": value_type}},
            }

        as_string = TypeAdapter(json_schema_to_type(schema("string")))
        as_integer = TypeAdapter(json_schema_to_type(schema("integer")))

        string_value = as_string.validate_python({"nested": {"value": "a"}})
        integer_value = as_integer.validate_python({"nested": {"value": 3}})
        assert string_value.nested.value == "a"  # ty: ignore[unresolved-attribute]
        assert integer_value.nested.value == 3  # ty: ignore[unresolved-attribute]
