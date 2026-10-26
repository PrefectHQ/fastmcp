from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from fastmcp.skills import Skill


def write_skill(root: Path, name: str = "review") -> Path:
    folder = root / name
    (folder / "refs").mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(
        f"---\r\nname: {name}\r\ndescription: Review changes\r\n---\r\n# Review\r\n".encode()
    )
    (folder / "refs" / "checklist.md").write_bytes(b"- tests\n")
    (folder / "logo.bin").write_bytes(b"\x00\xff")
    return folder


def test_keyword_constructor_renders_skill_md():
    skill = Skill(
        name="release-notes",
        description="Write release notes.",
        instructions="# Release notes\n",
        files={"template.md": "## Highlights\n", "data.bin": b"\x00"},
        frontmatter={"license": "MIT"},
    )

    assert skill.name == "release-notes"
    assert skill.description == "Write release notes."
    assert skill.frontmatter.license == "MIT"
    assert list(skill.files) == ["SKILL.md", "data.bin", "template.md"]
    assert skill.files["SKILL.md"].endswith(b"---\n# Release notes\n")
    assert Skill.from_files(skill.files) == skill


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"name": "Bad Name"}, "name must be"),
        ({"description": ""}, "description must"),
        ({"frontmatter": {"name": "x"}}, "Pass 'name'"),
        ({"files": {"SKILL.md": "x"}}, "instructions"),
        ({"files": {"../escape.md": "x"}}, "invalid segment"),
        ({"files": {"/abs.md": "x"}}, "relative POSIX"),
        ({"files": {"a": "x", "a/b": "y"}}, "also used as a directory"),
    ],
)
def test_constructor_rejects_invalid_skills(kwargs: dict[str, object], message: str):
    arguments: dict[str, object] = {
        "name": "review",
        "description": "Review changes",
        "instructions": "# Review\n",
        **kwargs,
    }
    with pytest.raises(ValueError, match=message):
        Skill(**arguments)  # ty: ignore[invalid-argument-type]


def test_from_files_requires_valid_frontmatter():
    with pytest.raises(ValueError, match="SKILL.md"):
        Skill.from_files({"notes.md": "x"})
    with pytest.raises(ValueError, match="frontmatter"):
        Skill.from_files({"SKILL.md": "# no frontmatter\n"})


def test_from_directory_snapshots_exact_bytes(tmp_path: Path):
    folder = write_skill(tmp_path)
    skill = Skill.from_directory(folder)

    assert skill.files == {
        "SKILL.md": (folder / "SKILL.md").read_bytes(),
        "logo.bin": b"\x00\xff",
        "refs/checklist.md": b"- tests\n",
    }
    (folder / "refs" / "checklist.md").write_bytes(b"changed")
    assert skill.files["refs/checklist.md"] == b"- tests\n"


def test_from_directory_requires_matching_name(tmp_path: Path):
    folder = write_skill(tmp_path)
    renamed = folder.rename(tmp_path / "other")
    with pytest.raises(ValueError, match="must match"):
        Skill.from_directory(renamed)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")
def test_from_directory_rejects_symlinks(tmp_path: Path):
    folder = write_skill(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, folder / "link.txt")
    with pytest.raises(ValueError, match="symlink"):
        Skill.from_directory(folder)


def test_from_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    package = tmp_path / "skillpkg"
    (package / "skills").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    write_skill(package / "skills")
    monkeypatch.syspath_prepend(str(tmp_path))

    skill = Skill.from_package("skillpkg", "skills/review")

    assert skill == Skill.from_directory(package / "skills" / "review")


def test_skill_is_immutable():
    skill = Skill(name="review", description="Review", instructions="")
    with pytest.raises(AttributeError):
        skill.name = "other"  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        skill.files["SKILL.md"] = b""  # ty: ignore[invalid-assignment]


def test_frontmatter_changes_do_not_leak_into_the_skill():
    skill = Skill(name="review", description="Review", instructions="")
    skill.frontmatter.root["description"] = "changed"
    skill.to_mcp_skill().frontmatter.root["description"] = "changed"
    assert skill.description == "Review"
    assert skill.to_mcp_skill().frontmatter.description == "Review"


def test_to_mcp_skill_describes_every_file(tmp_path: Path):
    skill = Skill.from_directory(write_skill(tmp_path))
    entry = skill.to_mcp_skill("team/review")

    assert entry.uri == "skill://team/review/SKILL.md"
    assert entry.frontmatter == skill.frontmatter
    assert isinstance(entry.resources, list)
    records = {record.uri: record for record in entry.resources}
    for path, content in skill.files.items():
        record = records[f"skill://team/review/{path}"]
        assert record.size == len(content)
        assert record.digest == f"sha256:{hashlib.sha256(content).hexdigest()}"


def test_skill_is_exported_from_fastmcp():
    import fastmcp

    assert fastmcp.Skill is Skill
