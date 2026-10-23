"""Skills published from loaded file bytes instead of a directory."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

import yaml
from mcp.server.context import ServerRequestContext
from typing_extensions import override

from fastmcp.resources.base import Resource, ResourceResult
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.base import Provider
from fastmcp.skills._resources import file_content, file_mime_type
from fastmcp.skills._snapshot import build_entry, file_uri
from fastmcp.skills._source import _Catalog, _Lookup, _SkillSource
from fastmcp.skills.models import Skill
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class _LoadedSkillFile(Resource):
    data: bytes

    async def read(self) -> ResourceResult:
        return file_content(str(self.uri), self.data)


class SkillFilesProvider(Provider, _SkillSource):
    """Publish skills from file bytes you load, such as rows from a database.

    Implement `skill_files()`. FastMCP serves every returned file as a resource
    and builds each Skills entry, including frontmatter, sizes, and digests,
    from the same bytes. Register the provider on the server and pass it to
    `SkillsExtension(providers=[...])`. Skills that fail validation are omitted
    from discovery with a warning, and their files stay readable.
    """

    @abstractmethod
    async def skill_files(self) -> Mapping[str, Mapping[str, bytes]]:
        """Return each skill's files, keyed by skill path and then file path.

        For example, `{"deploy": {"SKILL.md": b"...", "checklist.md": b"..."}}`
        publishes `skill://deploy/SKILL.md`. Called for every list, lookup, and
        read, so cache the result when loading is expensive.
        """

    @override
    async def _list_resources(self) -> Sequence[Resource]:
        return [
            _LoadedSkillFile(
                uri=file_uri(skill_path, relative_path),
                name=f"{skill_path}/{relative_path}",
                mime_type=file_mime_type(relative_path),
                data=content,
            )
            for skill_path, files in (await self.skill_files()).items()
            for relative_path, content in files.items()
        ]

    @override
    async def _owns_skill_resource(self, uri: str) -> bool:
        return await self._get_resource(uri) is not None

    @override
    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog:
        skills = await self.skill_files()
        entries: list[Skill] = []
        for skill_path, files in skills.items():
            entry = await self._served_entry(skill_path, files)
            if entry is not None:
                entries.append(entry)
        return _Catalog(entries, tuple(file_uri(path, "SKILL.md") for path in skills))

    @override
    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup:
        for skill_path, files in (await self.skill_files()).items():
            if file_uri(skill_path, "SKILL.md") == uri:
                entry = await self._served_entry(skill_path, files)
                return _Lookup(matches=1, skill=entry)
        return _Lookup()

    async def _served_entry(
        self, skill_path: str, files: Mapping[str, bytes]
    ) -> Skill | None:
        """Build the entry only when the server serves these exact bytes."""
        try:
            entry = build_entry(skill_path, files)
        except (ValueError, yaml.YAMLError, RecursionError) as exc:
            logger.warning("Omitting invalid skill %s: %s", skill_path, exc)
            return None
        from fastmcp.skills._composition import _served_uri

        server = get_context().fastmcp
        for relative_path, content in files.items():
            uri = _served_uri(file_uri(skill_path, relative_path), self, server)
            served = await server.get_resource(uri)
            if not isinstance(served, _LoadedSkillFile) or served.data != content:
                return None
        return entry
