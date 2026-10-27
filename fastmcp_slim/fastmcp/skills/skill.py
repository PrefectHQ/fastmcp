"""Storage-neutral agent skills."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

from pydantic import JsonValue

from fastmcp.skills._constants import MAX_SKILL_BYTES, MAX_SKILL_FILES
from fastmcp.skills.models import MCPSkill, SkillFrontmatter, SkillResource


def _validate_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("Skill file paths must be non-empty strings")
    if (
        path.startswith("/")
        or PureWindowsPath(path).drive
        or "\\" in path
        or "\x00" in path
    ):
        raise ValueError(f"Skill file path must be relative POSIX: {path!r}")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Skill file path contains an invalid segment: {path!r}")
    return path


def _render_skill_md(frontmatter: Mapping[str, JsonValue], instructions: str) -> bytes:
    import yaml

    header = yaml.safe_dump(dict(frontmatter), sort_keys=False, allow_unicode=True)
    return f"---\n{header}---\n{instructions}".encode()


class Skill:
    """An agent skill: a `SKILL.md` plus supporting files, independent of storage.

    A `Skill` holds exact file bytes. It doesn't know where it came from or which
    URI it is served under; a provider decides that. Author one in Python with the
    keyword constructor, or snapshot one from a directory, package, or mapping of
    files.

    Example:
        ```python
        from fastmcp.skills import Skill

        skill = Skill(
            name="release-notes",
            description="Write release notes from merged pull requests.",
            instructions="# Release notes\\n\\nGroup changes by theme...",
            files={"template.md": "## Highlights\\n"},
        )
        ```
    """

    __slots__ = ("_files", "_frontmatter")

    _files: Mapping[str, bytes]
    _frontmatter: SkillFrontmatter

    def __init__(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        files: Mapping[str, str | bytes] | None = None,
        frontmatter: Mapping[str, JsonValue] | None = None,
    ) -> None:
        extra = dict(frontmatter or {})
        for key in ("name", "description"):
            if key in extra:
                raise ValueError(f"Pass {key!r} as a Skill argument, not frontmatter")
        header = SkillFrontmatter.model_validate(
            {"name": name, "description": description, **extra}
        )
        contents: dict[str, str | bytes] = {
            "SKILL.md": _render_skill_md(header.root, instructions)
        }
        for path, content in (files or {}).items():
            if path == "SKILL.md":
                raise ValueError("Pass SKILL.md content as `instructions`")
            contents[path] = content
        self._initialize(contents)

    def _initialize(self, files: Mapping[str, str | bytes]) -> None:
        from fastmcp.skills._snapshot import parse_frontmatter

        normalized: dict[str, bytes] = {}
        for path, content in files.items():
            clean = _validate_relative_path(path)
            normalized[clean] = (
                content.encode("utf-8") if isinstance(content, str) else bytes(content)
            )
        if "SKILL.md" not in normalized:
            raise ValueError("A skill must contain SKILL.md")
        if len(normalized) > MAX_SKILL_FILES:
            raise ValueError(f"A skill may contain at most {MAX_SKILL_FILES} files")
        if sum(map(len, normalized.values())) > MAX_SKILL_BYTES:
            raise ValueError(f"A skill may contain at most {MAX_SKILL_BYTES} bytes")
        for path in normalized:
            parts = path.split("/")
            for index in range(1, len(parts)):
                if "/".join(parts[:index]) in normalized:
                    raise ValueError(
                        f"Skill file is also used as a directory: {path!r}"
                    )
        object.__setattr__(
            self, "_frontmatter", parse_frontmatter(normalized["SKILL.md"])
        )
        object.__setattr__(
            self, "_files", MappingProxyType(dict(sorted(normalized.items())))
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Skill is immutable")

    @classmethod
    def from_files(cls, files: Mapping[str, str | bytes]) -> Skill:
        """Create a skill from relative paths mapped to text or bytes.

        `files` must include `SKILL.md` with valid frontmatter.
        """
        skill = object.__new__(cls)
        skill._initialize(files)
        return skill

    @classmethod
    def from_directory(cls, path: str | os.PathLike[str]) -> Skill:
        """Read a skill folder once and return an immutable snapshot.

        The folder name must match the `name` in its `SKILL.md`. Symlinks and
        special files are rejected, so a snapshot never reaches outside the folder.
        """
        from fastmcp.skills._snapshot import scan_files

        root = Path(path).absolute()
        files = {
            file.relative_to(root).as_posix(): file.read_bytes()
            for file in scan_files(root)
        }
        skill = cls.from_files(files)
        if skill.name != root.name:
            raise ValueError(
                f"Skill folder {root.name!r} must match its SKILL.md name {skill.name!r}"
            )
        return skill

    @classmethod
    def from_package(cls, package: str, path: str) -> Skill:
        """Load a skill folder shipped as package data, such as `("my_pkg", "skills/review")`."""
        from importlib.resources import files as package_files

        clean = _validate_relative_path(path)
        root = package_files(package).joinpath(*clean.split("/"))
        if not root.is_dir():
            raise ValueError(f"Skill package path is not a directory: {package}:{path}")
        contents: dict[str, bytes] = {}

        def visit(directory: Any, prefix: str) -> None:
            for entry in directory.iterdir():
                relative = f"{prefix}{entry.name}"
                if entry.is_dir():
                    visit(entry, f"{relative}/")
                elif entry.is_file():
                    contents[relative] = entry.read_bytes()

        visit(root, "")
        skill = cls.from_files(contents)
        if skill.name != root.name:
            raise ValueError(
                f"Skill folder {root.name!r} must match its SKILL.md name {skill.name!r}"
            )
        return skill

    def to_directory(
        self, path: str | os.PathLike[str], *, overwrite: bool = False
    ) -> Path:
        """Write the skill's files into `path`, a folder named after the skill.

        The folder name must match the skill's `name`, so `from_directory` can
        read it back. An existing folder raises `FileExistsError` unless
        `overwrite` is set, in which case it is replaced: files that aren't
        part of the skill are removed.
        """
        root = Path(path)
        if root.name != self.name:
            raise ValueError(
                f"Skill folder {root.name!r} must match the skill's name {self.name!r}"
            )
        if root.is_symlink():
            raise ValueError(f"Skill folder is a symlink: {root}")
        if root.exists():
            if not overwrite:
                raise FileExistsError(
                    f"Skill directory already exists: {root}. "
                    "Use overwrite=True to replace."
                )
            if not root.is_dir():
                raise ValueError(f"Skill folder is not a directory: {root}")
            shutil.rmtree(root)
        for relative, content in self._files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return root

    @property
    def name(self) -> str:
        return self._frontmatter.name

    @property
    def description(self) -> str:
        return self._frontmatter.description

    @property
    def frontmatter(self) -> SkillFrontmatter:
        """A copy of the parsed frontmatter; changing it does not change the skill."""
        return self._frontmatter.model_copy(deep=True)

    @property
    def files(self) -> Mapping[str, bytes]:
        """Every file in the skill, keyed by relative path, including `SKILL.md`."""
        return self._files

    def to_mcp_skill(self, path: str | None = None) -> MCPSkill:
        """Build the Skills extension entry for this skill served at `skill://{path}/`."""
        prefix = f"skill://{quote(path or self.name, safe='/')}/"
        resources = [
            SkillResource(
                uri=prefix + quote(relative, safe="/"),
                digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
                size=len(content),
            )
            for relative, content in self._files.items()
        ]
        return MCPSkill(
            uri=f"{prefix}SKILL.md", frontmatter=self.frontmatter, resources=resources
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Skill) and self._files == other._files

    def __hash__(self) -> int:
        return hash(tuple(self._files.items()))

    def __repr__(self) -> str:
        return f"Skill(name={self.name!r}, files={len(self._files)})"
