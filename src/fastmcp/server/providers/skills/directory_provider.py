"""Directory scanning provider for discovering multiple skills."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.providers.skills.skill_provider import SkillProvider
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.versions import VersionSpec

logger = get_logger(__name__)


class SkillsDirectoryProvider(AggregateProvider):
    """Provider that scans directories and creates a SkillProvider per skill folder.

    This extends AggregateProvider to combine multiple SkillProviders into one.
    Each subdirectory containing a main file (default: SKILL.md) becomes a skill.
    Can scan multiple root directories - if a skill name appears in multiple roots,
    the first one found wins.

    Args:
        roots: Root directory(ies) containing skill folders. Can be a single path
            or a sequence of paths.
        reload: If True, re-discover skills on each request. Defaults to False.
        main_file_name: Name of the main skill file. Defaults to "SKILL.md".
        supporting_files: How supporting files are exposed in child SkillProviders:
            - "template": Accessed via ResourceTemplate, hidden from list_resources().
            - "resources": Each file exposed as individual Resource in list_resources().

    Example:
        ```python
        from pathlib import Path
        from fastmcp import FastMCP
        from fastmcp.server.providers.skills import SkillsDirectoryProvider

        mcp = FastMCP("Skills")
        # Single directory
        mcp.add_provider(SkillsDirectoryProvider(
            roots=Path.home() / ".claude" / "skills",
            reload=True,  # Re-scan on each request
        ))
        # Multiple directories
        mcp.add_provider(SkillsDirectoryProvider(
            roots=[Path("/etc/skills"), Path.home() / ".local" / "skills"],
        ))
        ```
    """

    def __init__(
        self,
        roots: str | Path | Sequence[str | Path],
        reload: bool = False,
        main_file_name: str = "SKILL.md",
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        super().__init__()
        # Normalize to sequence: single path becomes list
        if isinstance(roots, (str, Path)):
            roots = [roots]

        self._roots = [Path(r).resolve() for r in roots]
        self._reload = reload
        self._main_file_name = main_file_name
        self._supporting_files = supporting_files
        self._discovered = False

        # Discover skills at init
        self._discover_skills()

    def _discover_skills(self) -> None:
        """Scan root directories and create SkillProvider per valid skill folder."""
        # Clear existing providers if reloading
        self.providers.clear()

        seen_skill_names: set[str] = set()

        for root in self._roots:
            if not root.exists():
                logger.debug(f"Skills root does not exist: {root}")
                continue

            for skill_dir in root.iterdir():
                if not skill_dir.is_dir():
                    continue

                main_file = skill_dir / self._main_file_name
                if not main_file.exists():
                    continue

                skill_name = skill_dir.name
                # Skip if we've already seen this skill name (first wins)
                if skill_name in seen_skill_names:
                    logger.debug(
                        f"Skipping duplicate skill '{skill_name}' from {root} "
                        f"(already found in earlier root)"
                    )
                    continue

                try:
                    provider = SkillProvider(
                        skill_path=skill_dir,
                        main_file_name=self._main_file_name,
                        supporting_files=self._supporting_files,
                    )
                    self.providers.append(provider)
                    seen_skill_names.add(skill_name)
                except (FileNotFoundError, PermissionError, OSError):
                    logger.exception(f"Failed to load skill: {skill_dir.name}")

        self._discovered = True
        logger.debug(
            f"SkillsDirectoryProvider loaded {len(self.providers)} skills "
            f"from {len(self._roots)} root(s)"
        )

    async def _ensure_discovered(self) -> None:
        """Ensure skills are discovered, rediscovering if reload is enabled."""
        if self._reload or not self._discovered:
            self._discover_skills()

    # Override list methods to support reload
    async def _list_resources(self) -> Sequence[Resource]:
        await self._ensure_discovered()
        return await super()._list_resources()

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        await self._ensure_discovered()
        return await super()._list_resource_templates()

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        await self._ensure_discovered()
        return await super()._get_resource(uri, version)

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        await self._ensure_discovered()
        return await super()._get_resource_template(uri, version)

    def __repr__(self) -> str:
        roots_repr = self._roots[0] if len(self._roots) == 1 else self._roots
        return (
            f"SkillsDirectoryProvider(roots={roots_repr!r}, "
            f"reload={self._reload}, skills={len(self.providers)})"
        )
