"""Tests for MCP Apps Phase 1 — SDK compatibility.

Covers UI metadata models, tool/resource registration with ``ui=``,
extension negotiation, and the ``Context.client_supports_extension`` method.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.server.apps import (
    UI_EXTENSION_ID,
    UI_MIME_TYPE,
    ResourceUI,
    ToolUI,
    ui_to_meta_dict,
)
from fastmcp.server.context import Context

# ---------------------------------------------------------------------------
# Model serialization
# ---------------------------------------------------------------------------


class TestToolUI:
    def test_serializes_with_aliases(self):
        ui = ToolUI(resource_uri="ui://my-app/view.html", visibility=["app"])
        d = ui.model_dump(by_alias=True, exclude_none=True)
        assert d == {"resourceUri": "ui://my-app/view.html", "visibility": ["app"]}

    def test_excludes_none_fields(self):
        ui = ToolUI(resource_uri="ui://foo")
        d = ui.model_dump(by_alias=True, exclude_none=True)
        assert d == {"resourceUri": "ui://foo"}

    def test_all_fields(self):
        ui = ToolUI(
            resource_uri="ui://app",
            visibility=["app", "model"],
            csp="default-src 'self'",
            permissions=["clipboard-read"],
            domain="example.com",
            prefers_border=True,
        )
        d = ui.model_dump(by_alias=True, exclude_none=True)
        assert d == {
            "resourceUri": "ui://app",
            "visibility": ["app", "model"],
            "csp": "default-src 'self'",
            "permissions": ["clipboard-read"],
            "domain": "example.com",
            "prefersBorder": True,
        }

    def test_populate_by_name(self):
        ui = ToolUI(resource_uri="ui://app")
        assert ui.resource_uri == "ui://app"


class TestResourceUI:
    def test_serializes_with_aliases(self):
        ui = ResourceUI(prefers_border=True, csp="default-src 'self'")
        d = ui.model_dump(by_alias=True, exclude_none=True)
        assert d == {"prefersBorder": True, "csp": "default-src 'self'"}

    def test_excludes_none_fields(self):
        ui = ResourceUI()
        d = ui.model_dump(by_alias=True, exclude_none=True)
        assert d == {}


class TestUIToMetaDict:
    def test_from_tool_ui(self):
        ui = ToolUI(resource_uri="ui://app", visibility=["app"])
        result = ui_to_meta_dict(ui)
        assert result["resourceUri"] == "ui://app"
        assert result["visibility"] == ["app"]

    def test_from_resource_ui(self):
        ui = ResourceUI(prefers_border=False)
        result = ui_to_meta_dict(ui)
        assert result == {"prefersBorder": False}

    def test_passthrough_for_dict(self):
        raw: dict[str, Any] = {"resourceUri": "ui://app", "custom": "value"}
        result = ui_to_meta_dict(raw)
        assert result is raw


# ---------------------------------------------------------------------------
# Tool registration with ui=
# ---------------------------------------------------------------------------


class TestToolRegistrationWithUI:
    async def test_tool_ui_model(self):
        server = FastMCP("test")

        @server.tool(ui=ToolUI(resource_uri="ui://my-app/view.html"))
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        assert len(tools) == 1
        assert tools[0].meta is not None
        assert tools[0].meta["ui"]["resourceUri"] == "ui://my-app/view.html"

    async def test_tool_ui_dict(self):
        server = FastMCP("test")

        @server.tool(ui={"resourceUri": "ui://foo", "visibility": ["app"]})
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        assert tools[0].meta is not None
        assert tools[0].meta["ui"]["resourceUri"] == "ui://foo"
        assert tools[0].meta["ui"]["visibility"] == ["app"]

    async def test_ui_merges_with_existing_meta(self):
        server = FastMCP("test")

        @server.tool(meta={"custom": "data"}, ui=ToolUI(resource_uri="ui://app"))
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        meta = tools[0].meta
        assert meta is not None
        assert meta["custom"] == "data"
        assert meta["ui"]["resourceUri"] == "ui://app"

    async def test_ui_in_mcp_wire_format(self):
        server = FastMCP("test")

        @server.tool(ui=ToolUI(resource_uri="ui://app", visibility=["app"]))
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        mcp_tool = tools[0].to_mcp_tool()
        assert mcp_tool.meta is not None
        assert mcp_tool.meta["ui"]["resourceUri"] == "ui://app"
        assert mcp_tool.meta["ui"]["visibility"] == ["app"]

    async def test_tool_without_ui_has_no_ui_meta(self):
        server = FastMCP("test")

        @server.tool
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        meta = tools[0].meta
        assert meta is None or "ui" not in meta


# ---------------------------------------------------------------------------
# Resource registration with ui:// and ui=
# ---------------------------------------------------------------------------


class TestResourceWithUI:
    async def test_ui_scheme_defaults_mime_type(self):
        server = FastMCP("test")

        @server.resource("ui://my-app/view.html")
        def app_html() -> str:
            return "<html>hello</html>"

        resources = list(await server.list_resources())
        assert len(resources) == 1
        assert resources[0].mime_type == UI_MIME_TYPE

    async def test_explicit_mime_type_overrides_ui_default(self):
        server = FastMCP("test")

        @server.resource("ui://my-app/view.html", mime_type="text/html")
        def app_html() -> str:
            return "<html>hello</html>"

        resources = list(await server.list_resources())
        assert resources[0].mime_type == "text/html"

    async def test_resource_ui_metadata(self):
        server = FastMCP("test")

        @server.resource(
            "ui://my-app/view.html",
            ui=ResourceUI(prefers_border=True),
        )
        def app_html() -> str:
            return "<html>hello</html>"

        resources = list(await server.list_resources())
        assert resources[0].meta is not None
        assert resources[0].meta["ui"]["prefersBorder"] is True

    async def test_non_ui_scheme_no_mime_default(self):
        server = FastMCP("test")

        @server.resource("resource://data")
        def data() -> str:
            return "data"

        resources = list(await server.list_resources())
        assert resources[0].mime_type != UI_MIME_TYPE

    async def test_standalone_decorator_ui_scheme_defaults_mime_type(self):
        """Test that the standalone @resource decorator also applies ui:// MIME default."""
        from fastmcp.resources import resource

        @resource("ui://standalone-app/view.html")
        def standalone_app() -> str:
            return "<html>standalone</html>"

        server = FastMCP("test")
        server.add_resource(standalone_app)

        resources = list(await server.list_resources())
        assert len(resources) == 1
        assert resources[0].mime_type == UI_MIME_TYPE

    async def test_resource_template_ui_scheme_defaults_mime_type(self):
        """Test that resource templates also apply ui:// MIME default."""
        server = FastMCP("test")

        @server.resource("ui://template-app/{view}")
        def template_app(view: str) -> str:
            return f"<html>{view}</html>"

        templates = list(await server.list_resource_templates())
        assert len(templates) == 1
        assert templates[0].mime_type == UI_MIME_TYPE


# ---------------------------------------------------------------------------
# Extension advertisement
# ---------------------------------------------------------------------------


class TestExtensionAdvertisement:
    async def test_capabilities_include_ui_extension(self):
        server = FastMCP("test")

        @server.tool
        def my_tool() -> str:
            return "hello"

        async with Client(server) as client:
            init_result = client.initialize_result
            extras = init_result.capabilities.model_extra or {}
            extensions = extras.get("extensions", {})
            assert UI_EXTENSION_ID in extensions


# ---------------------------------------------------------------------------
# Context.client_supports_extension
# ---------------------------------------------------------------------------


class TestContextClientSupportsExtension:
    async def test_returns_false_when_no_session(self):
        server = FastMCP("test")
        async with Context(fastmcp=server) as ctx:
            assert ctx.client_supports_extension(UI_EXTENSION_ID) is False


# ---------------------------------------------------------------------------
# Integration — full client↔server round-trip
# ---------------------------------------------------------------------------


class TestIntegration:
    async def test_tool_with_ui_roundtrip(self):
        """UI metadata flows through to clients — no server-side stripping."""
        server = FastMCP("test")

        @server.tool(ui=ToolUI(resource_uri="ui://app/view.html", visibility=["app"]))
        async def my_tool() -> dict[str, str]:
            return {"result": "ok"}

        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            # _meta.ui is preserved — the host decides what to do with it
            meta = tools[0].meta
            assert meta is not None
            assert meta["ui"]["resourceUri"] == "ui://app/view.html"
            assert meta["ui"]["visibility"] == ["app"]

    async def test_resource_with_ui_scheme_roundtrip(self):
        server = FastMCP("test")

        @server.resource("ui://my-app/view.html")
        def app_html() -> str:
            return "<html><body>Hello</body></html>"

        async with Client(server) as client:
            resources = await client.list_resources()
            assert len(resources) == 1
            assert str(resources[0].uri) == "ui://my-app/view.html"
            assert resources[0].mimeType == UI_MIME_TYPE

    async def test_ui_tool_callable(self):
        """A tool registered with ui= is still callable normally."""
        server = FastMCP("test")

        @server.tool(ui=ToolUI(resource_uri="ui://app"))
        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(server) as client:
            result = await client.call_tool("greet", {"name": "Alice"})
            assert any("Hello, Alice!" in str(c) for c in result.content)

    async def test_extension_and_tool_together(self):
        """Server advertises extension AND tool has UI meta (stored on FastMCP Tool)."""
        server = FastMCP("test")

        @server.tool(ui=ToolUI(resource_uri="ui://dashboard", visibility=["app"]))
        def dashboard() -> str:
            return "data"

        # Verify the stored FastMCP Tool still has full metadata
        tools = list(await server.list_tools())
        assert tools[0].meta is not None
        assert tools[0].meta["ui"]["resourceUri"] == "ui://dashboard"

        # Verify the server advertises the extension
        async with Client(server) as client:
            extras = client.initialize_result.capabilities.model_extra or {}
            assert UI_EXTENSION_ID in extras.get("extensions", {})
