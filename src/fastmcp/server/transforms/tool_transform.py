"""Transform for applying tool transformations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.tools.tool import Tool


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
                transformed = self._transforms[tool.name].apply(tool)
                result.append(transformed)
            else:
                result.append(tool)
        return result

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool by transformed name."""
        # Check if this name is a transformed name
        original_name = self._name_reverse.get(name, name)

        # Get the original tool
        tool = await call_next(original_name, version=version)
        if tool is None:
            return None

        # Apply transform if applicable
        if original_name in self._transforms:
            transformed = self._transforms[original_name].apply(tool)
            # Only return if requested name matches transformed name
            if transformed.name == name:
                return transformed
            return None

        # No transform, return as-is only if name matches
        return tool if tool.name == name else None
