"""Result validation for tool calls that pin a version.

`Client.call_tool(name, version=...)` executes that exact version on the
server. The session's output-schema cache is keyed by tool name and holds only
the highest version's schema (`list_tools` deduplicates by identifier), so the
pinned result cannot be checked against it: the client has no schema for the
version that actually ran.
"""

from __future__ import annotations

import asyncio

import pytest

from fastmcp import Client, FastMCP


@pytest.fixture
def versioned_server() -> FastMCP:
    server = FastMCP("versioned")

    @server.tool(name="same", version="1.0")
    def same_v1() -> int:
        return 7

    @server.tool(name="same", version="2.0")
    def same_v2() -> int:
        return 8

    @server.tool(name="value", version="1.0")
    def value_v1() -> int:
        return 7

    @server.tool(name="value", version="2.0")
    def value_v2() -> str:
        return "hello"

    @server.tool(name="shape", version="1.0")
    def shape_v1() -> int:
        return 7

    @server.tool(name="shape", version="2.0")
    def shape_v2() -> dict:
        return {"a": 1}

    return server


async def test_pinned_call_returns_its_own_version_result(
    versioned_server: FastMCP,
) -> None:
    """A pinned call is not validated against the highest version's schema."""
    async with Client(versioned_server) as client:
        result = await client.call_tool("value", version="1.0")

    assert result.data == 7


async def test_pinned_call_hydrates_when_versions_differ_in_shape(
    versioned_server: FastMCP,
) -> None:
    """The highest version's schema does not accept this version's content."""
    async with Client(versioned_server) as client:
        result = await client.call_tool("shape", version="1.0")

    assert result.data == 7


async def test_pinned_call_with_matching_schemas_is_unchanged(
    versioned_server: FastMCP,
) -> None:
    async with Client(versioned_server) as client:
        pinned = await client.call_tool("same", version="1.0")
        highest = await client.call_tool("same")

    assert pinned.data == 7
    assert highest.data == 8


async def test_unpinned_call_still_validates_a_non_conforming_result() -> None:
    """Pinning a version must not turn result validation off in general."""
    server = FastMCP("liar")

    @server.tool
    def liar() -> int:
        """Declares int, returns a string."""
        return "oops"  # type: ignore[return-value]

    async with Client(server) as client:
        with pytest.raises(RuntimeError, match="Invalid structured content"):
            await client.call_tool("liar")


async def test_concurrent_pinned_calls_leave_the_schema_cache_intact(
    versioned_server: FastMCP,
) -> None:
    """Pinning is per call: concurrent calls must not disturb the shared cache.

    A pinned call is a property of one request, so it must not be expressed by
    clearing the session-wide schema entry: two pinned calls in flight would
    then restore each other's placeholder, and every later unpinned call would
    skip validation for the rest of the session.
    """
    async with Client(versioned_server) as client:
        await client.list_tools()
        before = client.session._tool_output_schemas.get("value")
        assert before is not None

        await asyncio.gather(
            client.call_tool("value", version="1.0"),
            client.call_tool("value", version="1.0"),
        )

        assert client.session._tool_output_schemas.get("value") == before

        # And the cache is still consulted for an ordinary call.
        result = await client.call_tool("value")
        assert result.data == "hello"
