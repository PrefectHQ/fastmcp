from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from fastmcp.skills._snapshot import parse_frontmatter, snapshot


def test_snapshot_preserves_raw_bytes_and_frontmatter(tmp_path: Path):
    root = tmp_path / "review"
    root.mkdir()
    main = b"---\r\nname: review\r\ndescription: Review code\r\ncustom:\r\n  enabled: true\r\n  count: 2\r\n  values: [null, 1.5, text]\r\n---\r\n# Review\r\n"
    (root / "SKILL.md").write_bytes(main)
    binary = b"\x00\xff\r\n"
    (root / "data.bin").write_bytes(binary)
    entry = snapshot(root, "team/review").entry
    assert entry.frontmatter.root["custom"] == {
        "enabled": True,
        "count": 2,
        "values": [None, 1.5, "text"],
    }
    assert isinstance(entry.resources, list)
    assert sorted((r.uri, r.size, r.digest) for r in entry.resources) == [
        (
            f"skill://team/review/{name}",
            len(content),
            f"sha256:{hashlib.sha256(content).hexdigest()}",
        )
        for name, content in [("SKILL.md", main), ("data.bin", binary)]
    ]


@pytest.mark.parametrize(
    "extra",
    [
        "name: review",
        "custom: {a: 1, a: 2}",
        "custom: {1: value}",
        "custom: 2026-10-12",
        "custom: .nan",
        "custom: .inf",
        "custom: !!binary SGVsbG8=",
        "custom: !!set {a: null}",
        "custom: &cycle [*cycle]",
        "metadata: {count: 2}",
    ],
)
def test_rejects_lossy_or_invalid_frontmatter(extra: str):
    data = f"---\nname: review\ndescription: Review code\n{extra}\n---\n".encode()
    with pytest.raises(ValueError):
        parse_frontmatter(data)


def test_snapshot_rejects_name_mismatch(tmp_path: Path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\n"
    )
    with pytest.raises(ValueError, match="must equal"):
        snapshot(tmp_path, "other")


@pytest.mark.parametrize("directory", [False, True])
def test_snapshot_rejects_symlinks(tmp_path: Path, directory: bool):
    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: review\ndescription: Review code\n---\n")
    target = tmp_path / "outside"
    if directory:
        target.mkdir()
    else:
        target.write_text("outside")
    (root / "linked").symlink_to(target, target_is_directory=directory)
    with pytest.raises(ValueError, match="symlink"):
        snapshot(root, "review")


@pytest.mark.skipif(
    sys.platform == "win32", reason="Colon paths are NTFS streams on Windows"
)
def test_rejects_paths_not_supported_by_resource_reader(tmp_path: Path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\n"
    )
    (tmp_path / "C:notes.md").write_text("unreadable")
    with pytest.raises(ValueError):
        snapshot(tmp_path, "review")
