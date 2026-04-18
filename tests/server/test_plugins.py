"""Tests for the FastMCP plugin primitive."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

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

    def test_incompatible_fastmcp_version_raises(self):
        class Incompat(Plugin):
            meta = PluginMeta(
                name="incompat",
                version="0.1.0",
                fastmcp_version="<0.1",
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

    def test_add_plugin_checks_fastmcp_version_at_registration(self):
        class Incompat(Plugin):
            meta = PluginMeta(
                name="incompat",
                version="0.1.0",
                fastmcp_version="<0.1",
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
        """A plugin's setup() can call server.add_plugin() and the setup pass sees it."""
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

        assert recorder.events == [
            ("setup", "loader"),
            ("setup", "child"),
            ("setup", "child"),
        ]
        assert [p.meta.name for p in mcp.plugins] == ["loader", "child", "child"]

    async def test_add_plugin_after_startup_raises(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        mcp = FastMCP("t")
        async with Client(mcp) as c:
            await c.ping()
            with pytest.raises(PluginError, match="already started"):
                mcp.add_plugin(P())

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
