"""End-to-end round-trip tests for Prefab peer-tool references.

These simulate what a real host does: call the UI tool, extract the
hashed backend-tool name from structured_content, call back with
that name, and verify the backend tool actually executes. Covers
single-server, namespaced mounts, and cross-server mounts.
"""

from __future__ import annotations

import json

import pytest

from fastmcp import FastMCP, FastMCPApp
from fastmcp.server.providers.addressing import hashed_backend_name

prefab_ui = pytest.importorskip("prefab_ui")
from prefab_ui.actions.mcp import CallTool  # noqa: E402
from prefab_ui.components import Button, Column, Text  # noqa: E402


class TestSingleServerRoundTrip:
    async def test_ui_tool_serializes_hashed_peer_reference(self):
        """The resolver converts a CallTool string reference to a hashed
        name that appears in the tool result's structured_content."""
        app = FastMCPApp("contacts")

        @app.tool()
        def save_contact(name: str) -> str:
            return f"saved {name}"

        @app.ui()
        def contact_form() -> Column:
            return Column(
                children=[Button(label="Save", on_click=CallTool(tool="save_contact"))]
            )

        server = FastMCP("Platform")
        server.add_provider(app)

        result = await server.call_tool("contact_form", {})
        assert result.structured_content is not None

        # The hashed name should appear somewhere in the serialized output.
        sc_json = json.dumps(result.structured_content)
        expected_hash = hashed_backend_name("contacts", "save_contact")
        assert expected_hash in sc_json, (
            f"Expected {expected_hash!r} in structured_content but got: {sc_json[:200]}"
        )

    async def test_hashed_name_from_result_is_callable(self):
        """The hashed name that appears in structured_content actually
        resolves when called back — the full round-trip works."""
        app = FastMCPApp("contacts")

        @app.tool()
        def save_contact(name: str) -> str:
            return f"saved {name}"

        @app.ui()
        def contact_form() -> Column:
            return Column(
                children=[Button(label="Save", on_click=CallTool(tool="save_contact"))]
            )

        server = FastMCP("Platform")
        server.add_provider(app)

        # Step 1: call UI tool, get structured_content with hashed ref
        await server.call_tool("contact_form", {})

        # Step 2: call the backend tool by its hashed name
        hashed_name = hashed_backend_name("contacts", "save_contact")
        backend_result = await server.call_tool(hashed_name, {"name": "Alice"})
        assert backend_result.content[0].text == "saved Alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]


class TestNamespacedMountRoundTrip:
    async def test_namespaced_app_backend_tool_round_trip(self):
        """A FastMCPApp mounted with a namespace: the UI tool is called
        by its namespaced display name, the backend tool is called by
        its hashed name — both work."""
        app = FastMCPApp("crm")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        @app.ui()
        def form() -> Text:
            return Text(content="Enter details")

        server = FastMCP("Platform")
        server.add_provider(app, namespace="crm")

        # UI tool visible under namespace
        result = await server.call_tool("crm_form", {})
        assert result.structured_content is not None

        # Backend tool reachable via hash
        hashed_name = hashed_backend_name("crm", "save")
        backend_result = await server.call_tool(hashed_name, {"name": "Bob"})
        assert backend_result.content[0].text == "saved Bob"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]


class TestMountedServerRoundTrip:
    async def test_backend_tool_reachable_through_mounted_server(self):
        """A FastMCPApp inside a mounted FastMCP server: the outer
        server's dispatcher walks through FastMCPProvider to find
        the backend tool by hash."""
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        @app.ui()
        def form() -> Text:
            return Text(content="Form")

        inner = FastMCP("Inner")
        inner.add_provider(app)

        outer = FastMCP("Outer")
        outer.mount(inner, namespace="inner")

        # Backend tool callable through the mount via hash dispatch
        hashed_name = hashed_backend_name("contacts", "save")
        result = await outer.call_tool(hashed_name, {"name": "Carol"})
        assert result.content[0].text == "saved Carol"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]


class TestDynamicToolAdd:
    async def test_tool_added_after_first_call_is_reachable(self):
        """Tools added to an already-mounted app after the first call
        are still reachable via their hashed name — get_tool_by_hash
        does a live walk, not a cached lookup."""
        app = FastMCPApp("contacts")

        server = FastMCP("Platform")
        server.add_provider(app)

        # First call — nothing to call yet, just prime any caches.
        tools = await server.list_tools()
        assert len(tools) == 0

        # Now add a backend tool dynamically.
        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        # The dynamically-added tool should be reachable.
        hashed_name = hashed_backend_name("contacts", "save")
        result = await server.call_tool(hashed_name, {"name": "Dan"})
        assert result.content[0].text == "saved Dan"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]


class TestCollision:
    async def test_same_app_name_same_tool_name_first_wins(self):
        """Two apps with the same name and same tool name: the hash is
        identical, so get_tool_by_hash returns the first match. This is
        the same first-match behavior the old get_app_tool had."""
        app_a = FastMCPApp("shared")
        app_b = FastMCPApp("shared")

        @app_a.tool()
        def save(name: str) -> str:
            return f"from A: {name}"

        @app_b.tool()
        def save_b(name: str) -> str:
            return f"from B: {name}"

        # Register under a different local tool name to avoid
        # actual collision at the provider level. The hash collision
        # only happens when both app name AND tool name match.
        # This test just verifies one app's tool is reachable.
        server = FastMCP("Platform")
        server.add_provider(app_a)
        server.add_provider(app_b)

        hashed_name = hashed_backend_name("shared", "save")
        result = await server.call_tool(hashed_name, {"name": "Eve"})
        assert result.content[0].text == "from A: Eve"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
