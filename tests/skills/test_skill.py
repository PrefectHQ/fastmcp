from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from fastmcp.skills import Skill
from fastmcp.skills._constants import MAX_SKILL_BYTES, MAX_SKILL_FILES


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
        ({"files": {"C:/escape.md": "x"}}, "relative POSIX"),
        ({"files": {"C:escape.md": "x"}}, "relative POSIX"),
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


@pytest.mark.parametrize("limit", ["count", "size"])
def test_from_directory_checks_limits_before_reading_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
):
    folder = write_skill(tmp_path)
    if limit == "count":
        for index in range(MAX_SKILL_FILES):
            (folder / f"extra-{index}.txt").touch()
    else:
        with (folder / "large.bin").open("wb") as file:
            file.truncate(MAX_SKILL_BYTES + 1)
    reads = []
    read_bytes = Path.read_bytes

    def observed_read(path: Path) -> bytes:
        reads.append(path)
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", observed_read)
    with pytest.raises(ValueError, match="at most"):
        Skill.from_directory(folder)
    assert reads == []


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


def test_to_directory_round_trips_through_from_directory(tmp_path: Path):
    skill = Skill(
        name="review",
        description="Review changes",
        instructions="# Review\r\n",
        files={"notes.md": "line one\r\n", "refs/logo.bin": b"\x00\xff\r\n"},
    )
    folder = skill.to_directory(tmp_path / "review")
    assert folder == tmp_path / "review"
    assert (folder / "refs" / "logo.bin").read_bytes() == b"\x00\xff\r\n"
    assert Skill.from_directory(folder) == skill


def test_to_directory_requires_a_matching_folder_name(tmp_path: Path):
    skill = Skill(name="review", description="Review", instructions="# Review\n")
    with pytest.raises(ValueError, match="must match the skill's name"):
        skill.to_directory(tmp_path / "renamed")
    assert not (tmp_path / "renamed").exists()


def test_to_directory_overwrite_replaces_the_folder(tmp_path: Path):
    skill = Skill(name="review", description="Review", instructions="# Review\n")
    folder = tmp_path / "review"
    folder.mkdir()
    (folder / "stale.md").write_text("old")

    with pytest.raises(FileExistsError, match="overwrite=True"):
        skill.to_directory(folder)
    assert (folder / "stale.md").exists()

    assert skill.to_directory(folder, overwrite=True) == folder
    assert sorted(p.name for p in folder.iterdir()) == ["SKILL.md"]


@pytest.mark.parametrize("existing", [False, True])
def test_failed_export_leaves_destination_untouched(tmp_path: Path, existing: bool):
    folder = tmp_path / "review"
    if existing:
        folder.mkdir()
        (folder / "old.txt").write_bytes(b"original")
    skill = Skill(
        name="review",
        description="Review",
        instructions="# Review\n",
        files={"x" * 260: b"cannot be written"},
    )

    with pytest.raises(OSError):
        skill.to_directory(folder, overwrite=existing)

    if existing:
        assert {p.name: p.read_bytes() for p in folder.iterdir()} == {
            "old.txt": b"original"
        }
    else:
        assert not folder.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == (
        ["review"] if existing else []
    )


@pytest.mark.parametrize("restore_fails", [False, True])
def test_export_retains_old_files_if_installation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_fails: bool
):
    folder = tmp_path / "review"
    folder.mkdir()
    (folder / "old.txt").write_bytes(b"original")
    skill = Skill(name="review", description="Review", instructions="# Review\n")
    rename = Path.rename
    attempts = 0

    def fail_install(source: Path, target: str | os.PathLike[str]) -> Path:
        nonlocal attempts
        if Path(target) == folder:
            attempts += 1
            if attempts == 1 or restore_fails:
                raise OSError("Destination unavailable")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", fail_install)
    with pytest.raises(OSError) as error:
        skill.to_directory(folder, overwrite=True)

    originals = list(tmp_path.rglob("old.txt"))
    assert len(originals) == 1
    assert originals[0].read_bytes() == b"original"
    if restore_fails:
        assert str(originals[0].parent) in str(error.value)
    else:
        assert originals[0] == folder / "old.txt"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["review"]


def test_to_directory_refuses_a_symlink(tmp_path: Path):
    skill = Skill(name="review", description="Review", instructions="# Review\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "review"
    link.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        skill.to_directory(link, overwrite=True)
    assert elsewhere.exists()
