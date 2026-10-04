"""Generated classes must retain the root context for local references."""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import TypeAdapter

from fastmcp import Client, FastMCP
from fastmcp.utilities import json_schema_type


@pytest.fixture(autouse=True)
def isolated_class_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        json_schema_type,
        "_classes",
        json_schema_type._LRUCache(max_entries=5000, max_weight=4_000_000),
    )
    monkeypatch.setattr(
        json_schema_type,
        "_adapters",
        json_schema_type._LRUCache(max_entries=1000, max_weight=4_000_000),
    )


def make_schema(value_type: str, model: bool) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "object",
        "title": "CacheContextRecord",
        "properties": {"value": {"$ref": "#/$defs/Value"}},
        "required": ["value"],
    }
    root: dict[str, Any] = {
        "type": "object",
        "properties": {"record": record},
        "required": ["record"],
        "$defs": {"Value": {"type": value_type}},
    }
    if model:
        record["additionalProperties"] = True
        root["additionalProperties"] = True
    return root


@pytest.mark.parametrize("model", [False, True], ids=["dataclass", "pydantic"])
def test_root_reference_context(model: bool) -> None:
    order = ["integer", "string"]
    adapters = {}
    for value_type in order:
        adapter = TypeAdapter[Any](
            json_schema_type.json_schema_to_type(make_schema(value_type, model))
        )
        adapters[value_type] = adapter
        value = 1 if value_type == "integer" else "123"
        result = adapter.validate_python({"record": {"value": value}})
        assert result.record.value == value
        assert type(result.record.value) is type(value)
    # Constructing the second root must not change the first adapter either.
    result = adapters["integer"].validate_python({"record": {"value": 1}})
    assert type(result.record.value) is int


@pytest.mark.parametrize("model", [False, True], ids=["dataclass", "pydantic"])
def test_identical_root_reuses_class(model: bool) -> None:
    schema = make_schema("string", model)
    assert json_schema_type.json_schema_to_type(
        schema
    ) is json_schema_type.json_schema_to_type(deepcopy(schema))


@pytest.mark.parametrize("model", [False, True], ids=["dataclass", "pydantic"])
def test_changed_root_is_not_reused(model: bool) -> None:
    schema = make_schema("integer", model)
    integer_type = json_schema_type.json_schema_to_type(schema)
    schema["$defs"]["Value"]["type"] = "string"
    string_type = json_schema_type.json_schema_to_type(schema)
    assert integer_type is not string_type
    result = TypeAdapter[Any](string_type).validate_python({"record": {"value": "123"}})
    assert result.record.value == "123"
    assert type(result.record.value) is str


@pytest.mark.parametrize("model", [False, True], ids=["dataclass", "pydantic"])
def test_transitive_references_use_root_context(model: bool) -> None:
    for value_type, value in [("integer", 1), ("string", "123")]:
        schema = make_schema(value_type, model)
        schema["definitions"] = {"Target": schema["$defs"]["Value"]}
        schema["$defs"]["Value"] = {"$ref": "#/definitions/Target"}
        result = TypeAdapter[Any](
            json_schema_type.json_schema_to_type(schema)
        ).validate_python({"record": {"value": value}})
        assert result.record.value == value
        assert type(result.record.value) is type(value)


@pytest.mark.parametrize("model", [False, True], ids=["dataclass", "pydantic"])
def test_recursive_references_use_root_context(model: bool) -> None:
    for value_type, value in [("integer", 1), ("string", "123")]:
        schema = make_schema(value_type, model)
        record = schema["properties"]["record"]
        record["properties"]["next"] = {
            "anyOf": [{"$ref": "#/$defs/Record"}, {"type": "null"}]
        }
        schema["$defs"]["Record"] = record
        schema["properties"]["record"] = {"$ref": "#/$defs/Record"}
        result = TypeAdapter[Any](
            json_schema_type.json_schema_to_type(schema)
        ).validate_python({"record": {"value": value, "next": {"value": value}}})
        assert result.record.value == value
        assert type(result.record.value) is type(value)
        assert result.record.next.value == value
        assert type(result.record.next.value) is type(value)
        assert result.record.next.next is None


@pytest.mark.parametrize("container", ["array", "mapping", "anyOf"])
def test_reference_context_in_containers(container: str) -> None:
    for value_type, value in [("integer", 1), ("string", "123")]:
        schema = make_schema(value_type, False)
        record = schema["properties"]["record"]
        item = {"value": value}
        if container == "array":
            root = {"type": "array", "items": record}
            payload = [item]
        elif container == "mapping":
            root = {"type": "object", "additionalProperties": record}
            payload = {"item": item}
        else:
            root = {"anyOf": [record, {"type": "null"}]}
            payload = item
        root["$defs"] = schema["$defs"]
        result = TypeAdapter[Any](
            json_schema_type.json_schema_to_type(root)
        ).validate_python(payload)
        if container == "array":
            result = result[0]
        elif container == "mapping":
            result = result["item"]
        assert result.value == value
        assert type(result.value) is type(value)


@pytest.mark.parametrize("model", [False, True], ids=["dataclass", "pydantic"])
async def test_client_root_reference_context(model: bool) -> None:
    server = FastMCP("cache-context", dereference_schemas=False)

    @server.tool(output_schema=make_schema("integer", model))
    def integer_tool() -> dict[str, Any]:
        result = {"record": {"value": 1}}
        return result

    @server.tool(output_schema=make_schema("string", model))
    def string_tool() -> dict[str, Any]:
        result = {"record": {"value": "123"}}
        return result

    order = ["integer", "string"]
    async with Client(server) as client:
        for value_type in order:
            value = 1 if value_type == "integer" else "123"
            result = await client.call_tool(f"{value_type}_tool")
            expected = {"record": {"value": value}}
            assert result.structured_content == expected
            assert result.data is not None
            assert result.data.record.value == value
            assert type(result.data.record.value) is type(value)
