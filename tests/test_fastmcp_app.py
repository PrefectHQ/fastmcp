"""Tests for FastMCPApp — the composable application provider.

Covers:
- @app.tool() decorator (visibility, calling patterns)
- @app.ui() decorator (model visibility, CSP auto-wiring)
- App tool registry and call_tool routing
- Callable resolver (_resolve_tool_ref)
- Composition with namespaced servers
- Provider interface delegation
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from prefab_ui.app import ResolvedTool

from fastmcp import Client, FastMCP
from fastmcp.server.app import (
    _APP_TOOLS,
    FastMCPApp,
    _resolve_tool_ref,
    get_app_tool,
)
from fastmcp.tools.base import Tool

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _clear_registries() -> None:
    """Clear process-level registries between tests."""
    _APP_TOOLS.clear()


# ---------------------------------------------------------------------------
# @app.tool() decorator
# ---------------------------------------------------------------------------


class TestAppTool:
    def setup_method(self) -> None:
        _clear_registries()

    def test_tool_bare_decorator(self):
        app = FastMCPApp("test")

        @app.tool
        def save(name: str) -> str:
            return name

        assert save("alice") == "alice"

    def test_tool_empty_parens(self):
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        assert save("alice") == "alice"

    def test_tool_custom_name(self):
        app = FastMCPApp("test")

        @app.tool("custom_save")
        def save(name: str) -> str:
            return name

        assert save("alice") == "alice"

    def test_tool_name_kwarg(self):
        app = FastMCPApp("test")

        @app.tool(name="my_tool")
        def save(name: str) -> str:
            return name

        assert save("alice") == "alice"

    def test_tool_name_conflict_raises(self):
        app = FastMCPApp("test")

        with pytest.raises(TypeError):

            @app.tool("x", name="y")
            def save() -> str:
                return ""

    async def test_tool_registers_in_provider(self):
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        tools = await app._list_tools()
        assert len(tools) == 1
        assert tools[0].name == "save"

    async def test_tool_custom_name_in_provider(self):
        app = FastMCPApp("test")

        @app.tool("custom_save")
        def save(name: str) -> str:
            return name

        tools = await app._list_tools()
        assert len(tools) == 1
        assert tools[0].name == "custom_save"

    def test_tool_registered_in_app_tools(self):
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        assert ("test", "save") in _APP_TOOLS
        assert _APP_TOOLS[("test", "save")].name == "save"

    def test_tool_custom_name_registered_correctly(self):
        app = FastMCPApp("myapp")

        @app.tool("custom_save")
        def save(name: str) -> str:
            return name

        assert ("myapp", "custom_save") in _APP_TOOLS

    async def test_tool_default_visibility_app_only(self):
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert meta["ui"]["visibility"] == ["app"]

    async def test_tool_model_visibility(self):
        app = FastMCPApp("test")

        @app.tool(model=True)
        def query(search: str) -> list:
            return []

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert meta["ui"]["visibility"] == ["app", "model"]

    async def test_tool_no_global_key_in_meta(self):
        """Tools should NOT have globalKey in meta (removed in refactor)."""
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert "globalKey" not in meta.get("ui", {})

    def test_tool_with_description(self):
        app = FastMCPApp("test")

        @app.tool(description="Save a contact")
        def save(name: str) -> str:
            return name

    def test_tool_with_auth(self):
        app = FastMCPApp("test")
        check = AsyncMock(return_value=True)

        @app.tool(auth=check)
        def save(name: str) -> str:
            return name

    def test_tool_with_timeout(self):
        app = FastMCPApp("test")

        @app.tool(timeout=30.0)
        def slow_save(name: str) -> str:
            return name


# ---------------------------------------------------------------------------
# @app.ui() decorator
# ---------------------------------------------------------------------------


class TestAppUI:
    def setup_method(self) -> None:
        _clear_registries()

    def test_ui_bare_decorator(self):
        app = FastMCPApp("test")

        @app.ui
        def dashboard() -> str:
            return "hi"

        assert dashboard() == "hi"

    def test_ui_empty_parens(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "hi"

        assert dashboard() == "hi"

    def test_ui_custom_name(self):
        app = FastMCPApp("test")

        @app.ui("my_dashboard")
        def dashboard() -> str:
            return "hi"

        assert dashboard() == "hi"

    async def test_ui_registers_in_provider(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        assert len(tools) == 1
        assert tools[0].name == "dashboard"

    async def test_ui_visibility_model_only(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert meta["ui"]["visibility"] == ["model"]

    def test_ui_not_in_app_tools(self):
        """UI entry points should NOT be in the app tool registry."""
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        assert ("test", "dashboard") not in _APP_TOOLS

    async def test_ui_has_resource_uri(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert meta["ui"]["resourceUri"] == "ui://prefab/renderer.html"

    async def test_ui_has_csp(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        csp = meta["ui"].get("csp")
        assert csp is not None

    async def test_ui_with_title_and_description(self):
        app = FastMCPApp("test")

        @app.ui(title="My Dashboard", description="Shows data")
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        assert tools[0].title == "My Dashboard"
        assert tools[0].description == "Shows data"

    async def test_ui_with_tags(self):
        app = FastMCPApp("test")

        @app.ui(tags={"dashboard", "main"})
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        assert tools[0].tags == {"dashboard", "main"}


# ---------------------------------------------------------------------------
# Callable resolver
# ---------------------------------------------------------------------------


class TestResolveToolRef:
    def setup_method(self) -> None:
        _clear_registries()

    def test_resolve_string_passes_through(self):
        """Strings pass through as-is — server resolves at call time."""
        result = _resolve_tool_ref("save_contact")
        assert isinstance(result, ResolvedTool)
        assert result.name == "save_contact"

    def test_resolve_callable_uses_name(self):
        def my_tool():
            pass

        result = _resolve_tool_ref(my_tool)
        assert isinstance(result, ResolvedTool)
        assert result.name == "my_tool"

    def test_resolve_fastmcp_metadata(self):
        from fastmcp.tools.function_tool import ToolMeta

        def my_tool():
            pass

        my_tool.__fastmcp__ = ToolMeta(name="custom_name")  # type: ignore[attr-defined]

        result = _resolve_tool_ref(my_tool)
        assert isinstance(result, ResolvedTool)
        assert result.name == "custom_name"

    def test_resolve_unresolvable_raises(self):
        with pytest.raises(ValueError):
            _resolve_tool_ref(42)


# ---------------------------------------------------------------------------
# get_app_tool registry
# ---------------------------------------------------------------------------


class TestGetAppTool:
    def setup_method(self) -> None:
        _clear_registries()

    def test_lookup_by_app_and_tool_name(self):
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return name

        tool = get_app_tool("contacts", "save")
        assert tool is not None
        assert tool.name == "save"

    def test_lookup_wrong_app_returns_none(self):
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return name

        assert get_app_tool("billing", "save") is None

    def test_lookup_wrong_tool_returns_none(self):
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return name

        assert get_app_tool("contacts", "missing") is None

    def test_two_apps_same_tool_name_no_collision(self):
        app1 = FastMCPApp("contacts")
        app2 = FastMCPApp("billing")

        @app1.tool()
        def save(name: str) -> str:
            return f"contact: {name}"

        @app2.tool("save")
        def save_billing(amount: int) -> str:
            return f"invoice: {amount}"

        t1 = get_app_tool("contacts", "save")
        t2 = get_app_tool("billing", "save")
        assert t1 is not None
        assert t2 is not None
        assert t1 is not t2


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class TestProviderInterface:
    def setup_method(self) -> None:
        _clear_registries()

    async def test_list_tools_empty(self):
        app = FastMCPApp("test")
        assert await app._list_tools() == []

    async def test_list_resources_empty(self):
        app = FastMCPApp("test")
        assert list(await app._list_resources()) == []

    async def test_get_tool_by_name(self):
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        tool = await app._get_tool("save")
        assert tool is not None
        assert tool.name == "save"

    async def test_get_tool_missing_returns_none(self):
        app = FastMCPApp("test")
        assert await app._get_tool("missing") is None


# ---------------------------------------------------------------------------
# call_tool with app_name routing
# ---------------------------------------------------------------------------


class TestCallToolAppRouting:
    def setup_method(self) -> None:
        _clear_registries()

    async def test_call_tool_with_app_name(self):
        """Server.call_tool routes directly when app_name is provided."""
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)

        result = await server.call_tool("save", {"name": "alice"}, app_name="contacts")
        assert result.content[0].text == "saved alice"  # type: ignore[union-attr]

    async def test_call_tool_by_name_without_app_name(self):
        """Regular name-based resolution still works."""
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)

        result = await server.call_tool("save", {"name": "bob"})
        assert result.content[0].text == "saved bob"  # type: ignore[union-attr]

    async def test_app_name_survives_namespace(self):
        """app_name routing works even when the app is namespaced."""
        app = FastMCPApp("crm")

        @app.tool()
        def save_contact(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        # app_name routes directly, bypassing namespace
        result = await server.call_tool(
            "save_contact", {"name": "alice"}, app_name="crm"
        )
        assert result.content[0].text == "saved alice"  # type: ignore[union-attr]

    async def test_namespaced_name_also_works(self):
        """Namespaced tool name works through normal resolution."""
        app = FastMCPApp("crm")

        @app.tool(model=True)
        def save_contact(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        result = await server.call_tool("crm_save_contact", {"name": "bob"})
        assert result.content[0].text == "saved bob"  # type: ignore[union-attr]

    async def test_app_name_auth_blocks_unauthorized(self):
        """Auth checks run even when routing via app_name."""
        from fastmcp.exceptions import NotFoundError
        from fastmcp.server.context import _current_transport

        app = FastMCPApp("test")
        deny_all = AsyncMock(return_value=False)

        @app.tool(auth=deny_all)
        def secret() -> str:
            return "classified"

        server = FastMCP("Platform")
        server.add_provider(app)

        token = _current_transport.set("streamable-http")
        try:
            with pytest.raises(NotFoundError):
                await server.call_tool("secret", {}, app_name="test")
        finally:
            _current_transport.reset(token)

    async def test_two_apps_same_tool_name_routed_correctly(self):
        """Two apps with same tool name are disambiguated by app_name."""
        contacts = FastMCPApp("contacts")
        billing = FastMCPApp("billing")

        @contacts.tool()
        def save(name: str) -> str:
            return f"contact: {name}"

        @billing.tool("save")
        def save_billing(amount: str) -> str:
            return f"invoice: {amount}"

        server = FastMCP("Platform")
        server.add_provider(contacts)
        server.add_provider(billing)

        r1 = await server.call_tool("save", {"name": "alice"}, app_name="contacts")
        r2 = await server.call_tool("save", {"amount": "100"}, app_name="billing")

        assert r1.content[0].text == "contact: alice"  # type: ignore[union-attr]
        assert r2.content[0].text == "invoice: 100"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# End-to-end via Client
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def setup_method(self) -> None:
        _clear_registries()

    async def test_ui_tool_visible_to_client(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        server = FastMCP("Platform")
        server.add_provider(app)

        async with Client(server) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert "dashboard" in names

    async def test_app_tool_model_true_visible(self):
        app = FastMCPApp("test")

        @app.tool(model=True)
        def query(search: str) -> list:
            return [search]

        server = FastMCP("Platform")
        server.add_provider(app)

        async with Client(server) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert "query" in names


# ---------------------------------------------------------------------------
# .run() convenience
# ---------------------------------------------------------------------------


class TestRun:
    def test_repr(self):
        app = FastMCPApp("Dashboard")
        assert repr(app) == "FastMCPApp('Dashboard')"


# ---------------------------------------------------------------------------
# add_tool programmatic
# ---------------------------------------------------------------------------


class TestAddTool:
    def setup_method(self) -> None:
        _clear_registries()

    async def test_add_tool_from_function(self):
        app = FastMCPApp("test")

        def save(name: str) -> str:
            return name

        tool = app.add_tool(save)
        assert tool.name == "save"

        tools = await app._list_tools()
        assert len(tools) == 1

    async def test_add_tool_registered_in_app_tools(self):
        app = FastMCPApp("test")

        def save(name: str) -> str:
            return name

        app.add_tool(save)
        assert ("test", "save") in _APP_TOOLS

    async def test_add_tool_object(self):
        app = FastMCPApp("test")
        tool = Tool.from_function(lambda x: x, name="my_tool")
        added = app.add_tool(tool)
        assert added.name == "my_tool"

        tools = await app._list_tools()
        assert len(tools) == 1


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


class TestComposition:
    def setup_method(self) -> None:
        _clear_registries()

    async def test_multiple_apps_on_one_server(self):
        crm = FastMCPApp("CRM")
        billing = FastMCPApp("Billing")

        @crm.tool()
        def save_contact(name: str) -> str:
            return name

        @billing.tool()
        def create_invoice(amount: int) -> int:
            return amount

        server = FastMCP("Platform")
        server.add_provider(crm, namespace="crm")
        server.add_provider(billing, namespace="billing")

        r1 = await server.call_tool("save_contact", {"name": "alice"}, app_name="CRM")
        r2 = await server.call_tool(
            "create_invoice", {"amount": 100}, app_name="Billing"
        )

        assert r1.content[0].text == "alice"  # type: ignore[union-attr]
        assert r2.content[0].text == "100"  # type: ignore[union-attr]

    async def test_ui_and_tool_on_same_app(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "ui"

        @app.tool()
        def save(name: str) -> str:
            return name

        tools = await app._list_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"dashboard", "save"}

    async def test_ui_registers_prefab_renderer_resource(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "ui"

        resources = await app._list_resources()
        uris = [str(r.uri) for r in resources]
        assert any("ui://prefab/renderer.html" in uri for uri in uris)
