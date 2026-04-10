"""Tests for FastMCPApp — the composable application provider.

Covers:
- @app.tool() decorator (visibility, calling patterns)
- @app.ui() decorator (model visibility, CSP auto-wiring)
- get_app_tool routing through provider chain
- Callable resolver (_resolve_tool_ref)
- Composition with namespaced servers
- Provider interface delegation
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from prefab_ui.app import ResolvedTool
from prefab_ui.components import Text

from fastmcp import Client, FastMCP
from fastmcp.apps.app import (
    FastMCPApp,
    _make_resolver,
)
from fastmcp.tools.base import Tool

# ---------------------------------------------------------------------------
# @app.tool() decorator
# ---------------------------------------------------------------------------


class TestFastMCPAppInit:
    def test_app_name_with_underscores_ok(self):
        # The old `___` separator is gone — backend tool routing now uses
        # a hashed positional address rather than a name-based prefix, so
        # any character is fine inside an app name.
        FastMCPApp("my_app")
        FastMCPApp("my__app")
        FastMCPApp("my___app")


class TestAppTool:
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

    async def test_tool_has_app_name_in_meta(self):
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return name

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert meta["fastmcp"]["app"] == "contacts"

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

    async def test_ui_has_app_name_in_meta(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert meta["fastmcp"]["app"] == "test"

    async def test_ui_has_resource_uri(self):
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert meta["ui"]["resourceUri"] == "ui://prefab/renderer.html"

    async def test_ui_tool_has_no_csp(self):
        """CSP belongs on the UI resource, not the tool (per MCP Apps spec)."""
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "dashboard"

        tools = await app._list_tools()
        meta = tools[0].meta
        assert meta is not None
        assert "csp" not in meta["ui"]

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
    def test_resolve_string_at_root(self):
        """Empty mount_path means the calling tool is at the server root,
        so peer references stay bare — they refer to root-level tools
        callable by their normal display name."""
        result = _make_resolver()("save_contact")
        assert isinstance(result, ResolvedTool)
        assert result.name == "save_contact"

    def test_resolve_string_with_mount_path(self):
        """With a mount path the resolver formats peer references as the
        deterministic ``<hash>_<local_name>`` so the dispatcher can route
        them via the reverse-hash map regardless of transforms."""
        from fastmcp.server.providers.addressing import hashed_backend_name

        result = _make_resolver((0,))("store_files")
        assert isinstance(result, ResolvedTool)
        assert result.name == hashed_backend_name((0,), "store_files")

    def test_resolve_string_at_nested_mount_path(self):
        """Nested mount paths feed into the same hash function — the
        result is opaque from the outside but deterministic per-position."""
        from fastmcp.server.providers.addressing import hashed_backend_name

        result = _make_resolver((0, 1, 2))("save_contact")
        assert isinstance(result, ResolvedTool)
        assert result.name == hashed_backend_name((0, 1, 2), "save_contact")

    def test_resolve_callable_at_root(self):
        def my_tool():
            pass

        result = _make_resolver()(my_tool)
        assert isinstance(result, ResolvedTool)
        assert result.name == "my_tool"

    def test_resolve_callable_with_mount_path(self):
        from fastmcp.server.providers.addressing import hashed_backend_name

        def store_files():
            pass

        result = _make_resolver((0,))(store_files)
        assert isinstance(result, ResolvedTool)
        assert result.name == hashed_backend_name((0,), "store_files")

    def test_resolve_fastmcp_metadata(self):
        from fastmcp.tools.function_tool import ToolMeta

        def my_tool():
            pass

        my_tool.__fastmcp__ = ToolMeta(name="custom_name")  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]

        result = _make_resolver()(my_tool)
        assert isinstance(result, ResolvedTool)
        assert result.name == "custom_name"

    def test_resolve_fastmcp_metadata_with_mount_path(self):
        from fastmcp.server.providers.addressing import hashed_backend_name
        from fastmcp.tools.function_tool import ToolMeta

        def my_tool():
            pass

        my_tool.__fastmcp__ = ToolMeta(name="custom_name")  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]

        result = _make_resolver((0,))(my_tool)
        assert isinstance(result, ResolvedTool)
        assert result.name == hashed_backend_name((0,), "custom_name")

    def test_resolve_unresolvable_raises(self):
        with pytest.raises(ValueError):
            _make_resolver()(42)


# ---------------------------------------------------------------------------
# get_app_tool — provider chain routing
# ---------------------------------------------------------------------------


class TestGetAppTool:
    async def test_direct_lookup(self):
        """FastMCPApp.get_app_tool finds tools by app name + tool name."""
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return name

        tool = await app.get_app_tool("contacts", "save")
        assert tool is not None
        assert tool.name == "save"

    async def test_wrong_app_returns_none(self):
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return name

        assert await app.get_app_tool("billing", "save") is None

    async def test_wrong_tool_returns_none(self):
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return name

        assert await app.get_app_tool("contacts", "missing") is None

    async def test_two_apps_no_collision(self):
        """Two apps with the same tool name are disambiguated."""
        app1 = FastMCPApp("contacts")
        app2 = FastMCPApp("billing")

        @app1.tool()
        def save(name: str) -> str:
            return f"contact: {name}"

        @app2.tool("save")
        def save_billing(amount: int) -> str:
            return f"invoice: {amount}"

        server = FastMCP("Platform")
        server.add_provider(app1)
        server.add_provider(app2)

        t1 = await server.get_app_tool("contacts", "save")
        t2 = await server.get_app_tool("billing", "save")
        assert t1 is not None
        assert t2 is not None
        assert t1 is not t2

    async def test_survives_namespace_transform(self):
        """get_app_tool bypasses namespace transforms."""
        app = FastMCPApp("crm")

        @app.tool()
        def save_contact(name: str) -> str:
            return name

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        # Normal get_tool with untransformed name fails
        tool = await server.get_tool("save_contact")
        assert tool is None

        # get_app_tool bypasses transforms
        tool = await server.get_app_tool("crm", "save_contact")
        assert tool is not None
        assert tool.name == "save_contact"

    async def test_ui_tool_not_findable_via_get_app_tool(self):
        """@app.ui() tools have model visibility and should NOT be
        returned by get_app_tool (only app-visible tools are)."""
        app = FastMCPApp("dashboard")

        @app.ui()
        def show() -> str:
            return "ui"

        tool = await app.get_app_tool("dashboard", "show")
        assert tool is None

    async def test_model_visible_tool_findable(self):
        """@app.tool(model=True) has app visibility and IS findable."""
        app = FastMCPApp("test")

        @app.tool(model=True)
        def query(q: str) -> str:
            return q

        tool = await app.get_app_tool("test", "query")
        assert tool is not None


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class TestProviderInterface:
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
    async def test_call_tool_with_hashed_name(self):
        """A backend tool with visibility=['app'] is callable via its
        hashed-name address — the same form a Prefab UI's resolver would
        produce when serializing a peer reference."""
        from fastmcp.server.providers.addressing import hashed_backend_name

        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)

        result = await server.call_tool(
            hashed_backend_name((0,), "save"), {"name": "alice"}
        )
        assert result.content[0].text == "saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_call_tool_model_visible_uses_display_name(self):
        """Tools with visibility=['app','model'] are callable by display name."""
        app = FastMCPApp("test")

        @app.tool(model=True)
        def save(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)

        result = await server.call_tool("save", {"name": "bob"})
        assert result.content[0].text == "saved bob"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_hashed_name_survives_namespace_mount(self):
        """The hashed-name path bypasses display-layer transforms entirely.
        A FastMCPApp mounted under a Namespace transform still has its
        backend tools reachable via the same hash."""
        from fastmcp.server.providers.addressing import hashed_backend_name

        app = FastMCPApp("crm")

        @app.tool()
        def save_contact(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        result = await server.call_tool(
            hashed_backend_name((0,), "save_contact"), {"name": "alice"}
        )
        assert result.content[0].text == "saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_namespaced_display_name_also_works(self):
        """Model-visible tools still resolve through Namespace as before."""
        app = FastMCPApp("crm")

        @app.tool(model=True)
        def save_contact(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        result = await server.call_tool("crm_save_contact", {"name": "bob"})
        assert result.content[0].text == "saved bob"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_hashed_name_auth_blocks_unauthorized(self):
        """Auth checks run on the hashed-name dispatch path too."""
        from fastmcp.exceptions import NotFoundError
        from fastmcp.server.context import _current_transport
        from fastmcp.server.providers.addressing import hashed_backend_name

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
                await server.call_tool(hashed_backend_name((0,), "secret"), {})
        finally:
            _current_transport.reset(token)

    async def test_two_apps_same_tool_name_routed_by_address(self):
        """Two FastMCPApps each with a `save` tool live at distinct
        addresses, so they hash differently and the dispatcher routes
        each call to the right app without name collisions."""
        from fastmcp.server.providers.addressing import hashed_backend_name

        contacts = FastMCPApp("contacts")
        billing = FastMCPApp("billing")

        @contacts.tool()
        def save(name: str) -> str:
            return f"contact: {name}"

        @billing.tool("save")
        def save_billing(amount: str) -> str:
            return f"invoice: {amount}"

        server = FastMCP("Platform")
        server.add_provider(contacts)  # → address (0,)
        server.add_provider(billing)  # → address (1,)

        r1 = await server.call_tool(
            hashed_backend_name((0,), "save"), {"name": "alice"}
        )
        r2 = await server.call_tool(
            hashed_backend_name((1,), "save"), {"amount": "100"}
        )

        assert r1.content[0].text == "contact: alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert r2.content[0].text == "invoice: 100"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]


# ---------------------------------------------------------------------------
# App-only tool filtering from server list_tools / get_tool
# ---------------------------------------------------------------------------


class TestAppOnlyToolFiltering:
    async def test_app_only_tool_hidden_from_list_tools(self):
        """@app.tool() (visibility=["app"]) should not appear in server.list_tools()."""
        app = FastMCPApp("crm")

        @app.tool()
        def save_contact(name: str) -> str:
            return name

        server = FastMCP("Platform")
        server.add_provider(app)

        tools = await server.list_tools()
        names = [t.name for t in tools]
        assert "save_contact" not in names

    async def test_model_visible_tool_in_list_tools(self):
        """@app.tool(model=True) (visibility=["app","model"]) appears in list_tools."""
        app = FastMCPApp("crm")

        @app.tool(model=True)
        def query(search: str) -> list[str]:
            return [search]

        server = FastMCP("Platform")
        server.add_provider(app)

        tools = await server.list_tools()
        names = [t.name for t in tools]
        assert "query" in names

    async def test_ui_tool_in_list_tools(self):
        """@app.ui() (visibility=["model"]) appears in list_tools."""
        app = FastMCPApp("dashboard")

        @app.ui()
        def show_dashboard() -> str:
            return "dashboard"

        server = FastMCP("Platform")
        server.add_provider(app)

        tools = await server.list_tools()
        names = [t.name for t in tools]
        assert "show_dashboard" in names

    async def test_app_only_tool_still_callable_via_app_name(self):
        """Even though filtered from list_tools, app-only tools are callable via call_tool with app_name."""
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)

        # Verify it's hidden from list_tools
        tools = await server.list_tools()
        names = [t.name for t in tools]
        assert "save" not in names

        # But still callable via the hashed-address routing path.
        from fastmcp.server.providers.addressing import hashed_backend_name

        result = await server.call_tool(
            hashed_backend_name((0,), "save"), {"name": "alice"}
        )
        assert result.content[0].text == "saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_app_only_tool_hidden_from_get_tool(self):
        """server.get_tool() returns None for app-only tools."""
        app = FastMCPApp("crm")

        @app.tool()
        def save_contact(name: str) -> str:
            return name

        server = FastMCP("Platform")
        server.add_provider(app)

        tool = await server.get_tool("save_contact")
        assert tool is None

    async def test_app_only_tool_hidden_with_namespace(self):
        """App-only tools hidden even when accessed through a namespace."""
        app = FastMCPApp("crm")

        @app.tool()
        def save(name: str) -> str:
            return name

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        tools = await server.list_tools()
        names = [t.name for t in tools]
        assert "crm_save" not in names


# ---------------------------------------------------------------------------
# End-to-end via Client
# ---------------------------------------------------------------------------


class TestEndToEnd:
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
    async def test_add_tool_from_function(self):
        app = FastMCPApp("test")

        def save(name: str) -> str:
            return name

        tool = app.add_tool(save)
        assert tool.name == "save"

        tools = await app._list_tools()
        assert len(tools) == 1

    async def test_add_tool_tagged_with_app_name(self):
        app = FastMCPApp("myapp")

        def save(name: str) -> str:
            return name

        tool = app.add_tool(save)
        assert tool.meta is not None
        assert tool.meta["fastmcp"]["app"] == "myapp"

    async def test_add_tool_findable_via_get_app_tool(self):
        app = FastMCPApp("myapp")

        def save(name: str) -> str:
            return name

        app.add_tool(save)
        tool = await app.get_app_tool("myapp", "save")
        assert tool is not None

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

        from fastmcp.server.providers.addressing import hashed_backend_name

        # CRM is at address (0,), billing at (1,) — registration order.
        r1 = await server.call_tool(
            hashed_backend_name((0,), "save_contact"), {"name": "alice"}
        )
        r2 = await server.call_tool(
            hashed_backend_name((1,), "create_invoice"), {"amount": 100}
        )

        assert r1.content[0].text == "alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert r2.content[0].text == "100"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

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

    async def test_ui_synthesizes_per_tool_renderer_resource(self):
        """Each @app.ui() tool gets its own renderer resource synthesized
        on demand from the server's address registry."""
        app = FastMCPApp("test")

        @app.ui()
        def dashboard() -> str:
            return "ui"

        server = FastMCP("Platform")
        server.add_provider(app)

        resources = list(await server.list_resources())
        prefab = [r for r in resources if "prefab/tool" in str(r.uri)]
        assert len(prefab) == 1


# ---------------------------------------------------------------------------
# Integration: full end-to-end with client, namespacing, and structured content
# ---------------------------------------------------------------------------


class TestAppIntegration:
    async def test_full_app_lifecycle_through_client(self):
        """End-to-end: mount an app on a namespaced server, call UI tool
        through a client (verifying structured_content is returned), then
        call the backend tool via its hashed-address name."""
        from fastmcp.server.providers.addressing import hashed_backend_name

        app = FastMCPApp("contacts")

        @app.ui()
        def contact_form() -> Text:
            return Text(content="Enter contact details")

        @app.tool()
        def save_contact(name: str, email: str) -> dict[str, str]:
            return {"name": name, "email": email}

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        # The @app.ui() tool should be visible (namespaced) to the client.
        # The @app.tool() backend tool should NOT appear.
        async with Client(server) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "crm_contact_form" in tool_names
            assert "crm_save_contact" not in tool_names

            # Call the UI tool through the client and check structured_content
            result = await client.call_tool_mcp("crm_contact_form", {})
            sc = result.structuredContent
            assert sc is not None

        # Call the backend tool via its hashed address — bypasses namespace
        # transforms and visibility filtering by going through the registry.
        backend_result = await server.call_tool(
            hashed_backend_name((0,), "save_contact"),
            {"name": "Alice", "email": "alice@example.com"},
        )
        result_text = backend_result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "Alice" in result_text
        assert "alice@example.com" in result_text
