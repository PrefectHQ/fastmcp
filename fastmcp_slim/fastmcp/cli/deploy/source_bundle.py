"""Resolve and archive local source for a Horizon deployment."""

from __future__ import annotations

import json
import os
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import ValidationError

from fastmcp.cli.deploy.source_archive import (
    ArchiveTooLargeError,
    SourceInvalidError,
    _collect_entries,
    _sha256,
    _write_archive,
)
from fastmcp.utilities.mcp_server_config import MCPServerConfig
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource

_PROJECT_ROOT_MARKERS = ("fastmcp.json", "pyproject.toml", "requirements.txt", ".git")
_ARCHIVED_CONFIG_PATH = "fastmcp.json"
_ENTRYPOINT_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_ENTRYPOINT_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SourceBundle:
    """Metadata for a completed deployment source archive."""

    archive_path: Path
    size_bytes: int
    checksum_sha256: str
    entrypoint: str
    dependency_path: str | None


async def create_source_bundle(
    server_spec: str | None,
    archive_path: Path,
) -> SourceBundle:
    """Resolve a FastMCP server input and create its deployment archive."""
    config, source_root, config_path = _load_config(server_spec)
    resolved_archive_path = _resolve_archive_path(source_root, archive_path)
    source_base = (
        _resolve_path(
            source_root,
            config.deployment.cwd,
            label="Deployment working directory",
            directory=True,
        )
        if config_path is not None and config.deployment.cwd is not None
        else source_root
    )
    configured_source_path = Path(config.source.path).expanduser()
    source_path = _resolve_path(
        source_root,
        configured_source_path
        if configured_source_path.is_absolute()
        else source_base / configured_source_path,
        label="Server source",
        file=True,
    )
    entrypoint = _resolve_entrypoint(
        source_root,
        source_path,
        config.source.entrypoint,
    )

    dependency_path: str | None = None
    dependency_files: tuple[Path, ...] = ()
    if config_path is None:
        dependency_path, dependency_files = _detect_dependency(
            source_root,
            source_path.parent,
        )

    archive_created = False
    try:
        entries = _collect_entries(
            source_root,
            required_paths=(source_path, *dependency_files),
            excluded_paths=(config_path,) if config_path is not None else (),
        )
        if config_path is not None:
            entries.append(
                (
                    _ARCHIVED_CONFIG_PATH,
                    _sanitized_config(config, source_root, source_base),
                )
            )
        entries.sort(key=lambda entry: entry[0])

        resolved_archive_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw_file = resolved_archive_path.open("xb")
        except FileExistsError as exc:
            raise SourceInvalidError("The source archive path already exists") from exc
        archive_created = True
        with raw_file:
            _write_archive(entries, raw_file, source_root)
        size_bytes = resolved_archive_path.stat().st_size
        checksum = _sha256(resolved_archive_path)
    except (ArchiveTooLargeError, SourceInvalidError):
        if archive_created:
            resolved_archive_path.unlink(missing_ok=True)
        raise
    except (OSError, tarfile.TarError) as exc:
        if archive_created:
            resolved_archive_path.unlink(missing_ok=True)
        raise SourceInvalidError(
            f"The source archive could not be created: {exc}"
        ) from exc

    return SourceBundle(
        archive_path=resolved_archive_path,
        size_bytes=size_bytes,
        checksum_sha256=checksum,
        entrypoint=entrypoint,
        dependency_path=dependency_path,
    )


def _sanitized_config(
    config: MCPServerConfig,
    source_root: Path,
    dependency_base: Path,
) -> bytes:
    environment = config.environment
    archived_environment: dict[str, object] = {"type": environment.type}
    if environment.python is not None:
        archived_environment["python"] = environment.python
    if environment.dependencies is not None:
        archived_environment["dependencies"] = [
            _sanitized_dependency(dependency) for dependency in environment.dependencies
        ]
    if environment.requirements is not None:
        archived_environment["requirements"] = _sanitized_path(
            source_root,
            dependency_base,
            environment.requirements,
            label="Requirements file",
        )
    if environment.project is not None:
        archived_environment["project"] = _sanitized_path(
            source_root,
            dependency_base,
            environment.project,
            label="Environment project",
        )
    if environment.editable is not None:
        archived_environment["editable"] = [
            _sanitized_path(
                source_root,
                dependency_base,
                path,
                label="Editable dependency",
            )
            for path in environment.editable
        ]

    archived: dict[str, object] = {"environment": archived_environment}
    if config.deployment.cwd is not None:
        archived["deployment"] = {"cwd": _relative_path(source_root, dependency_base)}
    return (json.dumps(archived, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sanitized_dependency(dependency: str) -> str:
    bare_url = urlsplit(dependency.strip())
    if bare_url.scheme:
        _validate_dependency_url(bare_url, label="URL")

    try:
        requirement = Requirement(dependency)
    except InvalidRequirement:
        return dependency
    if requirement.url is None:
        return dependency

    _validate_dependency_url(urlsplit(requirement.url), label=requirement.name)
    return dependency


def _validate_dependency_url(url: SplitResult, *, label: str) -> None:
    if url.username is not None or url.password is not None:
        raise SourceInvalidError(
            f"The dependency must not contain credentials: {label}"
        )
    if url.query:
        raise SourceInvalidError(
            f"The dependency must not contain URL query parameters: {label}"
        )


def _sanitized_path(
    source_root: Path,
    dependency_base: Path,
    value: str | Path,
    *,
    label: str,
) -> str:
    requested = _path_from_base(dependency_base, value)
    candidate = Path(os.path.abspath(requested))
    try:
        candidate.relative_to(source_root)
        candidate.resolve(strict=False).relative_to(source_root)
    except ValueError as exc:
        raise SourceInvalidError(f"{label} leaves the source root: {value}") from exc
    except (OSError, RuntimeError) as exc:
        raise SourceInvalidError(f"{label} could not be resolved: {value}") from exc
    return Path(os.path.relpath(candidate, dependency_base)).as_posix()


def _detect_dependency(
    source_root: Path,
    start: Path,
) -> tuple[str | None, tuple[Path, ...]]:
    current = start
    while True:
        requirements = current / "requirements.txt"
        if requirements.is_file():
            return _relative_path(source_root, requirements), (requirements,)

        pyproject = current / "pyproject.toml"
        uv_lock = current / "uv.lock"
        if uv_lock.is_file() and pyproject.is_file():
            return _relative_path(source_root, uv_lock), (uv_lock, pyproject)
        if pyproject.is_file():
            return _relative_path(source_root, pyproject), (pyproject,)
        if current == source_root:
            return None, ()
        current = current.parent


def _load_config(
    server_spec: str | None,
) -> tuple[MCPServerConfig, Path, Path | None]:
    if server_spec is None:
        config_path = MCPServerConfig.find_config()
        if config_path is None:
            raise SourceInvalidError(
                "Provide a server input or add fastmcp.json to the current directory"
            )
        return _load_config_file(config_path)

    if server_spec.endswith(".json"):
        return _load_config_file(Path(server_spec))

    source = FileSystemSource(path=server_spec)
    absolute_source_path = Path(os.path.abspath(Path(source.path).expanduser()))
    source_root = _find_explicit_source_root(absolute_source_path)
    if not absolute_source_path.is_relative_to(source_root):
        absolute_source_path = absolute_source_path.resolve(strict=False)
    source = source.model_copy(update={"path": str(absolute_source_path)})
    return MCPServerConfig(source=source), source_root, None


def _resolve_entrypoint(
    source_root: Path,
    source_path: Path,
    object_name: str | None,
) -> str:
    relative_path = _relative_path(source_root, source_path)
    if (
        len(relative_path) > 128
        or not _ENTRYPOINT_PATH_PATTERN.fullmatch(relative_path)
        or not relative_path.endswith(".py")
        or Path(relative_path).name == ".py"
    ):
        raise SourceInvalidError(
            "The deployment entrypoint must be a Python file with a Horizon-compatible path"
        )
    if object_name is not None and not _ENTRYPOINT_IDENTIFIER_PATTERN.fullmatch(
        object_name
    ):
        raise SourceInvalidError(
            "The deployment entrypoint object must be a Python identifier"
        )

    entrypoint = (
        f"{relative_path}:{object_name}" if object_name is not None else relative_path
    )
    if len(entrypoint) > 128:
        raise SourceInvalidError(
            "The deployment entrypoint is longer than 128 characters"
        )
    return entrypoint


def _find_explicit_source_root(source_path: Path) -> Path:
    source_directory = source_path.parent
    if project_root := _find_project_root(source_directory):
        return project_root

    resolved_directory = source_path.resolve(strict=False).parent
    return _find_project_root(resolved_directory) or resolved_directory


def _find_project_root(start: Path) -> Path | None:
    filesystem_root = Path(start.anchor)
    home_directory = Path.home().resolve()
    for directory in (start, *start.parents):
        if directory != start and directory in {filesystem_root, home_directory}:
            break
        if any(os.path.lexists(directory / marker) for marker in _PROJECT_ROOT_MARKERS):
            return directory.resolve()
    return None


def _load_config_file(config_path: Path) -> tuple[MCPServerConfig, Path, Path]:
    try:
        resolved_path = config_path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceInvalidError(
            f"The FastMCP configuration does not exist: {config_path}"
        ) from exc
    if not resolved_path.is_file():
        raise SourceInvalidError(
            f"The FastMCP configuration is not a file: {config_path}"
        )
    try:
        config = MCPServerConfig.from_file(resolved_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise SourceInvalidError(
            f"The FastMCP configuration is invalid: {config_path}"
        ) from exc
    return config, resolved_path.parent, resolved_path


def _resolve_path(
    source_root: Path,
    value: str | Path,
    *,
    label: str,
    file: bool = False,
    directory: bool = False,
) -> Path:
    requested = Path(value).expanduser()
    candidate = Path(
        os.path.abspath(
            requested if requested.is_absolute() else source_root / requested
        )
    )
    try:
        candidate.relative_to(source_root)
    except ValueError as exc:
        raise SourceInvalidError(f"{label} leaves the source root: {value}") from exc
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceInvalidError(f"{label} does not exist: {value}") from exc
    try:
        resolved.relative_to(source_root)
    except ValueError as exc:
        raise SourceInvalidError(f"{label} leaves the source root: {value}") from exc
    if file and not candidate.is_file():
        raise SourceInvalidError(f"{label} is not a file: {value}")
    if directory and not candidate.is_dir():
        raise SourceInvalidError(f"{label} is not a directory: {value}")
    return candidate


def _path_from_base(base: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _relative_path(source_root: Path, path: Path) -> str:
    return path.relative_to(source_root).as_posix() or "."


def _resolve_archive_path(source_root: Path, archive_path: Path) -> Path:
    requested = Path(archive_path).expanduser()
    if os.path.lexists(requested):
        raise SourceInvalidError("The source archive path already exists")
    try:
        resolved = requested.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SourceInvalidError(
            "The source archive path could not be resolved"
        ) from exc
    try:
        resolved.relative_to(source_root)
    except ValueError:
        return resolved
    raise SourceInvalidError("The source archive must be outside the source root")
