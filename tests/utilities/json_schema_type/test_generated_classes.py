"""Generated class names, field aliases, conversion limits, and the class cache."""

import gc
import json
import keyword
import weakref
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

TITLE = "Order Item (v2)"


def order_item_schema(children: dict[str, Any], title: str = TITLE) -> dict[str, Any]:
    return {
        "type": "object",
        "title": title,
        "properties": {"name": {"type": "string"}, "children": children},
    }


def nested_items_schema(title: str) -> dict[str, Any]:
    self_reference = {"$ref": "#", "title": title}
    return order_item_schema({"type": "array", "items": self_reference}, title)


NESTED_ITEMS_VALUE = {"name": "a", "children": [{"name": "b"}]}

AWKWARD_TITLES = {
    "parenthesized_version": "Order Item (v2)",
    "double_quotes": 'Item "A"',
    "apostrophe": "it's an item",
    "dot_and_slash": "a.b/c",
    "keyword": "class",
    "none_keyword": "None",
    "leading_digit": "3d model",
    "non_ascii": "名前",
    "empty": "",
    "very_long": "Item " * 60,
    "newline_and_tab": "Item\nOne\tTwo",
    "dunder": "__init__",
    "call_syntax": "Item(1)",
    "subscript_syntax": "list[str]",
    "boolean_words": "a or b",
    "conditional_words": "x if y else z",
    "lambda_words": "lambda: 0",
    "semicolon": "a; b",
    "hash_sign": "item#1",
    "trailing_backslash": "Item\\",
    "only_punctuation": "!!!",
}


SELF_REFERENCE = {"$ref": "#", "title": TITLE}
NESTED_ORDER_ITEMS = order_item_schema({"type": "array", "items": SELF_REFERENCE})
RESERVED_PROPERTIES = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "model_config": {"type": "string"},
        "_private": {"type": "string"},
        "name": {"type": "string"},
    },
}


class TestDescriptiveTitles:
    def test_title_becomes_valid_class_name(self):
        generated = json_schema_to_type(NESTED_ORDER_ITEMS)

        assert generated.__name__ == "Order_Item_v2"

    @pytest.mark.parametrize("title", AWKWARD_TITLES.values(), ids=AWKWARD_TITLES)
    def test_any_title_becomes_valid_class_name(self, title: str):
        generated = json_schema_to_type(nested_items_schema(title))

        assert generated.__name__.isidentifier()
        assert not keyword.iskeyword(generated.__name__)

    @pytest.mark.parametrize("title", AWKWARD_TITLES.values(), ids=AWKWARD_TITLES)
    def test_any_title_supports_nested_self_reference(self, title: str):
        generated = json_schema_to_type(nested_items_schema(title))

        item = TypeAdapter(generated).validate_python(NESTED_ITEMS_VALUE)

        child = item.children[0]  # ty: ignore[unresolved-attribute]
        assert isinstance(child, generated)
        assert child.name == "b"

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("class", "class_"),
            ("None", "None_"),
            ("3d model", "field_3d_model"),
            ("", "field"),
            ("!!!", "field"),
            ("list[str]", "list_str"),
            ("Item(1)", "Item_1"),
        ],
    )
    def test_title_class_names(self, title: str, expected: str):
        generated = json_schema_to_type(nested_items_schema(title))

        assert generated.__name__ == expected

    @pytest.mark.parametrize(
        "children,value",
        [
            ({"type": "array", "items": SELF_REFERENCE}, [{"name": "b"}]),
            ({"anyOf": [SELF_REFERENCE, {"type": "null"}]}, {"name": "b"}),
            (
                {"type": "object", "additionalProperties": SELF_REFERENCE},
                {"first": {"name": "b"}},
            ),
            ({"type": ["array", "null"], "items": SELF_REFERENCE}, [{"name": "b"}]),
        ],
        ids=["array", "union", "map", "type_list"],
    )
    def test_nested_self_reference_validates(
        self, children: dict[str, Any], value: Any
    ):
        generated = json_schema_to_type(order_item_schema(children))

        item = TypeAdapter(generated).validate_python({"name": "a", "children": value})

        nested = item.children  # ty: ignore[unresolved-attribute]
        child = nested["first"] if isinstance(nested, dict) else nested
        child = child[0] if isinstance(child, list) else child
        assert isinstance(child, generated)
        assert child.name == "b"


class TestClientSchemas:
    async def test_tool_output_with_self_reference(self):
        server = FastMCP("Orders")

        @server.tool(output_schema=NESTED_ORDER_ITEMS)
        def order() -> ToolResult:
            return ToolResult(
                structured_content={"name": "a", "children": [{"name": "b"}]}
            )

        async with Client(server) as client:
            result = await client.call_tool("order", {})

        assert type(result.data).__name__ == "Order_Item_v2"
        assert result.data.children[0].name == "b"

    @pytest.mark.parametrize("title", AWKWARD_TITLES.values(), ids=AWKWARD_TITLES)
    async def test_tool_output_with_any_title(self, title: str):
        server = FastMCP("Orders")

        @server.tool(output_schema=nested_items_schema(title))
        def order() -> ToolResult:
            return ToolResult(structured_content=NESTED_ITEMS_VALUE)

        async with Client(server) as client:
            result = await client.call_tool("order", {})

        assert type(result.data).__name__.isidentifier()
        assert not keyword.iskeyword(type(result.data).__name__)
        child = result.data.children[0]
        assert isinstance(child, type(result.data))
        assert child.name == "b"

    async def test_tool_output_with_reserved_property_names(self):
        server = FastMCP("Settings")
        content = {"model_config": "strict", "_private": "x", "name": "a"}

        @server.tool(output_schema=RESERVED_PROPERTIES)
        def settings() -> ToolResult:
            return ToolResult(structured_content=content)

        async with Client(server) as client:
            result = await client.call_tool("settings", {})

        assert isinstance(result.data, BaseModel)
        assert result.data.model_dump(by_alias=True) == content

    @pytest.mark.parametrize(
        "schema,content",
        [
            (NESTED_ORDER_ITEMS, {"name": "a", "children": [{"name": "b"}]}),
            (RESERVED_PROPERTIES, {"model_config": "strict", "name": "a"}),
        ],
        ids=["self_reference", "reserved_property_names"],
    )
    async def test_elicitation_schema(
        self, schema: dict[str, Any], content: dict[str, Any]
    ):
        server = FastMCP("Forms")
        validated: list[Any] = []

        @server.tool
        async def ask(ctx: Context) -> str:
            response = await ctx.session.elicit_form(
                message="Provide data",
                requested_schema=schema,
                related_request_id=ctx.request_id,
            )
            return response.action

        async def handler(
            message: str, response_type: Any, params: Any, context: Any
        ) -> ElicitResult[dict[str, Any]]:
            validated.append(TypeAdapter(response_type).validate_python(content))
            return ElicitResult(action="accept", content={"name": "a"})

        async with Client(server, mode="legacy", elicitation_handler=handler) as client:
            result = await client.call_tool("ask", {})

        assert result.data == "accept"
        assert len(validated) == 1

    @pytest.mark.parametrize("title", AWKWARD_TITLES.values(), ids=AWKWARD_TITLES)
    async def test_elicitation_schema_with_any_title(self, title: str):
        server = FastMCP("Forms")
        validated: list[Any] = []

        @server.tool
        async def ask(ctx: Context) -> str:
            response = await ctx.session.elicit_form(
                message="Provide data",
                requested_schema=nested_items_schema(title),
                related_request_id=ctx.request_id,
            )
            return response.action

        async def handler(
            message: str, response_type: Any, params: Any, context: Any
        ) -> ElicitResult[dict[str, Any]]:
            validated.append(
                TypeAdapter(response_type).validate_python(NESTED_ITEMS_VALUE)
            )
            return ElicitResult(action="accept", content={"name": "a"})

        async with Client(server, mode="legacy", elicitation_handler=handler) as client:
            result = await client.call_tool("ask", {})

        assert result.data == "accept"
        assert len(validated) == 1
        child = validated[0].children[0]
        assert isinstance(child, type(validated[0]))
        assert child.name == "b"


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
            "__class__",
            "__dict__",
            "__init__",
            "__module__",
            "__qualname__",
            "__doc__",
            "__eq__",
            "model_post_init",
            "model_validate",
            "model_copy",
            "_1",
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


DICTIONARY_DEFAULT = {"name": "text", "count": "int"}
ANNOTATIONS_PROPERTY = {
    "type": "object",
    "title": "Result",
    "additionalProperties": True,
    "properties": {
        "ok": {"type": "boolean"},
        "__annotations__": {"default": DICTIONARY_DEFAULT},
    },
}
ANNOTATIONS_CONTENTS = {
    "default_applied": {"ok": True},
    "value_provided": {"ok": True, "__annotations__": {"name": "other"}},
}
ANNOTATIONS_EXPECTED = {
    "default_applied": {"ok": True, "__annotations__": DICTIONARY_DEFAULT},
    "value_provided": {"ok": True, "__annotations__": {"name": "other"}},
}


class TestDictionaryDefaults:
    def test_dictionary_defaults_are_kept_as_field_data(self):
        generated = json_schema_to_type(ANNOTATIONS_PROPERTY)

        assert issubclass(generated, BaseModel)
        instance = generated.model_validate({"ok": True})
        assert instance.model_dump(by_alias=True) == {
            "ok": True,
            "__annotations__": DICTIONARY_DEFAULT,
        }

    def test_dictionary_defaults_leave_class_annotations_unchanged(self):
        generated = json_schema_to_type(ANNOTATIONS_PROPERTY)

        assert issubclass(generated, BaseModel)
        assert set(generated.__annotations__) == set(generated.model_fields)
        assert set(generated.__annotations__).isdisjoint(DICTIONARY_DEFAULT)

    @pytest.mark.parametrize("case", ANNOTATIONS_CONTENTS)
    async def test_dictionary_defaults_in_tool_results(self, case: str):
        server = FastMCP("Remote")

        @server.tool(output_schema=ANNOTATIONS_PROPERTY)
        def lookup() -> ToolResult:
            return ToolResult(structured_content=ANNOTATIONS_CONTENTS[case])

        async with Client(server) as client:
            result = await client.call_tool("lookup", {})

        assert isinstance(result.data, BaseModel)
        assert result.data.model_dump(by_alias=True) == ANNOTATIONS_EXPECTED[case]
        assert set(type(result.data).__annotations__) == set(
            type(result.data).model_fields
        )

    @pytest.mark.parametrize("case", ANNOTATIONS_CONTENTS)
    async def test_dictionary_defaults_in_elicitation_schemas(self, case: str):
        server = FastMCP("Forms")
        validated: list[BaseModel] = []

        @server.tool
        async def ask(ctx: Context) -> str:
            response = await ctx.session.elicit_form(
                message="Provide data",
                requested_schema=ANNOTATIONS_PROPERTY,
                related_request_id=ctx.request_id,
            )
            return response.action

        async def handler(
            message: str, response_type: Any, params: Any, context: Any
        ) -> ElicitResult[dict[str, Any]]:
            validated.append(
                TypeAdapter(response_type).validate_python(ANNOTATIONS_CONTENTS[case])
            )
            return ElicitResult(action="accept", content={"ok": True})

        async with Client(server, mode="legacy", elicitation_handler=handler) as client:
            result = await client.call_tool("ask", {})

        assert result.data == "accept"
        assert len(validated) == 1
        assert validated[0].model_dump(by_alias=True) == ANNOTATIONS_EXPECTED[case]
        assert set(type(validated[0]).__annotations__) == set(
            type(validated[0]).model_fields
        )


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


def wrapped_definition_schema(
    title: str, *, additional_properties: bool, description_size: int = 3_000
) -> dict[str, Any]:
    wrapper: dict[str, Any] = {
        "type": "object",
        "title": "Wrapper",
        "properties": {"child": {"$ref": "#/$defs/Large"}},
        "required": ["child"],
    }
    if additional_properties:
        wrapper["additionalProperties"] = True
    return {
        "type": "object",
        "title": title,
        "properties": {"wrapper": wrapper},
        "required": ["wrapper"],
        "$defs": {
            "Large": {
                "type": "object",
                "title": "Large",
                "description": "x" * description_size,
                "properties": {"x": {"type": "string"}},
            }
        },
    }


class TestConversionLimits:
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
    def test_failed_conversion_can_be_retried(self):
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

    def test_classes_from_one_conversion_are_charged_for_its_root_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        schema = wrapped_definition_schema("Charged", additional_properties=True)
        root_size = len(json.dumps(schema, sort_keys=True))
        monkeypatch.setattr(json_schema_type._classes, "max_weight", root_size * 3 // 2)
        json_schema_type._classes.clear()

        json_schema_to_type(schema)

        assert len(json_schema_type._classes) == 1

    @pytest.mark.parametrize("additional_properties", [False, True])
    def test_classes_referencing_a_large_definition_are_charged_for_it(
        self, monkeypatch: pytest.MonkeyPatch, additional_properties: bool
    ):
        monkeypatch.setattr(json_schema_type._classes, "max_weight", 2_000)
        monkeypatch.setattr(json_schema_type._adapters, "max_weight", 2_000)
        json_schema_type._classes.clear()
        json_schema_type._adapters.clear()
        large_classes: list[weakref.ref[type]] = []

        for i in range(4):
            schema = wrapped_definition_schema(
                f"Referencing{i}", additional_properties=additional_properties
            )
            value = json_schema_to_type_adapter(schema).validate_python(
                {"wrapper": {"child": {"x": "a"}}}
            )
            large_classes.append(weakref.ref(type(value.wrapper.child)))
            del value
        gc.collect()

        assert [ref() for ref in large_classes] == [None] * 4
        assert len(json_schema_type._classes) == 0
        assert len(json_schema_type._adapters) == 0
