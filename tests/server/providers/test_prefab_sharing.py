"""Renderer identity follows its content configuration, independently of tools."""

import pytest
from mcp_types import TextResourceContents

from fastmcp import Client, FastMCP, FastMCPApp
from fastmcp.apps import AppConfig, PrefabAppConfig, ResourceCSP, ResourcePermissions
from fastmcp.server.providers.addressing import hashed_resource_uri
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ToolTransformConfig

pytest.importorskip("prefab_ui")


async def test_matching_tools_share_one_listed_resource():
    server = FastMCP("test")
    server.tool(name="first", app=True)(lambda: "first")
    server.tool(name="second", app=PrefabAppConfig())(lambda: "second")

    async with Client(server) as client:
        tools = await client.list_tools()
        uris = {t.meta["ui"]["resourceUri"] for t in tools}
        assert len(uris) == 1
        resources = await client.list_resources()
        assert {str(r.uri) for r in resources} == uris
        assert len(resources) == 1


@pytest.mark.parametrize(
    "config",
    [
        PrefabAppConfig(csp=ResourceCSP(connect_domains=["https://api.example.com"])),
        PrefabAppConfig(csp=ResourceCSP(resource_domains=["https://cdn.example.com"])),
        PrefabAppConfig(csp=ResourceCSP(frame_domains=["https://frame.example.com"])),
        PrefabAppConfig(csp=ResourceCSP(base_uri_domains=["https://base.example.com"])),
        PrefabAppConfig(permissions=ResourcePermissions(camera={})),
        PrefabAppConfig(domain="https://app.example.com"),
        PrefabAppConfig(prefers_border=True),
        PrefabAppConfig(prefers_border=False),
    ],
)
async def test_different_resource_metadata_stays_distinct(config):
    server = FastMCP("test")
    server.tool(name="default", app=True)(lambda: "default")
    server.tool(name="configured", app=config)(lambda: "configured")
    async with Client(server) as client:
        tools = await client.list_tools()
        assert tools[0].meta["ui"]["resourceUri"] != tools[1].meta["ui"]["resourceUri"]
        resources = await client.list_resources()
        assert len(resources) == 2
        for resource in resources:
            content = (await client.read_resource(resource.uri))[0]
            assert content.meta["ui"] == resource.meta["ui"]


async def test_csp_domain_order_does_not_change_identity():
    server = FastMCP("test")
    domains = ["https://a.example.com", "https://b.example.com"]
    for name, values in [("first", domains), ("second", domains[::-1])]:
        config = PrefabAppConfig(csp=ResourceCSP(frame_domains=values))
        server.tool(name=name, app=config)(lambda: "view")
    resources = await server.list_resources()
    assert len(resources) == 1


async def test_legacy_per_tool_uri_preserves_read_content_and_metadata():
    app = FastMCPApp("dashboard")
    app.ui(name="show")(lambda: "show")
    server = FastMCP("test")
    server.add_provider(app, namespace="crm")
    legacy = hashed_resource_uri("dashboard", "show")
    async with Client(server) as client:
        tool = (await client.list_tools())[0]
        uri = tool.meta["ui"]["resourceUri"]
        assert uri != legacy
        canonical = (await client.read_resource(uri))[0]
        alias = (await client.read_resource(legacy))[0]
        assert isinstance(alias, TextResourceContents)
        assert str(alias.uri) == legacy
        assert alias.text == canonical.text
        assert alias.mime_type == canonical.mime_type
        assert alias.meta == canonical.meta


async def test_nested_mounts_share_renderer_and_keep_backend_identity():
    child = FastMCP("child")
    for name in ["one", "two"]:
        app = FastMCPApp(name)
        app.ui(name="show")(lambda: "show")
        child.add_provider(app, namespace=name)
    parent = FastMCP("parent")
    parent.mount(child, namespace="nested")
    async with Client(parent) as client:
        tools = await client.list_tools()
        assert len({t.meta["fastmcp"]["tool_hash"] for t in tools}) == 2
        uris = {t.meta["ui"]["resourceUri"] for t in tools}
        assert len(uris) == 1
        for uri in uris:
            assert isinstance(
                (await client.read_resource(uri))[0], TextResourceContents
            )


async def test_custom_resource_uri_is_untouched():
    server = FastMCP("test")
    server.tool(name="custom", app=AppConfig(resource_uri="ui://custom/view.html"))(
        lambda: "view"
    )
    async with Client(server) as client:
        tool = (await client.list_tools())[0]
        assert tool.meta["ui"]["resourceUri"] == "ui://custom/view.html"
        assert await client.list_resources() == []


async def test_listing_does_not_load_html(monkeypatch):
    def unexpected_html(*args, **kwargs):
        pytest.fail("listing loaded renderer HTML")

    monkeypatch.setattr("prefab_ui.renderer.get_renderer_html", unexpected_html)
    server = FastMCP("test")
    server.tool(name="one", app=True)(lambda: "one")
    server.tool(name="two", app=True)(lambda: "two")
    async with Client(server) as client:
        assert len(await client.list_tools()) == 2
        assert len(await client.list_resources()) == 1


async def test_renderer_mode_and_version_change_identity(monkeypatch):
    monkeypatch.delenv("PREFAB_RENDERER_URL", raising=False)
    monkeypatch.delenv("PREFAB_BUNDLED_RENDERER", raising=False)
    server = FastMCP("test")
    server.tool(name="view", app=True)(lambda: "view")
    cdn = str((await server.list_resources())[0].uri)
    monkeypatch.setenv("PREFAB_BUNDLED_RENDERER", "1")
    bundled = str((await server.list_resources())[0].uri)
    assert cdn != bundled
    monkeypatch.setattr("prefab_ui.__version__", "999.0.0")
    upgraded = str((await server.list_resources())[0].uri)
    assert upgraded != bundled
    monkeypatch.setenv("PREFAB_RENDERER_URL", "https://dev.example.com")
    external = str((await server.list_resources())[0].uri)
    assert external != upgraded


@pytest.mark.parametrize("mounted", [False, True])
async def test_transformed_resource_configuration_is_readable(mounted):
    server = FastMCP("test")
    server.tool(name="view", app=True)(lambda: "view")
    server.add_transform(
        ToolTransform(
            {
                "view": ToolTransformConfig(
                    meta={
                        "ui": {
                            "resourceUri": "ui://prefab/renderer.html",
                            "domain": "https://isolated.example",
                        }
                    }
                )
            }
        )
    )
    if mounted:
        parent = FastMCP("parent")
        parent.mount(server, namespace="nested")
        server = parent
    async with Client(server) as client:
        tool = (await client.list_tools())[0]
        uri = tool.meta["ui"]["resourceUri"]
        resources = await client.list_resources()
        assert uri in {str(r.uri) for r in resources}
        content = (await client.read_resource(uri))[0]
        assert content.meta["ui"]["domain"] == "https://isolated.example"
