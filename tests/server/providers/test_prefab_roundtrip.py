"""End-to-end round-trip tests for Prefab peer-tool references.

These simulate what a real host does: call the UI tool, extract the
hashed backend-tool name from structured_content, call back with
that name, and verify the backend tool actually executes. Covers
single-server, namespaced mounts, and cross-server mounts.
"""

from __future__ import annotations

import pytest

from fastmcp import FastMCP, FastMCPApp
from fastmcp.exceptions import ToolError
from fastmcp.experimental.transforms.code_mode import CodeMode
from fastmcp.server.middleware.tool_injection import ToolInjectionMiddleware
from fastmcp.server.providers.addressing import hash_tool, hashed_backend_name
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from fastmcp.server.transforms.search import RegexSearchTransform
from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools.base import Tool
from fastmcp.tools.tool_transform import ToolTransformConfig

prefab_ui = pytest.importorskip("prefab_ui")
from prefab_ui.actions.mcp import CallTool  # noqa: E402
from prefab_ui.components import Button, Column, Text  # noqa: E402


def _tool_refs(payload) -> list[str]:
    """Every tool name the rendered UI would call, in document order."""
    refs: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("action") == "toolCall" and isinstance(node.get("tool"), str):
                refs.append(node["tool"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return refs


class TestSingleServerRoundTrip:
    async def test_payload_carries_the_servers_own_tool_name(self):
        """The renderer is handed a name that exists in this server's
        tools/list, not the identity-addressed form."""
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
        assert _tool_refs(result.structured_content) == ["save_contact"]

    async def test_payload_records_the_identity_behind_each_reference(self):
        """The identity-addressed form survives alongside the rewritten name,
        so an outer server can re-resolve it — or fall back to it."""
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
        names = result.structured_content["_meta"]["fastmcp"]["toolNames"]
        assert names == {
            "save_contact": hashed_backend_name("contacts", "save_contact")
        }

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


class TestProxiedServerRoundTrip:
    """A gateway proxying an app-bearing backend.

    A proxy knows only what crossed the wire, so this is the topology that
    breaks if app-only tools are filtered out of tools/list or if the
    identity hash is stripped from meta.
    """

    @staticmethod
    def _backend() -> FastMCP:
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        @app.ui()
        def form() -> Text:
            return Text(content="Form")

        backend = FastMCP("Backend")
        backend.add_provider(app)
        return backend

    async def test_app_only_tool_is_forwarded_through_a_proxy(self):
        backend = self._backend()
        gateway = FastMCP("Gateway")
        gateway.add_provider(ProxyProvider(lambda: ProxyClient(backend)))

        names = [t.name for t in await gateway.list_tools()]
        assert "save" in names

    async def test_identity_hash_survives_the_proxy(self):
        backend = self._backend()
        gateway = FastMCP("Gateway")
        gateway.add_provider(ProxyProvider(lambda: ProxyClient(backend)))

        tool = next(t for t in await gateway.list_tools() if t.name == "save")
        assert tool.meta is not None
        assert tool.meta["fastmcp"]["tool_hash"] == hash_tool("contacts", "save")

    async def test_backend_tool_callable_by_hash_through_a_proxy(self):
        backend = self._backend()
        gateway = FastMCP("Gateway")
        gateway.add_provider(ProxyProvider(lambda: ProxyClient(backend)))

        hashed_name = hashed_backend_name("contacts", "save")
        result = await gateway.call_tool(hashed_name, {"name": "Dana"})
        assert result.content[0].text == "saved Dana"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_backend_tool_callable_through_a_namespaced_proxy(self):
        backend = self._backend()
        gateway = FastMCP("Gateway")
        gateway.add_provider(
            ProxyProvider(lambda: ProxyClient(backend)), namespace="up"
        )

        names = [t.name for t in await gateway.list_tools()]
        assert "up_save" in names

        hashed_name = hashed_backend_name("contacts", "save")
        result = await gateway.call_tool(hashed_name, {"name": "Erin"})
        assert result.content[0].text == "saved Erin"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_backend_tool_callable_through_chained_proxies(self):
        backend = self._backend()
        middle = FastMCP("Middle")
        middle.add_provider(ProxyProvider(lambda: ProxyClient(backend)))
        top = FastMCP("Top")
        top.add_provider(ProxyProvider(lambda: ProxyClient(middle)))

        hashed_name = hashed_backend_name("contacts", "save")
        result = await top.call_tool(hashed_name, {"name": "Frank"})
        assert result.content[0].text == "saved Frank"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]


class TestLateBoundToolNames:
    """The payload is re-addressed on the way out of every FastMCP server.

    Servers unwind innermost-first, so the outermost one rewrites last and its
    names — the only ones a client can invoke — are what the renderer receives.
    """

    @staticmethod
    def _app(marker: str = "x", app_name: str = "contacts") -> FastMCPApp:
        app = FastMCPApp(app_name)

        @app.tool()
        def save(name: str) -> str:
            return f"[{marker}] saved {name}"

        @app.ui()
        def form() -> Column:
            return Column(
                children=[Button(label="Save", on_click=CallTool(tool="save"))]
            )

        return app

    async def test_namespaced_server_emits_its_namespaced_name(self):
        server = FastMCP("Platform")
        server.add_provider(self._app(), namespace="crm")

        result = await server.call_tool("crm_form", {})
        assert _tool_refs(result.structured_content) == ["crm_save"]

    async def test_name_accumulates_through_nested_mounts(self):
        inner = FastMCP("Inner")
        inner.add_provider(self._app(), namespace="a")
        mid = FastMCP("Mid")
        mid.add_provider(inner, namespace="b")
        top = FastMCP("Top")
        top.add_provider(mid, namespace="c")

        result = await top.call_tool("c_b_a_form", {})
        assert _tool_refs(result.structured_content) == ["c_b_a_save"]

    async def test_gateway_emits_its_own_name_not_the_backends(self):
        backend = FastMCP("Backend")
        backend.add_provider(self._app())

        gateway = FastMCP("Gateway")
        gateway.add_provider(
            ProxyProvider(lambda: ProxyClient(backend)), namespace="up"
        )

        result = await gateway.call_tool("up_form", {})
        assert _tool_refs(result.structured_content) == ["up_save"]

    async def test_emitted_name_is_callable_on_the_same_server(self):
        """The whole point: what the renderer is told to call, it can call."""
        backend = FastMCP("Backend")
        backend.add_provider(self._app(marker="be"))

        gateway = FastMCP("Gateway")
        gateway.add_provider(
            ProxyProvider(lambda: ProxyClient(backend)), namespace="up"
        )

        result = await gateway.call_tool("up_form", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref in [t.name for t in await gateway.list_tools()]

        clicked = await gateway.call_tool(ref, {"name": "alice"})
        assert clicked.content[0].text == "[be] saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    @pytest.mark.parametrize(
        "transform_factory,expected_listing",
        [
            (
                lambda: RegexSearchTransform(),
                ["search_tools", "call_tool"],
            ),
            (
                lambda: CodeMode(),
                ["search", "get_schema", "execute"],
            ),
        ],
        ids=["tool-search", "code-mode"],
    )
    async def test_survives_a_collapsed_catalog(
        self, transform_factory, expected_listing
    ):
        """Tool search and code mode replace tools/list wholesale, so there is
        no better name to bind to. The reference stays identity-addressed and
        the hashed path still resolves it."""
        server = FastMCP("Platform")
        server.add_provider(self._app(marker="cat"))
        server.add_transform(transform_factory())

        assert [t.name for t in await server.list_tools()] == expected_listing

        result = await server.call_tool("form", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == hashed_backend_name("contacts", "save")

        clicked = await server.call_tool(ref, {"name": "alice"})
        assert clicked.content[0].text == "[cat] saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    @pytest.mark.parametrize(
        "compose",
        ["siblings", "nested", "prefixing-namespaces"],
    )
    async def test_a_duplicated_app_is_not_bound(self, compose):
        """One app composed twice leaves no fact in the listing saying which
        copy a UI belongs to, so no name is bound and the reference keeps its
        identity. Covers copies as siblings, nested inside one subtree, and
        under namespaces that prefix one another.
        """
        if compose == "nested":
            inner = FastMCP("Inner")
            inner.add_provider(self._app(marker="A"), namespace="a")
            inner.add_provider(self._app(marker="B"), namespace="b")
            server = FastMCP("Top")
            server.add_provider(inner, namespace="outer")
            entry = "outer_a_form"
        else:
            second = "a_form" if compose == "prefixing-namespaces" else "b"
            server = FastMCP("Top")
            server.add_provider(self._app(marker="A"), namespace="a")
            server.add_provider(self._app(marker="B"), namespace=second)
            entry = "a_form"

        result = await server.call_tool(entry, {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == hashed_backend_name("contacts", "save")

    async def test_a_duplicated_app_reports_the_ambiguity(self):
        """The unbound reference must fail with a message that names the real
        cause, at any depth — a nested duplicate previously surfaced as
        `Unknown tool`, sending readers after a missing registration.
        """
        inner = FastMCP("Inner")
        inner.add_provider(self._app(marker="A"), namespace="a")
        inner.add_provider(self._app(marker="B"), namespace="b")
        server = FastMCP("Top")
        server.add_provider(inner, namespace="outer")

        result = await server.call_tool("outer_a_form", {})
        (ref,) = _tool_refs(result.structured_content)

        with pytest.raises(ToolError, match="composed more than once"):
            await server.call_tool(ref, {"name": "alice"})

    @pytest.mark.parametrize("backend_namespace", [None, "crm"])
    async def test_collapsed_catalog_over_a_proxy(self, backend_namespace):
        """The collapsed-catalog fallback has to survive a backend that
        renamed its app tools. Nothing named `save` was ever listed across
        the wire, so the identity has to resolve against the remote listing
        rather than against a name that only exists at the origin.
        """
        app = self._app(marker="be")
        backend = FastMCP("Backend")
        backend.add_provider(app, namespace=backend_namespace)

        gateway = FastMCP("Gateway")
        gateway.add_provider(ProxyProvider(lambda: ProxyClient(backend)))
        gateway.add_transform(RegexSearchTransform())

        entry = f"{backend_namespace}_form" if backend_namespace else "form"
        result = await gateway.call_tool(entry, {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == hashed_backend_name("contacts", "save")

        clicked = await gateway.call_tool(ref, {"name": "alice"})
        assert clicked.content[0].text == "[be] saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_versions_of_one_tool_are_a_single_target(self):
        """Versions are listed individually and share an identity, but they
        also share a name that resolves to the highest version on its own.
        Only distinct names mean distinct copies of an app.
        """
        app = FastMCPApp("contacts")
        for version, prefix in (("1.0.0", "v1"), ("2.0.0", "v2")):

            def save(name: str, _prefix: str = prefix) -> str:
                return f"{_prefix} saved {name}"

            app.add_tool(Tool.from_function(save, name="save", version=version))

        @app.ui()
        def form() -> Column:
            return Column(
                children=[Button(label="Save", on_click=CallTool(tool="save"))]
            )

        server = FastMCP("Platform")
        server.add_provider(app)

        result = await server.call_tool("form", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == "save"

        clicked = await server.call_tool(ref, {"name": "alice"})
        assert clicked.content[0].text == "v2 saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_distinct_apps_sharing_a_backend_name(self):
        """Identity and name must agree in both directions. Two apps can each
        expose `save`: the identities differ and each has one candidate, but
        the shared name resolves to only one of them.
        """
        server = FastMCP("Platform")
        for app_name, entry, marker in (
            ("crm", "crm_ui", "CRM"),
            ("billing", "billing_ui", "BILLING"),
        ):
            app = FastMCPApp(app_name)

            @app.tool()
            def save(name: str, _marker: str = marker) -> str:
                return f"[{_marker}] saved {name}"

            @app.ui(entry)
            def form() -> Column:
                return Column(
                    children=[Button(label="Save", on_click=CallTool(tool="save"))]
                )

            server.add_provider(app)

        result = await server.call_tool("billing_ui", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == hashed_backend_name("billing", "save")

    async def test_proxy_refuses_a_remote_that_duplicates_an_app(self):
        """A remote mounting one app twice sends back two tools claiming one
        identity, and the proxy must refuse on the same terms a local
        composition would rather than returning whichever came first.
        """
        backend = FastMCP("Backend")
        backend.add_provider(self._app(marker="A"), namespace="a")
        backend.add_provider(self._app(marker="B"), namespace="b")

        gateway = FastMCP("Gateway")
        gateway.add_provider(ProxyProvider(lambda: ProxyClient(backend)))

        with pytest.raises(ToolError, match="composed more than once"):
            await gateway.call_tool(
                hashed_backend_name("contacts", "save"), {"name": "alice"}
            )

    async def test_middleware_owns_the_names_it_shadows(self):
        """Binding describes the listing a client will see, so it has to run
        the middleware chain. An injected tool sharing a backend's name owns
        that name at call time, and would be invisible to a listing taken
        beneath middleware.
        """
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return f"[APP] saved {name}"

        @app.ui()
        def form() -> Column:
            return Column(
                children=[Button(label="Save", on_click=CallTool(tool="save"))]
            )

        def injected(name: str) -> str:
            return f"[INJECTED] saved {name}"

        server = FastMCP("Platform")
        server.add_provider(app)
        server.add_middleware(
            ToolInjectionMiddleware([Tool.from_function(injected, name="save")])
        )

        result = await server.call_tool("form", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == hashed_backend_name("contacts", "save")

        clicked = await server.call_tool(ref, {"name": "alice"})
        assert clicked.content[0].text == "[APP] saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_middleware_produced_results_are_rebound(self):
        """Middleware can answer a call itself, and such a result never
        reaches the core dispatch path — so rebinding belongs above the
        chain, not inside it.
        """
        app = FastMCPApp("contacts")

        @app.tool()
        def save(name: str) -> str:
            return f"saved {name}"

        @app.ui()
        def form() -> Column:
            return Column(
                children=[Button(label="Save", on_click=CallTool(tool="save"))]
            )

        server = FastMCP("Platform")
        server.add_provider(app)

        entry = await server.get_tool("form")
        assert entry is not None
        server.add_middleware(
            ToolInjectionMiddleware([entry.model_copy(update={"name": "injected"})])
        )

        result = await server.call_tool("injected", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == "save"

        clicked = await server.call_tool(ref, {"name": "alice"})
        assert clicked.content[0].text == "saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_a_transform_cannot_unwire_an_app_tool(self):
        """A meta override that keeps the identity but drops app visibility
        leaves a tool that can be named yet no longer answers to its
        identity — which is the only address a collapsed catalog has.
        """
        backend = FastMCP("Backend")
        backend.add_provider(self._app(marker="be"))
        backend.add_transform(
            ToolTransform({"save": ToolTransformConfig(meta={"team": "crm"})})
        )

        transformed = next(t for t in await backend.list_tools() if t.name == "save")
        assert transformed.meta is not None
        assert transformed.meta["ui"]["visibility"] == ["app"]
        assert transformed.meta["team"] == "crm"

        gateway = FastMCP("Gateway")
        gateway.add_provider(ProxyProvider(lambda: ProxyClient(backend)))
        gateway.add_transform(RegexSearchTransform())

        result = await gateway.call_tool("form", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == hashed_backend_name("contacts", "save")

        clicked = await gateway.call_tool(ref, {"name": "alice"})
        assert clicked.content[0].text == "[be] saved alice"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_duplicate_copies_are_not_collapsed_by_a_shared_name(self):
        """Copies whose backends collide on a name are the worst case, not the
        safe one: two components become indistinguishable. Counting names
        alone would see a single unambiguous target and bind to it.
        """
        server = FastMCP("Platform")
        for entry, marker in (("form_a", "A"), ("form_b", "B")):
            app = FastMCPApp("contacts")

            @app.tool()
            def save(name: str, _marker: str = marker) -> str:
                return f"[{_marker}] saved {name}"

            @app.ui(entry)
            def form() -> Column:
                return Column(
                    children=[Button(label="Save", on_click=CallTool(tool="save"))]
                )

            server.add_provider(app)

        listed = await server.list_tools()
        assert [t.key for t in listed].count("tool:save@") == 2

        result = await server.call_tool("form_b", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == hashed_backend_name("contacts", "save")

    async def test_unresolvable_identity_is_restored(self):
        """An inner server binds to a name that means nothing further out, so
        a reference this server cannot resolve is restored to its identity
        rather than left — a stranded name has no route back, an identity does.
        """
        app = FastMCPApp("contacts")

        @app.ui()
        def form() -> Column:
            return Column(
                children=[Button(label="Go", on_click=CallTool(tool="not_registered"))]
            )

        server = FastMCP("Platform")
        server.add_provider(app)

        result = await server.call_tool("form", {})
        (ref,) = _tool_refs(result.structured_content)
        assert ref == hashed_backend_name("contacts", "not_registered")


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
    async def test_distinct_hashes_resolve_independently(self):
        """Two apps sharing a name but with different tool names hash
        differently, so each tool resolves to itself."""
        app_a = FastMCPApp("shared")
        app_b = FastMCPApp("shared")

        @app_a.tool()
        def save(name: str) -> str:
            return f"from A: {name}"

        @app_b.tool()
        def save_b(name: str) -> str:
            return f"from B: {name}"

        server = FastMCP("Platform")
        server.add_provider(app_a)
        server.add_provider(app_b)

        result = await server.call_tool(
            hashed_backend_name("shared", "save"), {"name": "Eve"}
        )
        assert result.content[0].text == "from A: Eve"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

        result_b = await server.call_tool(
            hashed_backend_name("shared", "save_b"), {"name": "Eve"}
        )
        assert result_b.content[0].text == "from B: Eve"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_ambiguous_identity_raises_rather_than_guessing(self):
        """The same app composed into two branches yields two tools with one
        identity. Routing to either would silently execute the wrong branch's
        tool, so the call is refused."""
        server = FastMCP("Platform")
        for marker, namespace in (("A", "a"), ("B", "b")):
            app = FastMCPApp("contacts")

            @app.tool()
            def save(name: str, _marker: str = marker) -> str:
                return f"from {_marker}: {name}"

            server.add_provider(app, namespace=namespace)

        with pytest.raises(ToolError, match="Ambiguous app tool"):
            await server.call_tool(
                hashed_backend_name("contacts", "save"), {"name": "Eve"}
            )

    async def test_distinct_app_names_route_independently_through_a_gateway(self):
        """The multi-tenant gateway shape: distinct app names stay unambiguous
        no matter how many backends sit behind one proxy."""

        def backend(marker: str, app_name: str) -> FastMCP:
            app = FastMCPApp(app_name)

            @app.tool()
            def save(name: str) -> str:
                return f"from {marker}: {name}"

            server = FastMCP(f"Backend-{marker}")
            server.add_provider(app)
            return server

        first = backend("A", "crm")
        second = backend("B", "billing")

        gateway = FastMCP("Gateway")
        gateway.add_provider(ProxyProvider(lambda: ProxyClient(first)), namespace="a")
        gateway.add_provider(ProxyProvider(lambda: ProxyClient(second)), namespace="b")

        result_a = await gateway.call_tool(
            hashed_backend_name("crm", "save"), {"name": "Eve"}
        )
        assert result_a.content[0].text == "from A: Eve"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

        result_b = await gateway.call_tool(
            hashed_backend_name("billing", "save"), {"name": "Eve"}
        )
        assert result_b.content[0].text == "from B: Eve"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

class TestHashedNameHonorsDisable:
    async def test_disable_blocks_hashed_backend_name(self):
        """Hashed UI callbacks must not skip server.disable()."""
        from fastmcp.exceptions import NotFoundError

        app = FastMCPApp("Demo")

        @app.tool()
        def next_page(offset: int = 0) -> dict:
            return {"offset": offset}

        server = FastMCP("Repro")
        server.add_provider(app)
        hashed = hashed_backend_name("Demo", "next_page")

        result = await server.call_tool(hashed, {"offset": 1})
        assert result.structured_content == {"offset": 1}

        server.disable(names={"next_page"})
        with pytest.raises((NotFoundError, ToolError), match="Unknown tool"):
            await server.call_tool(hashed, {"offset": 2})
