"""Byte-preserving resource reads for strict filesystem skills."""

from __future__ import annotations

import mimetypes
from contextlib import suppress
from pathlib import Path

from mcp.shared.path_security import PathEscapeError, safe_join

from fastmcp.resources.base import ResourceContent, ResourceResult


def _has_symlink(root: Path, relative_path: str) -> bool:
    lexical_path = root / relative_path
    return root.is_symlink() or any(
        ancestor.is_symlink()
        for ancestor in (lexical_path, *lexical_path.parents)
        if ancestor == root or root in ancestor.parents
    )


def is_skill_file(root: Path, relative_path: str) -> bool:
    """Whether `read_file` would serve this path."""
    try:
        path = safe_join(root, relative_path)
    except (PathEscapeError, OSError, RuntimeError):
        return False
    return not _has_symlink(root, relative_path) and path.is_file()


def read_file(root: Path, relative_path: str) -> ResourceResult:
    path = safe_join(root, relative_path)
    if _has_symlink(root, relative_path):
        raise ValueError("skill files must not be symlinks")
    if not path.is_file():
        raise ValueError("skill resource must be a regular file")
    content = path.read_bytes()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    value: str | bytes = content
    if mime_type.startswith("text/"):
        with suppress(UnicodeDecodeError):
            value = content.decode("utf-8")
    return ResourceResult([ResourceContent(value, mime_type=mime_type)])
