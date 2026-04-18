"""Tests for the FastMCP plugin primitive."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

import fastmcp
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.server.plugins import Plugin, PluginMeta
from fastmcp.server.plugins.base import (
    PluginCompatibilityError,
    PluginConfigError,
    PluginError,
)


class _TraceMiddleware(Middleware):
    """Tiny identity middleware tagged by name so we can see it in a stack."""

    def __init__(self, tag: str) -> None:
        self.tag = tag


class _Recorder:
    """Shared record of plugin lifecycle events for assertions in tests."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []


class TestPluginMeta:
    """PluginMeta is the source-of-truth metadata model."""

    def test_required_fields(self):
        meta = PluginMeta(name="x", version="0.1.0")
        assert meta.name == "x"
        assert meta.version == "0.1.0"
        assert meta.description is None
        assert meta.tags == []
        assert meta.dependencies == []
        assert meta.fastmcp_version is None
        assert meta.meta == {}

    def test_unknown_top_level_field_rejected(self):
        with pytest.raises(Exception):
            PluginMeta(name="x", version="0.1.0", owning_team="platform")  # ty: ignore[unknown-argument]

    def test_custom_fields_allowed_under_meta_dict(self):
        meta = PluginMeta(
            name="x",
            version="0.1.0",
            meta={"owning_team": "platform", "maintainer": "jlowin"},
        )
        assert meta.meta["owning_team"] == "platform"

    def test_subclass_can_add_typed_fields(self):
        class AcmeMeta(PluginMeta):
            owning_team: str

        meta = AcmeMeta(name="x", version="0.1.0", owning_team="platform")
        assert meta.owning_team == "platform"


class TestPluginConstruction:
    """Plugin construction validates meta and config at instantiation time."""

    def test_plugin_without_meta_raises(self):
        class NoMeta(Plugin):
            pass

        with pytest.raises(TypeError, match="meta"):
            NoMeta()

    def test_plugin_with_default_config(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        p = P()
        assert isinstance(p.config, Plugin.Config)

    def test_config_accepts_instance(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            class Config(BaseModel):
                who: str = "world"

        p = P(config=P.Config(who="jeremiah"))
        assert isinstance(p.config, P.Config)
        assert p.config.who == "jeremiah"

    def test_config_accepts_dict(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            class Config(BaseModel):
                who: str = "world"

        p = P(config={"who": "jeremiah"})
        assert isinstance(p.config, P.Config)
        assert p.config.who == "jeremiah"

    def test_invalid_config_raises_plugin_config_error(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            class Config(BaseModel):
                count: int

        with pytest.raises(PluginConfigError):
            P(config={"count": "not a number"})

    def test_bad_config_type_raises(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        with pytest.raises(PluginConfigError):
            P(config="not a config")  # ty: ignore[invalid-argument-type]


class TestPluginValidation:
    """Meta validation rejects malformed values eagerly."""

    def test_fastmcp_in_dependencies_rejected(self):
        class Bad(Plugin):
            meta = PluginMeta(
                name="bad",
                version="0.1.0",
                dependencies=["fastmcp>=3.0"],
            )

        with pytest.raises(PluginError, match="fastmcp"):
            Bad()

    def test_invalid_dependency_spec_rejected(self):
        class Bad(Plugin):
            meta = PluginMeta(
                name="bad",
                version="0.1.0",
                dependencies=["not a valid pep508 spec!!"],
            )

        with pytest.raises(PluginError, match="PEP 508"):
            Bad()

    def test_invalid_fastmcp_version_spec_rejected(self):
        class Bad(Plugin):
            meta = PluginMeta(
                name="bad",
                version="0.1.0",
                fastmcp_version="not-a-specifier",
            )

        with pytest.raises(PluginError, match="fastmcp_version"):
            Bad()

    def test_incompatible_fastmcp_version_raises(self, monkeypatch):
        # Pin the version we're checking against so the test doesn't depend
        # on whatever build-time version the running interpreter has (CI
        # builds can resolve to "0.0.0" via uv-dynamic-versioning's
        # fallback, which would match specifiers like "<0.1").
        monkeypatch.setattr(fastmcp, "__version__", "3.0.0")

        class Incompat(Plugin):
            meta = PluginMeta(
                name="incompat",
                version="0.1.0",
                fastmcp_version=">=100.0.0",
            )

        with pytest.raises(PluginCompatibilityError):
            Incompat().check_fastmcp_compatibility()


class TestRegistration:
    """Plugins register before startup; add_plugin is a list append."""

    def test_plugins_kwarg_registers(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        mcp = FastMCP("t", plugins=[P(), P()])
        assert [p.meta.name for p in mcp.plugins] == ["p", "p"]

    def test_add_plugin_appends(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        mcp = FastMCP("t")
        mcp.add_plugin(P())
        mcp.add_plugin(P())
        assert len(mcp.plugins) == 2

    def test_duplicates_allowed(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        mcp = FastMCP("t")
        mcp.add_plugin(P())
        mcp.add_plugin(P())
        # No dedup, no warn, no raise.
        assert len(mcp.plugins) == 2

    def test_add_plugin_checks_fastmcp_version_at_registration(self, monkeypatch):
        monkeypatch.setattr(fastmcp, "__version__", "3.0.0")

        class Incompat(Plugin):
            meta = PluginMeta(
                name="incompat",
                version="0.1.0",
                fastmcp_version=">=100.0.0",
            )

        mcp = FastMCP("t")
        with pytest.raises(PluginCompatibilityError):
            mcp.add_plugin(Incompat())

    def test_add_plugin_does_not_call_setup(self):
        """setup() runs during startup, not at add_plugin."""

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            async def setup(self, server):
                raise AssertionError("setup should not run at registration time")

        mcp = FastMCP("t")
        mcp.add_plugin(P())  # must not raise


class TestLifecycle:
    """Setup and teardown run during the server's lifespan."""

    async def test_setup_runs_during_startup(self):
        recorder = _Recorder()

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            async def setup(self, server):
                recorder.events.append(("setup", "p"))

            async def teardown(self):
                recorder.events.append(("teardown", "p"))

        mcp = FastMCP("t", plugins=[P()])
        async with Client(mcp) as c:
            await c.ping()
        assert recorder.events == [("setup", "p"), ("teardown", "p")]

    async def test_setup_order_follows_registration(self):
        recorder = _Recorder()

        def make(name: str) -> type[Plugin]:
            class _P(Plugin):
                meta = PluginMeta(name=name, version="0.1.0")

                async def setup(self, server):
                    recorder.events.append(("setup", name))

                async def teardown(self):
                    recorder.events.append(("teardown", name))

            return _P

        A, B, C = make("a"), make("b"), make("c")
        mcp = FastMCP("t", plugins=[A(), B()])
        mcp.add_plugin(C())

        async with Client(mcp) as c:
            await c.ping()

        # Setup in registration order; teardown reversed.
        assert [e for e in recorder.events if e[0] == "setup"] == [
            ("setup", "a"),
            ("setup", "b"),
            ("setup", "c"),
        ]
        assert [e for e in recorder.events if e[0] == "teardown"] == [
            ("teardown", "c"),
            ("teardown", "b"),
            ("teardown", "a"),
        ]

    async def test_loader_pattern_adds_plugins_during_setup(self):
        """A plugin's setup() can call server.add_plugin() and the setup pass sees it.

        Mid-cycle the loader-added children are present; after teardown
        they're removed (ephemeral cleanup), so the loader can freshly
        re-hydrate them on the next cycle.
        """
        recorder = _Recorder()

        class Child(Plugin):
            meta = PluginMeta(name="child", version="0.1.0")

            async def setup(self, server):
                recorder.events.append(("setup", "child"))

        class Loader(Plugin):
            meta = PluginMeta(name="loader", version="0.1.0")

            async def setup(self, server):
                recorder.events.append(("setup", "loader"))
                server.add_plugin(Child())
                server.add_plugin(Child())

        mcp = FastMCP("t", plugins=[Loader()])
        async with Client(mcp) as c:
            await c.ping()
            # Mid-cycle, the loader's children are registered.
            assert [p.meta.name for p in mcp.plugins] == [
                "loader",
                "child",
                "child",
            ]

        assert recorder.events == [
            ("setup", "loader"),
            ("setup", "child"),
            ("setup", "child"),
        ]
        # After teardown, ephemeral children have been removed.
        assert [p.meta.name for p in mcp.plugins] == ["loader"]

    async def test_add_plugin_after_startup_raises(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        mcp = FastMCP("t")
        async with Client(mcp) as c:
            await c.ping()
            with pytest.raises(PluginError, match="already started"):
                mcp.add_plugin(P())

    async def test_duplicate_registration_tears_down_once(self):
        """Registering the same instance twice must only call teardown() once.

        setup() runs per list entry (so the plugin receives both entries),
        but teardown() is an idempotent cleanup — a second call on a
        plugin that has closed its resources would likely raise on an
        already-closed connection.
        """
        recorder = _Recorder()

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            async def teardown(self):
                recorder.events.append(("teardown", "p"))

        p = P()
        mcp = FastMCP("t")
        mcp.add_plugin(p)
        mcp.add_plugin(p)

        async with Client(mcp) as c:
            await c.ping()

        assert [e for e in recorder.events if e[0] == "teardown"] == [
            ("teardown", "p"),
        ]

    async def test_teardown_exception_is_logged_not_raised(self):
        class Boom(Plugin):
            meta = PluginMeta(name="boom", version="0.1.0")

            async def teardown(self):
                raise RuntimeError("boom")

        mcp = FastMCP("t", plugins=[Boom()])
        # Should not raise out of the client context manager.
        async with Client(mcp) as c:
            await c.ping()

    async def test_setup_and_teardown_run_on_every_lifespan_cycle(self):
        """A server reused across multiple lifespan cycles re-runs setup/teardown."""
        recorder = _Recorder()

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            async def setup(self, server):
                recorder.events.append(("setup", "p"))

            async def teardown(self):
                recorder.events.append(("teardown", "p"))

        mcp = FastMCP("t", plugins=[P()])

        async with Client(mcp) as c:
            await c.ping()
        async with Client(mcp) as c:
            await c.ping()

        # Both cycles run setup and teardown; a one-shot guard would have
        # skipped the second cycle.
        assert recorder.events == [
            ("setup", "p"),
            ("teardown", "p"),
            ("setup", "p"),
            ("teardown", "p"),
        ]

    async def test_contributions_not_doubled_across_lifespan_cycles(self):
        """Contribution hooks are collected once per plugin, not per cycle."""

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            def middleware(self):
                return [_TraceMiddleware("p")]

        mcp = FastMCP("t", plugins=[P()])

        async with Client(mcp) as c:
            await c.ping()
        async with Client(mcp) as c:
            await c.ping()

        tags = [m.tag for m in mcp.middleware if isinstance(m, _TraceMiddleware)]
        assert tags == ["p"]

    async def test_teardown_runs_for_plugins_that_set_up_when_later_plugin_fails(self):
        """Partial-setup failure still triggers teardown on already-initialized plugins."""
        recorder = _Recorder()

        class Good(Plugin):
            meta = PluginMeta(name="good", version="0.1.0")

            async def setup(self, server):
                recorder.events.append(("setup", "good"))

            async def teardown(self):
                recorder.events.append(("teardown", "good"))

        class BadSetup(Plugin):
            meta = PluginMeta(name="bad", version="0.1.0")

            async def setup(self, server):
                recorder.events.append(("setup", "bad"))
                raise RuntimeError("setup failed")

            async def teardown(self):
                # Must not be called — setup() never completed.
                recorder.events.append(("teardown", "bad"))

        mcp = FastMCP("t", plugins=[Good(), BadSetup()])

        with pytest.raises(RuntimeError, match="setup failed"):
            async with Client(mcp) as c:
                await c.ping()

        assert ("setup", "good") in recorder.events
        assert ("setup", "bad") in recorder.events
        assert ("teardown", "good") in recorder.events
        # BadSetup never completed setup(); its teardown must not run.
        assert ("teardown", "bad") not in recorder.events

    async def test_contribution_collection_is_atomic_when_later_hook_raises(self):
        """A failing hook on one plugin must not leave partial contributions behind.

        If a plugin's ``middleware()`` succeeds but ``transforms()``
        raises, the middleware must not have been installed — otherwise a
        retry on the next lifespan attempt would pick up the plugin
        again (because we never marked it contributed) and append
        duplicate middleware on top of the partial prior state.
        """

        class Flaky(Plugin):
            meta = PluginMeta(name="flaky", version="0.1.0")
            _fail: bool = True

            def middleware(self):
                return [_TraceMiddleware("flaky")]

            def transforms(self):
                if Flaky._fail:
                    raise RuntimeError("transforms exploded")
                return []

        mcp = FastMCP("t", plugins=[Flaky()])
        baseline = list(mcp.middleware)

        with pytest.raises(RuntimeError, match="transforms exploded"):
            async with Client(mcp) as c:
                await c.ping()

        # Partial state from the failed cycle must not have landed.
        assert mcp.middleware == baseline

        # Retry succeeds; middleware is installed exactly once.
        Flaky._fail = False
        async with Client(mcp) as c:
            await c.ping()

        tags = [m.tag for m in mcp.middleware if isinstance(m, _TraceMiddleware)]
        assert tags == ["flaky"]

    async def test_add_plugin_is_atomic_when_routes_raises(self):
        """If plugin.routes() raises, the plugin must not be left in the server's list.

        Otherwise a later startup would run the half-registered plugin's
        lifecycle even though registration reported an error.
        """

        class RoutesBoom(Plugin):
            meta = PluginMeta(name="routes-boom", version="0.1.0")

            def routes(self):
                raise RuntimeError("routes exploded")

        mcp = FastMCP("t")
        with pytest.raises(RuntimeError, match="routes exploded"):
            mcp.add_plugin(RoutesBoom())

        assert mcp.plugins == []
        # Contribution book-keeping for the failed plugin was never created.
        # This is a weaker assertion — we just care the plugin isn't linger.
        assert not any(isinstance(p, RoutesBoom) for p in mcp.plugins)

    async def test_ephemeral_fastmcp_provider_is_removed_on_teardown(self):
        """Loader-added FastMCP providers are auto-wrapped; teardown must still remove them.

        ``add_provider`` wraps a FastMCP in a FastMCPProvider before it
        lands in ``self.providers``. Recording the pre-wrap object would
        cause teardown to miss the wrapped provider and leak it across
        cycles.
        """

        class ProviderPlugin(Plugin):
            meta = PluginMeta(name="wrapper", version="0.1.0")

            def __init__(self, config=None):
                super().__init__(config)
                self._child = FastMCP("child")

            def providers(self):
                return [self._child]

        class Loader(Plugin):
            meta = PluginMeta(name="loader", version="0.1.0")

            async def setup(self, server):
                server.add_plugin(ProviderPlugin())

        mcp = FastMCP("t", plugins=[Loader()])
        baseline_providers = list(mcp.providers)

        async with Client(mcp) as c:
            await c.ping()
        async with Client(mcp) as c:
            await c.ping()

        assert [p.meta.name for p in mcp.plugins] == ["loader"]
        # The wrapped provider that was added on each cycle was removed
        # on each teardown — the provider list is back to baseline.
        assert mcp.providers == baseline_providers

    async def test_loader_plugins_do_not_accumulate_across_cycles(self):
        """Loader-added (ephemeral) plugins and their contributions are removed on teardown.

        Without this, a loader that adds children in setup() causes the
        plugin list — and every contribution those children install — to
        grow on every lifespan cycle.
        """

        class Child(Plugin):
            meta = PluginMeta(name="child", version="0.1.0")

            def middleware(self):
                return [_TraceMiddleware("child")]

        class Loader(Plugin):
            meta = PluginMeta(name="loader", version="0.1.0")

            async def setup(self, server):
                server.add_plugin(Child())

        mcp = FastMCP("t", plugins=[Loader()])
        baseline_middleware = list(mcp.middleware)

        async with Client(mcp) as c:
            await c.ping()
        async with Client(mcp) as c:
            await c.ping()
        async with Client(mcp) as c:
            await c.ping()

        # After three cycles: the loader remains, the ephemeral child has
        # been removed, and the middleware it installed was reversed out
        # each time so nothing has accumulated.
        assert [p.meta.name for p in mcp.plugins] == ["loader"]
        assert mcp.middleware == baseline_middleware


class TestContributions:
    """Plugin contributions are installed during the setup pass."""

    async def test_middleware_contribution(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            def middleware(self):
                return [_TraceMiddleware("p")]

        mcp = FastMCP("t", plugins=[P()])
        async with Client(mcp) as c:
            await c.ping()

        tags = [m.tag for m in mcp.middleware if isinstance(m, _TraceMiddleware)]
        assert tags == ["p"]

    async def test_contribution_order_follows_registration(self):
        class P(Plugin):
            def __init__(self, name: str) -> None:
                super().__init__()
                self._name = name

            meta = PluginMeta(name="p", version="0.1.0")

            def middleware(self):
                return [_TraceMiddleware(self._name)]

        a, b = P("a"), P("b")
        mcp = FastMCP("t", plugins=[a, b])
        async with Client(mcp) as c:
            await c.ping()

        tags = [m.tag for m in mcp.middleware if isinstance(m, _TraceMiddleware)]
        assert tags == ["a", "b"]

    async def test_custom_route_contribution(self):
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def health(request):
            return JSONResponse({"ok": True})

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            def routes(self):
                return [Route("/healthz", endpoint=health, methods=["GET"])]

        mcp = FastMCP("t", plugins=[P()])
        async with Client(mcp) as c:
            await c.ping()

        assert any(
            getattr(r, "path", None) == "/healthz" for r in mcp._additional_http_routes
        )

    def test_plugin_route_mounted_on_http_app(self):
        """Plugin routes must be in place before http_app() snapshots routes.

        Regression test for collecting routes at ``add_plugin()`` time
        rather than during the lifespan's setup pass. HTTP transports
        call ``_get_additional_http_routes()`` at app construction, which
        happens before the lifespan runs; routes added during setup would
        sit in ``_additional_http_routes`` but never be mounted and would
        always 404.
        """

        def _walk_paths(routes):
            for route in routes:
                path = getattr(route, "path", None)
                if path is not None:
                    yield path
                inner = getattr(route, "routes", None)
                if inner:
                    yield from _walk_paths(inner)

        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def health(request):
            return JSONResponse({"ok": True})

        class Health(Plugin):
            meta = PluginMeta(name="health", version="0.1.0")

            def routes(self):
                return [Route("/healthz", endpoint=health, methods=["GET"])]

        mcp = FastMCP("t", plugins=[Health()])
        app = mcp.http_app()

        paths = set(_walk_paths(app.router.routes))
        assert "/healthz" in paths


class TestManifest:
    """manifest() produces a JSON-serializable dict and can write to disk."""

    def test_manifest_shape(self):
        class P(Plugin):
            meta = PluginMeta(
                name="p",
                version="0.1.0",
                description="demo",
                tags=["x"],
                dependencies=["demo>=0.1"],
                fastmcp_version=">=3.0",
                meta={"owning_team": "platform"},
            )

            class Config(BaseModel):
                who: str = "world"

        m = P.manifest()
        assert m is not None
        assert m["manifest_version"] == 1
        assert m["name"] == "p"
        assert m["version"] == "0.1.0"
        assert m["description"] == "demo"
        assert m["tags"] == ["x"]
        assert m["dependencies"] == ["demo>=0.1"]
        assert m["fastmcp_version"] == ">=3.0"
        assert m["meta"] == {"owning_team": "platform"}
        assert ":" in m["entry_point"]
        assert m["entry_point"].endswith(".P")
        assert m["config_schema"]["type"] == "object"
        assert "who" in m["config_schema"]["properties"]

    def test_manifest_custom_fields_subclass(self):
        class AcmeMeta(PluginMeta):
            owning_team: str

        class P(Plugin):
            meta = AcmeMeta(name="p", version="0.1.0", owning_team="platform")

        m = P.manifest()
        assert m is not None
        assert m["owning_team"] == "platform"

    def test_manifest_write_to_path(self, tmp_path: Path):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        out = tmp_path / "plugin.json"
        result = P.manifest(path=out)
        assert result is None
        data = json.loads(out.read_text())
        assert data["name"] == "p"

    def test_manifest_does_not_instantiate(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

            def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("manifest() must not instantiate the plugin")

        # Should succeed without calling __init__.
        assert P.manifest() is not None

    def test_manifest_validates_meta(self):
        """Invalid meta (e.g. malformed deps) must not emit a manifest.

        Otherwise `fastmcp plugin manifest` could publish artifacts with
        malformed PEP 508 dep strings or bad fastmcp_version specifiers —
        artifacts that downstream tooling can't parse consistently.
        """

        class BadDeps(Plugin):
            meta = PluginMeta(
                name="bad-deps",
                version="0.1.0",
                dependencies=["not a valid pep508 spec!!"],
            )

        with pytest.raises(PluginError, match="PEP 508"):
            BadDeps.manifest()

        class FastmcpInDeps(Plugin):
            meta = PluginMeta(
                name="fastmcp-in-deps",
                version="0.1.0",
                dependencies=["fastmcp>=3.0"],
            )

        with pytest.raises(PluginError, match="fastmcp"):
            FastmcpInDeps.manifest()
