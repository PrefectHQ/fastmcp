"""`experimental_skills=True` now means the new provider; alpha-only helpers are gone."""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmcp import Client, FastMCP
from fastmcp._warnings import FastMCPDeprecationWarning
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills.extension import SkillsExtension
from fastmcp.skills.provider import SkillsDirectoryProvider

REVIEW = Skill(name="review", description="Review changes", instructions="# Review\n")


async def test_experimental_skills_returns_the_replacement(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    REVIEW.to_directory(tmp_path / "review")
    # The 4.1 experimental setup, verbatim from its docs.
    mcp = FastMCP("Skills")
    with pytest.warns(FastMCPDeprecationWarning, match="experimental_skills") as record:
        skills = OldDirectoryProvider(tmp_path, experimental_skills=True)
    assert record[0].filename == __file__
    assert f"SkillsDirectoryProvider([{str(tmp_path)!r}])" in str(record[0].message)
    mcp.add_provider(skills)
    mcp.add_extension(SkillsExtension(providers=[skills]))

    assert isinstance(skills, SkillsDirectoryProvider)
    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        (entry,) = await client.list_skills()
    assert entry.uri == "skill://review/SKILL.md"


def test_experimental_skills_still_rejects_custom_main_file(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    with pytest.raises(ValueError, match="SKILL.md"):
        OldDirectoryProvider(
            tmp_path, main_file_name="README.md", experimental_skills=True
        )


def test_alpha_only_helper_is_gone():
    import fastmcp.utilities.skills as helpers

    assert not hasattr(helpers, "list_skill_summaries")


def test_experimental_skills_refuses_subclasses(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    class GuardedProvider(OldDirectoryProvider):
        pass

    with pytest.raises(TypeError, match="GuardedProvider"):
        GuardedProvider(tmp_path, experimental_skills=True)
