"""Code Mode must apply result limits after ranking the complete catalog."""

import json

import pytest

from fastmcp import Client, FastMCP
from fastmcp.experimental.transforms.code_mode import CodeMode, Search
from fastmcp.tools import Tool

CATALOG_SIZE = 60


def _ranked_catalog() -> FastMCP:
    """Build a catalog whose BM25 ranking is the reverse of its registration order.

    Every tool repeats the query token a different number of times, so no two
    scores tie. The best match is therefore registered last, which makes the
    difference between ranking-then-slicing and slicing-then-ranking visible in
    the returned names instead of hiding behind catalogue order.
    """
    server = FastMCP("Search limits")

    def add_one(x: int) -> int:
        """Add one to a number."""
        return x + 1

    for index in range(CATALOG_SIZE):
        server.add_tool(
            Tool.from_function(
                add_one,
                name=f"add_{index:02d}",
                description="Add one to a number. " + "add " * index,
            )
        )
    return server


# Relevance rises with the registration index, so this is the ranked order.
_RANKED_NAMES = [f"add_{index:02d}" for index in range(CATALOG_SIZE - 1, -1, -1)]


@pytest.mark.parametrize(
    "default_limit, override, expected_count",
    [
        (None, {}, CATALOG_SIZE),
        (None, {"limit": 55}, 55),
        (None, {"limit": 0}, 0),
        (5, {}, 5),
        (5, {"limit": 55}, 55),
        (5, {"limit": None}, CATALOG_SIZE),
        (55, {}, 55),
    ],
)
async def test_search_applies_limits_after_ranking(
    default_limit: int | None,
    override: dict[str, int | None],
    expected_count: int,
) -> None:
    server = _ranked_catalog()
    server.add_transform(
        CodeMode(discovery_tools=[Search(default_limit=default_limit)])
    )

    async with Client(server) as client:
        result = await client.call_tool(
            "search", {"query": "add", "detail": "full", **override}
        )

    matches = json.loads(result.data)
    assert [match["name"] for match in matches] == _RANKED_NAMES[:expected_count]


@pytest.mark.parametrize("limit", [-1, -5, -60, -61])
async def test_search_reads_a_negative_limit_as_a_count(limit: int) -> None:
    """A limit is a maximum result count, so it can never be negative.

    Python's slice semantics would otherwise read ``limit=-1`` as "all but the
    last one", which is a fine reading of a slice and a wrong reading of a cap.
    """
    server = _ranked_catalog()
    server.add_transform(CodeMode(discovery_tools=[Search()]))

    async with Client(server) as client:
        result = await client.call_tool(
            "search", {"query": "add", "detail": "full", "limit": limit}
        )

    assert json.loads(result.data) == []


async def test_search_first_result_is_the_best_match_registered_last() -> None:
    """Ranking decides what survives a small limit, not registration order."""
    server = _ranked_catalog()
    server.add_transform(CodeMode(discovery_tools=[Search(default_limit=3)]))

    async with Client(server) as client:
        result = await client.call_tool("search", {"query": "add", "detail": "full"})

    assert [match["name"] for match in json.loads(result.data)] == _RANKED_NAMES[:3]
