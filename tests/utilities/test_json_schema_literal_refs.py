"""`$ref` keys inside instance data are literal values, not references.

`default`, `const`, `enum`, and `examples` hold JSON instances. A dict there
that happens to contain a `$ref` key is the value itself, so dereferencing must
neither strip it (as a remote reference) nor inline a definition in its place.
"""

from typing import Annotated, Any

import pytest
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.utilities.json_schema import dereference_refs

REMOTE = {"$ref": "https://example.com/customer.json"}
LOCAL = {"$ref": "#/$defs/Text"}


@pytest.mark.parametrize("literal", [REMOTE, LOCAL])
@pytest.mark.parametrize(
    "keyword,wrap",
    [
        ("default", lambda v: v),
        ("const", lambda v: v),
        ("enum", lambda v: [v]),
        ("examples", lambda v: [v]),
    ],
)
def test_literal_refs_in_instance_data_are_preserved(
    literal: dict[str, Any], keyword: str, wrap: Any
) -> None:
    schema = {
        "$defs": {"Text": {"type": "string"}},
        "type": "object",
        "properties": {
            "config": {"type": "object", keyword: wrap(literal)},
            "text": {"$ref": "#/$defs/Text"},
        },
    }

    result = dereference_refs(schema)

    assert result["properties"]["config"][keyword] == wrap(literal)
    assert result["properties"]["text"] == {"type": "string"}
    assert "$defs" not in result


def test_literal_refs_inside_an_inlined_definition_are_preserved() -> None:
    schema = {
        "$defs": {
            "Text": {"type": "string"},
            "Config": {"type": "object", "default": LOCAL},
        },
        "type": "object",
        "properties": {
            "a": {"$ref": "#/$defs/Config", "description": "first"},
            "b": {"$ref": "#/$defs/Config"},
        },
    }

    a, b = dereference_refs(schema)["properties"].values()

    assert a == {"type": "object", "default": LOCAL, "description": "first"}
    assert b == {"type": "object", "default": LOCAL}
    assert a["default"] is not b["default"]


def test_property_named_like_a_data_keyword_is_still_a_schema() -> None:
    schema = {
        "$defs": {"Text": {"type": "string"}},
        "type": "object",
        "properties": {
            "default": {"$ref": "#/$defs/Text"},
            "evil": {"$ref": "https://example.com/evil.json"},
        },
    }

    result = dereference_refs(schema)

    assert result["properties"] == {"default": {"type": "string"}, "evil": {}}


async def test_tool_advertises_its_literal_ref_default() -> None:
    server = FastMCP("literal-default")

    @server.tool
    def describe_schema(
        config: Annotated[dict[str, Any], Field(default=REMOTE)],
    ) -> dict[str, Any]:
        return config

    async with Client(server) as client:
        tool = (await client.list_tools())[0]
        result = await client.call_tool("describe_schema", {})

    assert tool.input_schema["properties"]["config"]["default"] == REMOTE
    assert result.data == REMOTE
