"""Validate local paths for Horizon deployment source."""

from __future__ import annotations

import os
from pathlib import Path

_EXCLUDED_DIRECTORY_NAMES = {
    ".fastmcp",
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "env",
    "venv",
    "virtualenv",
}
_EXCLUDED_FILE_NAMES = {".DS_Store"}
_EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo"}


class SourceInvalidError(ValueError):
    """The local deployment source is invalid or unsafe."""


def resolve_path(
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
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceInvalidError(f"{label} does not exist: {value}") from exc
    try:
        resolved.relative_to(source_root)
    except ValueError:
        raise SourceInvalidError(f"{label} leaves the source root: {value}") from None
    if file and not candidate.is_file():
        raise SourceInvalidError(f"{label} is not a file: {value}")
    if directory and not candidate.is_dir():
        raise SourceInvalidError(f"{label} is not a directory: {value}")
    return candidate


def require_included_path(source_root: Path, path: Path, *, label: str) -> None:
    try:
        resolved = path.resolve(strict=False)
        relative_paths = (
            path.relative_to(source_root),
            resolved.relative_to(source_root),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SourceInvalidError(f"{label} leaves the source root: {path}") from exc
    for relative in relative_paths:
        if is_fixed_exclusion(relative):
            raise SourceInvalidError(f"{label} is excluded from deployment: {relative}")


def relative_path(source_root: Path, path: Path) -> str:
    relative = path.relative_to(source_root).as_posix()
    return relative or "."


def is_fixed_exclusion(relative: Path) -> bool:
    parts = relative.parts
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    name = relative.name
    return (
        name in _EXCLUDED_DIRECTORY_NAMES
        or name in _EXCLUDED_FILE_NAMES
        or name.endswith("fastmcp.json")
        or name == ".env"
        or name.startswith(".env.")
        or relative.suffix in _EXCLUDED_FILE_SUFFIXES
    )


def validate_symlink(source_root: Path, path: Path) -> Path:
    try:
        target = path.resolve(strict=True)
        target.relative_to(source_root)
    except (OSError, RuntimeError, ValueError):
        relative = path.relative_to(source_root)
        raise SourceInvalidError(
            f"Symbolic link leaves the source root: {relative}"
        ) from None
    return target
