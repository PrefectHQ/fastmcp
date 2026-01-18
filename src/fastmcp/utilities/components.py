from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, TypedDict, cast

from mcp.types import Icon
from pydantic import BeforeValidator, Field
from typing_extensions import Self, TypeVar

from fastmcp.server.tasks.config import TaskConfig
from fastmcp.utilities.types import FastMCPBaseModel

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

T = TypeVar("T", default=Any)


class FastMCPMeta(TypedDict, total=False):
    tags: list[str]
    version: str
    versions: list[str]


def get_fastmcp_metadata(meta: dict[str, Any] | None) -> FastMCPMeta:
    """Extract FastMCP metadata from a component's meta dict.

    Handles both the current `fastmcp` namespace and the legacy `_fastmcp`
    namespace for compatibility with older FastMCP servers.
    """
    if not meta:
        return {}
    return cast(FastMCPMeta, meta.get("fastmcp") or meta.get("_fastmcp") or {})


def _convert_set_default_none(maybe_set: set[T] | Sequence[T] | None) -> set[T]:
    """Convert a sequence to a set, defaulting to an empty set if None."""
    if maybe_set is None:
        return set()
    if isinstance(maybe_set, set):
        return maybe_set
    return set(maybe_set)


def _coerce_version(v: str | int | None) -> str | None:
    """Coerce version to string, accepting int or str.

    Raises ValueError if version contains '@' (used as key delimiter).
    """
    if v is None:
        return None
    version = str(v)
    if "@" in version:
        raise ValueError(
            f"Version string cannot contain '@' (used as key delimiter): {version!r}"
        )
    return version


class FastMCPComponent(FastMCPBaseModel):
    """Base class for FastMCP tools, prompts, resources, and resource templates."""

    KEY_PREFIX: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Warn if a subclass doesn't define KEY_PREFIX (inherited or its own)
        if not cls.KEY_PREFIX:
            import warnings

            warnings.warn(
                f"{cls.__name__} does not define KEY_PREFIX. "
                f"Component keys will not be type-prefixed, which may cause collisions.",
                UserWarning,
                stacklevel=2,
            )

    name: str = Field(
        description="The name of the component.",
    )
    version: Annotated[str | None, BeforeValidator(_coerce_version)] = Field(
        default=None,
        description="Optional version identifier for this component. "
        "Multiple versions of the same component (same name) can coexist.",
    )
    title: str | None = Field(
        default=None,
        description="The title of the component for display purposes.",
    )
    description: str | None = Field(
        default=None,
        description="The description of the component.",
    )
    icons: list[Icon] | None = Field(
        default=None,
        description="Optional list of icons for this component to display in user interfaces.",
    )
    tags: Annotated[set[str], BeforeValidator(_convert_set_default_none)] = Field(
        default_factory=set,
        description="Tags for the component.",
    )
    meta: dict[str, Any] | None = Field(
        default=None, description="Meta information about the component"
    )
    task_config: Annotated[
        TaskConfig,
        Field(description="Background task execution configuration (SEP-1686)."),
    ] = Field(default_factory=lambda: TaskConfig(mode="forbidden"))

    @classmethod
    def make_key(cls, identifier: str) -> str:
        """Construct the lookup key for this component type.

        Args:
            identifier: The raw identifier (name for tools/prompts, uri for resources)

        Returns:
            A prefixed key like "tool:name" or "resource:uri"
        """
        if cls.KEY_PREFIX:
            return f"{cls.KEY_PREFIX}:{identifier}"
        return identifier

    @property
    def key(self) -> str:
        """The globally unique lookup key for this component.

        Format: "{key_prefix}:{identifier}@{version}" or "{key_prefix}:{identifier}@"
        e.g. "tool:my_tool@v2", "tool:my_tool@", "resource:file://x.txt@"

        The @ suffix is ALWAYS present to enable unambiguous parsing of keys
        (URIs may contain @ characters, so we always include the delimiter).

        Subclasses should override this to use their specific identifier.
        Base implementation uses name.
        """
        base_key = self.make_key(self.name)
        return f"{base_key}@{self.version or ''}"

    def get_meta(self) -> dict[str, Any]:
        """Get the meta information about the component.

        Returns a dict that always includes a `fastmcp` key containing:
        - `tags`: sorted list of component tags
        - `version`: component version (only if set)

        Internal keys (prefixed with `_`) are stripped from the fastmcp namespace.
        """
        meta = dict(self.meta) if self.meta else {}

        fastmcp_meta: FastMCPMeta = {"tags": sorted(self.tags)}
        if self.version is not None:
            fastmcp_meta["version"] = self.version

        # Merge with upstream fastmcp meta, stripping internal keys
        if (upstream_meta := meta.get("fastmcp")) is not None:
            if not isinstance(upstream_meta, dict):
                raise TypeError("meta['fastmcp'] must be a dict")
            # Filter out internal keys (e.g., _internal used for enabled state)
            public_upstream = {
                k: v for k, v in upstream_meta.items() if not k.startswith("_")
            }
            fastmcp_meta = cast(FastMCPMeta, public_upstream | fastmcp_meta)
        meta["fastmcp"] = fastmcp_meta

        return meta

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        if not isinstance(other, type(self)):
            return False
        return self.model_dump() == other.model_dump()

    def __repr__(self) -> str:
        parts = [f"name={self.name!r}"]
        if self.version:
            parts.append(f"version={self.version!r}")
        parts.extend(
            [
                f"title={self.title!r}",
                f"description={self.description!r}",
                f"tags={self.tags}",
            ]
        )
        return f"{self.__class__.__name__}({', '.join(parts)})"

    def enable(self) -> None:
        """Removed in 3.0. Use server.enable(keys=[...]) instead."""
        raise NotImplementedError(
            f"Component.enable() was removed in FastMCP 3.0. "
            f"Use server.enable(keys=['{self.key}']) instead."
        )

    def disable(self) -> None:
        """Removed in 3.0. Use server.disable(keys=[...]) instead."""
        raise NotImplementedError(
            f"Component.disable() was removed in FastMCP 3.0. "
            f"Use server.disable(keys=['{self.key}']) instead."
        )

    def copy(self) -> Self:  # type: ignore[override]
        """Create a copy of the component."""
        return self.model_copy()

    def register_with_docket(self, docket: Docket) -> None:
        """Register this component with docket for background execution.

        No-ops if task_config.mode is "forbidden". Subclasses override to
        register their callable (self.run, self.read, self.render, or self.fn).
        """
        # Base implementation: no-op (subclasses override)

    async def add_to_docket(
        self, docket: Docket, *args: Any, **kwargs: Any
    ) -> Execution:
        """Schedule this component for background execution via docket.

        Subclasses override this to handle their specific calling conventions:
        - Tool: add_to_docket(docket, arguments: dict, **kwargs)
        - Resource: add_to_docket(docket, **kwargs)
        - ResourceTemplate: add_to_docket(docket, params: dict, **kwargs)
        - Prompt: add_to_docket(docket, arguments: dict | None, **kwargs)

        The **kwargs are passed through to docket.add() (e.g., key=task_key).
        """
        if not self.task_config.supports_tasks():
            raise RuntimeError(
                f"Cannot add {self.__class__.__name__} '{self.name}' to docket: "
                f"task execution not supported"
            )
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement add_to_docket()"
        )

    def get_span_attributes(self) -> dict[str, Any]:
        """Return span attributes for telemetry.

        Subclasses should call super() and merge their specific attributes.
        """
        return {"fastmcp.component.key": self.key}
