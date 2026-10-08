from collections.abc import Sequence

import pytest

from fastmcp import FastMCP, FastMCPApp
from fastmcp.exceptions import NotFoundError
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


class VersionCatalogNames(Transform):
    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [
            tool.model_copy(update={"name": f"{tool.name}_{tool.version}"})
            for tool in tools
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        local_name, _, selected = name.rpartition("_")
        tool = await call_next(local_name, version=VersionSpec(eq=selected))
        if tool is None:
            return None
        return tool.model_copy(update={"name": name})


@pytest.mark.parametrize("namespace", [False, True])
async def test_catalog_names_select_the_app_tool_version(namespace: bool) -> None:
    app = FastMCPApp("contacts")
    for version in ("1.0", "2.0"):

        def save(name: str, _version: str = version) -> str:
            return f"v{_version} saved {name}"

        app.add_tool(Tool.from_function(save, version=version))

    server = FastMCP("Directory", providers=[app])
    server.add_transform(VersionCatalogNames())
    if namespace:
        server.add_transform(Namespace("team"))

    selected = "2.0"
    displayed_name = f"team_save_{selected}" if namespace else f"save_{selected}"
    assert displayed_name in {tool.name for tool in await server.list_tools()}
    ordinary = await server.call_tool(displayed_name, {"name": "Alice"})
    addressed = await server.call_tool(
        hashed_backend_name("contacts", "save"), {"name": "Alice"}
    )
    assert ordinary.structured_content == addressed.structured_content
    assert addressed.structured_content == {"result": f"v{selected} saved Alice"}


class VersionDecliningApp(FastMCPApp):
    async def get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await super().get_tool(name, version)
        return None if tool is not None and tool.version == "1.0" else tool


async def test_catalog_fallback_retains_provider_lookup() -> None:
    app = VersionDecliningApp("contacts")
    for version in ("1.0", "2.0"):

        def save(name: str, _version: str = version) -> str:
            return f"v{_version} saved {name}"

        app.add_tool(Tool.from_function(save, version=version))
    server = FastMCP("Directory", providers=[app])
    server.add_transform(VersionCatalogNames())
    server.disable(version=VersionSpec(eq="2.0"))
    with pytest.raises(NotFoundError):
        await server.call_tool(
            hashed_backend_name("contacts", "save"), {"name": "Alice"}
        )
