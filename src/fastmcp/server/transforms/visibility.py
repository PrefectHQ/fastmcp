"""Visibility transform for marking component visibility state.

Each Visibility instance marks components via internal metadata. Multiple
visibility transforms can be stacked - later transforms override earlier ones.
Final filtering happens at the Provider level.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import mcp.types

from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
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
    from fastmcp.server.context import Context
    from fastmcp.tools.tool import Tool
    from fastmcp.utilities.components import FastMCPComponent

T = TypeVar("T", bound="FastMCPComponent")

# Visibility state stored at meta["fastmcp"]["_internal"]["visibility"]
_FASTMCP_KEY = "fastmcp"
_INTERNAL_KEY = "_internal"


class Visibility(Transform):
    """Sets visibility state on matching components.

    Does NOT filter inline - just marks components with visibility state.
    Later transforms in the chain can override earlier marks.
    Final filtering happens at the Provider level after all transforms run.

    Example:
        ```python
        # Disable components tagged "internal"
        Visibility(False, tags={"internal"})

        # Re-enable specific tool (override earlier disable)
        Visibility(True, names={"safe_tool"})

        # Allowlist via composition:
        Visibility(False, match_all=True)  # disable everything
        Visibility(True, tags={"public"})  # enable public
        ```
    """

    def __init__(
        self,
        enabled: bool,
        *,
        names: set[str] | None = None,
        keys: set[str] | None = None,
        version: VersionSpec | None = None,
        tags: set[str] | None = None,
        components: set[Literal["tool", "resource", "template", "prompt"]]
        | None = None,
        match_all: bool = False,
    ) -> None:
        """Initialize a visibility marker.

        Args:
            enabled: If True, mark matching as enabled; if False, mark as disabled.
            names: Component names or URIs to match.
            keys: Component keys to match (e.g., {"tool:my_tool@v1"}).
            version: Component version spec to match. Unversioned components (version=None)
                will NOT match a version spec.
            tags: Tags to match (component must have at least one).
            components: Component types to match (e.g., {"tool", "prompt"}).
            match_all: If True, matches all components regardless of other criteria.
        """
        self._enabled = enabled
        self.names = names
        self.keys = keys
        self.version = version
        self.tags = tags  # e.g., {"internal", "deprecated"}
        self.components = components  # e.g., {"tool", "prompt"}
        self.match_all = match_all

    def __repr__(self) -> str:
        action = "enable" if self._enabled else "disable"
        if self.match_all:
            return f"Visibility({self._enabled}, match_all=True)"
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
            return f"Visibility({action}, {', '.join(parts)})"
        return f"Visibility({action})"

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
        """Set visibility state in component metadata if rule matches."""
        if not self._matches(component):
            return component

        # Create new dicts to avoid mutating shared dicts
        # (e.g., when Tool.from_tool shares the meta dict between tools)
        if component.meta is None:
            component.meta = {
                _FASTMCP_KEY: {_INTERNAL_KEY: {"visibility": self._enabled}}
            }
        else:
            old_fastmcp = component.meta.get(_FASTMCP_KEY, {})
            old_internal = old_fastmcp.get(_INTERNAL_KEY, {})
            new_internal = {**old_internal, "visibility": self._enabled}
            new_fastmcp = {**old_fastmcp, _INTERNAL_KEY: new_internal}
            component.meta = {**component.meta, _FASTMCP_KEY: new_fastmcp}
        return component

    # -------------------------------------------------------------------------
    # Transform methods (mark components, don't filter)
    # -------------------------------------------------------------------------

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Mark tools by visibility state."""
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

    async def list_resources(self, resources: Sequence[Resource]) -> Sequence[Resource]:
        """Mark resources by visibility state."""
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
        self, templates: Sequence[ResourceTemplate]
    ) -> Sequence[ResourceTemplate]:
        """Mark resource templates by visibility state."""
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

    async def list_prompts(self, prompts: Sequence[Prompt]) -> Sequence[Prompt]:
        """Mark prompts by visibility state."""
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
    - No visibility mark exists (default is enabled)
    - Visibility mark is True

    Returns False if visibility mark is False.

    Args:
        component: Component to check.

    Returns:
        True if component should be enabled/visible to clients.
    """
    meta = component.meta or {}
    fastmcp = meta.get(_FASTMCP_KEY, {})
    internal = fastmcp.get(_INTERNAL_KEY, {})
    return internal.get("visibility", True)  # Default True if not set


# -------------------------------------------------------------------------
# Session visibility control
# -------------------------------------------------------------------------

if TYPE_CHECKING:
    from fastmcp.server.context import Context


async def get_visibility_rules(context: Context) -> list[dict[str, Any]]:
    """Load visibility rule dicts from session state."""
    return await context.get_state("_visibility_rules") or []


async def save_visibility_rules(
    context: Context,
    rules: list[dict[str, Any]],
    *,
    components: set[Literal["tool", "resource", "template", "prompt"]] | None = None,
) -> None:
    """Save visibility rule dicts to session state and send notifications.

    Args:
        context: The context to save rules for.
        rules: The visibility rules to save.
        components: Optional hint about which component types are affected.
            If None, sends notifications for all types (safe default).
            If provided, only sends notifications for specified types.
    """
    await context.set_state("_visibility_rules", rules)

    # Send notifications based on components hint
    # Note: MCP has no separate template notification - templates use ResourceListChangedNotification
    if components is None or "tool" in components:
        await context.send_notification(mcp.types.ToolListChangedNotification())
    if components is None or "resource" in components or "template" in components:
        await context.send_notification(mcp.types.ResourceListChangedNotification())
    if components is None or "prompt" in components:
        await context.send_notification(mcp.types.PromptListChangedNotification())


def create_visibility_transforms(rules: list[dict[str, Any]]) -> list[Visibility]:
    """Convert rule dicts to Visibility transforms."""
    transforms = []
    for params in rules:
        version = None
        if params.get("version"):
            version_dict = params["version"]
            version = VersionSpec(
                gte=version_dict.get("gte"),
                lt=version_dict.get("lt"),
                eq=version_dict.get("eq"),
            )
        transforms.append(
            Visibility(
                params["enabled"],
                names=set(params["names"]) if params.get("names") else None,
                keys=set(params["keys"]) if params.get("keys") else None,
                version=version,
                tags=set(params["tags"]) if params.get("tags") else None,
                components=(
                    set(params["components"]) if params.get("components") else None
                ),
                match_all=params.get("match_all", False),
            )
        )
    return transforms


async def get_session_transforms(context: Context) -> list[Visibility]:
    """Get session-specific Visibility transforms from state store."""
    try:
        # Will raise RuntimeError if no session available
        _ = context.session_id
    except RuntimeError:
        return []

    rules = await get_visibility_rules(context)
    return create_visibility_transforms(rules)


async def enable_components(
    context: Context,
    *,
    names: set[str] | None = None,
    keys: set[str] | None = None,
    version: VersionSpec | None = None,
    tags: set[str] | None = None,
    components: set[Literal["tool", "resource", "template", "prompt"]] | None = None,
    match_all: bool = False,
) -> None:
    """Enable components matching criteria for this session only.

    Session rules override global transforms. Rules accumulate - each call
    adds a new rule to the session. Later marks override earlier ones
    (Visibility transform semantics).

    Sends notifications to this session only: ToolListChangedNotification,
    ResourceListChangedNotification, and PromptListChangedNotification.

    Args:
        context: The context for this session.
        names: Component names or URIs to match.
        keys: Component keys to match (e.g., {"tool:my_tool@v1"}).
        version: Component version spec to match.
        tags: Tags to match (component must have at least one).
        components: Component types to match (e.g., {"tool", "prompt"}).
        match_all: If True, matches all components regardless of other criteria.
    """
    # Normalize empty sets to None (empty = match all)
    components = components if components else None

    # Load current rules
    rules = await get_visibility_rules(context)

    # Create new rule dict
    rule: dict[str, Any] = {
        "enabled": True,
        "names": list(names) if names else None,
        "keys": list(keys) if keys else None,
        "version": (
            {"gte": version.gte, "lt": version.lt, "eq": version.eq}
            if version
            else None
        ),
        "tags": list(tags) if tags else None,
        "components": list(components) if components else None,
        "match_all": match_all,
    }

    # Add and save (notifications sent by save_visibility_rules)
    rules.append(rule)
    await save_visibility_rules(context, rules, components=components)


async def disable_components(
    context: Context,
    *,
    names: set[str] | None = None,
    keys: set[str] | None = None,
    version: VersionSpec | None = None,
    tags: set[str] | None = None,
    components: set[Literal["tool", "resource", "template", "prompt"]] | None = None,
    match_all: bool = False,
) -> None:
    """Disable components matching criteria for this session only.

    Session rules override global transforms. Rules accumulate - each call
    adds a new rule to the session. Later marks override earlier ones
    (Visibility transform semantics).

    Sends notifications to this session only: ToolListChangedNotification,
    ResourceListChangedNotification, and PromptListChangedNotification.

    Args:
        context: The context for this session.
        names: Component names or URIs to match.
        keys: Component keys to match (e.g., {"tool:my_tool@v1"}).
        version: Component version spec to match.
        tags: Tags to match (component must have at least one).
        components: Component types to match (e.g., {"tool", "prompt"}).
        match_all: If True, matches all components regardless of other criteria.
    """
    # Normalize empty sets to None (empty = match all)
    components = components if components else None

    # Load current rules
    rules = await get_visibility_rules(context)

    # Create new rule dict
    rule: dict[str, Any] = {
        "enabled": False,
        "names": list(names) if names else None,
        "keys": list(keys) if keys else None,
        "version": (
            {"gte": version.gte, "lt": version.lt, "eq": version.eq}
            if version
            else None
        ),
        "tags": list(tags) if tags else None,
        "components": list(components) if components else None,
        "match_all": match_all,
    }

    # Add and save (notifications sent by save_visibility_rules)
    rules.append(rule)
    await save_visibility_rules(context, rules, components=components)


async def reset_visibility(context: Context) -> None:
    """Clear all session visibility rules.

    Use this to reset session visibility back to global defaults.

    Sends notifications to this session only: ToolListChangedNotification,
    ResourceListChangedNotification, and PromptListChangedNotification.

    Args:
        context: The context for this session.
    """
    await save_visibility_rules(context, [])


ComponentT = TypeVar("ComponentT", bound="FastMCPComponent")


async def apply_session_transforms(
    components: Sequence[ComponentT],
) -> Sequence[ComponentT]:
    """Apply session-specific visibility transforms to components.

    This helper applies session-level enable/disable rules by marking
    components with their visibility state. Session transforms override
    global transforms due to mark-based semantics (later marks win).

    Args:
        components: The components to apply session transforms to.

    Returns:
        The components with session transforms applied.
    """
    from fastmcp.server.context import _current_context

    current_ctx = _current_context.get()
    if current_ctx is None:
        return components

    session_transforms = await get_session_transforms(current_ctx)
    if not session_transforms:
        return components

    # Apply each transform's marking to each component
    result = list(components)
    for transform in session_transforms:
        result = [transform._mark_component(c) for c in result]
    return result
