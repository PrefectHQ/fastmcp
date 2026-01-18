"""Enabled transform for marking component enabled state.

Each Enabled instance marks components via internal metadata. Multiple
enabled transforms can be stacked - later transforms override earlier ones.
Final filtering happens at the Provider level.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, TypeVar

from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.transforms import (
    GetPromptNext,
    GetResourceNext,
    GetResourceTemplateNext,
    GetToolNext,
    ListPromptsNext,
    ListResourcesNext,
    ListResourceTemplatesNext,
    ListToolsNext,
    Transform,
)
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.tools.tool import Tool
    from fastmcp.utilities.components import FastMCPComponent

T = TypeVar("T", bound="FastMCPComponent")

# Enabled state stored at meta["fastmcp"]["_internal"]["enabled"]
_FASTMCP_KEY = "fastmcp"
_INTERNAL_KEY = "_internal"


class Enabled(Transform):
    """Sets enabled state on matching components.

    Does NOT filter inline - just marks components with enabled state.
    Later transforms in the chain can override earlier marks.
    Final filtering happens at the Provider level after all transforms run.

    Example:
        ```python
        # Disable components tagged "internal"
        Enabled(False, tags=frozenset({"internal"}))

        # Re-enable specific tool (override earlier disable)
        Enabled(True, names={"safe_tool"})

        # Allowlist via composition:
        Enabled(False, match_all=True)  # disable everything
        Enabled(True, tags=frozenset({"public"}))  # enable public
        ```
    """

    def __init__(
        self,
        enabled: bool,
        *,
        names: set[str] | None = None,
        keys: set[str] | None = None,
        version: VersionSpec | None = None,
        tags: frozenset[str] | None = None,
        components: frozenset[str] | None = None,
        match_all: bool = False,
    ) -> None:
        """Initialize an enabled marker.

        Args:
            enabled: If True, mark matching as enabled; if False, mark as disabled.
            names: Component names or URIs to match.
            keys: Component keys to match (e.g., {"tool:my_tool@v1"}).
            version: Component version spec to match. Unversioned components (version=None)
                will NOT match a version spec.
            tags: Tags to match (component must have at least one).
            components: Component types to match (e.g., frozenset({"tool", "prompt"})).
            match_all: If True, matches all components regardless of other criteria.
        """
        self._enabled = enabled
        self.names = names
        self.keys = keys
        self.version = version
        self.tags = tags  # e.g., frozenset({"internal", "deprecated"})
        self.components = components  # e.g., frozenset({"tool", "prompt"})
        self.match_all = match_all

    def __repr__(self) -> str:
        action = "enable" if self._enabled else "disable"
        if self.match_all:
            return f"Enabled({self._enabled}, match_all=True)"
        parts = []
        if self.names:
            parts.append(f"names={set(self.names)}")
        if self.keys:
            parts.append(f"keys={set(self.keys)}")
        if self.version:
            parts.append(f"version={self.version!r}")
        if self.components:
            parts.append(f"components={set(self.components)}")
        if self.tags:
            parts.append(f"tags={set(self.tags)}")
        if parts:
            return f"Enabled({action}, {', '.join(parts)})"
        return f"Enabled({action})"

    def _matches(self, component: FastMCPComponent) -> bool:
        """Check if this transform applies to the component.

        All specified criteria must match (intersection semantics).
        An empty rule (no criteria) matches nothing.
        Use match_all=True to match everything.

        Args:
            component: Component to check.

        Returns:
            True if this transform should mark the component.
        """
        # Match-all flag matches everything
        if self.match_all:
            return True

        # Empty criteria matches nothing (safe default)
        if (
            self.names is None
            and self.keys is None
            and self.version is None
            and self.components is None
            and self.tags is None
        ):
            return False

        # Check component type if specified
        if self.components is not None:
            component_type = component.key.split(":")[
                0
            ]  # e.g., "tool" from "tool:foo@"
            if component_type not in self.components:
                return False

        # Check keys if specified (exact match only)
        if self.keys is not None:
            if component.key not in self.keys:
                return False

        # Check names if specified
        if self.names is not None:
            # For resources, also check URI; for templates, check uri_template
            matches_name = component.name in self.names
            matches_uri = False
            if isinstance(component, Resource):
                matches_uri = str(component.uri) in self.names
            elif isinstance(component, ResourceTemplate):
                matches_uri = component.uri_template in self.names
            if not (matches_name or matches_uri):
                return False

        # Check version if specified
        # Note: match_none=False means unversioned components don't match a version spec
        if self.version is not None and not self.version.matches(
            component.version, match_none=False
        ):
            return False

        # Check tags if specified (component must have at least one matching tag)
        return self.tags is None or bool(component.tags & self.tags)

    def _mark_component(self, component: T) -> T:
        """Set enabled state in component metadata if rule matches."""
        if not self._matches(component):
            return component

        # Create new dicts to avoid mutating shared dicts
        # (e.g., when Tool.from_tool shares the meta dict between tools)
        if component.meta is None:
            component.meta = {_FASTMCP_KEY: {_INTERNAL_KEY: {"enabled": self._enabled}}}
        else:
            old_fastmcp = component.meta.get(_FASTMCP_KEY, {})
            old_internal = old_fastmcp.get(_INTERNAL_KEY, {})
            new_internal = {**old_internal, "enabled": self._enabled}
            new_fastmcp = {**old_fastmcp, _INTERNAL_KEY: new_internal}
            component.meta = {**component.meta, _FASTMCP_KEY: new_fastmcp}
        return component

    # -------------------------------------------------------------------------
    # Transform methods (mark components, don't filter)
    # -------------------------------------------------------------------------

    async def list_tools(self, call_next: ListToolsNext) -> Sequence[Tool]:
        """Mark tools by enabled state."""
        tools = await call_next()
        return [self._mark_component(t) for t in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Mark tool if found."""
        tool = await call_next(name, version=version)
        if tool is None:
            return None
        return self._mark_component(tool)

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def list_resources(self, call_next: ListResourcesNext) -> Sequence[Resource]:
        """Mark resources by enabled state."""
        resources = await call_next()
        return [self._mark_component(r) for r in resources]

    async def get_resource(
        self,
        uri: str,
        call_next: GetResourceNext,
        *,
        version: VersionSpec | None = None,
    ) -> Resource | None:
        """Mark resource if found."""
        resource = await call_next(uri, version=version)
        if resource is None:
            return None
        return self._mark_component(resource)

    # -------------------------------------------------------------------------
    # Resource Templates
    # -------------------------------------------------------------------------

    async def list_resource_templates(
        self, call_next: ListResourceTemplatesNext
    ) -> Sequence[ResourceTemplate]:
        """Mark resource templates by enabled state."""
        templates = await call_next()
        return [self._mark_component(t) for t in templates]

    async def get_resource_template(
        self,
        uri: str,
        call_next: GetResourceTemplateNext,
        *,
        version: VersionSpec | None = None,
    ) -> ResourceTemplate | None:
        """Mark resource template if found."""
        template = await call_next(uri, version=version)
        if template is None:
            return None
        return self._mark_component(template)

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    async def list_prompts(self, call_next: ListPromptsNext) -> Sequence[Prompt]:
        """Mark prompts by enabled state."""
        prompts = await call_next()
        return [self._mark_component(p) for p in prompts]

    async def get_prompt(
        self, name: str, call_next: GetPromptNext, *, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Mark prompt if found."""
        prompt = await call_next(name, version=version)
        if prompt is None:
            return None
        return self._mark_component(prompt)


def is_enabled(component: FastMCPComponent) -> bool:
    """Check if component is enabled.

    Returns True if:
    - No enabled mark exists (default is enabled)
    - Enabled mark is True

    Returns False if enabled mark is False.

    Args:
        component: Component to check.

    Returns:
        True if component should be enabled/visible to clients.
    """
    meta = component.meta or {}
    fastmcp = meta.get(_FASTMCP_KEY, {})
    internal = fastmcp.get(_INTERNAL_KEY, {})
    return internal.get("enabled", True)  # Default True if not set
