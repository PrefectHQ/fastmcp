"""Deprecation warnings for the resource-only skills providers.

These providers shipped in FastMCP 3 and 4.1. They are deprecated in 4.2 and
removed in 4.3 in favor of `fastmcp.skills.provider`. Each warning names the
replacement call and, because the replacement refuses to start on folders that
are not valid Agent Skills, lists the folders that need fixing first.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from pathlib import Path

import fastmcp
from fastmcp._warnings import FastMCPDeprecationWarning

_SPEC = "https://agentskills.io/specification"


def warn_directory_provider(
    cls: type,
    roots: Sequence[Path],
    *,
    reload: bool,
    main_file_name: str,
    stacklevel: int,
) -> None:
    """Warn that a resource-only directory provider is deprecated."""
    if not fastmcp.settings.deprecation_warnings:
        return
    from fastmcp.skills.provider import _scan_skill_roots

    old = f"`{cls.__name__}`"
    if not cls.__module__.startswith("fastmcp."):
        old += (
            " (a subclass of `fastmcp.server.providers.skills.SkillsDirectoryProvider`)"
        )
    paths = [str(root) for root in roots]
    reload_arg = ", reload=True" if reload else ""
    _, invalid = _scan_skill_roots(roots)
    warnings.warn(
        f"{old} is deprecated and will be removed in FastMCP 4.3. Replace it with "
        "`from fastmcp.skills.provider import SkillsDirectoryProvider` and "
        f"`SkillsDirectoryProvider({paths!r}{reload_arg})`. "
        + _readiness(invalid, main_file_name),
        FastMCPDeprecationWarning,
        stacklevel=stacklevel + 1,
    )


def warn_skill_provider(skill_path: Path, main_file_name: str) -> None:
    """Warn that the single-folder `SkillProvider` is deprecated."""
    if not fastmcp.settings.deprecation_warnings:
        return
    from fastmcp.skills.provider import _invalid_folder
    from fastmcp.skills.skill import Skill

    invalid: list[str] = []
    try:
        skill = Skill.from_directory(skill_path)
        if skill.name != skill_path.name:
            raise ValueError(f"folder name must match its SKILL.md name {skill.name!r}")
    except (OSError, ValueError) as exc:
        invalid.append(_invalid_folder(skill_path, skill_path.name, exc))
    warnings.warn(
        "`SkillProvider` is deprecated and will be removed in FastMCP 4.3. Replace it "
        "with `from fastmcp.skills import Skill`, "
        "`from fastmcp.skills.provider import SkillCatalogProvider` and "
        f"`SkillCatalogProvider([Skill.from_directory({str(skill_path)!r})])`. "
        + _readiness(invalid, main_file_name),
        FastMCPDeprecationWarning,
        stacklevel=3,
    )


def _readiness(invalid: list[str], main_file_name: str = "SKILL.md") -> str:
    rename = (
        f"Rename each `{main_file_name}` to `SKILL.md` first. "
        if main_file_name != "SKILL.md"
        else ""
    )
    intro = (
        "The replacement serves skills through the MCP skills extension as well as "
        "`skill://` resources, and it stops the server at startup if a folder is not "
        f"a valid Agent Skill ({_SPEC}). {rename}"
    )
    if main_file_name != "SKILL.md":
        return intro + "The folders can only be checked once they use `SKILL.md`."
    if not invalid:
        return intro + "Every skill folder found already passes."
    return intro + "Fix these folders first:\n" + "\n".join(invalid)
