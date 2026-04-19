"""Tests for the FastMCP plugin primitive."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from importlib import metadata as importlib_metadata
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import Generic, TypeVar

import pytest
from packaging.version import Version
from pydantic import BaseModel, ValidationError

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


class _TestPlugin(Plugin):
    """Base for test plugins. Relies on Plugin's auto-derived meta —
    subclasses override `meta` only when a test asserts on a specific
    name or version."""


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


class TestFromPackage:
    """PluginMeta.from_package() derives metadata from importlib.metadata."""

    # pydantic is a hard dependency of fastmcp, so it's always installed
    # in the test environment and has well-formed metadata we can read.
    # We deliberately don't use fastmcp itself as the smoke-test
    # distribution because from_package() refuses to pin fastmcp (see
    # test_fastmcp_as_distribution_is_rejected).

    def test_derives_version_description_from_real_package(self):
        meta = PluginMeta.from_package("pydantic", name="pydantic-smoke-test")

        assert meta.name == "pydantic-smoke-test"
        assert meta.version == dist_version("pydantic")
        # Description is whatever pydantic itself declares; only assert
        # that the field is populated.
        assert meta.description is not None
        # Dep pin uses `Version.public` (strips only local segment;
        # preserves pre/dev/post, which ARE valid with `>=` per PEP 440).
        public = Version(dist_version("pydantic")).public
        assert meta.dependencies == [f"pydantic>={public}"]

    def test_overrides_take_precedence(self):
        meta = PluginMeta.from_package(
            "pydantic",
            name="override-test",
            version="99.0.0",
            description="I override the derived description",
            tags=["security"],
        )
        assert meta.version == "99.0.0"
        assert meta.description == "I override the derived description"
        assert meta.tags == ["security"]

    def test_overriding_dependencies_replaces_the_pin(self):
        """If a plugin author passes dependencies explicitly, the containing
        distribution pin isn't re-added — author owns the list."""
        meta = PluginMeta.from_package(
            "pydantic",
            name="custom-deps",
            dependencies=["regex>=2024.0"],
        )
        assert meta.dependencies == ["regex>=2024.0"]

    def test_missing_distribution_raises_plugin_error(self):
        with pytest.raises(PluginError, match="not installed"):
            PluginMeta.from_package(
                "this-package-definitely-does-not-exist-1234abcd",
                name="missing",
            )

    def test_fastmcp_as_distribution_is_rejected(self):
        """`fastmcp` is implicit per the primitive contract; pinning it
        in `dependencies` would produce a manifest that fails validation."""
        with pytest.raises(PluginError, match="implicit"):
            PluginMeta.from_package("fastmcp", name="would-be-fastmcp-plugin")

    def test_fastmcp_rejection_is_case_insensitive(self):
        """PEP 503 canonicalization lowercases the distribution name, so
        `FastMCP` and `FASTMCP` both canonicalize to `fastmcp` and must
        be rejected. `fast-mcp` / `fast_mcp` canonicalize to `fast-mcp`
        — a different distribution — and are not rejected here."""
        for variant in ("FastMCP", "FASTMCP", "fAsTmCp"):
            with pytest.raises(PluginError, match="implicit"):
                PluginMeta.from_package(variant, name="x")

    @pytest.mark.parametrize(
        "dist_version_str, expected_pin",
        [
            # Pre/dev/post segments are valid with `>=` and must be
            # preserved so the pin tracks prerelease channels accurately.
            ("1.2.3.dev0", "synthetic-pin>=1.2.3.dev0"),
            ("1.2.3rc1", "synthetic-pin>=1.2.3rc1"),
            ("1.2.3.post1", "synthetic-pin>=1.2.3.post1"),
            # Local versions are NOT valid with `>=` per PEP 440; we
            # strip only that segment via Version.public.
            ("1.2.3+abc.def", "synthetic-pin>=1.2.3"),
            # Dev build with a local segment: strip just the local.
            ("1.2.3.dev5+abc123", "synthetic-pin>=1.2.3.dev5"),
            # Plain release — unchanged.
            ("2.0.0", "synthetic-pin>=2.0.0"),
        ],
    )
    def test_pin_preserves_pre_dev_post_but_strips_local(
        self, monkeypatch, dist_version_str, expected_pin
    ):
        """PEP 440 restricts only local versions from `>=` / `<=`;
        prereleases, dev, and post segments remain valid. The pin uses
        `Version.public` (strips only the local segment) so development
        channels keep their identity in the generated pin."""
        real_distribution = importlib_metadata.distribution

        class FakeDist:
            version = dist_version_str

            def __init__(self):
                self.metadata = real_distribution("pydantic").metadata

        def fake_distribution(name):
            if name == "synthetic-pin":
                return FakeDist()
            return real_distribution(name)

        # `from_package` reaches `importlib_metadata.distribution` through
        # the `plugins.base` module's alias; patch there.
        from fastmcp.server.plugins import base as plugins_base

        monkeypatch.setattr(
            plugins_base.importlib_metadata, "distribution", fake_distribution
        )

        meta = PluginMeta.from_package("synthetic-pin", name="pin-test")
        assert meta.dependencies == [expected_pin]

        # Resulting meta round-trips through _validate_meta cleanly.
        Plugin._validate_meta(meta)

    def test_whitespace_only_author_header_falls_back_to_email(self, monkeypatch):
        """A METADATA file with `Author: ` (whitespace only) must not
        block the `Author-email` fallback. Similar for `Home-page`
        falling back to Project-URL."""
        real_distribution = importlib_metadata.distribution
        pydantic_metadata = real_distribution("pydantic").metadata

        class FakeMessage:
            def items(self):
                return [
                    ("Metadata-Version", "2.1"),
                    ("Name", "whitespace-test"),
                    ("Version", "1.0.0"),
                    ("Author", "   "),  # whitespace only
                    ("Author-email", "real@example.com"),
                    ("Home-page", ""),  # empty
                    ("Project-URL", "Homepage, https://example.com"),
                ]

        class FakeDist:
            version = "1.0.0"
            metadata = FakeMessage()

        def fake_distribution(name):
            if name == "whitespace-test":
                return FakeDist()
            return real_distribution(name)

        from fastmcp.server.plugins import base as plugins_base

        monkeypatch.setattr(
            plugins_base.importlib_metadata, "distribution", fake_distribution
        )

        meta = PluginMeta.from_package("whitespace-test", name="ws-test")
        # Whitespace Author didn't block Author-email.
        assert meta.author == "real@example.com"
        # Empty Home-page fell through to the Project-URL label match.
        assert meta.homepage == "https://example.com"

        # Avoid "pydantic_metadata unused" lint noise.
        _ = pydantic_metadata

    def test_name_override_required_if_not_provided(self):
        """`name` is required on PluginMeta; from_package doesn't default
        it from the distribution name (plugin name and distribution name
        serve different purposes)."""
        with pytest.raises(ValidationError):
            PluginMeta.from_package("pydantic")


class TestPluginConstruction:
    """Plugin construction validates meta and config at instantiation time."""

    def test_plugin_without_meta_auto_derives_from_class_name(self):
        class ChannelPlugin(Plugin):
            pass

        p = ChannelPlugin()
        # Class name is kebab-cased and the trailing "Plugin" suffix stripped.
        assert p.meta.name == "channel"
        assert p.meta.version == "0.1.0"

    def test_plugin_meta_auto_derivation_handles_acronyms(self):
        class PIIRedactor(Plugin):
            pass

        class CodeMode(Plugin):
            pass

        class HTTPServerPlugin(Plugin):
            pass

        assert PIIRedactor.meta.name == "pii-redactor"
        assert CodeMode.meta.name == "code-mode"
        # Trailing "-plugin" stripped, internal acronym preserved.
        assert HTTPServerPlugin.meta.name == "http-server"

    def test_explicit_meta_is_not_overridden(self):
        class P(Plugin):
            meta = PluginMeta(name="custom", version="2.0.0")

        assert P.meta.name == "custom"
        assert P.meta.version == "2.0.0"

    def test_plugin_with_default_config(self):
        """A Plugin without a generic parameter gets an empty default config."""

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        p = P()
        assert isinstance(p.config, BaseModel)
        # No fields to inspect — the point is that construction works with None.

    def test_config_accepts_instance(self):
        class PConfig(BaseModel):
            who: str = "world"

        class P(Plugin[PConfig]):
            meta = PluginMeta(name="p", version="0.1.0")

        p = P(PConfig(who="jeremiah"))
        assert isinstance(p.config, PConfig)
        assert p.config.who == "jeremiah"

    def test_config_accepts_dict(self):
        class PConfig(BaseModel):
            who: str = "world"

        class P(Plugin[PConfig]):
            meta = PluginMeta(name="p", version="0.1.0")

        p = P({"who": "jeremiah"})
        assert isinstance(p.config, PConfig)
        assert p.config.who == "jeremiah"

    def test_generic_parameter_binds_config_cls(self):
        """`Plugin[ConfigType]` stashes the Config on the subclass so dict
        validation, manifest generation, and runtime introspection all use
        the author-declared model."""

        class PConfig(BaseModel):
            who: str = "world"

        class P(Plugin[PConfig]):
            meta = PluginMeta(name="p", version="0.1.0")

        assert P._config_cls is PConfig

    def test_unparameterized_plugin_uses_empty_default_config(self):
        """A Plugin without a generic parameter gets an empty default that
        rejects unknown keys (extra='forbid')."""

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        # No-arg construction works.
        P()
        # Unknown config keys are rejected by the empty default.
        with pytest.raises(PluginConfigError, match="extra"):
            P({"who": "jeremiah"})

    def test_invalid_config_raises_plugin_config_error(self):
        """Wrong-typed value for a declared field wraps ValidationError
        into PluginConfigError — exercising the generic Plugin[C] path."""

        class PConfig(BaseModel):
            count: int

        class P(Plugin[PConfig]):
            meta = PluginMeta(name="p", version="0.1.0")

        with pytest.raises(PluginConfigError, match="count"):
            P({"count": "not a number"})

    def test_required_field_missing_raises_plugin_config_error_on_no_args(self):
        """Required config field with no default must surface as
        PluginConfigError (not a raw pydantic.ValidationError) when the
        plugin is constructed with no arguments."""

        class PConfig(BaseModel):
            api_key: str  # required, no default

        class P(Plugin[PConfig]):
            meta = PluginMeta(name="p", version="0.1.0")

        with pytest.raises(PluginConfigError, match="api_key"):
            P()

    def test_bad_config_type_raises(self):
        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        with pytest.raises(PluginConfigError):
            P("not a config")  # type: ignore[arg-type]

    def test_non_basemodel_generic_arg_raises_at_class_creation(self):
        """`Plugin[T]` where T is not a pydantic BaseModel must fail loudly.

        The `# ty: ignore` tells the static checker that violating the type
        bound is intentional here — we're exercising the *runtime* guard.
        """

        class NotAModel:
            pass

        with pytest.raises(TypeError, match="BaseModel subclass"):

            class _Bad(Plugin[NotAModel]):  # ty: ignore[invalid-type-arguments]
                meta = PluginMeta(name="bad", version="0.1.0")

    def test_intermediate_generic_subclass_parameterization_is_not_misread_as_config(
        self,
    ):
        """A concrete subclass of an intermediate Plugin base with its
        own generic parameter must not have its generic arg misread as
        the plugin's config type.

        Given `class Intermediate(Plugin[Cfg], Generic[T])` and
        `class Concrete(Intermediate[int])`, `int` is the intermediate's
        own TypeVar substitution, NOT the plugin config. `Concrete`
        should inherit `Cfg` through the intermediate, not raise because
        `int` isn't a `BaseModel`.
        """
        _T = TypeVar("_T")

        class Cfg(BaseModel):
            value: int = 0

        class Intermediate(Plugin[Cfg], Generic[_T]):
            meta = PluginMeta(name="intermediate", version="0.1.0")

        class Concrete(Intermediate[int]):
            meta = PluginMeta(name="concrete", version="0.1.0")

        assert Intermediate._config_cls is Cfg
        assert Concrete._config_cls is Cfg
        assert isinstance(Concrete().config, Cfg)


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
            with pytest.raises(PluginError, match="plugin-entry pass"):
                mcp.add_plugin(P())

    async def test_add_plugin_raises_when_called_from_provider_lifespan(self):
        """Post-setup-pass registration must be rejected, not silently allowed.

        `_started` is set only after provider lifespans enter, so a
        provider's `lifespan()` callback runs with `_started` False but
        the plugin-entry pass already complete. Registering a plugin in
        that window would skip `run()` and contribution collection for
        the current cycle and leave the plugin in `self.plugins`; the
        guard must reject it.
        """
        from contextlib import asynccontextmanager

        from fastmcp.server.providers import Provider

        class PluginInProviderLifespan(Provider):
            def __init__(self, server):
                super().__init__()
                self.server = server
                self.raised: Exception | None = None

            @asynccontextmanager
            async def lifespan(self):
                class Late(Plugin):
                    meta = PluginMeta(name="late", version="0.1.0")

                try:
                    self.server.add_plugin(Late())
                except Exception as exc:
                    self.raised = exc
                yield

        mcp = FastMCP("t")
        provider = PluginInProviderLifespan(mcp)
        mcp.add_provider(provider)

        async with Client(mcp) as c:
            await c.ping()

        assert isinstance(provider.raised, PluginError)
        assert "plugin-entry pass" in str(provider.raised)

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

    async def test_ephemeral_cleanup_removes_by_identity_not_equality(self):
        """A permanent contribution that compares equal to an ephemeral one is preserved.

        list.remove() uses `==`, which is the wrong matcher when a
        middleware defines value-based equality. A loader-added middleware
        that happens to `==` a user-registered middleware must not cause
        the user's to be removed during ephemeral cleanup.
        """

        class EqMiddleware(Middleware):
            """Middleware that compares equal to any other EqMiddleware."""

            def __eq__(self, other):
                return isinstance(other, EqMiddleware)

            def __hash__(self):
                return 0

        permanent = EqMiddleware()

        class Child(Plugin):
            meta = PluginMeta(name="child", version="0.1.0")

            def middleware(self):
                # A distinct instance, but equal to `permanent` by __eq__.
                return [EqMiddleware()]

        class Loader(Plugin):
            meta = PluginMeta(name="loader", version="0.1.0")

            async def setup(self, server):
                server.add_plugin(Child())

        mcp = FastMCP("t", middleware=[permanent], plugins=[Loader()])
        assert permanent in mcp.middleware

        async with Client(mcp) as c:
            await c.ping()

        # The ephemeral child's middleware was removed; the permanent
        # user-registered one (which was `==` to it) is still installed.
        assert any(m is permanent for m in mcp.middleware)

    async def test_reregistering_ephemeral_instance_as_permanent_clears_marker(self):
        """A previously-ephemeral instance re-registered by the user is permanent.

        Without clearing the marker on normal `add_plugin`, the second
        registration would inherit `_fastmcp_ephemeral = True` from the
        first (loader-added) cycle and get deleted during teardown, losing
        its contributions.
        """
        leaked: list[Plugin] = []

        class Child(Plugin):
            meta = PluginMeta(name="child", version="0.1.0")

            def middleware(self):
                return [_TraceMiddleware("child")]

        class Loader(Plugin):
            meta = PluginMeta(name="loader", version="0.1.0")

            async def setup(self, server):
                # The loader is in control of the instance, so we can
                # hand it back to the test via a closure.
                child = Child()
                leaked.append(child)
                server.add_plugin(child)

        mcp = FastMCP("t", plugins=[Loader()])

        async with Client(mcp) as c:
            await c.ping()

        # Ephemeral cleanup ran — child is no longer in the plugin list,
        # and its middleware is gone.
        assert [p.meta.name for p in mcp.plugins] == ["loader"]
        child_instance = leaked[0]
        assert child_instance._fastmcp_ephemeral is True

        # User re-registers the same instance as a permanent plugin.
        mcp.add_plugin(child_instance)
        assert child_instance._fastmcp_ephemeral is False

        async with Client(mcp) as c:
            await c.ping()

        # After a second cycle, the permanent registration survives and
        # its middleware is installed exactly once.
        assert child_instance in mcp.plugins
        tags = [m.tag for m in mcp.middleware if isinstance(m, _TraceMiddleware)]
        assert tags == ["child"]

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


class TestRunHook:
    """Plugins that override `run()` directly (the long-running pattern)."""

    async def test_run_override_wraps_server_lifetime(self):
        """A plugin overriding run() sees the server live between setup and teardown."""
        from contextlib import asynccontextmanager

        recorder = _Recorder()

        class Long(Plugin):
            meta = PluginMeta(name="long", version="0.1.0")

            @asynccontextmanager
            async def run(self, server):
                recorder.events.append(("enter", "long"))
                try:
                    yield
                finally:
                    recorder.events.append(("exit", "long"))

        mcp = FastMCP("t", plugins=[Long()])
        async with Client(mcp) as c:
            await c.ping()
            # Mid-cycle: enter fired, exit hasn't.
            assert ("enter", "long") in recorder.events
            assert ("exit", "long") not in recorder.events

        # After teardown: both fired.
        assert recorder.events == [("enter", "long"), ("exit", "long")]

    async def test_run_override_can_use_async_with(self):
        """A plugin's run() can acquire an async-context resource and release it on exit."""
        from contextlib import asynccontextmanager

        recorder = _Recorder()

        @asynccontextmanager
        async def fake_resource():
            recorder.events.append(("acquire", "resource"))
            try:
                yield "handle"
            finally:
                recorder.events.append(("release", "resource"))

        class WithResource(Plugin):
            meta = PluginMeta(name="with-resource", version="0.1.0")

            @asynccontextmanager
            async def run(self, server):
                async with fake_resource() as handle:
                    self.handle = handle
                    yield

        p = WithResource()
        mcp = FastMCP("t", plugins=[p])
        async with Client(mcp) as c:
            await c.ping()
            assert p.handle == "handle"

        # async with cleanup fired on exit path
        assert recorder.events == [
            ("acquire", "resource"),
            ("release", "resource"),
        ]

    async def test_run_override_cancellation_propagates_into_background_task(self):
        """A long-running background task inside run() is cancelled on shutdown."""
        from contextlib import asynccontextmanager

        recorder = _Recorder()

        class Background(Plugin):
            meta = PluginMeta(name="background", version="0.1.0")

            @asynccontextmanager
            async def run(self, server):
                async def worker():
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        recorder.events.append(("cancelled", "worker"))
                        raise

                task = asyncio.create_task(worker())
                try:
                    yield
                finally:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

        mcp = FastMCP("t", plugins=[Background()])
        async with Client(mcp) as c:
            await c.ping()

        assert recorder.events == [("cancelled", "worker")]

    async def test_run_override_raising_before_yield_aborts_startup(self):
        """If a plugin's run() raises before yielding, startup fails cleanly."""
        from contextlib import asynccontextmanager

        class BadStart(Plugin):
            meta = PluginMeta(name="bad-start", version="0.1.0")

            @asynccontextmanager
            async def run(self, server):
                raise RuntimeError("cannot start")
                yield  # unreachable

        mcp = FastMCP("t", plugins=[BadStart()])
        with pytest.raises(RuntimeError, match="cannot start"):
            async with Client(mcp) as c:
                await c.ping()

    async def test_run_override_composes_with_simple_setup_teardown_plugins(self):
        """A server can mix run-override plugins with setup/teardown plugins."""
        from contextlib import asynccontextmanager

        recorder = _Recorder()

        class Simple(Plugin):
            meta = PluginMeta(name="simple", version="0.1.0")

            async def setup(self, server):
                recorder.events.append(("setup", "simple"))

            async def teardown(self):
                recorder.events.append(("teardown", "simple"))

        class LongRunning(Plugin):
            meta = PluginMeta(name="long-running", version="0.1.0")

            @asynccontextmanager
            async def run(self, server):
                recorder.events.append(("enter", "long-running"))
                try:
                    yield
                finally:
                    recorder.events.append(("exit", "long-running"))

        mcp = FastMCP("t", plugins=[Simple(), LongRunning()])
        async with Client(mcp) as c:
            await c.ping()

        # Enter order follows registration; exit order is reversed.
        assert recorder.events == [
            ("setup", "simple"),
            ("enter", "long-running"),
            ("exit", "long-running"),
            ("teardown", "simple"),
        ]


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
        class PConfig(BaseModel):
            who: str = "world"

        class P(Plugin[PConfig]):
            meta = PluginMeta(
                name="p",
                version="0.1.0",
                description="demo",
                tags=["x"],
                dependencies=["demo>=0.1"],
                fastmcp_version=">=3.0",
                meta={"owning_team": "platform"},
            )

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

    def test_manifest_omits_empty_config_internal_name_and_docstring(self):
        """For plugins without a Config, the manifest's `config_schema`
        must not leak `_EmptyConfig` — neither as `title` nor as
        `description` (pydantic emits both by default)."""

        class P(Plugin):
            meta = PluginMeta(name="p", version="0.1.0")

        m = P.manifest()
        assert m is not None
        schema = m["config_schema"]
        assert "_EmptyConfig" not in schema.get("title", "")
        # Pydantic v2 emits the class docstring as `description`; strip it too.
        assert (
            "description" not in schema or "_EmptyConfig" not in schema["description"]
        )
        assert "Plugin[ConfigType]" not in schema.get("description", "")

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


class TestPluginCapabilities:
    """Plugins contribute partial ServerCapabilities dicts via `capabilities()`."""

    def test_default_returns_empty(self):
        """Plugin with no override contributes nothing."""
        assert _TestPlugin().capabilities() == {}

    async def test_experimental_contribution_reaches_initialize_response(self):
        """An experimental capability entry flows through to the client."""

        class P(_TestPlugin):
            def capabilities(self):
                return {"experimental": {"my/ext": {}}}

        mcp = FastMCP("t", plugins=[P()])

        async with Client(mcp) as c:
            result = c.initialize_result
            assert result is not None
            experimental = result.capabilities.experimental or {}
            assert experimental.get("my/ext") == {}

    async def test_multiple_plugins_merge_into_same_field(self):
        """Contributions to the same top-level field are deep-merged."""

        class A(_TestPlugin):
            def capabilities(self):
                return {"experimental": {"alpha": {"version": 1}}}

        class B(_TestPlugin):
            def capabilities(self):
                return {"experimental": {"beta": {}}}

        mcp = FastMCP("t", plugins=[A(), B()])

        async with Client(mcp) as c:
            result = c.initialize_result
            assert result is not None
            experimental = result.capabilities.experimental or {}
            assert experimental.get("alpha") == {"version": 1}
            assert experimental.get("beta") == {}

    async def test_later_plugin_overrides_earlier_on_same_key(self):
        """Plugins run in sequence; later contributions override earlier ones.

        Plugin order is a user-facing configuration knob — same as
        middleware order — so overriding a built-in or earlier plugin's
        capability is intentional, not an error.
        """

        class Earlier(_TestPlugin):
            def capabilities(self):
                return {"experimental": {"shared": {"owner": "earlier"}}}

        class Later(_TestPlugin):
            def capabilities(self):
                return {"experimental": {"shared": {"owner": "later"}}}

        mcp = FastMCP("t", plugins=[Earlier(), Later()])

        async with Client(mcp) as c:
            result = c.initialize_result
            assert result is not None
            experimental = result.capabilities.experimental or {}
            assert experimental.get("shared") == {"owner": "later"}

    async def test_plugin_can_add_non_experimental_field(self):
        """Plugins can advertise top-level capability fields the server didn't set.

        `logging` is off by default on a FastMCP server; a plugin turning
        it on must surface in the initialize response.
        """

        class P(_TestPlugin):
            def capabilities(self):
                return {"logging": {}}

        mcp = FastMCP("t", plugins=[P()])

        async with Client(mcp) as c:
            result = c.initialize_result
            assert result is not None
            assert result.capabilities.logging is not None

    async def test_plugin_can_override_built_in_subfield(self):
        """Deep-merge applies to typed sub-fields of pre-populated capability objects.

        FastMCP already advertises `tools.listChanged=True` by default; a
        plugin flipping it to `False` exercises the merge path through a
        pydantic sub-model (not just the experimental dict).
        """

        class P(_TestPlugin):
            def capabilities(self):
                return {"tools": {"listChanged": False}}

        mcp = FastMCP("t", plugins=[P()])

        async with Client(mcp) as c:
            result = c.initialize_result
            assert result is not None
            assert result.capabilities.tools is not None
            assert result.capabilities.tools.listChanged is False

    async def test_plugin_owned_capability_dict_is_not_mutated_across_plugins(self):
        """Plugin-returned dicts must not be mutated by the merge.

        A plugin that returns a cached/class-level dict from
        `capabilities()` gets the same object back on subsequent calls.
        If the merge wrote that dict into `merged` by reference, a later
        plugin's contribution would add keys to the earlier plugin's
        dict, leaking state across initializations.
        """

        class A(_TestPlugin):
            _caps = {"experimental": {"alpha": {}}}

            def capabilities(self):
                return self._caps

        class B(_TestPlugin):
            def capabilities(self):
                return {"experimental": {"beta": {}}}

        a = A()
        mcp = FastMCP("t", plugins=[a, B()])

        async with Client(mcp) as c:
            result = c.initialize_result
            assert result is not None
            experimental = result.capabilities.experimental or {}
            assert "alpha" in experimental
            assert "beta" in experimental

        # A's cached dict must not have been mutated to contain B's entry.
        assert a._caps == {"experimental": {"alpha": {}}}

    async def test_loader_added_plugin_capabilities_contribute(self):
        """Plugins added via the loader pattern still contribute capabilities."""

        class Loaded(_TestPlugin):
            def capabilities(self):
                return {"experimental": {"loaded": {}}}

        class Loader(_TestPlugin):
            async def setup(self, server):
                server.add_plugin(Loaded())

        mcp = FastMCP("t", plugins=[Loader()])

        async with Client(mcp) as c:
            result = c.initialize_result
            assert result is not None
            experimental = result.capabilities.experimental or {}
            assert experimental.get("loaded") == {}
