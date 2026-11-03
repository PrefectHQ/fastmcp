"""Providers that serve `Skill` objects defined in code, loaded from folders, or from any source."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, unquote

import anyio
from mcp.server.context import ServerRequestContext
from pydantic import AnyUrl, ConfigDict

from fastmcp.resources.base import Resource, ResourceContent, ResourceResult
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.base import Provider
from fastmcp.skills._routing import _may_read, _resolve_read
from fastmcp.skills.models import MCPSkill
from fastmcp.skills.skill import Skill, _validate_relative_path
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.server.extensions import ServerExtension

logger = get_logger(__name__)

mimetypes.add_type("text/markdown", ".md")

SkillCatalog = Mapping[str, Skill] | Iterable[Skill]


class SkillFileResource(Resource):
    """One file of a `Skill`, served with its exact bytes."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    skill: Skill
    skill_path: str
    file_path: str

    async def read(self) -> ResourceResult:
        content = self.skill.files[self.file_path]
        value: str | bytes = content
        if self.mime_type.startswith("text/"):
            with suppress(UnicodeDecodeError):
                value = content.decode("utf-8")
        return ResourceResult([ResourceContent(value, mime_type=self.mime_type)])


class SkillManifestResource(Resource):
    """The `_manifest` file listing `Client.read_skill` uses without the Skills extension."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    skill: Skill

    async def read(self) -> str:
        files = [
            {
                "path": path,
                "size": len(content),
                "hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
            }
            for path, content in self.skill.files.items()
        ]
        return json.dumps({"skill": self.skill.name, "files": files}, indent=2)


class SkillCatalogProvider(Provider):
    """Serve `Skill` objects as MCP resources and Skills extension entries.

    Each skill's files are served under `skill://{path}/`, where `path` defaults
    to the skill's name. Pass a mapping to choose paths; the last path segment
    must equal the skill's name. Every client can read the files as resources,
    and the provider registers `SkillsExtension` so discovery clients can use
    `skills/list` and `skills/get`.

    For skills that change at runtime, such as rows in a database, subclass and
    override `load_skills()`. It is called once per MCP request.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.skills.provider import SkillCatalogProvider
        from fastmcp.skills import Skill

        review = Skill(
            name="code-review",
            description="Review a pull request for correctness and style.",
            instructions="# Code review\\n\\nRead the diff first...",
        )

        mcp = FastMCP("Skills")
        skills = SkillCatalogProvider([review, Skill.from_directory("./skills/deploy")])
        mcp.add_provider(skills)
        ```
    """

    def __init__(self, skills: SkillCatalog | None = None) -> None:
        super().__init__()
        self._static = _normalize(skills or ())

    def required_extensions(self) -> Sequence[ServerExtension]:
        from fastmcp.skills.extension import SkillsExtension

        return (SkillsExtension(),)

    async def load_skills(self) -> SkillCatalog:
        """Return the skills to serve. Override to load them from another source."""
        return self._static

    async def _catalog(self) -> dict[str, Skill]:
        context = None
        with suppress(RuntimeError):
            context = get_context()
        if context is None:
            return _normalize(await self.load_skills())
        # Concurrent lookups in one request wait on the same lock, so
        # load_skills() runs once and every caller sees the same catalog.
        key = f"_fastmcp_skill_catalog:{id(self)}"
        lock = context._request_state.setdefault(f"{key}:lock", anyio.Lock())
        async with lock:
            if key not in context._request_state:
                context._request_state[key] = _normalize(await self.load_skills())
            return context._request_state[key]

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def _list_resources(self) -> Sequence[Resource]:
        resources: list[Resource] = []
        for path, skill in (await self._catalog()).items():
            resources.extend(_file_resource(path, skill, file) for file in skill.files)
            resources.append(_manifest_resource(path, skill))
        return resources

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        located = _locate(uri, await self._catalog())
        if located is None:
            return None
        path, skill, file = located
        if file == "_manifest":
            return _manifest_resource(path, skill)
        if file in skill.files:
            return _file_resource(path, skill, file)
        return None

    # -------------------------------------------------------------------------
    # Skills extension source
    # -------------------------------------------------------------------------

    def _owns_skill_resource(self, uri: str) -> bool:
        # A static catalog knows its URIs; a loaded one may serve any skill:// URI.
        if type(self).load_skills is SkillCatalogProvider.load_skills:
            return _locate(uri, self._static) is not None
        return uri.startswith("skill://")

    async def _skill_uris(self, uri: str | None = None) -> Sequence[str]:
        uris = [_skill_md_uri(path) for path in await self._catalog()]
        return uris if uri is None else [u for u in uris if u == uri]

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> Sequence[MCPSkill]:
        return [
            skill.to_mcp_skill(path)
            for path, skill in (await self._catalog()).items()
            if await self._is_served(path, skill)
        ]

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> MCPSkill | None:
        for path, skill in (await self._catalog()).items():
            if _skill_md_uri(path) == uri and await self._is_served(path, skill):
                return skill.to_mcp_skill(path)
        return None

    async def _is_served(self, path: str, skill: Skill) -> bool:
        """Advertise a skill only when this caller can read every file, byte for byte."""
        server = get_context().fastmcp
        for file in skill.files:
            uri = _file_uri(path, file)
            served = await _resolve_read(server, uri)
            if not (
                isinstance(served, SkillFileResource)
                and served.skill == skill
                and await _may_read(server, uri)
            ):
                return False
        return True

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class SkillsDirectoryProvider(SkillCatalogProvider):
    """Serve every skill folder found below one or more root directories.

    A folder containing a `SKILL.md` is a skill, served at its path relative to
    the root, so `root/ops/deploy/SKILL.md` becomes `skill://ops/deploy/SKILL.md`.
    The folder name must match the `name` in its `SKILL.md`. Folders that aren't
    valid skills are skipped with a warning. When a path appears under more than
    one root, the first root wins.

    Skills are read once, at construction. Pass `reload=True` to re-read the
    folders on every request while you're editing them.

    Example:
        ```python
        from pathlib import Path

        from fastmcp import FastMCP
        from fastmcp.skills.provider import SkillsDirectoryProvider

        mcp = FastMCP("Local Skills")
        mcp.add_provider(SkillsDirectoryProvider(Path.home() / ".claude" / "skills"))
        ```
    """

    def __init__(
        self,
        roots: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
        *,
        reload: bool = False,
    ) -> None:
        if isinstance(roots, str | os.PathLike):
            roots = [roots]
        self._roots = tuple(Path(root).expanduser().absolute() for root in roots)
        self._reload = reload
        super().__init__(None if reload else self._scan())

    async def load_skills(self) -> SkillCatalog:
        if self._reload:
            return await anyio.to_thread.run_sync(self._scan)
        return self._static

    def _owns_skill_resource(self, uri: str) -> bool:
        if self._reload:
            return uri.startswith("skill://")
        return _locate(uri, self._static) is not None

    def _scan(self) -> dict[str, Skill]:
        skills: dict[str, Skill] = {}
        for root in self._roots:
            for path, folder in _skill_folders(root):
                if path in skills:
                    logger.debug("Skipping duplicate skill %r from %s", path, root)
                    continue
                if any(_nested(path, other) for other in skills):
                    logger.warning("Skipping skill %r nested in another skill", path)
                    continue
                try:
                    skill = Skill.from_directory(folder)
                except (OSError, ValueError) as exc:
                    logger.warning("Skipping invalid skill at %s: %s", folder, exc)
                    continue
                if skill.name != path.rsplit("/", 1)[-1]:
                    logger.warning(
                        "Skipping skill at %s: folder name must match its SKILL.md name %r",
                        root / path,
                        skill.name,
                    )
                    continue
                skills[path] = skill
        return skills

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(roots={list(self._roots)!r}, reload={self._reload})"
        )


def _skill_folders(root: Path) -> list[tuple[str, Path]]:
    """Find skill folders below `root` as (relative path, folder) pairs.

    A skill folder's subfolders belong to the skill, so the walk stops there.
    A symlink is followed only when it points at a skill folder.
    """
    found: list[tuple[str, Path]] = []

    def visit(directory: Path, prefix: str) -> None:
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            logger.warning("Skipping unreadable skills folder %s: %s", directory, exc)
            return
        for entry in entries:
            path = f"{prefix}{entry.name}"
            try:
                if not entry.is_dir():
                    continue
                if (entry / "SKILL.md").is_file():
                    found.append((path, entry.resolve()))
                    continue
                descend = not entry.is_symlink()
            except OSError as exc:
                logger.warning("Skipping unreadable skills folder %s: %s", entry, exc)
                continue
            if descend:
                visit(entry, f"{path}/")

    if root.is_dir():
        visit(root, "")
    return found


def _nested(path: str, other: str) -> bool:
    return path.startswith(f"{other}/") or other.startswith(f"{path}/")


def _normalize(skills: SkillCatalog) -> dict[str, Skill]:
    if isinstance(skills, Mapping):
        mapping = cast("Mapping[str, Skill]", skills)
        catalog = {
            _validate_relative_path(path): skill for path, skill in mapping.items()
        }
    else:
        catalog = {}
        for skill in skills:
            if skill.name in catalog:
                raise ValueError(f"Duplicate skill name: {skill.name!r}")
            catalog[skill.name] = skill
    for path, skill in catalog.items():
        if not isinstance(skill, Skill):
            raise TypeError(f"Expected a Skill at {path!r}, got {type(skill).__name__}")
        if path.split("/")[-1] != skill.name:
            raise ValueError(
                f"Skill path {path!r} must end with the skill's name {skill.name!r}"
            )
    for path in catalog:
        parts = path.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in catalog:
                raise ValueError(f"Skill path {path!r} is nested inside another skill")
    return catalog


def _file_uri(path: str, file: str) -> str:
    return f"skill://{quote(path, safe='/')}/{quote(file, safe='/')}"


def _skill_md_uri(path: str) -> str:
    return _file_uri(path, "SKILL.md")


def _locate(uri: str, catalog: Mapping[str, Skill]) -> tuple[str, Skill, str] | None:
    if not uri.startswith("skill://"):
        return None
    decoded = unquote(uri[len("skill://") :])
    for path, skill in catalog.items():
        if decoded.startswith(f"{path}/"):
            return path, skill, decoded[len(path) + 1 :]
    return None


def _file_resource(path: str, skill: Skill, file: str) -> SkillFileResource:
    mime_type = mimetypes.guess_type(file)[0] or "application/octet-stream"
    return SkillFileResource(
        uri=AnyUrl(_file_uri(path, file)),
        name=f"{path}/{file}",
        description=skill.description if file == "SKILL.md" else None,
        mime_type=mime_type,
        skill=skill,
        skill_path=path,
        file_path=file,
    )


def _manifest_resource(path: str, skill: Skill) -> SkillManifestResource:
    return SkillManifestResource(
        uri=AnyUrl(_file_uri(path, "_manifest")),
        name=f"{path}/_manifest",
        description=f"File listing for {skill.name}",
        mime_type="application/json",
        skill=skill,
    )
