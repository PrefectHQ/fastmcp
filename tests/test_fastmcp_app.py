"""Tests for FastMCPApp — the composable application provider.

Covers:
- @app.tool() decorator (global keys, visibility, calling patterns)
- @app.ui() decorator (model visibility, CSP auto-wiring)
- Global key registry and call_tool routing
- Callable resolver (_resolve_tool_ref)
- Composition with namespaced servers
- Provider interface delegation
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest
from prefab_ui.app import ResolvedTool

from fastmcp import Client, FastMCP
from fastmcp.server.app import (
    _APP_TOOL_REGISTRY,
    _FN_TO_GLOBAL_KEY,
    FastMCPApp,
    _make_global_key,
    _resolve_tool_ref,
)
from fastmcp.tools.base import Tool

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

GLOBAL_KEY_PATTERN = re.compile(r"^.+-[0-9a-f]{8}$")


def _clear_registries() -> None:
    """Clear process-level registries between tests."""
    _APP_TOOL_REGISTRY.clear()
    _FN_TO_GLOBAL_KEY.clear()


# ---------------------------------------------------------------------------
# Global key generation
# ---------------------------------------------------------------------------


class TestGlobalKeyGeneration:
    def test_make_global_key_format(self):
        key = _make_global_key("save_contact")
        assert GLOBAL_KEY_PATTERN.match(key)
        assert key.startswith("save_contact-")

    def test_make_global_key_uniqueness(self):
        keys = {_make_global_key("my_tool") for _ in range(100)}
        assert len(keys) == 100


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

        # Function is returned unchanged
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

    def test_tool_gets_global_key(self):
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        # Check the function is registered in the global key registry
        assert id(save) in _FN_TO_GLOBAL_KEY
        global_key = _FN_TO_GLOBAL_KEY[id(save)]
        assert GLOBAL_KEY_PATTERN.match(global_key)
        assert global_key.startswith("save-")
        assert global_key in _APP_TOOL_REGISTRY

    async def test_tool_global_key_in_meta(self):
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        tools = await app._list_tools()
        tool = tools[0]
        assert tool.meta is not None
        assert "ui" in tool.meta
        assert "globalKey" in tool.meta["ui"]
        assert GLOBAL_KEY_PATTERN.match(tool.meta["ui"]["globalKey"])

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

    def test_ui_no_global_key(self):
        """UI entry points should NOT get global keys."""
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        assert id(dashboard) not in _FN_TO_GLOBAL_KEY

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

    def test_resolve_global_key(self):
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        result = _resolve_tool_ref(save)
        # str return → wrapped tool → ResolvedTool with unwrap_result
        assert isinstance(result, ResolvedTool)
        assert GLOBAL_KEY_PATTERN.match(result.name)
        assert result.name.startswith("save-")
        assert result.unwrap_result is True

    def test_resolve_global_key_object_return(self):
        """Tools returning dicts don't need unwrapping."""

        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> dict:
            return {"name": name}

        result = _resolve_tool_ref(save)
        assert isinstance(result, ResolvedTool)
        assert GLOBAL_KEY_PATTERN.match(result.name)
        assert result.name.startswith("save-")
        assert result.unwrap_result is False

    def test_resolve_fastmcp_metadata(self):
        """Functions with __fastmcp__ metadata but no global key."""

        from fastmcp.tools.function_tool import ToolMeta

        def my_tool():
            pass

        my_tool.__fastmcp__ = ToolMeta(name="custom_name")  # type: ignore[attr-defined]

        result = _resolve_tool_ref(my_tool)
        assert isinstance(result, ResolvedTool)
        assert result.name == "custom_name"

    def test_resolve_bare_function(self):
        def my_tool():
            pass

        result = _resolve_tool_ref(my_tool)
        assert isinstance(result, ResolvedTool)
        assert result.name == "my_tool"

    def test_resolve_string_name(self):
        """CallTool("save") resolves to the global key."""
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return name

        result = _resolve_tool_ref("save")
        assert isinstance(result, ResolvedTool)
        assert GLOBAL_KEY_PATTERN.match(result.name)
        assert result.name.startswith("save-")
        assert result.unwrap_result is True

    def test_resolve_string_name_object_return(self):
        """String resolution also sets unwrap_result correctly."""
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> dict:
            return {"name": name}

        result = _resolve_tool_ref("save")
        assert isinstance(result, ResolvedTool)
        assert result.name.startswith("save-")
        assert result.unwrap_result is False

    def test_resolve_string_unknown_passes_through(self):
        """Unknown string names pass through as-is."""
        result = _resolve_tool_ref("unknown_tool")
        assert isinstance(result, ResolvedTool)
        assert result.name == "unknown_tool"

    def test_resolve_unresolvable_raises(self):
        with pytest.raises(ValueError):
            _resolve_tool_ref(42)


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
# call_tool with global key routing
# ---------------------------------------------------------------------------


class TestCallToolGlobalKeyRouting:
    def setup_method(self) -> None:
        _clear_registries()

    async def test_call_tool_by_global_key(self):
        """Server.call_tool can find a FastMCPApp tool by its global key."""
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)

        global_key = _FN_TO_GLOBAL_KEY[id(save)]

        result = await server.call_tool(global_key, {"name": "alice"})
        assert result.content[0].text == "saved alice"  # type: ignore[union-attr]

    async def test_call_tool_by_name(self):
        """Regular name-based resolution still works."""
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)

        result = await server.call_tool("save", {"name": "bob"})
        assert result.content[0].text == "saved bob"  # type: ignore[union-attr]

    async def test_global_key_survives_namespace(self):
        """Global key works even when the app is mounted under a namespace."""
        app = FastMCPApp("crm")

        @app.tool()
        def save_contact(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        global_key = _FN_TO_GLOBAL_KEY[id(save_contact)]

        # Global key should still work
        result = await server.call_tool(global_key, {"name": "alice"})
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

    async def test_global_key_auth_blocks_unauthorized(self):
        """Auth checks run even when resolving via global key."""
        from fastmcp.exceptions import NotFoundError
        from fastmcp.server.context import _current_transport

        app = FastMCPApp("test")
        deny_all = AsyncMock(return_value=False)

        @app.tool(auth=deny_all)
        def secret() -> str:
            return "classified"

        server = FastMCP("Platform")
        server.add_provider(app)

        global_key = _FN_TO_GLOBAL_KEY[id(secret)]

        # Simulate non-stdio transport so auth is not skipped
        token = _current_transport.set("streamable-http")
        try:
            with pytest.raises(NotFoundError):
                await server.call_tool(global_key, {})
        finally:
            _current_transport.reset(token)


# ---------------------------------------------------------------------------
# End-to-end via Client
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def setup_method(self) -> None:
        _clear_registries()

    async def test_ui_tool_visible_to_client(self):
        """UI entry-point tools show up in list_tools via Client."""
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
        """App tools with model=True are visible via Client."""
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

    async def test_call_tool_via_global_key_through_client(self):
        """Client can call a tool using its global key."""
        app = FastMCPApp("test")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)

        global_key = _FN_TO_GLOBAL_KEY[id(save)]

        async with Client(server) as client:
            result = await client.call_tool(global_key, {"name": "test"})
            assert "saved test" in result.content[0].text


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

    async def test_add_tool_gets_global_key(self):
        app = FastMCPApp("test")

        def save(name: str) -> str:
            return name

        tool = app.add_tool(save)
        assert tool.meta is not None
        assert "globalKey" in tool.meta.get("ui", {})

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

        # Both tools reachable by global key
        crm_key = _FN_TO_GLOBAL_KEY[id(save_contact)]
        billing_key = _FN_TO_GLOBAL_KEY[id(create_invoice)]

        r1 = await server.call_tool(crm_key, {"name": "alice"})
        r2 = await server.call_tool(billing_key, {"amount": 100})

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
