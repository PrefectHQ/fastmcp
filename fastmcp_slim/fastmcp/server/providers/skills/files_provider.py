"""Skills published from loaded file bytes instead of a directory."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from fastmcp.utilities.versions import VersionSpec

logger = get_logger(__name__)


class _LoadedSkillFile(Resource):
    data: bytes

    async def read(self) -> ResourceResult:
        return file_content(str(self.uri), self.data)


@dataclass(frozen=True, slots=True)
class _LoadedSkills:
    skills: Mapping[str, Mapping[str, bytes]]
    resources: dict[str, _LoadedSkillFile]


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
        publishes `skill://deploy/SKILL.md`. Called once per MCP request; every
        lookup and read in that request reuses the result.
        """

    async def _load(self) -> _LoadedSkills:
        """Load once per request so every lookup and entry sees the same bytes."""
        from fastmcp.server.context import _current_context

        context = _current_context.get()
        key = f"_fastmcp_skill_files:{id(self)}"
        if context is not None and key in context._request_state:
            return context._request_state[key]
        skills = await self.skill_files()
        resources = {
            file_uri(skill_path, relative_path): _LoadedSkillFile(
                uri=file_uri(skill_path, relative_path),
                name=f"{skill_path}/{relative_path}",
                mime_type=file_mime_type(relative_path),
                data=content,
            )
            for skill_path, files in skills.items()
            for relative_path, content in files.items()
        }
        loaded = _LoadedSkills(skills, resources)
        if context is not None:
            context._request_state[key] = loaded
        return loaded

    @override
    async def _list_resources(self) -> Sequence[Resource]:
        return list((await self._load()).resources.values())

    @override
    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        resource = (await self._load()).resources.get(uri)
        if resource is None or (version and not version.matches(resource.version)):
            return None
        return resource

    @override
    async def _owns_skill_resource(self, uri: str) -> bool:
        return uri in (await self._load()).resources

    @override
    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog:
        skills = (await self._load()).skills
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
        for skill_path, files in (await self._load()).skills.items():
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
        server = get_context().fastmcp
        for relative_path, content in files.items():
            served = await server.get_resource(file_uri(skill_path, relative_path))
            if not isinstance(served, _LoadedSkillFile) or served.data != content:
                return None
        return entry
