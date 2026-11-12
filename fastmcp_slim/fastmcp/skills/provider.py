"""Providers that serve `Skill` objects defined in code, loaded from folders, or from any source."""

from __future__ import annotations

import json
import mimetypes
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast
from urllib.parse import quote, unquote

import anyio
from mcp.server.context import ServerRequestContext
from pydantic import AnyUrl, ConfigDict, Field, ValidationError

from fastmcp.resources.base import Resource, ResourceContent, ResourceResult
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.base import Provider
from fastmcp.skills._routing import _may_read, _resolve_read
from fastmcp.skills.models import MCPSkill
from fastmcp.skills.skill import (
    Skill,
    SkillFile,
    SkillManifest,
    _is_authored_skill,
    _validate_relative_path,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.server.extensions import ServerExtension

logger = get_logger(__name__)

mimetypes.add_type("text/markdown", ".md")

_T = TypeVar("_T")

SkillCatalog = Mapping[str, Skill] | Iterable[Skill]


class SkillManifestProvider(Provider):
    """Serve skills from their manifests, and read each file only when a client reads it.

    Discovery needs only each skill's frontmatter and the digest and size of its
    files. A provider whose files live in object storage or a database can
    advertise every skill without loading any file, and fetch one file when a
    client reads it.

    Subclass and override `load_manifests()` and `read_file()`.
    `load_manifests()` is called once per MCP request.

    Example:
        ```python
        from fastmcp import FastMCP, Skill
        from fastmcp.skills import SkillFile, SkillManifest
        from fastmcp.skills.provider import SkillManifestProvider

        review = Skill(name="review", description="Review changes.", instructions="")
        blobs = {review.manifest.files["SKILL.md"].digest: review.files["SKILL.md"]}


        class StoredSkills(SkillManifestProvider):
            async def load_manifests(self) -> dict[str, SkillManifest]:
                return {"review": review.manifest}

            async def read_file(self, path: str, file_path: str, file: SkillFile) -> bytes:
                return blobs[file.digest]


        mcp = FastMCP("Skills", providers=[StoredSkills()])
        ```
    """

    def required_extensions(self) -> Sequence[ServerExtension]:
        from fastmcp.skills.extension import SkillsExtension

        return (SkillsExtension(),)

    async def load_manifests(self) -> Mapping[str, SkillManifest]:
        """Return the skills to serve, keyed by the path each is served under."""
        raise NotImplementedError

    async def read_file(self, path: str, file_path: str, file: SkillFile) -> bytes:
        """Return the content of `file_path` in the skill served at `skill://{path}/`."""
        raise NotImplementedError

    async def _manifests(self) -> dict[str, SkillManifest]:
        async def load() -> dict[str, SkillManifest]:
            manifests = {
                _validate_relative_path(path): manifest
                for path, manifest in (await self.load_manifests()).items()
            }
            _validate_skill_paths(
                {
                    path: manifest.frontmatter.name
                    for path, manifest in manifests.items()
                }
            )
            return manifests

        return await _once_per_request(self, "manifests", load)

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def _list_resources(self) -> Sequence[Resource]:
        resources: list[Resource] = []
        for path, manifest in (await self._manifests()).items():
            resources.extend(
                _file_resource(self, path, manifest, file_path, file)
                for file_path, file in manifest.files.items()
            )
            resources.append(_manifest_resource(path, manifest))
        return resources

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        located = _locate(uri, await self._manifests())
        if located is None:
            return None
        path, manifest, file_path = located
        if file_path == "_manifest":
            return _manifest_resource(path, manifest)
        file = manifest.files.get(file_path)
        if file is None:
            return None
        return _file_resource(self, path, manifest, file_path, file)

    # -------------------------------------------------------------------------
    # Skills extension source
    # -------------------------------------------------------------------------

    def _owns_skill_resource(self, uri: str) -> bool:
        return uri.startswith("skill://")

    async def _skill_uris(self, uri: str | None = None) -> Sequence[str]:
        uris = [_skill_md_uri(path) for path in await self._manifests()]
        return uris if uri is None else [u for u in uris if u == uri]

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> Sequence[MCPSkill]:
        return [
            manifest.to_mcp_skill(path)
            for path, manifest in (await self._manifests()).items()
            if await self._is_served(path, manifest)
        ]

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> MCPSkill | None:
        for path, manifest in (await self._manifests()).items():
            if _skill_md_uri(path) == uri and await self._is_served(path, manifest):
                return manifest.to_mcp_skill(path)
        return None

    async def _is_served(self, path: str, manifest: SkillManifest) -> bool:
        """Advertise a skill only when this caller can read every file as listed."""
        server = get_context().fastmcp
        for file_path, file in manifest.files.items():
            uri = _file_uri(path, file_path)
            served = await _resolve_read(server, uri)
            if not (
                isinstance(served, SkillFileResource)
                and served.file == file
                and await _may_read(server, uri)
            ):
                return False
        return True

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class SkillFileResource(Resource):
    """One file of a skill, read from its provider when a client reads it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: SkillManifestProvider = Field(exclude=True, repr=False)
    skill_path: str
    file_path: str
    file: SkillFile

    async def read(self) -> ResourceResult:
        content = await self.provider.read_file(
            self.skill_path, self.file_path, self.file
        )
        value: str | bytes = content
        if self.mime_type.startswith("text/"):
            with suppress(UnicodeDecodeError):
                value = content.decode("utf-8")
        return ResourceResult([ResourceContent(value, mime_type=self.mime_type)])


class SkillManifestResource(Resource):
    """The `_manifest` file listing `Client.read_skill` uses without the Skills extension."""

    manifest: SkillManifest

    async def read(self) -> str:
        files = [
            {"path": path, "size": file.size, "hash": file.digest}
            for path, file in self.manifest.files.items()
        ]
        return json.dumps(
            {"skill": self.manifest.frontmatter.name, "files": files}, indent=2
        )


class SkillCatalogProvider(SkillManifestProvider):
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

    async def load_skills(self) -> SkillCatalog:
        """Return the skills to serve. Override to load them from another source."""
        return self._static

    async def load_manifests(self) -> Mapping[str, SkillManifest]:
        return {
            path: skill._manifest for path, skill in (await self._catalog()).items()
        }

    async def read_file(self, path: str, file_path: str, file: SkillFile) -> bytes:
        return (await self._catalog())[path].files[file_path]

    async def _catalog(self) -> dict[str, Skill]:
        async def load() -> dict[str, Skill]:
            return _normalize(await self.load_skills())

        return await _once_per_request(self, "catalog", load)

    def _owns_skill_resource(self, uri: str) -> bool:
        # A static catalog knows its URIs; a loaded one may serve any skill:// URI.
        if type(self).load_skills is SkillCatalogProvider.load_skills:
            return _locate(uri, self._static) is not None
        return uri.startswith("skill://")


class SkillsDirectoryProvider(SkillCatalogProvider):
    """Serve every skill folder found below one or more root directories.

    A folder containing a `SKILL.md` is a skill, served at its path relative to
    the root, so `root/ops/deploy/SKILL.md` becomes `skill://ops/deploy/SKILL.md`.
    Each `SKILL.md` must follow the [Agent Skills spec](https://agentskills.io/specification):
    YAML frontmatter with a `description` and a `name` of lowercase letters,
    digits and hyphens that matches its folder. An invalid folder raises
    `ValueError` naming the fix, so a server never starts with skills missing.
    Hidden folders are skipped. When a path appears under more than one root,
    the first root wins.

    Skills are read once, at construction. Pass `reload=True` to re-read the
    folders on every request while you're editing them; if a re-read finds an
    invalid folder, the error is logged and the last valid skills keep serving.

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
        # Scan even with reload=True so an invalid folder fails at startup.
        self._last_good = self._scan()
        super().__init__(self._last_good)

    async def load_skills(self) -> SkillCatalog:
        if not self._reload:
            return self._static
        try:
            self._last_good = await anyio.to_thread.run_sync(self._scan)
        except ValueError as exc:
            # A folder broke mid-edit: keep serving the last good scan.
            logger.error("Keeping the last valid skills; %s", exc)
        return self._last_good

    def _owns_skill_resource(self, uri: str) -> bool:
        if self._reload:
            return uri.startswith("skill://")
        return _locate(uri, self._static) is not None

    def _scan(self) -> dict[str, Skill]:
        skills, invalid = _scan_skill_roots(self._roots)
        if invalid:
            raise ValueError(
                f"{len(invalid)} skill folder(s) are not valid Agent Skills "
                "(https://agentskills.io/specification):\n" + "\n".join(invalid)
            )
        return skills

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(roots={list(self._roots)!r}, reload={self._reload})"
        )


def _scan_skill_roots(roots: Sequence[Path]) -> tuple[dict[str, Skill], list[str]]:
    """Load every skill folder below `roots`.

    Returns the valid skills by relative path, plus one line per invalid folder
    saying what is wrong and how to fix it. The first root wins a duplicate path.
    """
    skills: dict[str, Skill] = {}
    invalid: list[str] = []
    for root in roots:
        for path, folder in _skill_folders(root):
            if path in skills:
                logger.debug("Skipping duplicate skill %r from %s", path, root)
                continue
            if any(_nested(path, other) for other in skills):
                logger.warning("Skipping skill %r nested in another skill", path)
                continue
            folder_name = path.rsplit("/", 1)[-1]
            try:
                skill = Skill.from_directory(folder)
                if skill.name != folder_name:
                    raise ValueError(
                        f"folder name must match its SKILL.md name {skill.name!r}"
                    )
            except (OSError, ValueError) as exc:
                invalid.append(_invalid_folder(root / path, folder_name, exc))
                continue
            skills[path] = skill
    return skills, invalid


def _invalid_folder(folder: Path, folder_name: str, exc: Exception) -> str:
    """One line per invalid folder: what's wrong, then how to fix it."""
    if isinstance(exc, ValidationError):
        reason = exc.errors()[0]["msg"].removeprefix("Value error, ")
    else:
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    if isinstance(exc, OSError) or any(
        word in reason for word in ("symlink", "non-regular", "at most")
    ):
        return f"- {folder}: {reason}"
    name = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", folder_name.lower()))
    name = name.strip("-")[:64] or "my-skill"
    rename = f"rename the folder to {name!r} and " if name != folder_name else ""
    return (
        f"- {folder}: {reason}. Fix: {rename}start SKILL.md with "
        f"'---\\nname: {name}\\ndescription: <what it does and when to use it>\\n---'"
    )


def _skill_folders(root: Path) -> list[tuple[str, Path]]:
    """Find skill folders below `root` as (relative path, folder) pairs.

    A skill folder's subfolders belong to the skill, so the walk stops there.
    A symlink is followed only when it points at a skill folder. Hidden folders,
    such as `.git`, are skipped.
    """
    found: list[tuple[str, Path]] = []

    def visit(directory: Path, prefix: str) -> None:
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            logger.warning("Skipping unreadable skills folder %s: %s", directory, exc)
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
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
            if not _is_authored_skill(skill):
                raise TypeError(f"Expected a Skill, got {type(skill).__name__}")
            if skill.name in catalog:
                raise ValueError(f"Duplicate skill name: {skill.name!r}")
            catalog[skill.name] = skill
    for path, skill in catalog.items():
        if not _is_authored_skill(skill):
            raise TypeError(f"Expected a Skill at {path!r}, got {type(skill).__name__}")
    _validate_skill_paths({path: skill.name for path, skill in catalog.items()})
    return catalog


def _validate_skill_paths(names: Mapping[str, str]) -> None:
    """Check that each path ends with its skill's name and holds no other skill."""
    for path, name in names.items():
        if path.split("/")[-1] != name:
            raise ValueError(
                f"Skill path {path!r} must end with the skill's name {name!r}"
            )
    for path in names:
        parts = path.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in names:
                raise ValueError(f"Skill path {path!r} is nested inside another skill")


async def _once_per_request(
    provider: Provider, name: str, load: Callable[[], Awaitable[_T]]
) -> _T:
    context = None
    with suppress(RuntimeError):
        context = get_context()
    if context is None:
        return await load()
    # Concurrent lookups in one request wait on the same lock, so `load` runs
    # once and every caller sees the same result.
    key = f"_fastmcp_skill_{name}:{id(provider)}"
    lock = context._request_state.setdefault(f"{key}:lock", anyio.Lock())
    async with lock:
        if key not in context._request_state:
            context._request_state[key] = await load()
        return context._request_state[key]


def _file_uri(path: str, file: str) -> str:
    return f"skill://{quote(path, safe='/')}/{quote(file, safe='/')}"


def _skill_md_uri(path: str) -> str:
    return _file_uri(path, "SKILL.md")


def _locate(uri: str, catalog: Mapping[str, _T]) -> tuple[str, _T, str] | None:
    if not uri.startswith("skill://"):
        return None
    decoded = unquote(uri[len("skill://") :])
    for path, skill in catalog.items():
        if decoded.startswith(f"{path}/"):
            return path, skill, decoded[len(path) + 1 :]
    return None


def _file_resource(
    provider: SkillManifestProvider,
    path: str,
    manifest: SkillManifest,
    file_path: str,
    file: SkillFile,
) -> SkillFileResource:
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    return SkillFileResource(
        uri=AnyUrl(_file_uri(path, file_path)),
        name=f"{path}/{file_path}",
        description=manifest.frontmatter.description
        if file_path == "SKILL.md"
        else None,
        mime_type=mime_type,
        provider=provider,
        skill_path=path,
        file_path=file_path,
        file=file,
    )


def _manifest_resource(path: str, manifest: SkillManifest) -> SkillManifestResource:
    return SkillManifestResource(
        uri=AnyUrl(_file_uri(path, "_manifest")),
        name=f"{path}/_manifest",
        description=f"File listing for {manifest.frontmatter.name}",
        mime_type="application/json",
        manifest=manifest,
    )
