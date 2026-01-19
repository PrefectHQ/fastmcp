"""Shared utilities and data structures for skills providers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Markdown content potentially starting with ---

    Returns:
        Tuple of (frontmatter dict, remaining content)
    """
    if not content.startswith("---"):
        return {}, content

    # Find the closing ---
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}, content

    frontmatter_text = content[3 : 3 + end_match.start()]
    remaining = content[3 + end_match.end() :]

    # Parse YAML (simple key: value parsing, no complex types)
    frontmatter: dict[str, Any] = {}
    for line in frontmatter_text.strip().split("\n"):
        if ":" in line:
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

    return frontmatter, remaining


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
    # Sort for deterministic ordering across platforms
    for file_path in sorted(skill_dir.rglob("*")):
        if file_path.is_file():
            rel_path = file_path.relative_to(skill_dir)
            files.append(
                SkillFileInfo(
                    # Use POSIX paths for cross-platform URI consistency
                    path=rel_path.as_posix(),
                    size=file_path.stat().st_size,
                    hash=compute_file_hash(file_path),
                )
            )
    return files
