"""Plugin primitive for FastMCP.

Plugins package server-side behavior — middleware, component transforms,
providers, and custom HTTP routes — into reusable, configurable,
distributable units. A plugin is a subclass of `Plugin` with a
class-level `PluginMeta` and an optional nested `Config` model.

See the design document for the full specification.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.message import Message as EmailMessage
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, ValidationError

import fastmcp
from fastmcp.exceptions import FastMCPError
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

    version: str
    """Plugin version (plugin's own semver, independent of fastmcp)."""

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
    def from_package(cls, distribution: str, /, **overrides: Any) -> PluginMeta:
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

        derived: dict[str, Any] = {"version": dist.version}

        # description ← Summary header
        summary = headers.get("Summary")
        if summary and summary.strip():
            derived["description"] = summary.strip()

        # author ← Author, falling back to Author-email
        author = headers.get("Author") or headers.get("Author-email")
        if author and author.strip():
            derived["author"] = author.strip()

        # homepage ← Home-page, falling back to the first Project-URL
        # that looks like a canonical homepage reference
        homepage = headers.get("Home-page")
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
                    homepage = url.strip()
                    break
        if homepage and homepage.strip():
            derived["homepage"] = homepage.strip()

        # dependencies — pin the containing distribution at the base
        # release of its current version. Using `Version.base_version`
        # strips local (`+abc.def`), pre (`rc1`), dev (`.dev0`), and
        # post segments, because PEP 440 only allows local versions with
        # `==` / `!=` — a pin like `>=1.2.3+abc` would fail to parse as
        # a Requirement (flagged by `Plugin._validate_meta`). Plugin
        # authors can add more via the `dependencies` override.
        try:
            base_version = Version(dist.version).base_version
        except InvalidVersion as exc:
            raise PluginError(
                f"PluginMeta.from_package({distribution!r}): could not "
                f"parse distribution version {dist.version!r}: {exc}"
            ) from exc
        derived["dependencies"] = [f"{distribution}>={base_version}"]

        derived.update(overrides)
        return cls(**derived)


class Plugin:
    """Base class for FastMCP plugins.

    Subclass to define a plugin. A subclass must declare a class-level
    `meta` attribute (a `PluginMeta` instance). It may optionally
    declare a nested `Config` (subclass of `pydantic.BaseModel`)
    describing its configuration schema, and override any of the lifecycle
    and contribution hooks.

    Example:
        ```python
        from fastmcp.server.plugins import Plugin, PluginMeta
        from pydantic import BaseModel


        class PIIRedactor(Plugin):
            meta = PluginMeta(
                name="pii-redactor",
                version="0.3.0",
                dependencies=[
                    "fastmcp-plugin-pii>=0.3.0",
                    "regex>=2024.0",
                ],
            )

            class Config(BaseModel):
                patterns: list[str] = ["ssn", "email"]

            def middleware(self):
                return [PIIMiddleware(self.config)]
        ```
    """

    meta: ClassVar[PluginMeta]
    """Class-level metadata. Required on every subclass."""

    class Config(BaseModel):
        """Default empty configuration. Subclasses override to declare fields."""

        model_config = ConfigDict(extra="forbid")

    config: BaseModel

    # Framework-internal marker. Set to True by `FastMCP.add_plugin` when
    # the plugin is added from inside another plugin's setup() (the loader
    # pattern). The server removes ephemeral plugins and their
    # contributions on teardown so loaders don't accumulate duplicates
    # across lifespan cycles.
    _fastmcp_ephemeral: bool = False

    def __init__(self, config: BaseModel | dict[str, Any] | None = None) -> None:
        # A subclass's nested Config is a distinct class from Plugin.Config;
        # we accept any BaseModel instance here and validate at runtime that
        # it's (or coerces to) the subclass's own Config type. This is why
        # `config` is typed as BaseModel rather than the nested Config — the
        # nested declaration does not imply subclass relationship.
        meta = getattr(type(self), "meta", None)
        if not isinstance(meta, PluginMeta):
            raise TypeError(
                f"{type(self).__name__} must declare a class-level "
                f"'meta' attribute of type PluginMeta"
            )
        self._validate_meta(meta)

        config_cls = type(self).Config
        if config is None:
            value: BaseModel = config_cls()
        elif isinstance(config, config_cls):
            value = config
        elif isinstance(config, dict):
            try:
                value = config_cls(**config)
            except ValidationError as exc:
                raise PluginConfigError(
                    f"Invalid configuration for {type(self).__name__}: {exc}"
                ) from exc
        else:
            raise PluginConfigError(
                f"Config for {type(self).__name__} must be a {config_cls.__name__} "
                f"instance or dict, not {type(config).__name__}"
            )
        self.config = value

    # -- validation -----------------------------------------------------------

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

        config_cls = getattr(cls, "Config", Plugin.Config)
        data: dict[str, Any] = {
            "manifest_version": 1,
            **meta.model_dump(),
            "config_schema": config_cls.model_json_schema(),
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
