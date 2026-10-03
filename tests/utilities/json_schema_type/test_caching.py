"""Generated classes must retain the root context for local references."""

import json
from copy import deepcopy
from typing import Any

import pytest
from mcp_types import TextContent
from pydantic import TypeAdapter

from fastmcp import Client, FastMCP
from fastmcp.utilities import json_schema_type


@pytest.fixture(autouse=True)
def isolated_class_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(json_schema_type, "_classes", {})


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
@pytest.mark.parametrize("first_type", ["integer", "string"])
@pytest.mark.parametrize("text", ["abc", "123"])
def test_root_reference_context(model: bool, first_type: str, text: str) -> None:
    order = [first_type, "string" if first_type == "integer" else "integer"]
    adapters = {}
    for value_type in order:
        adapter = TypeAdapter[Any](
            json_schema_type.json_schema_to_type(make_schema(value_type, model))
        )
        adapters[value_type] = adapter
        value = 1 if value_type == "integer" else text
        result = adapter.validate_python({"record": {"value": value}})
        assert result.record.value == value
        assert type(result.record.value) is type(value)
    # Constructing the second root must not change the first adapter either.
    first_value = 1 if first_type == "integer" else text
    result = adapters[first_type].validate_python({"record": {"value": first_value}})
    assert type(result.record.value) is type(first_value)


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
@pytest.mark.parametrize("first_type", ["integer", "string"])
@pytest.mark.parametrize("text", ["abc", "123"])
@pytest.mark.parametrize("dereference", [False, True], ids=["refs", "inlined"])
async def test_client_root_reference_context(
    model: bool, first_type: str, text: str, dereference: bool
) -> None:
    server = FastMCP("cache-context", dereference_schemas=dereference)
    executed = []

    @server.tool(output_schema=make_schema("integer", model))
    def integer_tool() -> dict[str, Any]:
        result = {"record": {"value": 1}}
        executed.append(deepcopy(result))
        return result

    @server.tool(output_schema=make_schema("string", model))
    def string_tool() -> dict[str, Any]:
        result = {"record": {"value": text}}
        executed.append(deepcopy(result))
        return result

    order = [first_type, "string" if first_type == "integer" else "integer"]
    async with Client(server) as client:
        tools = await client.list_tools()
        for tool in tools:
            assert tool.output_schema is not None
            prop = tool.output_schema["properties"]["record"]["properties"]["value"]
            assert ("$ref" in prop) is not dereference
        for value_type in order:
            value = 1 if value_type == "integer" else text
            result = await client.call_tool(f"{value_type}_tool")
            expected = {"record": {"value": value}}
            assert executed[-1] == expected
            assert result.structured_content == expected
            assert isinstance(result.content[0], TextContent)
            assert json.loads(result.content[0].text) == expected
            assert not result.is_error
            assert result.data is not None
            assert result.data.record.value == value
            assert type(result.data.record.value) is type(value)
