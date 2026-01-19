"""Version filter transform for filtering components by version range."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from fastmcp.server.transforms import (
    GetPromptNext,
    GetResourceNext,
    GetResourceTemplateNext,
    GetToolNext,
    Transform,
)
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.tool import Tool


class VersionFilter(Transform):
    """Filters components by version range.

    When applied to a provider or server, only components within the version
    range are visible. Within that filtered set, the highest version of each
    component is exposed to clients (standard deduplication behavior).

    Parameters mirror comparison operators for clarity:

        # Versions < 3.0 (v1 and v2)
        server.add_transform(VersionFilter(version_lt="3.0"))

        # Versions >= 2.0 and < 3.0 (only v2.x)
        server.add_transform(VersionFilter(version_gte="2.0", version_lt="3.0"))

    Works with any version string - PEP 440 (1.0, 2.0) or dates (2025-01-01).

    Args:
        version_gte: Versions >= this value pass through.
        version_lt: Versions < this value pass through.
    """

    def __init__(
        self,
        *,
        version_gte: str | None = None,
        version_lt: str | None = None,
    ) -> None:
        if version_gte is None and version_lt is None:
            raise ValueError(
                "At least one of version_gte or version_lt must be specified"
            )
        self.version_gte = version_gte
        self.version_lt = version_lt
        self._spec = VersionSpec(gte=version_gte, lt=version_lt)

    def __repr__(self) -> str:
        parts = []
        if self.version_gte:
            parts.append(f"version_gte={self.version_gte!r}")
        if self.version_lt:
            parts.append(f"version_lt={self.version_lt!r}")
        return f"VersionFilter({', '.join(parts)})"

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [t for t in tools if self._spec.matches(t.version)]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        return await call_next(name, version=self._spec.intersect(version))

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def list_resources(self, resources: Sequence[Resource]) -> Sequence[Resource]:
        return [r for r in resources if self._spec.matches(r.version)]

    async def get_resource(
        self,
        uri: str,
        call_next: GetResourceNext,
        *,
        version: VersionSpec | None = None,
    ) -> Resource | None:
        return await call_next(uri, version=self._spec.intersect(version))

    # -------------------------------------------------------------------------
    # Resource Templates
    # -------------------------------------------------------------------------

    async def list_resource_templates(
        self, templates: Sequence[ResourceTemplate]
    ) -> Sequence[ResourceTemplate]:
        return [t for t in templates if self._spec.matches(t.version)]

    async def get_resource_template(
        self,
        uri: str,
        call_next: GetResourceTemplateNext,
        *,
        version: VersionSpec | None = None,
    ) -> ResourceTemplate | None:
        return await call_next(uri, version=self._spec.intersect(version))

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    async def list_prompts(self, prompts: Sequence[Prompt]) -> Sequence[Prompt]:
        return [p for p in prompts if self._spec.matches(p.version)]

    async def get_prompt(
        self, name: str, call_next: GetPromptNext, *, version: VersionSpec | None = None
    ) -> Prompt | None:
        return await call_next(name, version=self._spec.intersect(version))
