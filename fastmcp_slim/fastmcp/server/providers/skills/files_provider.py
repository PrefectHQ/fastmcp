"""Skills published from loaded file bytes instead of a directory."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping

import yaml

from fastmcp.server.providers.skills.catalog_provider import SkillCatalogProvider
from fastmcp.skills.skill import Skill
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class SkillFilesProvider(SkillCatalogProvider):
    """Publish skills from file bytes you load, such as rows from a database.

    Implement `skill_files()`. Each skill's files become a `Skill`, so they are
    served and advertised exactly like any other `SkillCatalogProvider` skill.
    Skills that fail validation are omitted with a warning. To build `Skill`
    objects yourself, override `load_skills()` on `SkillCatalogProvider` instead.
    """

    def __init__(self) -> None:
        super().__init__()

    @abstractmethod
    async def skill_files(self) -> Mapping[str, Mapping[str, bytes]]:
        """Return each skill's files, keyed by skill path and then file path.

        For example, `{"deploy": {"SKILL.md": b"...", "checklist.md": b"..."}}`
        publishes `skill://deploy/SKILL.md`. Called once per MCP request.
        """

    async def load_skills(self) -> Mapping[str, Skill]:
        skills: dict[str, Skill] = {}
        for path, files in (await self.skill_files()).items():
            try:
                skill = Skill.from_files(files)
            except (ValueError, yaml.YAMLError, RecursionError) as exc:
                logger.warning("Omitting invalid skill %s: %s", path, exc)
                continue
            if path.split("/")[-1] != skill.name:
                logger.warning("Omitting skill %s: its name is %r", path, skill.name)
                continue
            skills[path] = skill
        return skills
