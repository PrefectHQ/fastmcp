"""GenerativeUI provider metadata."""

import pytest

from fastmcp import Client, FastMCP

pytest.importorskip("prefab_ui.generative")
from fastmcp.apps.generative import GenerativeUI  # noqa: E402


async def test_csp_is_on_renderer_resource_not_tool():
    """CSP belongs on the ui:// resource; tool meta carries only resourceUri."""
    mcp = FastMCP("test", providers=[GenerativeUI()])

    async with Client(mcp) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()

    tool = next(t for t in tools if t.name == "generate_prefab_ui")
    assert tool.meta is not None
    assert set(tool.meta["ui"]) == {"resourceUri"}

    renderer = next(
        r for r in resources if str(r.uri) == tool.meta["ui"]["resourceUri"]
    )
    assert renderer.meta is not None
    assert renderer.meta["ui"]["csp"]["resourceDomains"]
    assert renderer.meta["ui"]["csp"]["connectDomains"]
