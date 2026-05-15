"""End-to-end tests for the on-demand Prefab renderer synthesis.

The whole architecture exists to fix #3735 / PR #3754: a user passing a
``PrefabAppConfig(csp=ResourceCSP(frame_domains=[...]))`` should see
their ``frame_domains`` actually arrive on the renderer resource's CSP,
and CSP should NOT leak into the tool's wire metadata. These tests
exercise the synthesis path directly through the public server API.
"""

from __future__ import annotations

import pytest

from fastmcp import FastMCP, FastMCPApp

prefab_ui = pytest.importorskip("prefab_ui")
from fastmcp.apps.config import PrefabAppConfig, ResourceCSP  # noqa: E402


class TestUserCSPReachesResource:
    """The original bug: user CSP must land on the resource, not vanish."""

    async def test_frame_domains_reach_resource(self):
        mcp = FastMCP("test")

        @mcp.tool(
            app=PrefabAppConfig(
                csp=ResourceCSP(frame_domains=["https://example1234.com"])
            )
        )
        def show_widget() -> str:
            return "widget"

        # Find the synthesized prefab resource for this tool.
        resources = list(await mcp.list_resources())
        renderer = next((r for r in resources if "prefab/tool" in str(r.uri)), None)
        assert renderer is not None, "no prefab resource was synthesized"
        assert renderer.meta is not None

        csp = renderer.meta["ui"]["csp"]
        assert "https://example1234.com" in csp.get("frameDomains", []), (
            f"frame_domains missing from resource CSP: {csp}"
        )

    async def test_all_four_domain_fields_preserved(self):
        """The old singleton silently dropped frame_domains and
        base_uri_domains; the synthesizer covers all four fields."""
        mcp = FastMCP("test")

        @mcp.tool(
            app=PrefabAppConfig(
                csp=ResourceCSP(
                    connect_domains=["https://api.example.com"],
                    resource_domains=["https://cdn.example.com"],
                    frame_domains=["https://embed.example.com"],
                    base_uri_domains=["https://base.example.com"],
                )
            )
        )
        def widget() -> str:
            return "x"

        resources = list(await mcp.list_resources())
        renderer = next(r for r in resources if "prefab/tool" in str(r.uri))
        assert renderer.meta is not None
        csp = renderer.meta["ui"]["csp"]

        assert "https://api.example.com" in csp.get("connectDomains", [])
        assert "https://cdn.example.com" in csp.get("resourceDomains", [])
        assert "https://embed.example.com" in csp.get("frameDomains", [])
        assert "https://base.example.com" in csp.get("baseUriDomains", [])


class TestCSPStrippedFromToolMeta:
    """CSP belongs on the resource, not the tool. The wire format that
    clients see for tools must not contain it."""

    async def test_csp_not_in_listed_tool_meta(self):
        mcp = FastMCP("test")

        @mcp.tool(
            app=PrefabAppConfig(csp=ResourceCSP(frame_domains=["https://example.com"]))
        )
        def show_widget() -> str:
            return "widget"

        tools = list(await mcp.list_tools())
        tool = next(t for t in tools if t.name == "show_widget")
        assert tool.meta is not None
        ui = tool.meta["ui"]
        assert "csp" not in ui, f"csp leaked into tool meta: {ui}"
        assert "permissions" not in ui


class TestPerToolURIs:
    """Each prefab tool gets its own URI — distinct CSP per tool becomes
    possible because no two tools share a renderer resource."""

    async def test_two_tools_get_distinct_uris(self):
        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def tool_a() -> str:
            return "a"

        @mcp.tool(app=True)
        def tool_b() -> str:
            return "b"

        tools = list(await mcp.list_tools())
        a = next(t for t in tools if t.name == "tool_a")
        b = next(t for t in tools if t.name == "tool_b")
        assert a.meta is not None
        assert b.meta is not None
        uri_a = a.meta["ui"]["resourceUri"]
        uri_b = b.meta["ui"]["resourceUri"]
        assert uri_a != uri_b
        assert uri_a.startswith("ui://prefab/tool/")
        assert uri_b.startswith("ui://prefab/tool/")


class TestFastMCPAppMounts:
    """Tools inside FastMCPApps get URIs derived from the app's mount address."""

    async def test_app_tool_uri_uses_address(self):
        app = FastMCPApp("dashboard")

        @app.ui()
        def show() -> str:
            return "rendered"

        mcp = FastMCP("Platform")
        mcp.add_provider(app)

        resources = list(await mcp.list_resources())
        prefab = [r for r in resources if "prefab/tool" in str(r.uri)]
        assert len(prefab) == 1

    async def test_namespaced_mount_still_synthesizes_resource(self):
        app = FastMCPApp("crm")

        @app.ui()
        def contact_form() -> str:
            return "form"

        mcp = FastMCP("Platform")
        mcp.add_provider(app, namespace="customers")

        resources = list(await mcp.list_resources())
        prefab = [r for r in resources if "prefab/tool" in str(r.uri)]
        assert len(prefab) == 1


class TestReadResource:
    """The synthesized resources are actually fetchable via read_resource."""

    async def test_read_resource_returns_renderer_html(self):
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(app=True)
        def my_tool() -> str:
            return "hi"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            uri = next(t for t in tools if t.name == "my_tool").meta["ui"][
                "resourceUri"
            ]
            contents = await client.read_resource(uri)

        assert len(contents) > 0
        text = contents[0].text if hasattr(contents[0], "text") else ""
        assert "<html" in text.lower() or "<!doctype" in text.lower()


class TestNonPrefabToolsUntouched:
    async def test_plain_tool_has_no_ui_meta(self):
        mcp = FastMCP("test")

        @mcp.tool
        def greet(name: str) -> str:
            return name

        tools = list(await mcp.list_tools())
        tool = next(t for t in tools if t.name == "greet")
        assert not tool.meta or "ui" not in (tool.meta or {})

    async def test_plain_server_has_no_synthesized_resources(self):
        mcp = FastMCP("test")

        @mcp.tool
        def greet(name: str) -> str:
            return name

        resources = list(await mcp.list_resources())
        assert not any("prefab/tool" in str(r.uri) for r in resources)
