"""Recursive objects with the same display name must retain their own fields."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

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


@pytest.mark.parametrize("model", [False, True], ids=["dataclass", "pydantic"])
@pytest.mark.parametrize("titles", [False, True], ids=["untitled", "same-title"])
def test_mutual_recursion_keeps_distinct_types(model: bool, titles: bool) -> None:
    person: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "pet": {"anyOf": [{"$ref": "#/$defs/Pet"}, {"type": "null"}]},
        },
        "required": ["name"],
    }
    pet: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "owner": {"anyOf": [{"$ref": "#/$defs/Person"}, {"type": "null"}]},
        },
        "required": ["name"],
    }
    for definition in (person, pet):
        if model:
            definition["additionalProperties"] = True
        if titles:
            definition["title"] = "Record"
    schema = {
        "type": "object",
        "properties": {"person": {"$ref": "#/$defs/Person"}},
        "$defs": {"Person": person, "Pet": pet},
    }
    original = deepcopy(schema)
    generated = json_schema_type.json_schema_to_type(schema)
    result = TypeAdapter[Any](generated).validate_python(
        {
            "person": {
                "name": "Ann",
                "pet": {
                    "name": "Rex",
                    "owner": {"name": "Bob", "pet": {"name": "Max"}},
                },
            }
        }
    )

    assert result.person.pet.owner.pet.name == "Max"
    assert result.person.pet.owner.pet.owner is None
    assert type(result.person.pet.owner) is type(result.person)
    assert type(result.person.pet.owner.pet) is type(result.person.pet)
    assert type(result.person).__name__ == ("Record" if titles else "Root")
    assert schema == original
    assert json_schema_type.json_schema_to_type(schema) is generated


class HydrationPerson(BaseModel):
    name: str
    pet: HydrationPet | None = None


class HydrationPet(BaseModel):
    name: str
    owner: HydrationPerson | None = None


HydrationPerson.model_rebuild()
HydrationPet.model_rebuild()


async def test_client_hydrates_mutually_recursive_models() -> None:
    server = FastMCP()

    @server.tool
    def get_person() -> HydrationPerson:
        return HydrationPerson(
            name="Ann",
            pet=HydrationPet(
                name="Rex",
                owner=HydrationPerson(name="Bob", pet=HydrationPet(name="Max")),
            ),
        )

    async with Client(server) as client:
        first = await client.call_tool("get_person", {})
        second = await client.call_tool("get_person", {})

    for result in (first, second):
        assert result.structured_content == {
            "name": "Ann",
            "pet": {
                "name": "Rex",
                "owner": {"name": "Bob", "pet": {"name": "Max", "owner": None}},
            },
        }
        assert result.data.pet.owner.pet.name == "Max"
        assert result.data.pet.owner.pet.owner is None
        assert type(result.data.pet.owner.pet) is type(result.data.pet)
    assert type(first.data) is type(second.data)
