"""Session visibility must preserve explicit empty component selections."""

from typing import Literal

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context


@pytest.mark.parametrize("selector", ["names", "keys"])
@pytest.mark.parametrize("selection", ["empty", "matching", "nonmatching", "omitted"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_session_selection_matches_global_selection(
    selector: Literal["names", "keys"],
    selection: Literal["empty", "matching", "nonmatching", "omitted"],
    enabled: bool,
) -> None:
    server = FastMCP("Selection")
    names: set[str] | None = None
    keys: set[str] | None = None
    if selection != "omitted":
        name = "report" if selection == "matching" else "other"
        value = name if selector == "names" else f"tool:{name}@"
        selected = set() if selection == "empty" else {value}
        if selector == "names":
            names = selected
        else:
            keys = selected

    @server.tool(tags={"finance"})
    def report() -> str:
        return "report"

    @server.tool
    async def update_visibility(ctx: Context) -> list[str]:
        if enabled:
            await ctx.enable_components(tags={"finance"}, names=names, keys=keys)
        else:
            await ctx.disable_components(tags={"finance"}, names=names, keys=keys)
        return sorted(tool.name for tool in await ctx.fastmcp.list_tools())

    if enabled:
        server.disable(tags={"finance"})
        server.enable(tags={"finance"}, names=names, keys=keys)
    else:
        server.disable(tags={"finance"}, names=names, keys=keys)

    async with Client(server) as client:
        expected = sorted(tool.name for tool in await client.list_tools())
        result = await client.call_tool("update_visibility")

    assert result.data == expected
    should_match = selection in {"matching", "omitted"}
    assert ("report" in result.data) is (enabled if should_match else not enabled)
