"""Strict filesystem snapshots for experimental Skills publication."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from mcp.shared.path_security import safe_join
from yaml.nodes import MappingNode

from fastmcp.skills.models import MCPSkill, SkillFrontmatter, SkillResource

# SEP-2640 per-skill limits; servers should not serve skills that exceed them.
MAX_SKILL_RESOURCES = 512
MAX_SKILL_BYTES = 16 * 1024 * 1024


class _FrontmatterLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        # Aliases let a few bytes expand into an arbitrarily large document.
        if self.check_event(yaml.AliasEvent):
            raise ValueError("frontmatter must not use YAML aliases")
        return super().compose_node(parent, index)

    def construct_mapping(
        self, node: MappingNode, deep: bool = False
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ValueError("frontmatter mapping keys must be strings")
            if key in result:
                raise ValueError(f"duplicate frontmatter key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _validate_json(value: Any, ancestors: set[int]) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list):
        raise ValueError("frontmatter must contain only finite JSON values")
    if id(value) in ancestors:
        raise ValueError("frontmatter must not contain recursive aliases")
    ancestors.add(id(value))
    try:
        children = value.values() if isinstance(value, dict) else value
        for child in children:
            _validate_json(child, ancestors)
    finally:
        ancestors.remove(id(value))


def parse_frontmatter(content: bytes) -> SkillFrontmatter:
    text = content.decode("utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if match is None:
        raise ValueError("SKILL.md must begin with YAML frontmatter")
    value = yaml.load(match.group(1), Loader=_FrontmatterLoader)  # noqa: S506 - SafeLoader subclass rejects duplicate keys
    if not isinstance(value, dict):
        raise ValueError("frontmatter must be a mapping")
    _validate_json(value, set())
    return SkillFrontmatter.model_validate(value)


def scan_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("skill root must be a directory, not a symlink")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"skill contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ValueError(
                f"skill contains a non-regular file: {path.relative_to(root)}"
            )
    return files


@dataclass(frozen=True)
class Snapshot:
    entry: MCPSkill
    files: tuple[tuple[str, Path], ...]


def snapshot(root: Path, skill_path: str) -> Snapshot:
    paths = scan_files(root)
    main = root / "SKILL.md"
    if main not in paths:
        raise ValueError("skill must contain SKILL.md")
    if len(paths) > MAX_SKILL_RESOURCES:
        raise ValueError(f"skill has more than {MAX_SKILL_RESOURCES} files")
    if sum(path.stat().st_size for path in paths) > MAX_SKILL_BYTES:
        raise ValueError(f"skill is larger than {MAX_SKILL_BYTES} bytes")
    records: list[SkillResource] = []
    files: list[tuple[str, Path]] = []
    frontmatter: SkillFrontmatter | None = None
    for path in paths:
        safe_join(root, path.relative_to(root).as_posix())
        content = path.read_bytes()
        uri = f"skill://{quote(skill_path, safe='/')}/{quote(path.relative_to(root).as_posix(), safe='/')}"
        records.append(
            SkillResource(
                uri=uri,
                digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
                size=len(content),
            )
        )
        files.append((uri, path))
        if path == main:
            frontmatter = parse_frontmatter(content)
    if scan_files(root) != paths:
        raise ValueError("skill files changed while building the manifest")
    if sum(record.size for record in records) > MAX_SKILL_BYTES:
        raise ValueError(f"skill is larger than {MAX_SKILL_BYTES} bytes")
    assert frontmatter is not None
    entry = MCPSkill(
        uri=f"skill://{quote(skill_path, safe='/')}/SKILL.md",
        frontmatter=frontmatter,
        resources=records,
    )
    return Snapshot(entry=entry, files=tuple(files))
