"""Hashed tool names reach only tools that are reachable by name.

A FastMCPApp backend tool is also callable by its identity-addressed name,
`<hash>_<local_name>`. That address must lead to the same tool, with the same
transforms, visibility, and auth applied, as calling the tool by the name the
server exposes it under. A tool that a transform hides or that visibility
disables stays unreachable under its hashed name too.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from fastmcp import Client, Context, FastMCP, FastMCPApp
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.addressing import hashed_backend_name
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools.base import Tool
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.versions import VersionSpec

SAVE = hashed_backend_name("contacts", "save")


class HideTools(Transform):
    """Removes the named tools from both listing and lookup."""

    def __init__(self, *names: str) -> None:
        self.names = set(names)

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [t for t in tools if t.name not in self.names]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name in self.names:
            return None
        return await call_next(name, version=version)


class RefuseLookup(Transform):
    """Lists every tool but refuses to resolve the named ones."""

    def __init__(self, *names: str) -> None:
        self.names = set(names)

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name in self.names:
            return None
        return await call_next(name, version=version)


def contacts_app(calls: list[str]) -> FastMCPApp:
    app = FastMCPApp("contacts")

    @app.tool()
    def save(name: str) -> str:
        calls.append(name)
        return f"saved {name}"

    return app


def tagged_contacts_app(calls: list[str]) -> FastMCPApp:
    app = FastMCPApp("contacts")

    def save(name: str) -> str:
        calls.append(name)
        return f"saved {name}"

    app.add_tool(Tool.from_function(save, tags={"internal"}))
    return app


def server_with(app: FastMCPApp) -> FastMCP:
    server = FastMCP("Child")
    server.add_provider(app)
    return server


def hidden_by_server_transform(calls: list[str]) -> FastMCP:
    server = server_with(contacts_app(calls))
    server.add_transform(HideTools("save"))
    return server


def hidden_by_provider_transform(calls: list[str]) -> FastMCP:
    app = contacts_app(calls)
    app.add_transform(HideTools("save"))
    return server_with(app)


def hidden_under_its_namespaced_name(calls: list[str]) -> FastMCP:
    server = FastMCP("Platform")
    server.add_provider(contacts_app(calls), namespace="crm")
    server.add_transform(HideTools("crm_save"))
    return server


def hidden_by_mounted_server_transform(calls: list[str]) -> FastMCP:
    child = server_with(contacts_app(calls))
    child.add_transform(HideTools("save"))
    server = FastMCP("Platform")
    server.mount(child, namespace="child")
    return server


def refused_by_lookup_only_transform(calls: list[str]) -> FastMCP:
    server = server_with(contacts_app(calls))
    server.add_transform(RefuseLookup("save"))
    return server


def disabled_by_name(calls: list[str]) -> FastMCP:
    server = server_with(contacts_app(calls))
    server.disable(names={"save"})
    return server


def disabled_by_tag(calls: list[str]) -> FastMCP:
    server = server_with(tagged_contacts_app(calls))
    server.disable(tags={"internal"})
    return server


def disabled_on_the_provider(calls: list[str]) -> FastMCP:
    app = contacts_app(calls)
    app.disable(names={"save"})
    return server_with(app)


def disabled_in_a_mounted_server(calls: list[str]) -> FastMCP:
    child = server_with(contacts_app(calls))
    child.disable(names={"save"})
    server = FastMCP("Platform")
    server.mount(child, namespace="child")
    return server


@pytest.mark.parametrize(
    "build",
    [
        hidden_by_server_transform,
        hidden_by_provider_transform,
        hidden_under_its_namespaced_name,
        hidden_by_mounted_server_transform,
        refused_by_lookup_only_transform,
        disabled_by_name,
        disabled_by_tag,
        disabled_on_the_provider,
        disabled_in_a_mounted_server,
    ],
)
async def test_unreachable_tool_is_unreachable_by_hashed_name(
    build: Callable[[list[str]], FastMCP],
):
    calls: list[str] = []
    server = build(calls)

    async with Client(server) as client:
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool(SAVE, {"name": "alice"})

    assert calls == []


async def test_session_disable_applies_to_hashed_name():
    calls: list[str] = []
    server = server_with(contacts_app(calls))

    @server.tool
    async def lock(ctx: Context) -> str:
        await ctx.disable_components(names={"save"})
        return "locked"

    async with Client(server) as client:
        await client.call_tool(SAVE, {"name": "before"})
        await client.call_tool("lock", {})
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool(SAVE, {"name": "after"})

    assert calls == ["before"]


def renamed_by_tool_transform(calls: list[str]) -> FastMCP:
    server = server_with(contacts_app(calls))
    server.add_transform(ToolTransform({"save": ToolTransformConfig(name="store")}))
    return server


def renamed_by_mount(calls: list[str]) -> FastMCP:
    server = FastMCP("Platform")
    server.mount(
        server_with(contacts_app(calls)),
        namespace="child",
        tool_names={"save": "store"},
    )
    return server


def beside_an_unrelated_hiding_transform(calls: list[str]) -> FastMCP:
    server = server_with(contacts_app(calls))
    server.add_transform(HideTools("unrelated"))
    return server


@pytest.mark.parametrize(
    "build",
    [
        renamed_by_tool_transform,
        renamed_by_mount,
        beside_an_unrelated_hiding_transform,
    ],
)
async def test_reachable_tool_stays_reachable_by_hashed_name(
    build: Callable[[list[str]], FastMCP],
):
    calls: list[str] = []
    server = build(calls)

    async with Client(server) as client:
        result = await client.call_tool(SAVE, {"name": "alice"})

    assert result.data == "saved alice"
    assert calls == ["alice"]


class DenyingApp(FastMCPApp):
    """An app provider whose public lookup refuses `save`."""

    async def get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        if name == "save":
            return None
        return await super().get_tool(name, version)


class MaintenanceLock(Transform):
    """Refuses `save` while a `maintenance_marker` tool exists beneath it."""

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name == "save" and await call_next("maintenance_marker") is not None:
            return None
        return await call_next(name, version=version)


async def test_provider_public_lookup_applies_to_hashed_name():
    calls: list[str] = []
    app = DenyingApp("contacts")

    @app.tool()
    def save(name: str) -> str:
        calls.append(name)
        return f"saved {name}"

    async with Client(server_with(app)) as client:
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool(SAVE, {"name": "alice"})

    assert calls == []


@pytest.mark.parametrize("under_maintenance", [True, False])
async def test_transform_sees_other_tools_during_hashed_lookup(
    under_maintenance: bool,
):
    calls: list[str] = []
    server = server_with(contacts_app(calls))
    if under_maintenance:

        @server.tool
        def maintenance_marker() -> str:
            return "down"

    server.add_transform(MaintenanceLock())

    async with Client(server) as client:
        if under_maintenance:
            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool(SAVE, {"name": "alice"})
        else:
            await client.call_tool(SAVE, {"name": "alice"})

    assert calls == ([] if under_maintenance else ["alice"])


async def test_hashed_name_falls_back_past_a_disabled_highest_version():
    app = FastMCPApp("contacts")
    for version in ("1.0", "2.0"):

        def save(name: str, _version: str = version) -> str:
            return f"v{_version} saved {name}"

        app.add_tool(
            Tool.from_function(
                save,
                version=version,
                meta={"ui": {"visibility": ["app", "model"]}},
            )
        )
    server = server_with(app)
    server.disable(version=VersionSpec(eq="2.0"))

    async with Client(server) as client:
        by_name = await client.call_tool("save", {"name": "alice"})
        by_hash = await client.call_tool(SAVE, {"name": "alice"})

    assert by_name.data == "v1.0 saved alice"
    assert by_hash.data == "v1.0 saved alice"
