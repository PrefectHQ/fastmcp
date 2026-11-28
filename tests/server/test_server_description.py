"""Server descriptions are advertised independently of usage instructions."""

from typing import Literal

import pytest
from mcp_types import SERVER_INFO_META_KEY, Icon

from fastmcp import Client, FastMCP


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("description", ["Search the web.", "検索 🌐", "", None])
async def test_server_description_round_trip(
    mode: Literal["auto", "legacy"], description: str | None
) -> None:
    icons = [Icon(src="https://example.com/icon.png")]
    server = FastMCP(
        "demo-server",
        description=description,
        instructions="Use search for web queries.",
        version="1.0.0",
        website_url="https://example.com",
        icons=icons,
    )

    @server.tool
    def search(query: str) -> str:
        """Search the web."""
        return f"Results for {query}"

    async with Client(server, mode=mode) as client:
        if mode == "legacy":
            result = await client.initialize()
        else:
            result = client.session.discover_result
        assert result is not None
        server_info = client.session.server_info
        assert server_info is not None
        assert server_info.description == description
        assert server_info.name == "demo-server"
        assert server_info.version == "1.0.0"
        assert server_info.website_url == "https://example.com"
        assert server_info.icons == icons
        assert result.instructions == "Use search for web queries."
        wire_result = result.model_dump(by_alias=True, exclude_none=True)
        wire_info = (
            wire_result["serverInfo"]
            if mode == "legacy"
            else wire_result["_meta"][SERVER_INFO_META_KEY]
        )
        if description is None:
            assert "description" not in wire_info
        else:
            assert wire_info["description"] == description
        assert (await client.call_tool("search", {"query": "FastMCP"})).data == (
            "Results for FastMCP"
        )


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_server_description_defaults_to_none(
    mode: Literal["auto", "legacy"],
) -> None:
    server = FastMCP("demo-server", "Existing positional instructions")
    async with Client(server, mode=mode) as client:
        assert client.session.server_info.description is None
        result = client.session.initialize_result or client.session.discover_result
        assert result is not None
        assert result.instructions == "Existing positional instructions"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_description_does_not_supply_instructions(
    mode: Literal["auto", "legacy"],
) -> None:
    server = FastMCP("demo-server", description="Search the web.")
    async with Client(server, mode=mode) as client:
        assert client.session.server_info.description == "Search the web."
        result = client.session.initialize_result or client.session.discover_result
        assert result is not None
        assert result.instructions is None
