"""Deprecated: download skills with `Client.read_skill` and `Skill.to_directory`.

Every name here warns with the replacement and will be removed in FastMCP 5.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp._warnings import FastMCPDeprecationWarning

if TYPE_CHECKING:
    from fastmcp.client import Client

_INSTALL = (
    "Use `skill = await client.read_skill(uri)` then "
    "`skill.to_directory(target_dir / skill.name, overwrite=...)`, where `uri` is "
    "`skill://<name>/SKILL.md` or an entry's `.uri` from `await client.list_skills()` "
    "(needs `Client(..., extensions=[SkillsClientExtension()])` from `fastmcp.skills`)."
)


def _warn(old: str, new: str) -> None:
    import fastmcp

    if fastmcp.settings.deprecation_warnings:
        warnings.warn(
            f"`fastmcp.utilities.skills.{old}` is deprecated. {new}",
            FastMCPDeprecationWarning,
            stacklevel=3,
        )


async def download_skill(
    client: Client,
    skill_name: str,
    target_dir: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Deprecated: read one skill and write it to `target_dir/<name>`."""
    _warn("download_skill", _INSTALL)
    skill = await client.read_skill(f"skill://{skill_name}/SKILL.md")
    target = Path(target_dir).expanduser() / skill.name
    return skill.to_directory(target, overwrite=overwrite)


async def sync_skills(
    client: Client, target_dir: str | Path, *, overwrite: bool = False
) -> list[Path]:
    """Deprecated: read every listed skill and write each to `target_dir`."""
    _warn("sync_skills", "Loop over `await client.list_skills()`. " + _INSTALL)
    paths = []
    for entry in await client.list_skills():
        skill = await client.read_skill(entry.uri)
        target = Path(target_dir).expanduser() / skill.name
        if target.exists() and not overwrite:
            continue
        paths.append(skill.to_directory(target, overwrite=overwrite))
    return paths


_GONE = {
    "list_skill_summaries": "Use `await client.list_skills()`; each entry has "
    "`.uri` and `.frontmatter.name` / `.frontmatter.description`.",
    "list_skills": "Use `await client.list_skills()`.",
    "get_skill_manifest": "Use `await client.get_skill(uri)`; its `.resources` "
    "list each file's URI, size and digest.",
}


def __getattr__(name: str) -> object:
    # ImportError, not AttributeError, so `from ... import name` keeps the message.
    if name in _GONE:
        raise ImportError(
            f"`fastmcp.utilities.skills.{name}` was removed. {_GONE[name]}"
        )
    if name in {"SkillSummary", "SkillFile", "SkillManifest"}:
        raise ImportError(
            f"`fastmcp.utilities.skills.{name}` was removed. Use `MCPSkill` "
            "from `fastmcp.skills`."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
