"""Shared utilities and data structures for skills providers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SkillFileInfo:
    """Information about a file within a skill."""

    path: str  # Relative path within skill directory
    size: int
    hash: str  # sha256 hash


@dataclass
class SkillInfo:
    """Parsed information about a skill."""

    name: str  # Directory name (canonical identifier)
    description: str  # From frontmatter or first line
    path: Path  # Absolute path to skill directory
    main_file: str  # Name of main file (e.g., "SKILL.md")
    files: list[SkillFileInfo] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)


def _parse_frontmatter_line_based(frontmatter_text: str) -> dict[str, Any]:
    """Legacy line-based frontmatter parser (key: value only).

    Used as a fallback when full YAML parsing fails so plain scalar keys
    (including values that contain `: `) are not discarded entirely.
    Multiline YAML block scalars (`|` / `>`) are not supported here —
    those require a successful `yaml.safe_load`.
    """
    frontmatter: dict[str, Any] = {}
    for line in frontmatter_text.strip().split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Handle quoted strings
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        # Handle lists [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = value[1:-1].split(",")
            value = [item.strip().strip("\"'") for item in items if item.strip()]

        frontmatter[key] = value
    return frontmatter


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Uses `yaml.BaseLoader` so multiline block scalars (`|` / `>`) work while
    every scalar value is returned as a plain string. `SkillInfo.frontmatter`
    is a public mapping; letting a full loader (e.g. `safe_load`) apply
    implicit scalar typing would silently turn `version: 1.10` into the float
    `1.1`, `enabled: yes` into `True`, or a date-like value into
    `datetime.date` for every skill author, which is a backwards-incompatible
    surprise rather than an internal detail. `BaseLoader` has no such
    constructors, so values round-trip as written. If YAML parsing fails
    (invalid YAML, recursion limit, etc.), falls back to a line-based
    key:value parser so plain frontmatter is not discarded.

    Args:
        content: Markdown content potentially starting with ---

    Returns:
        Tuple of (frontmatter dict, remaining content). If no frontmatter
        block is found at all (no opening/closing `---` delimiters),
        returns `({}, content)` unchanged. If a delimited block is found
        but is empty or does not parse to a YAML mapping (and the line-based
        fallback also yields nothing), returns `({}, remaining)` with the
        delimited block stripped.
    """
    content = content.removeprefix("\ufeff")

    if not content.startswith("---"):
        return {}, content

    # Find the closing ---
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}, content

    frontmatter_text = content[3 : 3 + end_match.start()]
    remaining = content[3 + end_match.end() :]

    try:
        parsed = yaml.load(frontmatter_text, Loader=yaml.BaseLoader)
    except (yaml.YAMLError, RecursionError):
        # Prefer partial recovery over discarding every key (issue #4416 review).
        return _parse_frontmatter_line_based(frontmatter_text), remaining

    if not isinstance(parsed, dict):
        return {}, remaining

    return parsed, remaining


def compute_file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"


def scan_skill_files(skill_dir: Path) -> list[SkillFileInfo]:
    """Scan a skill directory for all files."""
    files = []
    resolved_skill_dir = skill_dir.resolve()

    # Sort for deterministic ordering across platforms
    for file_path in sorted(skill_dir.rglob("*")):
        if file_path.is_file():
            resolved_file_path = file_path.resolve()
            if not resolved_file_path.is_relative_to(resolved_skill_dir):
                continue

            rel_path = file_path.relative_to(skill_dir)
            files.append(
                SkillFileInfo(
                    # Use POSIX paths for cross-platform URI consistency
                    path=rel_path.as_posix(),
                    size=resolved_file_path.stat().st_size,
                    hash=compute_file_hash(resolved_file_path),
                )
            )
    return files
