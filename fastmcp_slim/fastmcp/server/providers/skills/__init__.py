"""Deprecated: skills providers moved to `fastmcp.skills.provider`.

Every name here warns with the replacement and will be removed in FastMCP 5.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastmcp._warnings import FastMCPDeprecationWarning
from fastmcp.skills.provider import (
    SkillCatalogProvider,
    _invalid_folder,
)
from fastmcp.skills.provider import (
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
    folder = Path(skill_path)
    try:
        skill = Skill.from_directory(folder)
    except (OSError, ValueError) as exc:
        raise ValueError(_invalid_folder(folder, folder.name, exc)) from exc
    return SkillCatalogProvider([skill])


class _VendorPreset(SkillsDirectoryProvider):
    _ROOTS: tuple[str, ...] = ()

    def __init__(self, reload: bool = False, supporting_files: Any = None) -> None:
        paths = ", ".join(f'Path("{root}").expanduser()' for root in self._ROOTS)
        _warn(
            type(self).__name__,
            "Use `from fastmcp.skills.provider import SkillsDirectoryProvider` and "
            f"`SkillsDirectoryProvider([{paths}])`.",
        )
        _SkillsDirectoryProvider.__init__(self, self._ROOTS, reload=reload)


class ClaudeSkillsProvider(_VendorPreset):
    _ROOTS = ("~/.claude/skills",)


class CursorSkillsProvider(_VendorPreset):
    _ROOTS = ("~/.cursor/skills",)


class VSCodeSkillsProvider(_VendorPreset):
    _ROOTS = ("~/.copilot/skills",)


class CopilotSkillsProvider(_VendorPreset):
    _ROOTS = ("~/.copilot/skills",)


class CodexSkillsProvider(_VendorPreset):
    _ROOTS = ("/etc/codex/skills", "~/.codex/skills")


class GeminiSkillsProvider(_VendorPreset):
    _ROOTS = ("~/.gemini/skills",)


class GooseSkillsProvider(_VendorPreset):
    _ROOTS = ("~/.config/agents/skills",)


class OpenCodeSkillsProvider(_VendorPreset):
    _ROOTS = ("~/.config/opencode/skills",)


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
