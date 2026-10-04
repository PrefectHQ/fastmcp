"""Hashed tool names follow the same lookup rules as listed names.

A FastMCPApp backend tool is also callable by its identity-addressed name,
`<hash>_<local_name>`. Calling a tool that way resolves the same tool, with
the same transforms, enabled state, auth, and version fallback, as calling it
by the name the server lists it under.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

import pytest

from fastmcp import Client, Context, FastMCP, FastMCPApp
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import require_scopes, restrict_tag
from fastmcp.server.providers.addressing import hashed_backend_name
from fastmcp.server.transforms import GetToolNext, Transform, VersionFilter
from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools.base import Tool
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.versions import VersionSpec

SAVE = hashed_backend_name("contacts", "save")


class FilterTools(Transform):
    """Drops the named tools from both listing and lookup."""

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


def filtered_by_server_transform(calls: list[str]) -> FastMCP:
    server = server_with(contacts_app(calls))
    server.add_transform(FilterTools("save"))
    return server


def filtered_by_provider_transform(calls: list[str]) -> FastMCP:
    app = contacts_app(calls)
    app.add_transform(FilterTools("save"))
    return server_with(app)


def filtered_under_its_namespaced_name(calls: list[str]) -> FastMCP:
    server = FastMCP("Platform")
    server.add_provider(contacts_app(calls), namespace="crm")
    server.add_transform(FilterTools("crm_save"))
    return server


def filtered_by_mounted_server_transform(calls: list[str]) -> FastMCP:
    child = server_with(contacts_app(calls))
    child.add_transform(FilterTools("save"))
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
        filtered_by_server_transform,
        filtered_by_provider_transform,
        filtered_under_its_namespaced_name,
        filtered_by_mounted_server_transform,
        refused_by_lookup_only_transform,
        disabled_by_name,
        disabled_by_tag,
        disabled_on_the_provider,
        disabled_in_a_mounted_server,
    ],
)
async def test_hashed_name_is_unknown_when_listed_name_is_unknown(
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


def beside_an_unrelated_filter(calls: list[str]) -> FastMCP:
    server = server_with(contacts_app(calls))
    server.add_transform(FilterTools("unrelated"))
    return server


@pytest.mark.parametrize(
    "build",
    [
        renamed_by_tool_transform,
        renamed_by_mount,
        beside_an_unrelated_filter,
    ],
)
async def test_hashed_name_resolves_renamed_tools(
    build: Callable[[list[str]], FastMCP],
):
    calls: list[str] = []
    server = build(calls)

    async with Client(server) as client:
        result = await client.call_tool(SAVE, {"name": "alice"})

    assert result.data == "saved alice"
    assert calls == ["alice"]


class SaveDisabledApp(FastMCPApp):
    """An app provider whose `get_tool` override declines `save`."""

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


async def test_provider_get_tool_override_applies_to_hashed_name():
    calls: list[str] = []
    app = SaveDisabledApp("contacts")

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


def versioned_contacts_app() -> FastMCPApp:
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
    return app


async def test_hashed_name_falls_back_past_a_disabled_highest_version():
    server = server_with(versioned_contacts_app())
    server.disable(version=VersionSpec(eq="2.0"))

    async with Client(server) as client:
        by_name = await client.call_tool("save", {"name": "alice"})
        by_hash = await client.call_tool(SAVE, {"name": "alice"})

    assert by_name.data == "v1.0 saved alice"
    assert by_hash.data == "v1.0 saved alice"


async def test_hashed_name_respects_a_version_filter():
    server = server_with(versioned_contacts_app())
    server.add_transform(VersionFilter(version_lt="2"))

    async with Client(server) as client:
        by_name = await client.call_tool("save", {"name": "alice"})
        by_hash = await client.call_tool(SAVE, {"name": "alice"})

    assert by_name.data == "v1.0 saved alice"
    assert by_hash.data == "v1.0 saved alice"


class SpawnLaterLookup(Transform):
    """Starts a task that looks up `save` by name once released."""

    def __init__(self, server: FastMCP) -> None:
        self.server = server
        self.release = asyncio.Event()
        self.probes: list[asyncio.Task[Tool | None]] = []

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name == "save" and not self.probes:
            self.probes.append(asyncio.create_task(self._lookup_later()))
        return await call_next(name, version=version)

    async def _lookup_later(self) -> Tool | None:
        await self.release.wait()
        return await self.server.get_tool("save")


async def test_task_started_during_hashed_lookup_resolves_names_normally():
    server = server_with(contacts_app([]))

    @server.tool(name="save")
    def plain_save(name: str) -> str:
        """Save without an app."""
        return f"plain {name}"

    spawner = SpawnLaterLookup(server)
    server.add_transform(spawner)

    async with Client(server) as client:
        await client.call_tool(SAVE, {"name": "alice"})
        spawner.release.set()
        (probe,) = spawner.probes
        later = await probe

    assert later is not None
    assert later.description == "Save without an app."


async def test_app_only_tools_stay_unavailable_by_name_after_hashed_lookup():
    server = server_with(contacts_app([]))
    spawner = SpawnLaterLookup(server)
    server.add_transform(spawner)

    async with Client(server) as client:
        await client.call_tool(SAVE, {"name": "alice"})
        spawner.release.set()
        (probe,) = spawner.probes
        later = await probe

    assert later is None


class StripAuth(Transform):
    """Lists and resolves every tool as a copy that has no auth checks."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [t.model_copy(update={"auth": None}) for t in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        if tool is None:
            return None
        return tool.model_copy(update={"auth": None})


def admin_contacts_app(calls: list[str], tags: set[str]) -> FastMCPApp:
    app = FastMCPApp("contacts")

    def save(name: str) -> str:
        calls.append(name)
        return f"saved {name}"

    app.add_tool(
        Tool.from_function(
            save,
            tags=tags,
            auth=restrict_tag("admin", scopes=["admin"]),
            meta={"ui": {"visibility": ["app", "model"]}},
        )
    )
    return app


def admin_tag_removed_by_server_transform(calls: list[str]) -> FastMCP:
    server = server_with(admin_contacts_app(calls, {"admin"}))
    server.add_transform(ToolTransform({"save": ToolTransformConfig(tags={"public"})}))
    return server


def admin_tag_added_by_server_transform(calls: list[str]) -> FastMCP:
    server = server_with(admin_contacts_app(calls, {"public"}))
    server.add_transform(ToolTransform({"save": ToolTransformConfig(tags={"admin"})}))
    return server


def admin_tag_removed_in_mounted_server(calls: list[str]) -> FastMCP:
    child = server_with(admin_contacts_app(calls, {"admin"}))
    child.add_transform(ToolTransform({"save": ToolTransformConfig(tags={"public"})}))
    server = FastMCP("Platform")
    server.mount(child, namespace="child")
    return server


def admin_tag_removed_and_renamed(calls: list[str]) -> FastMCP:
    server = server_with(admin_contacts_app(calls, {"admin"}))
    server.add_transform(
        ToolTransform({"save": ToolTransformConfig(name="store", tags={"public"})})
    )
    return server


def scoped_tool_with_auth_removed_by_server_transform(calls: list[str]) -> FastMCP:
    app = FastMCPApp("contacts")

    def save(name: str) -> str:
        calls.append(name)
        return f"saved {name}"

    app.add_tool(
        Tool.from_function(
            save,
            auth=require_scopes("admin"),
            meta={"ui": {"visibility": ["app", "model"]}},
        )
    )
    server = server_with(app)
    server.add_transform(StripAuth())
    return server


def scoped_tool_with_auth_removed_in_mounted_server(calls: list[str]) -> FastMCP:
    child = scoped_tool_with_auth_removed_by_server_transform(calls)
    server = FastMCP("Platform")
    server.mount(child, namespace="child")
    return server


@pytest.mark.parametrize(
    "build, listed_name, allowed",
    [
        (admin_tag_removed_by_server_transform, "save", False),
        (admin_tag_added_by_server_transform, "save", True),
        (admin_tag_removed_in_mounted_server, "child_save", False),
        (admin_tag_removed_and_renamed, "store", False),
        (scoped_tool_with_auth_removed_by_server_transform, "save", False),
        (scoped_tool_with_auth_removed_in_mounted_server, "child_save", False),
    ],
)
async def test_hashed_lookup_checks_authorization_of_the_same_tool_as_the_listed_name(
    build: Callable[[list[str]], FastMCP], listed_name: str, allowed: bool
):
    async def calls_made_through(name: str) -> list[str]:
        calls: list[str] = []
        server = build(calls)
        async with Client(server) as client:
            try:
                await client.call_tool(name, {"name": "alice"})
            except ToolError as exc:
                assert "Unknown tool" in str(exc)
        return calls

    expected = ["alice"] if allowed else []
    assert await calls_made_through(listed_name) == expected
    assert await calls_made_through(SAVE) == expected


@pytest.mark.parametrize(
    "meta",
    [
        {"team": "crm"},
        {},
        None,
    ],
)
async def test_hashed_name_resolves_tools_renamed_with_replaced_meta(
    meta: dict[str, str] | None,
):
    calls: list[str] = []
    server = server_with(contacts_app(calls))
    server.add_transform(
        ToolTransform({"save": ToolTransformConfig(name="store", meta=meta)})
    )

    async with Client(server) as client:
        by_name = await client.call_tool("store", {"name": "alice"})
        by_hash = await client.call_tool(SAVE, {"name": "alice"})

    assert by_name.data == "saved alice"
    assert by_hash.data == "saved alice"
    assert calls == ["alice", "alice"]
