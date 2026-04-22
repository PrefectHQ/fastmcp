"""Plugin primitive for FastMCP.

Plugins package server-side behavior — middleware, component transforms,
providers, and custom HTTP routes — into reusable, configurable,
distributable units. A plugin is a subclass of `Plugin` (optionally
parameterized with a pydantic config model — `Plugin[MyConfig]` — for
typed configuration).

See the design document for the full specification.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.message import Message as EmailMessage
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    TypeVar,
    cast,
    get_args,
    get_origin,
)

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, ValidationError
from typing_extensions import Self

import fastmcp
from fastmcp.exceptions import FastMCPError
from fastmcp.server.auth.auth import AuthProvider
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers import Provider
from fastmcp.server.transforms import Transform
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from starlette.routing import BaseRoute

    from fastmcp.server.server import FastMCP


class PluginError(FastMCPError):
    """Base class for plugin-related errors."""


class PluginConfigError(PluginError):
    """Raised when a plugin's configuration fails validation."""


class PluginCompatibilityError(PluginError):
    """Raised when a plugin declares a FastMCP version it is not compatible with."""


class PluginMeta(BaseModel):
    """Descriptive metadata for a plugin.

    Users who want typed custom fields subclass this model. Users who want
    to attach ad-hoc fields without defining a model put them in the
    `meta` dict. Unknown top-level fields are rejected to prevent future
    collisions with standard fields.
    """

    name: str
    """Plugin name. Required. Must be unique within a server."""

    version: str | None = None
    """Plugin's independent semver, if it has one. `None` means the
    plugin is bundled with its containing package (typically fastmcp
    itself) and doesn't track a separate release cadence — which is the
    correct answer for first-party plugins that ship in-tree. Published
    plugins derive this from their PyPI distribution via
    `PluginMeta.from_package(...)`.
    """

    description: str | None = None
    """Short human-readable description."""

    tags: list[str] = []
    """Free-form tags for discovery and filtering."""

    author: str | None = None
    """Author identifier (person, team, or org)."""

    homepage: str | None = None
    """Homepage URL."""

    dependencies: list[str] = []
    """PEP 508 requirement specifiers for packages required to import and
    run the plugin. Includes the plugin's own containing package plus any
    runtime extras. FastMCP itself is implicit and must not be listed.
    """

    fastmcp_version: str | None = None
    """Optional PEP 440 specifier expressing compatibility with FastMCP
    core (e.g. `">=3.0"`). Verified at registration time.
    """

    meta: dict[str, Any] = {}
    """Free-form bag for custom fields that have not been standardized.
    Namespaced to prevent collisions with future standard fields.
    """

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_package(cls, distribution: str, /, **overrides: Any) -> Self:
        """Derive plugin metadata from an installed Python distribution.

        Reads `version`, `description`, `author`, and `homepage` from the
        distribution's metadata (as recorded in its `pyproject.toml` and
        exposed via `importlib.metadata`), and pins the distribution
        itself as the sole entry in `dependencies` — so the manifest
        automatically reflects the containing package and stays in sync
        with every new release. Runtime dependencies declared in the
        distribution's `Requires-Dist` are NOT harvested; plugin authors
        pass additional runtime deps via the `dependencies` override.

        Any keyword argument overrides the derived value.

        Example:
            ```python
            class MyPiiRedactor(Plugin):
                meta = PluginMeta.from_package(
                    "fastmcp-plugin-my-pii",   # distribution name on PyPI
                    name="my-pii",              # plugin identifier
                    tags=["security"],
                )
            ```

        Args:
            distribution: The installed distribution name to read from
                (e.g. `"fastmcp-plugin-my-pii"`). Must be importable via
                `importlib.metadata`. Cannot be `fastmcp` itself — use
                `fastmcp_version` for core compatibility.
            **overrides: Any `PluginMeta` field. Overrides take precedence
                over the derived value. `name` is required unless a
                `name` override is supplied; the distribution name is not
                used as the plugin name by default since the two serve
                different purposes (distribution = wheel identity, plugin
                name = runtime identifier shown to Horizon / CLI users).

        Raises:
            PluginError: If the distribution is not installed in the
                current environment, if `distribution` is `fastmcp`
                (which would produce an invalid manifest), or if the
                distribution's version cannot be parsed.
        """
        # FastMCP itself is implicit; pinning it would produce a manifest
        # that Plugin._validate_meta rejects. Plugin authors expressing
        # core compatibility should use the `fastmcp_version` field.
        if canonicalize_name(distribution) == "fastmcp":
            raise PluginError(
                f"PluginMeta.from_package({distribution!r}): "
                f"`fastmcp` is implicit and must not be used as the "
                f"containing distribution. Use the `fastmcp_version` "
                f"field on PluginMeta to express core compatibility."
            )

        try:
            dist = importlib_metadata.distribution(distribution)
        except importlib_metadata.PackageNotFoundError as exc:
            raise PluginError(
                f"PluginMeta.from_package({distribution!r}): distribution "
                f"is not installed in the current environment. Install it "
                f"(e.g. via `uv pip install {distribution}`) before "
                f"calling from_package."
            ) from exc

        # `dist.metadata` is an email.message.Message at runtime, but
        # `importlib.metadata.PackageMetadata`'s stubs don't expose that
        # interface. Cast to email.message.Message to flatten header
        # access (item lookup returns None on miss; `items()` yields one
        # entry per header, including repeated keys like Project-URL).
        raw = cast(EmailMessage, dist.metadata)
        headers: dict[str, str] = {}
        all_project_urls: list[str] = []
        for key, value in raw.items():
            if key == "Project-URL":
                all_project_urls.append(value)
            else:
                # For repeated headers we only need one; first-wins.
                headers.setdefault(key, value)

        def _first_non_blank(*values: str | None) -> str | None:
            """Return the first value whose `.strip()` is truthy, or None.

            Guards against whitespace-only headers silently blocking the
            fallback chain (e.g. a METADATA file with `Author:    ` would
            otherwise make the `Author-email` fallback unreachable).
            """
            for v in values:
                if v is not None and v.strip():
                    return v.strip()
            return None

        derived: dict[str, Any] = {"version": dist.version}

        # description ← Summary header
        summary = _first_non_blank(headers.get("Summary"))
        if summary:
            derived["description"] = summary

        # author ← Author, falling back to Author-email
        author = _first_non_blank(headers.get("Author"), headers.get("Author-email"))
        if author:
            derived["author"] = author

        # homepage ← Home-page, falling back to the first Project-URL
        # whose label looks like a canonical homepage reference
        homepage = _first_non_blank(headers.get("Home-page"))
        if not homepage:
            for entry in all_project_urls:
                # Project-URL values are `"Label, URL"` pairs.
                label, _, url = entry.partition(",")
                if label.strip().lower() in {
                    "homepage",
                    "home",
                    "repository",
                    "source",
                }:
                    homepage = _first_non_blank(url)
                    if homepage:
                        break
        if homepage:
            derived["homepage"] = homepage

        # dependencies — pin the containing distribution at its current
        # version, minus the local segment. PEP 440 only restricts local
        # versions (`+abc.def`) from use with `>=` / `<=`; prereleases
        # (`rc1`), dev (`.dev0`), and post segments are all valid there,
        # so we preserve them to keep the pin meaningful for actively
        # developed distributions. `Version.public` strips exactly the
        # local segment.
        try:
            public = Version(dist.version).public
        except InvalidVersion as exc:
            raise PluginError(
                f"PluginMeta.from_package({distribution!r}): could not "
                f"parse distribution version {dist.version!r}: {exc}"
            ) from exc
        derived["dependencies"] = [f"{distribution}>={public}"]

        derived.update(overrides)
        return cls(**derived)


class _EmptyConfig(BaseModel):
    """Default config for plugins that don't declare their own via the
    `Plugin[ConfigType]` generic parameter."""

    model_config = ConfigDict(extra="forbid")


C = TypeVar("C", bound=BaseModel)
"""Type variable for a plugin's config model. Bound to `BaseModel` so
any pydantic model is valid. Plugins without a config omit the generic
parameter; the runtime falls back to `_EmptyConfig` in that case.
"""


def _derive_plugin_name(cls_name: str) -> str:
    """Kebab-case a class name, stripping a trailing ``Plugin`` suffix.

    `ChannelPlugin` → `"channel"`, `CodeMode` → `"code-mode"`,
    `PIIRedactor` → `"pii-redactor"`.
    """
    # Split acronym from following capitalized word: `PIIRedactor` → `PII-Redactor`
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", cls_name)
    # Split lowercase/digit from following uppercase: `CodeMode` → `Code-Mode`
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    name = name.lower()
    if name.endswith("-plugin") and name != "-plugin":
        name = name[: -len("-plugin")]
    return name


def _resolve_plugin_config_cls(cls: type) -> type[BaseModel] | None:
    """Resolve the config class bound to `Plugin[C]` for a subclass.

    Walks `cls.__orig_bases__`, recursing through intermediate `Plugin`
    subclasses and propagating TypeVar substitutions. Returns the bound
    `BaseModel` subclass, or `None` if the binding is still a TypeVar
    (unresolved — typically an intermediate abstract base).

    Raises `TypeError` if a resolved argument is concrete but not a
    `BaseModel` subclass (a misuse of `Plugin[NonPydanticType]`).
    """

    def _resolve(base: Any, substitutions: dict[Any, Any]) -> Any:
        origin = get_origin(base)
        if origin is None or not (
            isinstance(origin, type) and issubclass(origin, Plugin)
        ):
            return None
        args = get_args(base)
        # Apply outer-scope substitutions so a parent's TypeVar bound to
        # a concrete type at this level becomes that concrete type here.
        resolved_args = tuple(substitutions.get(a, a) for a in args)

        if origin is Plugin:
            # We're at the root parameterization.
            if not resolved_args:
                return None
            cfg = resolved_args[0]
            # Still a TypeVar: unresolved at this level of the chain.
            if isinstance(cfg, TypeVar):
                return None
            return cfg

        # Intermediate Plugin subclass. Push down its own TypeVar
        # substitutions (from its `__parameters__`) and recurse into its
        # bases to find the Plugin parameterization.
        origin_params = getattr(origin, "__parameters__", ())
        new_subs = {
            **substitutions,
            **dict(zip(origin_params, resolved_args, strict=False)),
        }
        for inner in getattr(origin, "__orig_bases__", ()):
            found = _resolve(inner, new_subs)
            if found is not None:
                return found
        return None

    for base in getattr(cls, "__orig_bases__", ()):
        resolved = _resolve(base, substitutions={})
        if resolved is None:
            continue
        if not (isinstance(resolved, type) and issubclass(resolved, BaseModel)):
            raise TypeError(
                f"{cls.__name__}: Plugin[...] generic parameter must be a "
                f"pydantic BaseModel subclass, got {resolved!r}"
            )
        return resolved
    return None


class Plugin(Generic[C]):
    """Base class for FastMCP plugins.

    Subclass to define a plugin. A subclass may optionally declare a
    class-level `meta` attribute (a `PluginMeta` instance); if omitted,
    a default is derived from the class name (kebab-cased, trailing
    `Plugin` stripped) and no independent version. Declare `meta` explicitly
    when publishing or when Horizon/registry-facing metadata matters.

    **Config typing.** Parameterize `Plugin` with a pydantic model to
    give your plugin typed configuration — `self.config.<field>` is then
    correctly typed in editors and type checkers, and passing a dict or
    model instance to the constructor validates against the model.
    Plugins without a config omit the parameter.

    Example:
        ```python
        from pydantic import BaseModel
        from fastmcp.server.plugins import Plugin, PluginMeta


        class PIIRedactorConfig(BaseModel):
            patterns: list[str] = ["ssn", "email"]


        class PIIRedactor(Plugin[PIIRedactorConfig]):
            meta = PluginMeta(name="pii-redactor", version="0.3.0")

            def middleware(self):
                # self.config is typed as PIIRedactorConfig
                return [PIIMiddleware(self.config.patterns)]
        ```
    """

    meta: ClassVar[PluginMeta]
    """Class-level metadata. Auto-derived from the class name with no
    independent version if the subclass doesn't declare one — the
    honest default for bundled first-party plugins. Declare `meta`
    explicitly (or use `PluginMeta.from_package(...)`) when publishing
    as a separate package or when Horizon/registry-facing metadata
    matters.
    """

    _config_cls: ClassVar[type[BaseModel]] = _EmptyConfig
    """Config model class resolved from the `Plugin[C]` generic parameter.
    Auto-populated by `__init_subclass__`; falls back to `_EmptyConfig`
    for plugins that don't parameterize `Plugin`.
    """

    config: C
    """The validated config instance. Typed as `C`, the generic
    parameter, so `self.config.<field>` type-checks correctly."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Auto-derive meta if the subclass didn't declare its own. We
        # check `cls.__dict__` rather than attribute lookup so inherited
        # meta from an intermediate subclass isn't treated as a local
        # declaration — each concrete Plugin class gets its own name.
        if "meta" not in cls.__dict__:
            cls.meta = PluginMeta(name=_derive_plugin_name(cls.__name__))
        # Resolve the Config model from the generic parameter. We walk the
        # `__orig_bases__` chain and propagate TypeVar substitutions, so
        # both direct parameterization (`class P(Plugin[Cfg])`) and
        # deferred binding (`class Abstract(Plugin[_T])` →
        # `class P(Abstract[Cfg])`) resolve correctly. Intermediate
        # generic bases with their own unrelated TypeVars are unaffected
        # because we substitute through each step rather than treating
        # `args[0]` as the config unconditionally.
        config_cls = _resolve_plugin_config_cls(cls)
        if config_cls is not None:
            cls._config_cls = config_cls
        # Enforce the JSON-serializable contract on the resolved config.
        # Every plugin config must round-trip through JSON so plugins
        # can be loaded from config files, rendered by registry/Horizon
        # forms, and published to manifest artifacts. Runs on every
        # Plugin subclass — including `_EmptyConfig`, which passes
        # trivially.
        cls._validate_config_cls(cls._config_cls)

    # Framework-internal marker. Set to True by `FastMCP.add_plugin` when
    # the plugin is added from inside another plugin's setup() (the loader
    # pattern). The server removes ephemeral plugins and their
    # contributions on teardown so loaders don't accumulate duplicates
    # across lifespan cycles.
    _fastmcp_ephemeral: bool = False

    def __init__(self, config: C | dict[str, Any] | None = None) -> None:
        meta = getattr(type(self), "meta", None)
        if not isinstance(meta, PluginMeta):
            raise TypeError(
                f"{type(self).__name__} must declare a class-level "
                f"'meta' attribute of type PluginMeta"
            )
        self._validate_meta(meta)

        config_cls = type(self)._config_cls

        def _wrap(exc: ValidationError) -> PluginConfigError:
            # For unparameterized plugins, pydantic's error string
            # includes "1 validation error for _EmptyConfig" — an
            # internal class name users shouldn't see. Emit a scoped
            # message instead; for parameterized plugins, forward
            # pydantic's full diagnostic.
            if config_cls is _EmptyConfig:
                keys = list(config.keys()) if isinstance(config, dict) else []
                return PluginConfigError(
                    f"Invalid configuration for {type(self).__name__}: this "
                    f"plugin declares no config fields but received "
                    f"{keys}."
                )
            return PluginConfigError(
                f"Invalid configuration for {type(self).__name__}: {exc}"
            )

        if config is None:
            try:
                value: BaseModel = config_cls()
            except ValidationError as exc:
                # Required config fields with no default: surface the
                # failure as PluginConfigError so callers that catch
                # the documented exception type behave consistently
                # with the dict path below.
                raise _wrap(exc) from exc
        elif isinstance(config, config_cls):
            value = config
        elif isinstance(config, dict):
            try:
                value = config_cls(**config)
            except ValidationError as exc:
                raise _wrap(exc) from exc
        else:
            # `_EmptyConfig` is an internal implementation detail for
            # unparameterized plugins. Don't leak its name to authors.
            expected = (
                "dict"
                if config_cls is _EmptyConfig
                else f"{config_cls.__name__} instance or dict"
            )
            raise PluginConfigError(
                f"Config for {type(self).__name__} must be a {expected}, "
                f"not {type(config).__name__}"
            )
        self.config = cast(C, value)

    # -- validation -----------------------------------------------------------

    @staticmethod
    def _validate_config_cls(config_cls: type[BaseModel]) -> None:
        """Ensure a plugin's config model is fully JSON-serializable.

        Plugin configs are the distribution surface — they're loaded
        from JSON/YAML, rendered into Horizon/registry forms, and
        published in manifests. They must round-trip through JSON
        without loss. Enforced at class creation so authoring mistakes
        fail loudly at import time rather than at `fastmcp plugin
        manifest` / registry-render time.

        To expose callable-ish behavior through config, plugin authors
        should surface a string-keyed enum (e.g.
        `mode: Literal["json", "markdown"] = "json"`) and resolve to
        the real callable internally. Runtime Python extensibility —
        custom callables, connection pools, etc. — belongs on the
        plugin's `__init__` signature, not the Config model.

        Two checks: (1) `model_json_schema()` must succeed — catches
        fields pydantic can't describe in JSON at all (raw callables,
        classes without pydantic hooks). (2) If the config can be
        built without arguments (all fields have defaults), exercise
        the runtime serialization path via `model_dump(mode="json")`
        — catches the "partial-hooks" case where a type has
        `__get_pydantic_json_schema__` but no matching serializer
        (schema generation alone would silently pass).

        Configs with required fields skip the dump check at class
        creation — we can't construct an instance without a value.
        Partial-hooks violations on those fields surface on first
        `Config(**data).model_dump(mode="json")`. Configs with
        unresolved forward references skip the entire check; it
        re-runs at manifest time once the model is complete.
        """
        if not getattr(config_cls, "__pydantic_complete__", True):
            return

        try:
            config_cls.model_json_schema()
        except Exception as exc:
            raise PluginError(
                f"Plugin config {config_cls.__name__} is not JSON-"
                f"serializable: {exc}. Every field must be expressible "
                f"in JSON. Callable fields and raw Python classes without "
                f"pydantic serialization hooks are not supported."
            ) from exc

        # If the config builds without args, exercise the real
        # serialization path to catch types that have a schema hook
        # but no serializer.
        try:
            instance = config_cls()
        except ValidationError as exc:
            # A ValidationError here can mean two things: (1) required
            # fields without defaults — can't build without user
            # input, expected, skip the dump test; or (2) a default
            # value failed a field validator, which is a real authoring
            # bug and should surface as PluginError at class creation.
            if all(err.get("type") == "missing" for err in exc.errors()):
                return
            raise PluginError(
                f"Plugin config {config_cls.__name__} has an invalid "
                f"default value: {exc}"
            ) from exc
        except Exception as exc:
            # Non-ValidationError failures (TypeError from a broken
            # default_factory, RuntimeError from model_post_init, etc.)
            # are also author-side bugs — wrap so the error carries
            # plugin attribution rather than propagating bare.
            raise PluginError(
                f"Plugin config {config_cls.__name__} could not be "
                f"instantiated with defaults: {exc}"
            ) from exc
        try:
            instance.model_dump(mode="json")
        except Exception as exc:
            raise PluginError(
                f"Plugin config {config_cls.__name__} cannot be "
                f"serialized to JSON at runtime: {exc}. Every field "
                f"must have both a JSON schema and a JSON serializer."
            ) from exc

    @staticmethod
    def _validate_meta(meta: PluginMeta) -> None:
        """Check that the plugin's declared metadata is internally consistent."""
        for dep in meta.dependencies:
            try:
                req = Requirement(dep)
            except InvalidRequirement as exc:
                raise PluginError(
                    f"Plugin {meta.name!r}: invalid PEP 508 requirement {dep!r}: {exc}"
                ) from exc
            if req.name.lower().replace("_", "-") == "fastmcp":
                raise PluginError(
                    f"Plugin {meta.name!r}: 'fastmcp' must not appear in "
                    f"dependencies. Use the 'fastmcp_version' field instead."
                )

        if meta.fastmcp_version is not None:
            try:
                SpecifierSet(meta.fastmcp_version)
            except InvalidSpecifier as exc:
                raise PluginError(
                    f"Plugin {meta.name!r}: invalid fastmcp_version "
                    f"specifier {meta.fastmcp_version!r}: {exc}"
                ) from exc

    def check_fastmcp_compatibility(self) -> None:
        """Raise if the declared `fastmcp_version` excludes the running FastMCP."""
        spec_str = self.meta.fastmcp_version
        if spec_str is None:
            return
        spec = SpecifierSet(spec_str)
        current = fastmcp.__version__
        if current not in spec:
            raise PluginCompatibilityError(
                f"Plugin {self.meta.name!r} requires fastmcp {spec_str}, "
                f"but running fastmcp is {current}."
            )

    # -- lifecycle ------------------------------------------------------------

    @asynccontextmanager
    async def run(self, server: FastMCP) -> AsyncIterator[None]:
        """Async context manager wrapping the plugin's lifetime.

        The framework enters `async with plugin.run(server):` on the
        server's lifespan stack. Everything before the `yield` runs
        during startup (in plugin registration order); the `yield` spans
        the server's active lifetime; everything after the `yield` runs
        on shutdown (in reverse registration order). Cancellation on
        shutdown unwinds the context manager automatically.

        The default implementation calls `setup(server)` before the
        `yield` and `teardown()` after it, so plugins that just need
        one-shot init/cleanup can keep overriding just those two
        methods. Long-running plugins (channels, integration bridges,
        background workers) override `run()` directly to use
        `async with` for resource management and task groups:

            @asynccontextmanager
            async def run(self, server):
                async with httpx.AsyncClient() as client:
                    self.client = client
                    yield
        """
        await self.setup(server)
        try:
            yield
        finally:
            try:
                await self.teardown()
            except Exception:
                # Exceptions during teardown are logged, not raised, so a
                # broken plugin can't take down the server's shutdown
                # sequence. Plugins that want different semantics should
                # override `run()` directly.
                logger.exception("Plugin %r raised during teardown", self.meta.name)

    async def setup(self, server: FastMCP) -> None:
        """One-shot async initialization. Called by the default `run()`
        before the `yield`.

        Override for simple init work — compile regexes, warm caches,
        open connections, register additional plugins from a loader. For
        anything involving long-lived resources or background tasks,
        override `run()` directly instead and use `async with`.
        """

    async def teardown(self) -> None:
        """One-shot async cleanup. Called by the default `run()` after
        the `yield`.

        Override for simple cleanup work — close connections, flush
        buffers. For resource management that would benefit from
        `async with`, override `run()` directly instead.
        """

    # -- contribution hooks ---------------------------------------------------

    def middleware(self) -> list[Middleware]:
        """Return MCP-layer middleware to install on the server."""
        return []

    def transforms(self) -> list[Transform]:
        """Return component transforms (tools, resources, prompts)."""
        return []

    def providers(self) -> list[Provider]:
        """Return component providers."""
        return []

    def auth(self) -> list[AuthProvider]:
        """Return auth providers to compose into the server's auth pipeline.

        Any `AuthProvider` subclass is accepted — `TokenVerifier` (machine-
        to-machine token checks), `OAuthProvider` / `RemoteAuthProvider` /
        any full-server provider that owns OAuth routes and metadata, or
        even a pre-composed `MultiAuth`.

        The framework collects contributions across all plugins, combines
        them with any `auth=` the server was constructed with, and wraps
        the result in a `MultiAuth` when more than one source is present.
        `TokenVerifier` contributions become verifiers; anything else is
        treated as a full server. At most one server contribution is
        allowed across the user-declared auth and all plugins combined —
        multiple full servers would be ambiguous (MultiAuth has a single
        server slot). A `PluginError` is raised if this invariant is
        violated.

        The default returns an empty list.
        """
        return []

    def capabilities(self) -> dict[str, Any]:
        """Return a partial `ServerCapabilities` dict to merge into the server's capabilities.

        The returned dict follows the MCP `ServerCapabilities` shape.
        Contributions from all plugins are deep-merged in registration
        order, then applied on top of the server's built-in capabilities.
        Later plugins can add to or override earlier plugins' entries;
        this is intentional — plugin order is a user-facing configuration
        knob, same as middleware order.

        A plugin advertising an experimental protocol extension:

        ```python
        def capabilities(self):
            return {"experimental": {"my/ext": {}}}
        ```

        A plugin modifying a built-in capability field follows the same
        shape, keyed by the `ServerCapabilities` field name.
        """
        return {}

    def routes(self) -> list[BaseRoute]:
        """Return custom HTTP routes to mount on the server's ASGI app.

        Routes contributed here are **not authenticated by the framework**
        — the MCP auth provider does not gate them. They are appropriate
        for webhook endpoints whose callers carry their own authentication
        scheme (e.g. an HMAC-signed header), and the plugin is responsible
        for verifying inbound requests inside the handler.

        Routes otherwise receive the full incoming HTTP request unchanged,
        including all headers the client sent. If a caller has provided
        the same credentials it would use for an authenticated MCP call,
        those headers are available on `request.headers` for the handler
        to inspect — the plugin chooses whether and how to validate them.
        """
        return []

    # -- introspection --------------------------------------------------------

    @classmethod
    def manifest(
        cls,
        path: str | Path | None = None,
    ) -> dict[str, Any] | None:
        """Return the plugin's manifest as a dict, or write it to `path` as JSON.

        Does not instantiate the plugin. The manifest is a JSON-serializable
        dict that combines the plugin's metadata, its config schema, and an
        importable entry point. Downstream consumers (Horizon, registries,
        CI tooling) read the manifest to discover plugins and render
        configuration forms without installing the plugin's dependencies.
        """
        meta = getattr(cls, "meta", None)
        if not isinstance(meta, PluginMeta):
            raise TypeError(
                f"{cls.__name__} must declare a class-level "
                f"'meta' attribute of type PluginMeta"
            )

        # Validate meta the same way instance construction does, so
        # `fastmcp plugin manifest` can't emit an artifact (malformed
        # PEP 508 deps, bad fastmcp_version specifier, fastmcp declared
        # as a dep, ...) that downstream tooling couldn't otherwise
        # have produced from a live plugin instance.
        cls._validate_meta(meta)

        config_cls = cls._config_cls
        # Re-run the JSON-serializable check here. Plugins with forward-
        # reference configs skip validation at class creation, so manifest
        # emission is the publish-time boundary that has to enforce it.
        cls._validate_config_cls(config_cls)
        config_schema = config_cls.model_json_schema()
        # `_EmptyConfig` is an internal implementation detail; don't
        # leak its name or docstring into the published manifest JSON
        # consumed by Horizon, registries, and CI tooling. Pydantic v2
        # emits both `title` (from `__name__`) and `description` (from
        # the class docstring) in `model_json_schema()`; strip both.
        if config_cls is _EmptyConfig:
            config_schema.pop("title", None)
            config_schema.pop("description", None)
        data: dict[str, Any] = {
            "manifest_version": 1,
            **meta.model_dump(),
            "config_schema": config_schema,
            "entry_point": f"{cls.__module__}:{cls.__qualname__}",
        }

        if path is None:
            return data

        target = Path(path)
        target.write_text(json.dumps(data, indent=2, sort_keys=False))
        return None


__all__ = [
    "Plugin",
    "PluginCompatibilityError",
    "PluginConfigError",
    "PluginError",
    "PluginMeta",
]
