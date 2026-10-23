"""Generated commands must distinguish empty arrays from omitted arguments."""

from typing import Any

import pytest
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.cli.generate import generate_cli_script


async def generated_app(server: FastMCP) -> Any:
    async with Client(server) as client:
        tools = await client.list_tools()
    source = generate_cli_script(
        "arrays", "arrays", repr("http://unused/mcp"), set(), tools
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(source, "<generated>", "exec"), namespace)
    namespace["CLIENT_SPEC"] = server
    return namespace["app"]


async def test_required_json_array_preserves_empty_value() -> None:
    server = FastMCP("Arrays")
    received: list[list[dict[str, int]]] = []

    @server.tool
    def count_items(items: list[dict[str, int]]) -> int:
        received.append(items)
        return len(items)

    app = await generated_app(server)
    await app.run_async(
        ["call-tool", "count_items", "--items", "[]"], result_action="return_value"
    )

    assert received == [[]]


@pytest.mark.parametrize("value", ["[]", '[{"id":2}]'])
async def test_optional_json_array_preserves_explicit_override(value: str) -> None:
    server = FastMCP("Arrays")
    received: list[list[dict[str, int]]] = []

    @server.tool
    def count_items(items: list[dict[str, int]] = [{"id": 1}]) -> int:
        received.append(items)
        return len(items)

    app = await generated_app(server)
    await app.run_async(
        ["call-tool", "count_items", "--items", value], result_action="return_value"
    )

    assert received == ([[]] if value == "[]" else [[{"id": 2}]])


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], ["server-default"]),
        (["--empty-tags"], []),
        (["--tags", "one", "--tags", "two"], ["one", "two"]),
    ],
)
async def test_simple_array_preserves_omission_and_explicit_values(
    arguments: list[str], expected: list[str]
) -> None:
    server = FastMCP("Arrays")
    received: list[list[str]] = []

    @server.tool
    def tag_items(
        tags: list[str] = Field(default_factory=lambda: ["server-default"]),
    ) -> int:
        received.append(tags)
        return len(tags)

    app = await generated_app(server)
    await app.run_async(
        ["call-tool", "tag_items", *arguments], result_action="return_value"
    )

    assert received == [expected]
