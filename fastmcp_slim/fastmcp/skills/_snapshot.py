"""Strict `SKILL.md` frontmatter parsing and skill folder scanning."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode

from fastmcp.skills.models import SkillFrontmatter
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


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
    try:
        value = yaml.load(match.group(1), Loader=_FrontmatterLoader)  # noqa: S506 - SafeLoader subclass rejects duplicate keys
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("frontmatter must be a mapping")
    _validate_json(value, set())
    frontmatter = SkillFrontmatter.model_validate(value)
    if any(not isinstance(item, str) for item in (frontmatter.metadata or {}).values()):
        logger.info(
            "Skill %r has non-string metadata values; the Agent Skills spec "
            "expects strings, so they are served as written",
            frontmatter.name,
        )
    if isinstance(frontmatter.allowed_tools, list):
        logger.info(
            "Skill %r lists allowed-tools as a list; the Agent Skills spec "
            "expects a space-separated string, so it is served as written",
            frontmatter.name,
        )
    return frontmatter


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
