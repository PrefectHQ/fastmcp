"""Deprecated: skills providers moved to `fastmcp.skills.provider`.

Every name here warns with the replacement and will be removed in FastMCP 5.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from fastmcp._warnings import FastMCPDeprecationWarning
from fastmcp.skills.provider import (
    SkillCatalogProvider,
    SkillsDirectoryProvider as _SkillsDirectoryProvider,
)
from fastmcp.skills.skill import Skill

_MIGRATION = (
    "Use `from fastmcp.skills.provider import SkillsDirectoryProvider` and call "
    "`SkillsDirectoryProvider(roots, reload=...)`; `main_file_name`, "
    "`supporting_files` and `experimental_skills` are gone. Each SKILL.md needs "
    "YAML frontmatter with `name` (matching its folder) and `description`."
)


def _warn(old: str, new: str) -> None:
    import fastmcp

    if fastmcp.settings.deprecation_warnings:
        warnings.warn(
            f"`{old}` from `fastmcp.server.providers.skills` is deprecated. {new}",
            FastMCPDeprecationWarning,
            stacklevel=3,
        )


class SkillsDirectoryProvider(_SkillsDirectoryProvider):
    """Deprecated alias for `fastmcp.skills.provider.SkillsDirectoryProvider`."""

    def __init__(
        self,
        roots: str | Path | Sequence[str | Path],
        reload: bool = False,
        main_file_name: str = "SKILL.md",
        supporting_files: Any = None,
        *,
        experimental_skills: Any = None,
    ) -> None:
        _warn("SkillsDirectoryProvider", _MIGRATION)
        if main_file_name != "SKILL.md":
            raise ValueError(
                "Skills must use SKILL.md; rename the main file. " + _MIGRATION
            )
        super().__init__(roots, reload=reload)


def SkillProvider(skill_path: str | Path, **_: Any) -> SkillCatalogProvider:
    """Deprecated: serve one skill folder."""
    _warn(
        "SkillProvider",
        "Use `SkillCatalogProvider([Skill.from_directory(path)])` from "
        "`fastmcp.skills.provider` and `fastmcp.skills`, or point "
        "`SkillsDirectoryProvider` at the parent folder.",
    )
    return SkillCatalogProvider([Skill.from_directory(skill_path)])


def _preset(name: str, *roots: str) -> Callable[..., _SkillsDirectoryProvider]:
    paths = ", ".join(f'Path("{root}").expanduser()' for root in roots)

    def preset(reload: bool = False, **_: Any) -> _SkillsDirectoryProvider:
        _warn(
            name,
            "Use `from fastmcp.skills.provider import SkillsDirectoryProvider` and "
            f"`SkillsDirectoryProvider([{paths}])`.",
        )
        return _SkillsDirectoryProvider(roots, reload=reload)

    preset.__name__ = preset.__qualname__ = name
    return preset


ClaudeSkillsProvider = _preset("ClaudeSkillsProvider", "~/.claude/skills")
CursorSkillsProvider = _preset("CursorSkillsProvider", "~/.cursor/skills")
VSCodeSkillsProvider = _preset("VSCodeSkillsProvider", "~/.copilot/skills")
CopilotSkillsProvider = _preset("CopilotSkillsProvider", "~/.copilot/skills")
CodexSkillsProvider = _preset(
    "CodexSkillsProvider", "/etc/codex/skills", "~/.codex/skills"
)
GeminiSkillsProvider = _preset("GeminiSkillsProvider", "~/.gemini/skills")
GooseSkillsProvider = _preset("GooseSkillsProvider", "~/.config/agents/skills")
OpenCodeSkillsProvider = _preset("OpenCodeSkillsProvider", "~/.config/opencode/skills")

__all__ = [
    "ClaudeSkillsProvider",
    "CodexSkillsProvider",
    "CopilotSkillsProvider",
    "CursorSkillsProvider",
    "GeminiSkillsProvider",
    "GooseSkillsProvider",
    "OpenCodeSkillsProvider",
    "SkillProvider",
    "SkillsDirectoryProvider",
    "VSCodeSkillsProvider",
]
