"""Code Mode must apply result limits after ranking the complete catalog."""

import json

import pytest

from fastmcp import Client, FastMCP
from fastmcp.experimental.transforms.code_mode import CodeMode, Search
from fastmcp.tools import Tool


@pytest.mark.parametrize(
    "default_limit, override, expected_count",
    [
        (None, {}, 60),
        (None, {"limit": 55}, 55),
        (None, {"limit": 0}, 0),
        (5, {}, 5),
        (5, {"limit": 55}, 55),
        (5, {"limit": None}, 60),
        (55, {}, 55),
    ],
)
async def test_search_applies_limits_after_ranking(
    default_limit: int | None,
    override: dict[str, int | None],
    expected_count: int,
) -> None:
    server = FastMCP("Search limits")

    def add_one(x: int) -> int:
        """Add one to a number."""
        return x + 1

    names = [f"add_{index:02d}" for index in range(60)]
    for name in names:
        server.add_tool(Tool.from_function(add_one, name=name))
    server.add_transform(
        CodeMode(discovery_tools=[Search(default_limit=default_limit)])
    )

    async with Client(server) as client:
        result = await client.call_tool(
            "search", {"query": "add", "detail": "full", **override}
        )

    matches = json.loads(result.data)
    assert [match["name"] for match in matches] == names[:expected_count]
