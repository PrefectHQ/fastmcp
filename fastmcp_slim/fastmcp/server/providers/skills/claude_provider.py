"""Claude-specific skills provider for Claude Code skills."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastmcp.server.providers.skills.directory_provider import SkillsDirectoryProvider


class ClaudeSkillsProvider(SkillsDirectoryProvider):
    """Provider for Claude Code skills from ~/.claude/skills/.

    A convenience subclass that sets the default root to Claude's skills location.

    Args:
        reload: If True, re-scan on every request. Defaults to False.
        supporting_files: How supporting files are exposed:
            - "template": Accessed via ResourceTemplate, hidden from list_resources().
            - "resources": Each file exposed as individual Resource in list_resources().

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.providers.skills import ClaudeSkillsProvider

        mcp = FastMCP("Claude Skills")
        mcp.add_provider(ClaudeSkillsProvider())  # Uses default location
        ```
    """

    def __init__(
        self,
        reload: bool = False,
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        root = Path.home() / ".claude" / "skills"

        super().__init__(
            roots=[root],
            reload=reload,
            main_file_name="SKILL.md",
            supporting_files=supporting_files,
        )
