from collections.abc import Sequence

import pytest

from fastmcp import FastMCP, FastMCPApp
from fastmcp.server.providers.addressing import hashed_backend_name
from fastmcp.server.transforms import GetToolNext, Namespace, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec


class CatalogNames(Transform):
    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        if len(tools) < 2:
            return tools
        return [
            tool.model_copy(update={"name": f"catalog_{tool.name}"}) for tool in tools
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if not name.startswith("catalog_"):
            return None
        tool = await call_next(name.removeprefix("catalog_"), version=version)
        if tool is None:
            return None
        return tool.model_copy(update={"name": name})


@pytest.mark.parametrize("namespace", [False, True])
async def test_catalog_names_work_for_name_and_app_calls(namespace: bool) -> None:
    app = FastMCPApp("contacts")

    @app.tool()
    def save(name: str) -> str:
        return f"saved {name}"

    @app.tool()
    def count() -> int:
        return 0

    server = FastMCP("Directory", providers=[app])
    server.add_transform(CatalogNames())
    if namespace:
        server.add_transform(Namespace("team"))

    displayed_name = "team_catalog_save" if namespace else "catalog_save"
    assert displayed_name in {tool.name for tool in await server.list_tools()}
    ordinary = await server.call_tool(displayed_name, {"name": "Alice"})
    addressed = await server.call_tool(
        hashed_backend_name("contacts", "save"), {"name": "Alice"}
    )
    assert ordinary.structured_content == addressed.structured_content
