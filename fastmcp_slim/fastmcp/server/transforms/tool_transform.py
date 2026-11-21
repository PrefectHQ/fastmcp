"""Transform for applying tool transformations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.versions import VersionSpec

logger = get_logger(__name__)

if TYPE_CHECKING:
    from fastmcp.tools.base import Tool


class ToolTransform(Transform):
    """Applies tool transformations to modify tool schemas.

    Wraps ToolTransformConfig to apply argument renames, schema changes,
    hidden arguments, and other transformations at the transform level.

    Example:
        ```python
        transform = ToolTransform({
            "my_tool": ToolTransformConfig(
                name="renamed_tool",
                arguments={"old_arg": ArgTransformConfig(name="new_arg")}
            )
        })
        ```
    """

    def __init__(self, transforms: dict[str, ToolTransformConfig]) -> None:
        """Initialize ToolTransform.

        Args:
            transforms: Map of original tool name → transform config.
        """
        self._transforms = transforms

        # Build reverse mapping: final_name → original_name
        self._name_reverse: dict[str, str] = {}
        for original_name, config in transforms.items():
            final_name = config.name if config.name else original_name
            self._name_reverse[final_name] = original_name

        # Validate no duplicate target names
        seen_targets: dict[str, str] = {}
        for original_name, config in transforms.items():
            target = config.name if config.name else original_name
            if target in seen_targets:
                raise ValueError(
                    f"ToolTransform has duplicate target name {target!r}: "
                    f"both {seen_targets[target]!r} and {original_name!r} map to it"
                )
            seen_targets[target] = original_name

        # Tools whose transform failed, so each failure is logged once.
        self._reported: set[str] = set()

    def __repr__(self) -> str:
        names = list(self._transforms.keys())
        if len(names) <= 3:
            return f"ToolTransform({names!r})"
        return f"ToolTransform({names[:3]!r}... +{len(names) - 3} more)"

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Apply transforms to matching tools."""
        result: list[Tool] = []
        for tool in tools:
            if tool.name in self._transforms:
                transformed = self._apply(self._transforms[tool.name], tool)
                if transformed is not None:
                    result.append(transformed)
            else:
                result.append(tool)
        return result

    def _apply(self, config: ToolTransformConfig, tool: Tool) -> Tool | None:
        """The transformed tool, or None when the config cannot apply to it.

        A config is checked against the tool it targets only here, since that
        tool may come from a provider long after the transform is added. A
        config that does not fit (say, hiding a required argument with no
        default) leaves that one tool out instead of failing every request.
        """
        try:
            return config.apply(tool)
        except ValueError as e:
            if tool.name not in self._reported:
                self._reported.add(tool.name)
                logger.error(
                    "Tool transform for %r cannot apply, so the tool is hidden: %s",
                    tool.name,
                    e,
                )
            return None

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool by transformed name."""
        # Check if this name is a transformed name
        original_name = self._name_reverse.get(name, name)
        config = self._transforms.get(original_name)

        # Get the original tool
        tool = await call_next(original_name, version=version)

        # A config that sets a version replaces the source version, so the
        # requested version is checked against the transformed version. A
        # source that also matches the request keeps priority; otherwise the
        # source is resolved the same way as an unversioned request.
        if config is not None and "version" in config.model_fields_set:
            if version is not None and not version.matches(config.version):
                return None
            if tool is None and version is not None:
                tool = await call_next(original_name, version=None)

        if tool is None:
            return None

        # Apply transform if applicable
        if config is not None:
            transformed = self._apply(config, tool)
            if transformed is None:
                # The transformed tool is hidden, so its new name is free: a
                # tool of that name listed in its place must answer calls too.
                if original_name != name and name not in self._transforms:
                    fallback = await call_next(name, version=version)
                    if fallback is not None and fallback.name == name:
                        return fallback
                return None
            # Only return if requested name matches transformed name
            if transformed.name == name:
                return transformed
            return None

        # No transform, return as-is only if name matches
        return tool if tool.name == name else None
