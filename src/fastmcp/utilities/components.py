from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, TypedDict

from mcp.types import Icon
from pydantic import BeforeValidator, Field
from typing_extensions import Self, TypeVar

import fastmcp
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.utilities.types import FastMCPBaseModel

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

T = TypeVar("T", default=Any)


class FastMCPMeta(TypedDict, total=False):
    tags: list[str]


def _convert_set_default_none(maybe_set: set[T] | Sequence[T] | None) -> set[T]:
    """Convert a sequence to a set, defaulting to an empty set if None."""
    if maybe_set is None:
        return set()
    if isinstance(maybe_set, set):
        return maybe_set
    return set(maybe_set)


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

        Format: "{key_prefix}:{identifier}" e.g. "tool:my_tool", "resource:file://x.txt"

        Subclasses should override this to use their specific identifier.
        Base implementation uses name.
        """
        return self.make_key(self.name)

    def get_meta(
        self, include_fastmcp_meta: bool | None = None
    ) -> dict[str, Any] | None:
        """
        Get the meta information about the component.

        If include_fastmcp_meta is True, a `_fastmcp` key will be added to the
        meta, containing a `tags` field with the tags of the component.
        """

        if include_fastmcp_meta is None:
            include_fastmcp_meta = fastmcp.settings.include_fastmcp_meta

        meta = self.meta or {}

        if include_fastmcp_meta:
            fastmcp_meta = FastMCPMeta(tags=sorted(self.tags))
            # overwrite any existing _fastmcp meta with keys from the new one
            if upstream_meta := meta.get("_fastmcp"):
                fastmcp_meta = upstream_meta | fastmcp_meta
            meta["_fastmcp"] = fastmcp_meta

        return meta or None

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        if not isinstance(other, type(self)):
            return False
        return self.model_dump() == other.model_dump()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, title={self.title!r}, description={self.description!r}, tags={self.tags})"

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
