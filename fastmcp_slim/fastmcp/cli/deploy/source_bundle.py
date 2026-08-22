"""Create deterministic archives for Horizon deployment source."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pathspec

from fastmcp.cli.deploy.source import GENERATED_REQUIREMENTS_PATH, DeploySource
from fastmcp.cli.deploy.source_paths import (
    SourceInvalidError,
    is_fixed_exclusion,
    require_included_path,
    validate_symlink,
)

MAX_SOURCE_UPLOAD_BYTES = 250 * 1024 * 1024


class ArchiveTooLargeError(ValueError):
    """The compressed source archive exceeds Horizon's upload limit."""


class _CompressedSizeWriter:
    def __init__(self, file: io.BufferedWriter) -> None:
        self._file = file

    def write(self, data: bytes) -> int:
        if self._file.tell() + len(data) > MAX_SOURCE_UPLOAD_BYTES:
            raise ArchiveTooLargeError(
                "The compressed source archive exceeds the 250 MB Horizon limit"
            )
        return self._file.write(data)

    def flush(self) -> None:
        self._file.flush()


@dataclass(frozen=True)
class SourceBundle:
    """An immutable archive ready for source upload."""

    archive_path: Path
    size_bytes: int
    checksum_sha256: str
    entrypoint: str
    dependency_path: str | None


def create_source_bundle(
    source: DeploySource,
    archive_path: Path,
) -> SourceBundle:
    """Create a deterministic source archive and return its upload metadata."""
    source_root = source.source_root.resolve(strict=True)
    try:
        archive_path = Path(archive_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SourceInvalidError(
            "The source archive path could not be resolved"
        ) from exc
    try:
        archive_path.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise SourceInvalidError("The source archive must be outside the source root")
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    entries = _archive_entries(
        source_root,
        archive_path,
        required_paths=source.required_paths,
        excluded_paths=source.excluded_paths,
    )
    if source.generated_requirements is not None:
        generated_path = source_root / GENERATED_REQUIREMENTS_PATH
        if os.path.lexists(generated_path):
            raise SourceInvalidError(
                f"The generated dependency path is reserved: {GENERATED_REQUIREMENTS_PATH}"
            )
        entries.append(
            (GENERATED_REQUIREMENTS_PATH, source.generated_requirements.encode())
        )
    entries.sort(key=lambda entry: entry[0])

    try:
        with (
            archive_path.open("wb") as raw_file,
            gzip.GzipFile(
                filename="",
                fileobj=_CompressedSizeWriter(raw_file),
                mode="wb",
                mtime=0,
            ) as gzip_file,
            tarfile.open(
                fileobj=gzip_file,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive,
        ):
            for name, entry_source in entries:
                _add_archive_entry(archive, name, entry_source, source_root)
    except (ArchiveTooLargeError, SourceInvalidError):
        archive_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        archive_path.unlink(missing_ok=True)
        raise SourceInvalidError(
            f"The source archive could not be created: {exc}"
        ) from exc

    size_bytes = archive_path.stat().st_size
    return SourceBundle(
        archive_path=archive_path,
        size_bytes=size_bytes,
        checksum_sha256=_sha256(archive_path),
        entrypoint=source.entrypoint,
        dependency_path=source.dependency_path,
    )


def _archive_entries(
    source_root: Path,
    archive_path: Path,
    *,
    required_paths: tuple[Path, ...],
    excluded_paths: tuple[Path, ...],
) -> list[tuple[str, Path | bytes]]:
    required = _expand_required_paths(source_root, required_paths)
    required_directories = {path for path in required if path.is_dir()}
    excluded = _expand_excluded_paths(excluded_paths)
    if any(
        path.absolute() in excluded or path.resolve(strict=False) in excluded
        for path in required
    ):
        raise SourceInvalidError(
            "The selected config file cannot be a required source path"
        )
    entries: dict[str, Path] = {}
    rules_by_directory: dict[
        Path,
        tuple[tuple[Path, pathspec.GitIgnoreSpec], ...],
    ] = {source_root: ()}

    def raise_walk_error(error: OSError) -> None:
        raise SourceInvalidError(
            f"The source directory could not be read: {error.filename or source_root}"
        ) from error

    for current_root, directory_names, file_names in os.walk(
        source_root,
        topdown=True,
        onerror=raise_walk_error,
        followlinks=False,
    ):
        current = Path(current_root)
        rules = rules_by_directory[current]
        local_rules = _read_gitignore(source_root, current)
        if local_rules is not None:
            rules = (*rules, (current, local_rules))

        traversable_directories: list[str] = []
        for name in sorted(directory_names):
            path = current / name
            relative = path.relative_to(source_root)
            if is_fixed_exclusion(relative):
                continue
            if _is_gitignored(path, rules, directory=True):
                if any(
                    required.is_relative_to(path) for required in required_directories
                ):
                    raise SourceInvalidError(
                        f"Required source directory is ignored: {relative}"
                    )
                continue
            if path.is_symlink():
                validate_symlink(source_root, path)
                entries[relative.as_posix()] = path
            else:
                traversable_directories.append(name)
                rules_by_directory[path] = rules
        directory_names[:] = traversable_directories

        for name in sorted(file_names):
            path = current / name
            if path.absolute() == archive_path or path.absolute() in excluded:
                continue
            relative = path.relative_to(source_root)
            if is_fixed_exclusion(relative):
                continue
            if path.absolute() not in required and _is_gitignored(path, rules):
                continue
            if path.is_symlink():
                validate_symlink(source_root, path)
            elif not path.is_file():
                continue
            entries[relative.as_posix()] = path

    for path in required:
        relative = path.relative_to(source_root)
        if path.is_dir():
            continue
        if not path.is_file():
            raise SourceInvalidError(f"Required source file is missing: {relative}")
        entries[relative.as_posix()] = path

    return list(entries.items())


def _expand_excluded_paths(excluded_paths: tuple[Path, ...]) -> set[Path]:
    excluded: set[Path] = set()
    for path in excluded_paths:
        excluded.add(path.absolute())
        try:
            excluded.add(path.resolve(strict=True))
        except (OSError, RuntimeError) as exc:
            raise SourceInvalidError(
                f"The excluded source path could not be resolved: {path}"
            ) from exc
    return excluded


def _expand_required_paths(
    source_root: Path,
    required_paths: tuple[Path, ...],
) -> set[Path]:
    pending = list(required_paths)
    required: set[Path] = set()
    while pending:
        path = pending.pop().absolute()
        if path in required:
            continue
        require_included_path(source_root, path, label="Required source path")
        required.add(path)
        if path.is_symlink():
            pending.append(validate_symlink(source_root, path))
    return required


def _read_gitignore(
    source_root: Path,
    directory: Path,
) -> pathspec.GitIgnoreSpec | None:
    gitignore = directory / ".gitignore"
    if not gitignore.is_file():
        return None
    if gitignore.is_symlink():
        validate_symlink(source_root, gitignore)
    try:
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        relative = gitignore.relative_to(source_root)
        raise SourceInvalidError(
            f"The .gitignore file could not be read: {relative}"
        ) from exc
    return pathspec.GitIgnoreSpec.from_lines(lines)


def _is_gitignored(
    path: Path,
    rules: tuple[tuple[Path, pathspec.GitIgnoreSpec], ...],
    *,
    directory: bool = False,
) -> bool:
    ignored = False
    for base, spec in rules:
        relative = path.relative_to(base).as_posix()
        result = spec.check_file(relative + "/" if directory else relative)
        if result.include is not None:
            ignored = result.include
    return ignored


def _add_archive_entry(
    archive: tarfile.TarFile,
    name: str,
    entry_source: Path | bytes,
    source_root: Path,
) -> None:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}

    if isinstance(entry_source, bytes):
        info.mode = 0o644
        info.size = len(entry_source)
        archive.addfile(info, io.BytesIO(entry_source))
        return

    if entry_source.is_symlink():
        target = validate_symlink(source_root, entry_source)
        info.type = tarfile.SYMTYPE
        info.mode = 0o777
        info.linkname = os.path.relpath(target, entry_source.parent).replace(
            os.sep, "/"
        )
        archive.addfile(info)
        return

    file_stat = entry_source.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise SourceInvalidError(f"Source entry is not a regular file: {name}")
    info.mode = 0o755 if file_stat.st_mode & 0o111 else 0o644
    info.size = file_stat.st_size
    with entry_source.open("rb") as source_file:
        archive.addfile(info, source_file)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as archive_file:
        for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
