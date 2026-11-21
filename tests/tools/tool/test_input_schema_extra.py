"""Input schema customization is discovery metadata, not runtime validation."""

from collections.abc import Callable
from typing import Annotated, Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import Field

from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ValidationError
from fastmcp.server.providers.local_provider import LocalProvider
from fastmcp.tools import Tool, tool
from fastmcp.tools.function_tool import FunctionTool, ToolMeta
from fastmcp.tools.tool_transform import ArgTransform


@pytest.fixture
def schema_extra() -> dict[str, Any]:
    return {"anyOf": [{"required": ["team_id"]}, {"required": ["club_id"]}]}


@pytest.mark.parametrize(
    "registration",
    [
        "server",
        "provider",
        "standalone",
        "tool_factory",
        "function_factory",
        "metadata",
    ],
)
async def test_schema_extra_is_exposed_by_registration_paths(
    registration: str, schema_extra: dict[str, Any]
) -> None:
    mcp = FastMCP()

    def lookup_team(
        team_id: str | None = None, club_id: str | None = None
    ) -> dict[str, str | None]:
        return {"team_id": team_id, "club_id": club_id}

    inferred = Tool.from_function(lookup_team).parameters
    if registration == "server":
        mcp.tool("lookup", input_schema_extra=schema_extra)(lookup_team)
    elif registration == "provider":
        provider = LocalProvider()
        provider.tool("lookup", input_schema_extra=schema_extra)(lookup_team)
        mcp.add_provider(provider)
    elif registration == "standalone":
        decorated = tool("lookup", input_schema_extra=schema_extra)(lookup_team)
        mcp.add_tool(decorated)
    elif registration == "tool_factory":
        mcp.add_tool(
            Tool.from_function(
                lookup_team, name="lookup", input_schema_extra=schema_extra
            )
        )
    elif registration == "function_factory":
        mcp.add_tool(
            FunctionTool.from_function(
                lookup_team, name="lookup", input_schema_extra=schema_extra
            )
        )
    else:
        mcp.add_tool(
            FunctionTool.from_function(
                lookup_team,
                metadata=ToolMeta(name="lookup", input_schema_extra=schema_extra),
            )
        )

    async with Client(mcp) as client:
        listed = await client.list_tools()
        assert len(listed) == 1
        assert listed[0].name == "lookup"
        assert listed[0].input_schema == {**inferred, **schema_extra}
        validator = Draft202012Validator(listed[0].input_schema)
        assert not validator.is_valid({})
        assert validator.is_valid({"team_id": "team-1"})
        assert validator.is_valid({"club_id": "club-1"})
        assert validator.is_valid({"team_id": "team-1", "club_id": "club-1"})

        result = await client.call_tool("lookup", {"team_id": "team-1"})
        assert result.data == {"team_id": "team-1", "club_id": None}


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
async def test_schema_constraints_do_not_change_runtime_validation(
    keyword: str,
) -> None:
    mcp = FastMCP()

    @mcp.tool(
        input_schema_extra={
            keyword: [{"required": ["team_id"]}, {"required": ["club_id"]}]
        }
    )
    def lookup_team(
        team_id: str | None = None, club_id: str | None = None
    ) -> dict[str, str | None]:
        return {"team_id": team_id, "club_id": club_id}

    async with Client(mcp) as client:
        schema = (await client.list_tools())[0].input_schema
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        assert not validator.is_valid({})
        assert validator.is_valid({"team_id": None})
        assert validator.is_valid({"club_id": None})
        assert validator.is_valid({"team_id": "team-1", "club_id": "club-1"}) == (
            keyword == "anyOf"
        )

        for arguments in (
            {},
            {"team_id": None},
            {"club_id": None},
            {"team_id": "team-1", "club_id": "club-1"},
        ):
            result = await client.call_tool("lookup_team", arguments)
            assert result.data == {
                "team_id": arguments.get("team_id"),
                "club_id": arguments.get("club_id"),
            }


@pytest.mark.parametrize("factory", [Tool.from_function, FunctionTool.from_function])
def test_factories_preserve_standalone_metadata(
    factory: Callable[..., FunctionTool], schema_extra: dict[str, Any]
) -> None:
    @tool(input_schema_extra=schema_extra)
    def lookup_team(team_id: str | None = None, club_id: str | None = None) -> None:
        pass

    assert factory(lookup_team).parameters["anyOf"] == schema_extra["anyOf"]


def test_metadata_and_individual_schema_options_are_mutually_exclusive() -> None:
    def greet(name: str) -> str:
        return name

    with pytest.raises(TypeError, match="both 'metadata' and individual parameters"):
        FunctionTool.from_function(greet, metadata=ToolMeta(), input_schema_extra={})


def test_schema_extra_preserves_inferred_fields_and_dependencies(
    schema_extra: dict[str, Any],
) -> None:
    def lookup_team(
        ctx: Context,
        tenant: str,
        team_id: Annotated[str | None, Field(description="Team identifier")] = None,
        club_id: str | None = None,
        limit: Annotated[int, Field(ge=1, alias="maxResults")] = 10,
    ) -> str:
        return tenant

    inferred = Tool.from_function(lookup_team).parameters
    created = Tool.from_function(lookup_team, input_schema_extra=schema_extra)
    assert created.parameters == {**inferred, **schema_extra}
    assert created.parameters["required"] == ["tenant"]
    assert "ctx" not in created.parameters["properties"]
    assert (
        created.parameters["properties"]["team_id"]["description"] == "Team identifier"
    )
    assert created.parameters["properties"]["maxResults"]["minimum"] == 1
    assert created.parameters["properties"]["maxResults"]["default"] == 10


def test_schema_extra_is_copied_per_registration(
    schema_extra: dict[str, Any],
) -> None:
    def lookup_team(team_id: str | None = None, club_id: str | None = None) -> None:
        pass

    first = Tool.from_function(lookup_team, input_schema_extra=schema_extra)
    second = Tool.from_function(lookup_team, input_schema_extra=schema_extra)
    assert schema_extra == {
        "anyOf": [{"required": ["team_id"]}, {"required": ["club_id"]}]
    }
    first.parameters["anyOf"][0]["required"].append("first_only")
    schema_extra["anyOf"][1]["required"].append("caller_only")
    assert first.parameters["anyOf"][1] == {"required": ["club_id"]}
    assert second.parameters["anyOf"] == [
        {"required": ["team_id"]},
        {"required": ["club_id"]},
    ]
    assert "anyOf" not in Tool.from_function(lookup_team).parameters


@pytest.mark.parametrize("extra", [None, {}])
async def test_unconstrained_tools_keep_schema_and_defaults(
    extra: dict[str, Any] | None,
) -> None:
    def lookup_team(
        team_id: str | None = None, club_id: str | None = None
    ) -> dict[str, str | None]:
        return {"team_id": team_id, "club_id": club_id}

    inferred = Tool.from_function(lookup_team).parameters
    created = Tool.from_function(lookup_team, input_schema_extra=extra)
    assert created.parameters == inferred
    assert Draft202012Validator(created.parameters).is_valid({})
    result = await created.run({})
    assert result.structured_content == {"team_id": None, "club_id": None}


async def test_explicit_overrides_replace_keys_but_not_python_validation() -> None:
    def count_items(count: Annotated[int, Field(ge=1)]) -> int:
        return count

    created = Tool.from_function(
        count_items,
        input_schema_extra={
            "properties": {"count": {"type": "string"}},
            "required": [],
        },
    )
    assert created.parameters["properties"] == {"count": {"type": "string"}}
    assert created.parameters["required"] == []
    assert created.parameters["additionalProperties"] is False

    with pytest.raises(ValidationError, match="Missing required argument"):
        await created.run({})
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        await created.run({"count": 0})
    result = await created.run({"count": "3"})
    assert result.structured_content == {"result": 3}
    assert type(result.structured_content["result"]) is int


@pytest.mark.parametrize("schema_type", ["array", "string", None])
def test_input_schema_must_remain_an_object(schema_type: str | None) -> None:
    def greet(name: str) -> str:
        return name

    with pytest.raises(ValueError, match="Input schemas must have type 'object'"):
        Tool.from_function(greet, input_schema_extra={"type": schema_type})


async def test_identity_transform_preserves_schema_and_isolates_copies(
    schema_extra: dict[str, Any],
) -> None:
    def lookup_team(
        team_id: str | None = None, club_id: str | None = None
    ) -> dict[str, str | None]:
        return {"team_id": team_id, "club_id": club_id}

    original = Tool.from_function(lookup_team, input_schema_extra=schema_extra)
    transformed = Tool.from_tool(original, name="renamed_lookup")
    mcp = FastMCP()
    mcp.add_tool(transformed)

    async with Client(mcp) as client:
        listed = (await client.list_tools())[0]
        assert listed.name == "renamed_lookup"
        assert listed.input_schema == original.parameters
        assert not Draft202012Validator(listed.input_schema).is_valid({})
        result = await client.call_tool("renamed_lookup", {"club_id": "club-1"})
        assert result.data == {"team_id": None, "club_id": "club-1"}

    transformed.parameters["anyOf"][0]["required"].append("copy_only")
    assert original.parameters["anyOf"] == schema_extra["anyOf"]


async def test_argument_transforms_rebuild_schema_without_stale_constraints(
    schema_extra: dict[str, Any],
) -> None:
    def lookup_team(
        team_id: str | None = None, club_id: str | None = None
    ) -> dict[str, str | None]:
        return {"team_id": team_id, "club_id": club_id}

    original = Tool.from_function(lookup_team, input_schema_extra=schema_extra)
    transformed = Tool.from_tool(
        original,
        transform_args={
            "team_id": ArgTransform(name="team"),
            "club_id": ArgTransform(hide=True, default="club-1"),
        },
    )
    assert "anyOf" not in transformed.parameters
    assert set(transformed.parameters["properties"]) == {"team"}
    assert (
        transformed.parameters["properties"]["team"]
        == original.parameters["properties"]["team_id"]
    )
    assert transformed.parameters["required"] == []
    result = await transformed.run({"team": "team-1"})
    assert result.structured_content == {"team_id": "team-1", "club_id": "club-1"}
