"""Shared composition members are collected once in their effective order."""

from typing import Any

import httpx2
import pytest
import yaml

import fastmcp.utilities.openapi.schemas as schemas
from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.schemas import _allof_members


def test_deep_composition_chain_retains_leaf_members():
    leaf = {"type": "object", "properties": {"name": {"type": "string"}}}
    definitions: dict[str, Any] = {"L0": leaf}
    for level in range(1, 251):
        definitions[f"L{level}"] = {
            "allOf": [{"$ref": f"#/components/schemas/L{level - 1}"}]
        }
    assert _allof_members({"$ref": "#/components/schemas/L250"}, definitions) == [leaf]


def test_shared_members_are_collected_once():
    leaf = {"type": "object", "properties": {"name": {"type": "string"}}}
    defs: dict[str, Any] = {"L0": leaf}
    for level in range(1, 9):
        defs[f"L{level}"] = {
            "allOf": [{"$ref": f"#/$defs/L{level - 1}"} for _ in range(3)]
        }
    members = _allof_members({"$ref": "#/$defs/L8"}, defs)
    assert sum(member is leaf for member in members) == 1


def test_repeated_members_keep_last_occurrence_order():
    first = {"properties": {"name": {"type": "string"}}}
    second = {"properties": {"name": {"type": "integer"}}}
    members = _allof_members(
        {
            "allOf": [
                {"$ref": "#/$defs/A"},
                {"$ref": "#/$defs/B"},
                {"$ref": "#/$defs/A"},
            ]
        },
        {"A": first, "B": second},
    )
    assert [m for m in members if "properties" in m] == [second, first]


async def test_shared_body_members_advertise_and_send_declared_fields():
    definitions: dict[str, Any] = {
        "L0": {"type": "object", "properties": {"name": {"type": "string"}}}
    }
    for level in range(1, 19):
        definitions[f"L{level}"] = {
            "allOf": [{"$ref": f"#/components/schemas/L{level - 1}"} for _ in range(3)]
        }
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "API", "version": "1"},
        "components": {"schemas": definitions},
        "paths": {
            "/items": {
                "post": {
                    "operationId": "create_item",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/L18",
                                    "properties": {"local": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    seen: list[httpx2.Request] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(
        base_url="https://api.example.com", transport=httpx2.MockTransport(capture)
    ) as http:
        server = FastMCP.from_openapi(spec, client=http)
        async with Client(server) as client:
            tool = (await client.list_tools())[0]
            assert set(tool.input_schema["properties"]) == {"name", "local"}
            await client.call_tool("create_item", {"name": "item", "local": "value"})
    assert seen[0].content == b'{"name":"item","local":"value"}'


def test_cyclic_members_keep_contextual_merge_order():
    first = {"properties": {"name": {"default": "A"}}}
    last = {"properties": {"name": {"default": "C"}}}
    definitions = {
        "A": {"allOf": [first, {"$ref": "#/$defs/C"}]},
        "C": {
            "allOf": [
                {"$ref": "#/$defs/A"},
                {"$ref": "#/$defs/C"},
                {"$ref": "#/$defs/A"},
                last,
            ]
        },
    }
    members = _allof_members(
        {"allOf": [{"$ref": "#/$defs/C"}, {"$ref": "#/$defs/A"}]},
        definitions,
    )
    properties = {}
    for member in members:
        properties.update(member.get("properties", {}))
    assert properties["name"]["default"] == "C"


def test_cached_members_respect_initial_resolving_context():
    leaf = {"properties": {"name": {"default": "value"}}}
    reference = {"$ref": "#/$defs/A"}
    assert _allof_members(reference, {"A": leaf}, {"A"}) == [reference]


def test_composition_dependency_budget(monkeypatch):
    monkeypatch.setattr(schemas, "_MAX_COMPOSITION_MEMBERS", 15)
    definitions: dict[str, Any] = {"L0": {"properties": {"name": {"type": "string"}}}}
    for i in range(1, 6):
        definitions[f"L{i}"] = {"$ref": f"#/$defs/L{i - 1}"}
    with pytest.raises(ValueError, match="Schema composition has too many"):
        _allof_members({"$ref": "#/$defs/L5"}, definitions)


def test_composition_visit_budget(monkeypatch):
    monkeypatch.setattr(schemas, "_MAX_COMPOSITION_MEMBERS", 15)
    leaf = {"properties": {"name": {"type": "string"}}}
    with pytest.raises(ValueError, match="too many visited members"):
        _allof_members({"allOf": [leaf] * 20}, {})


def test_cyclic_composition_budget(monkeypatch):
    monkeypatch.setattr(schemas, "_MAX_COMPOSITION_MEMBERS", 50)
    definitions = {
        "A": {"allOf": [{"$ref": "#/$defs/B"}] * 10},
        "B": {"allOf": [{"$ref": "#/$defs/A"}] * 10},
    }
    with pytest.raises(ValueError, match="Schema composition has too many"):
        _allof_members({"$ref": "#/$defs/A"}, definitions)


def test_repeated_collections_share_the_merge_budget(monkeypatch):
    monkeypatch.setattr(schemas, "_MAX_COMPOSITION_MEMBERS", 300)
    group = {"allOf": [{"properties": {str(i): {"type": "string"}}} for i in range(40)]}
    with pytest.raises(ValueError, match="too many merged members"):
        _allof_members({"allOf": [group] * 10}, {})


def test_aliased_cyclic_members_keep_reference_order():
    definitions = yaml.safe_load(
        """
A: &shared
  allOf:
    - properties:
        name:
          default: A
    - $ref: '#/$defs/B'
B:
  allOf:
    - properties:
        name:
          default: B
    - $ref: '#/$defs/C'
C: *shared
"""
    )
    assert definitions["A"] is definitions["C"]
    properties = {}
    for member in _allof_members({"$ref": "#/$defs/A"}, definitions):
        properties.update(member.get("properties", {}))
    assert properties["name"]["default"] == "A"
