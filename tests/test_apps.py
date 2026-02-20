"""Tests for MCP Apps Phase 1 — SDK compatibility.

Covers app config models, tool/resource registration with ``app=``,
extension negotiation, and the ``Context.client_supports_extension`` method.
"""

from __future__ import annotations

from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.apps import (
    UI_EXTENSION_ID,
    UI_MIME_TYPE,
    AppConfig,
    ResourceCSP,
    ResourcePermissions,
    app_config_to_meta_dict,
)
from fastmcp.server.context import Context

# ---------------------------------------------------------------------------
# Model serialization
# ---------------------------------------------------------------------------


class TestAppConfig:
    def test_serializes_with_aliases(self):
        cfg = AppConfig(resource_uri="ui://my-app/view.html", visibility=["app"])
        d = cfg.model_dump(by_alias=True, exclude_none=True)
        assert d == {"resourceUri": "ui://my-app/view.html", "visibility": ["app"]}

    def test_excludes_none_fields(self):
        cfg = AppConfig(resource_uri="ui://foo")
        d = cfg.model_dump(by_alias=True, exclude_none=True)
        assert d == {"resourceUri": "ui://foo"}

    def test_all_fields(self):
        cfg = AppConfig(
            resource_uri="ui://app",
            visibility=["app", "model"],
            csp=ResourceCSP(resource_domains=["https://cdn.example.com"]),
            permissions=ResourcePermissions(camera={}, clipboard_write={}),
            domain="example.com",
            prefers_border=True,
        )
        d = cfg.model_dump(by_alias=True, exclude_none=True)
        assert d == {
            "resourceUri": "ui://app",
            "visibility": ["app", "model"],
            "csp": {"resourceDomains": ["https://cdn.example.com"]},
            "permissions": {"camera": {}, "clipboardWrite": {}},
            "domain": "example.com",
            "prefersBorder": True,
        }

    def test_populate_by_name(self):
        cfg = AppConfig(resource_uri="ui://app")
        assert cfg.resource_uri == "ui://app"


class TestResourceCSP:
    def test_serializes_with_aliases(self):
        csp = ResourceCSP(
            connect_domains=["https://api.example.com"],
            resource_domains=["https://cdn.example.com"],
        )
        d = csp.model_dump(by_alias=True, exclude_none=True)
        assert d == {
            "connectDomains": ["https://api.example.com"],
            "resourceDomains": ["https://cdn.example.com"],
        }

    def test_excludes_none_fields(self):
        csp = ResourceCSP(resource_domains=["https://unpkg.com"])
        d = csp.model_dump(by_alias=True, exclude_none=True)
        assert d == {"resourceDomains": ["https://unpkg.com"]}

    def test_all_fields(self):
        csp = ResourceCSP(
            connect_domains=["https://api.example.com"],
            resource_domains=["https://cdn.example.com"],
            frame_domains=["https://embed.example.com"],
            base_uri_domains=["https://base.example.com"],
        )
        d = csp.model_dump(by_alias=True, exclude_none=True)
        assert d == {
            "connectDomains": ["https://api.example.com"],
            "resourceDomains": ["https://cdn.example.com"],
            "frameDomains": ["https://embed.example.com"],
            "baseUriDomains": ["https://base.example.com"],
        }

    def test_populate_by_name(self):
        csp = ResourceCSP(connect_domains=["https://api.example.com"])
        assert csp.connect_domains == ["https://api.example.com"]

    def test_empty(self):
        csp = ResourceCSP()
        d = csp.model_dump(by_alias=True, exclude_none=True)
        assert d == {}

    def test_extra_fields_preserved(self):
        """Unknown CSP directives from future spec versions pass through."""
        csp = ResourceCSP(
            resource_domains=["https://cdn.example.com"],
            **{"workerDomains": ["https://worker.example.com"]},
        )
        d = csp.model_dump(by_alias=True, exclude_none=True)
        assert d["resourceDomains"] == ["https://cdn.example.com"]
        assert d["workerDomains"] == ["https://worker.example.com"]


class TestResourcePermissions:
    def test_serializes_with_aliases(self):
        perms = ResourcePermissions(microphone={}, clipboard_write={})
        d = perms.model_dump(by_alias=True, exclude_none=True)
        assert d == {"microphone": {}, "clipboardWrite": {}}

    def test_excludes_none_fields(self):
        perms = ResourcePermissions(camera={})
        d = perms.model_dump(by_alias=True, exclude_none=True)
        assert d == {"camera": {}}

    def test_all_fields(self):
        perms = ResourcePermissions(
            camera={}, microphone={}, geolocation={}, clipboard_write={}
        )
        d = perms.model_dump(by_alias=True, exclude_none=True)
        assert d == {
            "camera": {},
            "microphone": {},
            "geolocation": {},
            "clipboardWrite": {},
        }

    def test_populate_by_name(self):
        perms = ResourcePermissions(clipboard_write={})
        assert perms.clipboard_write == {}

    def test_extra_fields_preserved(self):
        """Unknown permissions from future spec versions pass through."""
        perms = ResourcePermissions(camera={}, **{"midi": {}})
        d = perms.model_dump(by_alias=True, exclude_none=True)
        assert d["camera"] == {}
        assert d["midi"] == {}

    def test_empty(self):
        perms = ResourcePermissions()
        d = perms.model_dump(by_alias=True, exclude_none=True)
        assert d == {}


class TestAppConfigForResources:
    """AppConfig without resource_uri/visibility — for use on resources."""

    def test_serializes_with_aliases(self):
        cfg = AppConfig(
            prefers_border=True,
            csp=ResourceCSP(resource_domains=["https://cdn.example.com"]),
        )
        d = cfg.model_dump(by_alias=True, exclude_none=True)
        assert d == {
            "prefersBorder": True,
            "csp": {"resourceDomains": ["https://cdn.example.com"]},
        }

    def test_excludes_none_fields(self):
        cfg = AppConfig()
        d = cfg.model_dump(by_alias=True, exclude_none=True)
        assert d == {}

    def test_with_permissions(self):
        cfg = AppConfig(
            permissions=ResourcePermissions(microphone={}, clipboard_write={}),
        )
        d = cfg.model_dump(by_alias=True, exclude_none=True)
        assert d == {
            "permissions": {"microphone": {}, "clipboardWrite": {}},
        }


class TestAppConfigToMetaDict:
    def test_from_app_config_with_tool_fields(self):
        cfg = AppConfig(resource_uri="ui://app", visibility=["app"])
        result = app_config_to_meta_dict(cfg)
        assert result["resourceUri"] == "ui://app"
        assert result["visibility"] == ["app"]

    def test_from_app_config_resource_fields_only(self):
        cfg = AppConfig(prefers_border=False)
        result = app_config_to_meta_dict(cfg)
        assert result == {"prefersBorder": False}

    def test_passthrough_for_dict(self):
        raw: dict[str, Any] = {"resourceUri": "ui://app", "custom": "value"}
        result = app_config_to_meta_dict(raw)
        assert result is raw


# ---------------------------------------------------------------------------
# Tool registration with app=
# ---------------------------------------------------------------------------


class TestToolRegistrationWithApp:
    async def test_app_config_model(self):
        server = FastMCP("test")

        @server.tool(app=AppConfig(resource_uri="ui://my-app/view.html"))
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        assert len(tools) == 1
        assert tools[0].meta is not None
        assert tools[0].meta["ui"]["resourceUri"] == "ui://my-app/view.html"

    async def test_app_dict(self):
        server = FastMCP("test")

        @server.tool(app={"resourceUri": "ui://foo", "visibility": ["app"]})
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        assert tools[0].meta is not None
        assert tools[0].meta["ui"]["resourceUri"] == "ui://foo"
        assert tools[0].meta["ui"]["visibility"] == ["app"]

    async def test_app_merges_with_existing_meta(self):
        server = FastMCP("test")

        @server.tool(meta={"custom": "data"}, app=AppConfig(resource_uri="ui://app"))
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        meta = tools[0].meta
        assert meta is not None
        assert meta["custom"] == "data"
        assert meta["ui"]["resourceUri"] == "ui://app"

    async def test_app_in_mcp_wire_format(self):
        server = FastMCP("test")

        @server.tool(app=AppConfig(resource_uri="ui://app", visibility=["app"]))
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        mcp_tool = tools[0].to_mcp_tool()
        assert mcp_tool.meta is not None
        assert mcp_tool.meta["ui"]["resourceUri"] == "ui://app"
        assert mcp_tool.meta["ui"]["visibility"] == ["app"]

    async def test_tool_without_app_has_no_ui_meta(self):
        server = FastMCP("test")

        @server.tool
        def my_tool() -> str:
            return "hello"

        tools = list(await server.list_tools())
        meta = tools[0].meta
        assert meta is None or "ui" not in meta


# ---------------------------------------------------------------------------
# Resource registration with ui:// and app=
# ---------------------------------------------------------------------------


class TestResourceWithApp:
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

    async def test_resource_app_metadata(self):
        server = FastMCP("test")

        @server.resource(
            "ui://my-app/view.html",
            app=AppConfig(prefers_border=True),
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
        """The standalone @resource decorator also applies ui:// MIME default."""
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
        """Resource templates also apply ui:// MIME default."""
        server = FastMCP("test")

        @server.resource("ui://template-app/{view}")
        def template_app(view: str) -> str:
            return f"<html>{view}</html>"

        templates = list(await server.list_resource_templates())
        assert len(templates) == 1
        assert templates[0].mime_type == UI_MIME_TYPE

    async def test_resource_rejects_resource_uri(self):
        """AppConfig with resource_uri raises ValueError on resources."""
        server = FastMCP("test")
        with pytest.raises(ValueError, match="resource_uri cannot be set on resources"):

            @server.resource(
                "ui://my-app/view.html",
                app=AppConfig(resource_uri="ui://other"),
            )
            def app_html() -> str:
                return "<html>hello</html>"

    async def test_resource_rejects_visibility(self):
        """AppConfig with visibility raises ValueError on resources."""
        server = FastMCP("test")
        with pytest.raises(ValueError, match="visibility cannot be set on resources"):

            @server.resource(
                "ui://my-app/view.html",
                app=AppConfig(visibility=["app"]),
            )
            def app_html() -> str:
                return "<html>hello</html>"


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
    async def test_tool_with_app_roundtrip(self):
        """App metadata flows through to clients — no server-side stripping."""
        server = FastMCP("test")

        @server.tool(
            app=AppConfig(resource_uri="ui://app/view.html", visibility=["app"])
        )
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

    async def test_ui_resource_read_preserves_mime_type(self):
        """Reading a ui:// resource returns content with the correct MIME type."""
        server = FastMCP("test")

        @server.resource("ui://my-app/view.html")
        def app_html() -> str:
            return "<html><body>Hello</body></html>"

        async with Client(server) as client:
            result = await client.read_resource_mcp("ui://my-app/view.html")
            assert len(result.contents) == 1
            assert result.contents[0].mimeType == UI_MIME_TYPE

    async def test_app_tool_callable(self):
        """A tool registered with app= is still callable normally."""
        server = FastMCP("test")

        @server.tool(app=AppConfig(resource_uri="ui://app"))
        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(server) as client:
            result = await client.call_tool("greet", {"name": "Alice"})
            assert any("Hello, Alice!" in str(c) for c in result.content)

    async def test_extension_and_tool_together(self):
        """Server advertises extension AND tool has app meta."""
        server = FastMCP("test")

        @server.tool(app=AppConfig(resource_uri="ui://dashboard", visibility=["app"]))
        def dashboard() -> str:
            return "data"

        tools = list(await server.list_tools())
        assert tools[0].meta is not None
        assert tools[0].meta["ui"]["resourceUri"] == "ui://dashboard"

        async with Client(server) as client:
            extras = client.initialize_result.capabilities.model_extra or {}
            assert UI_EXTENSION_ID in extras.get("extensions", {})

    async def test_csp_and_permissions_roundtrip(self):
        """CSP and permissions metadata flows through to clients correctly."""
        server = FastMCP("test")

        @server.resource(
            "ui://secure-app/view.html",
            app=AppConfig(
                csp=ResourceCSP(
                    resource_domains=["https://unpkg.com"],
                    connect_domains=["https://api.example.com"],
                ),
                permissions=ResourcePermissions(microphone={}, clipboard_write={}),
            ),
        )
        def secure_app() -> str:
            return "<html>secure</html>"

        @server.tool(
            app=AppConfig(
                resource_uri="ui://secure-app/view.html",
                csp=ResourceCSP(resource_domains=["https://cdn.example.com"]),
                permissions=ResourcePermissions(camera={}),
            )
        )
        def secure_tool() -> str:
            return "result"

        async with Client(server) as client:
            # Verify resource metadata
            resources = await client.list_resources()
            assert len(resources) == 1
            meta = resources[0].meta
            assert meta is not None
            assert meta["ui"]["csp"]["resourceDomains"] == ["https://unpkg.com"]
            assert meta["ui"]["csp"]["connectDomains"] == ["https://api.example.com"]
            assert meta["ui"]["permissions"]["microphone"] == {}
            assert meta["ui"]["permissions"]["clipboardWrite"] == {}

            # Verify tool metadata
            tools = await client.list_tools()
            assert len(tools) == 1
            tool_meta = tools[0].meta
            assert tool_meta is not None
            assert tool_meta["ui"]["csp"]["resourceDomains"] == [
                "https://cdn.example.com"
            ]
            assert tool_meta["ui"]["permissions"]["camera"] == {}

    async def test_resource_read_propagates_meta_to_content_items(self):
        """resources/read must include _meta on content items so hosts can read CSP."""
        server = FastMCP("test")

        @server.resource(
            "ui://csp-app/view.html",
            app=AppConfig(
                csp=ResourceCSP(resource_domains=["https://unpkg.com"]),
            ),
        )
        def app_view() -> str:
            return "<html>app</html>"

        async with Client(server) as client:
            read_result = await client.read_resource_mcp("ui://csp-app/view.html")
            content_item = read_result.contents[0]
            assert content_item.meta is not None
            assert content_item.meta["ui"]["csp"]["resourceDomains"] == [
                "https://unpkg.com"
            ]
