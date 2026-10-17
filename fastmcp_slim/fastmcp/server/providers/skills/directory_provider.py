"""Directory scanning provider for discovering multiple skills."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, unquote

import yaml
from anyio.to_thread import run_sync
from mcp.server.context import ServerRequestContext
from mcp.shared.path_security import PathEscapeError, safe_join

from fastmcp.resources.base import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.providers.skills._common import SkillInfo
from fastmcp.server.providers.skills.skill_provider import (
    SkillFileResource,
    SkillFileTemplate,
    SkillProvider,
    SkillResource,
)
from fastmcp.skills._snapshot import Snapshot, snapshot
from fastmcp.skills._source import _Catalog, _Lookup, _SkillSource
from fastmcp.skills.models import GetSkillResult, ListSkillsResult, Skill
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.versions import VersionSpec

logger = get_logger(__name__)


class SkillsDirectoryProvider(AggregateProvider, _SkillSource):
    """Provider that scans directories and creates a SkillProvider per skill folder.

    This extends AggregateProvider to combine multiple SkillProviders into one.
    Each subdirectory containing a main file (default: SKILL.md) becomes a skill.
    Can scan multiple root directories - if a skill name appears in multiple roots,
    the first one found wins.

    Args:
        roots: Root directory(ies) containing skill folders. Can be a single path
            or a sequence of paths.
        experimental_skills: Opt into experimental SEP-2640 discovery, strict
            manifests, nested skills, and live byte-preserving reads. Defaults to False.
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
        *,
        experimental_skills: bool = False,
    ) -> None:
        super().__init__()
        # Normalize to sequence: single path becomes list
        if isinstance(roots, (str, Path)):
            roots = [roots]

        self._experimental_skills = experimental_skills
        if experimental_skills and main_file_name != "SKILL.md":
            raise ValueError("experimental_skills requires main_file_name='SKILL.md'")
        self._roots = [
            Path(r).absolute() if experimental_skills else Path(r).resolve()
            for r in roots
        ]
        self._reload = reload
        self._main_file_name = main_file_name
        self._supporting_files = supporting_files
        self._discovered = False

        if not experimental_skills:
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
                try:
                    skill_dir = safe_join(root, skill_dir.name)
                except (PathEscapeError, OSError, RuntimeError):
                    logger.warning(
                        f"Skipping skill directory that cannot be used: {skill_dir.name}"
                    )
                    continue

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
                except (
                    FileNotFoundError,
                    PermissionError,
                    OSError,
                    PathEscapeError,
                    UnicodeDecodeError,
                ):
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
        if self._experimental_skills:
            return await self._protocol_list_resources()
        await self._ensure_discovered()
        return await super()._list_resources()

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        if self._experimental_skills:
            return await self._protocol_list_resource_templates()
        await self._ensure_discovered()
        return await super()._list_resource_templates()

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        if self._experimental_skills:
            return await self._protocol_get_resource(uri, version)
        await self._ensure_discovered()
        return await super()._get_resource(uri, version)

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        if self._experimental_skills:
            return await self._protocol_get_resource_template(uri, version)
        await self._ensure_discovered()
        return await super()._get_resource_template(uri, version)

    def __repr__(self) -> str:
        roots_repr = self._roots[0] if len(self._roots) == 1 else self._roots
        return (
            f"SkillsDirectoryProvider(roots={roots_repr!r}, "
            f"reload={self._reload}, skills={len(self.providers)})"
        )

    def _candidate_directories(self) -> list[tuple[str, Path]]:
        candidates: list[tuple[str, Path]] = []
        for root in self._roots:
            if root.is_symlink():
                logger.warning("Ignoring symlink Skills root: %s", root)
                continue
            if not root.is_dir():
                continue
            for main in sorted(root.rglob("SKILL.md")):
                directory = main.parent
                if not main.is_file() or directory == root:
                    continue
                if any(
                    p.is_symlink()
                    for p in (directory, *directory.parents)
                    if p == root or root in p.parents
                ):
                    continue
                candidates.append((directory.relative_to(root).as_posix(), directory))
        return candidates

    def _directories(self) -> list[tuple[str, Path]]:
        candidates = self._candidate_directories()
        counts = Counter(name for name, _ in candidates)
        for name, count in counts.items():
            if count > 1:
                logger.warning("Omitting duplicate skill path: %s", name)
        return [(name, path) for name, path in candidates if counts[name] == 1]

    def _snapshots(self) -> list[Snapshot]:
        snapshots: list[Snapshot] = []
        for name, path in self._directories():
            try:
                snapshots.append(snapshot(path, name))
            except (OSError, ValueError, yaml.YAMLError, RecursionError) as exc:
                logger.warning("Omitting invalid skill %s: %s", path, exc)
        return snapshots

    def _resource_provider(
        self, root: Path, name: str, relative: str | None = None
    ) -> SkillProvider:
        if relative is None or relative == "_manifest":
            provider = SkillProvider(root, supporting_files=self._supporting_files)
            info = provider.skill_info
            info.name = name
            info.path = root
            info.preserve_bytes = True
            return provider
        info = SkillInfo(
            name=name,
            description=f"Skill: {name}",
            path=root,
            main_file="SKILL.md",
            preserve_bytes=True,
        )
        return SkillProvider(
            root, supporting_files=self._supporting_files, _skill_info=info
        )

    async def _protocol_list_resources(self) -> Sequence[Resource]:
        resources: dict[str, Resource] = {}
        for name, root in await run_sync(self._directories):
            try:
                provider = await run_sync(self._resource_provider, root, name)
            except (OSError, ValueError):
                continue
            for resource in await provider.list_resources():
                resources[str(resource.uri)] = resource
        return list(resources.values())

    def _directories_for_name(self, name: str) -> list[Path]:
        directories = []
        for root in self._roots:
            try:
                directory = safe_join(root, name)
            except (PathEscapeError, OSError, RuntimeError):
                continue
            lexical = root / name
            if any(
                path.is_symlink()
                for path in (lexical, *lexical.parents)
                if path == root or root in path.parents
            ):
                continue
            if (directory / "SKILL.md").is_file():
                directories.append(lexical)
        return directories

    def _uri_parts(self, uri: str) -> list[str] | None:
        if not uri.startswith("skill://"):
            return None
        encoded = uri[len("skill://") :]
        decoded = unquote(encoded)
        parts = decoded.split("/")
        if (
            quote(decoded, safe="/") != encoded
            or len(parts) < 2
            or any(part in {"", ".", ".."} for part in parts)
            or "\\" in decoded
        ):
            return None
        return parts

    def _resolve_file(self, uri: str) -> tuple[Path, str] | None:
        parts = self._uri_parts(uri)
        if parts is None:
            return None
        for length in range(len(parts) - 1, 0, -1):
            directories = self._directories_for_name("/".join(parts[:length]))
            if len(directories) > 1:
                return None
            if directories:
                return directories[0], "/".join(parts[length:])
        return None

    async def _protocol_get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        resolved = self._resolve_file(uri)
        if resolved is None:
            return None
        root, relative = resolved
        if (
            relative not in {"SKILL.md", "_manifest"}
            and self._supporting_files != "resources"
        ):
            return None
        path = root / relative
        if relative != "_manifest" and not path.is_file():
            return None
        parts = self._uri_parts(uri)
        assert parts is not None
        name = "/".join(parts[: -len(relative.split("/"))])
        if relative == "_manifest":
            provider = await run_sync(self._resource_provider, root, name, relative)
        else:
            provider = self._resource_provider(root, name, relative)
        return await provider.get_resource(uri, version)

    async def _protocol_list_resource_templates(self) -> Sequence[ResourceTemplate]:
        if self._supporting_files != "template":
            return []
        templates: list[ResourceTemplate] = []
        for name, root in await run_sync(self._directories):
            provider = self._resource_provider(root, name, "SKILL.md")
            templates.extend(await provider.list_resource_templates())
        return templates

    async def _protocol_get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        if self._supporting_files != "template":
            return None
        resolved = self._resolve_file(uri)
        if resolved is None:
            return None
        root, relative = resolved
        if relative in {"SKILL.md", "_manifest"}:
            return None
        parts = self._uri_parts(uri)
        assert parts is not None
        name = "/".join(parts[: -len(relative.split("/"))])
        provider = self._resource_provider(root, name, relative)
        return await provider.get_resource_template(uri, version)

    async def _visible_entry(self, snap: Snapshot) -> Skill | None:
        server = get_context().fastmcp
        for uri, path in snap.files:
            component: Resource | ResourceTemplate | None = await server.get_resource(
                uri
            )
            if component is not None:
                if isinstance(component, SkillResource) and not component.is_manifest:
                    backing = component.skill_info.path / component.skill_info.main_file
                elif isinstance(component, SkillFileResource):
                    backing = component.skill_info.path / component.file_path
                else:
                    return None
                if backing != path or not component.skill_info.preserve_bytes:
                    return None
            else:
                component = await server.get_resource_template(uri)
                if not isinstance(component, SkillFileTemplate):
                    return None
                params = component.matches(uri)
                if params is None or component.skill_info.path / params["path"] != path:
                    return None
        return snap.entry

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog:
        entries = []
        for snap in await run_sync(self._snapshots):
            entry = await self._visible_entry(snap)
            if entry is not None:
                entries.append(entry)
        return _Catalog(
            ListSkillsResult(skills=entries), tuple(await self._skill_uris())
        )

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup:
        parts = self._uri_parts(uri)
        if parts is None or parts[-1] != "SKILL.md":
            return _Lookup()
        name = "/".join(parts[:-1])
        directories = self._directories_for_name(name)
        if len(directories) != 1:
            return _Lookup(matches=len(directories))
        try:
            snap = await run_sync(snapshot, directories[0], name)
        except (OSError, ValueError, yaml.YAMLError, RecursionError) as exc:
            logger.warning("Omitting invalid skill %s: %s", directories[0], exc)
            return _Lookup(matches=1)
        entry = await self._visible_entry(snap)
        return _Lookup(
            matches=1, result=GetSkillResult(skill=entry) if entry is not None else None
        )

    def _owns_skill_resource(self, uri: str) -> bool:
        parts = self._uri_parts(uri)
        return parts is not None and any(
            self._directories_for_name("/".join(parts[:length]))
            for length in range(len(parts) - 1, 0, -1)
        )

    def _validate_skill_source(self) -> None:
        if not self._experimental_skills:
            raise ValueError("Skills discovery requires experimental_skills=True")

    async def _skill_uris(self, uri: str | None = None) -> Sequence[str]:
        self._validate_skill_source()
        if uri is None:
            candidates = await run_sync(self._candidate_directories)
            return [
                f"skill://{quote(name, safe='/')}/SKILL.md" for name, _ in candidates
            ]
        parts = self._uri_parts(uri)
        if parts is None or parts[-1] != "SKILL.md":
            return []
        directories = self._directories_for_name("/".join(parts[:-1]))
        return [uri] * len(directories)
