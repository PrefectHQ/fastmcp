"""Vendor-specific skills providers for various AI coding platforms."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastmcp.server.providers.skills.directory_provider import SkillsDirectoryProvider


class CursorSkillsProvider(SkillsDirectoryProvider):
    """Cursor skills from ~/.cursor/skills/."""

    def __init__(
        self,
        reload: bool = False,
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        root = Path.home() / ".cursor" / "skills"

        super().__init__(
            roots=[root],
            reload=reload,
            main_file_name="SKILL.md",
            supporting_files=supporting_files,
        )


class VSCodeSkillsProvider(SkillsDirectoryProvider):
    """VS Code skills from ~/.copilot/skills/."""

    def __init__(
        self,
        reload: bool = False,
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        root = Path.home() / ".copilot" / "skills"

        super().__init__(
            roots=[root],
            reload=reload,
            main_file_name="SKILL.md",
            supporting_files=supporting_files,
        )


class CodexSkillsProvider(SkillsDirectoryProvider):
    """Codex skills from /etc/codex/skills/ and ~/.codex/skills/.

    Scans both system-level and user-level directories. System skills take
    precedence if duplicates exist.
    """

    def __init__(
        self,
        reload: bool = False,
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        system_root = Path("/etc/codex/skills")
        user_root = Path.home() / ".codex" / "skills"

        # Include both paths (system first, then user)
        roots = [system_root, user_root]

        super().__init__(
            roots=roots,
            reload=reload,
            main_file_name="SKILL.md",
            supporting_files=supporting_files,
        )


class GeminiSkillsProvider(SkillsDirectoryProvider):
    """Gemini skills from ~/.gemini/skills/."""

    def __init__(
        self,
        reload: bool = False,
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        root = Path.home() / ".gemini" / "skills"

        super().__init__(
            roots=[root],
            reload=reload,
            main_file_name="SKILL.md",
            supporting_files=supporting_files,
        )


class GooseSkillsProvider(SkillsDirectoryProvider):
    """Goose skills from ~/.config/agents/skills/."""

    def __init__(
        self,
        reload: bool = False,
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        root = Path.home() / ".config" / "agents" / "skills"

        super().__init__(
            roots=[root],
            reload=reload,
            main_file_name="SKILL.md",
            supporting_files=supporting_files,
        )


class CopilotSkillsProvider(SkillsDirectoryProvider):
    """GitHub Copilot skills from ~/.copilot/skills/."""

    def __init__(
        self,
        reload: bool = False,
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        root = Path.home() / ".copilot" / "skills"

        super().__init__(
            roots=[root],
            reload=reload,
            main_file_name="SKILL.md",
            supporting_files=supporting_files,
        )


class OpenCodeSkillsProvider(SkillsDirectoryProvider):
    """OpenCode skills from ~/.config/opencode/skills/."""

    def __init__(
        self,
        reload: bool = False,
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        root = Path.home() / ".config" / "opencode" / "skills"

        super().__init__(
            roots=[root],
            reload=reload,
            main_file_name="SKILL.md",
            supporting_files=supporting_files,
        )
