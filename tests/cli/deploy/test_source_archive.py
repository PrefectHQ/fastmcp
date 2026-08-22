from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path

import pytest

import fastmcp.cli.deploy.source_archive as source_archive_module
import fastmcp.cli.deploy.source_bundle as source_bundle_module
from fastmcp.cli.deploy.source_bundle import (
    ArchiveTooLargeError,
    SourceInvalidError,
    create_source_bundle,
)


def write_server(path: Path, name: str = "mcp") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from fastmcp import FastMCP\n"
        f'{name} = FastMCP("Test")\n'
        f"@{name}.tool\n"
        "def ping() -> str:\n"
        '    return "pong"\n'
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source_root = tmp_path / "project"
    source_root.mkdir()
    monkeypatch.chdir(source_root)
    return source_root


def archive_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        return {member.name for member in archive.getmembers()}


async def test_archive_is_deterministic(project: Path, tmp_path: Path) -> None:
    write_server(project / "server.py")
    executable = project / "start.sh"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    first = await create_source_bundle("server.py", tmp_path / "first.tar.gz")
    second = await create_source_bundle("server.py", tmp_path / "second.tar.gz")

    assert first.checksum_sha256 == second.checksum_sha256
    assert first.archive_path.read_bytes() == second.archive_path.read_bytes()
    with tarfile.open(first.archive_path, "r:gz") as archive:
        server = archive.getmember("server.py")
        script = archive.getmember("start.sh")
        assert (server.uid, server.gid, server.uname, server.gname) == (0, 0, "", "")
        assert server.mtime == 0
        assert server.mode == 0o644
        assert script.mode == (0o644 if os.name == "nt" else 0o755)


async def test_archive_applies_security_and_git_exclusions(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / "keep.txt").write_text("keep")
    (project / "ignored.txt").write_text("ignored")
    (project / ".gitignore").write_text("*.txt\n")
    app = project / "app"
    app.mkdir()
    (app / ".gitignore").write_text("!keep.txt\n")
    (app / "keep.txt").write_text("keep")
    (app / "env").mkdir()
    (app / "env" / "settings.py").write_text("DEBUG = False\n")
    (project / ".env").write_text("TOKEN=secret\n")
    (project / ".env.production").write_text("TOKEN=secret\n")
    (project / ".envrc").write_text("export TOKEN=secret\n")
    (project / ".netrc").write_text("machine example.com password secret\n")
    (project / ".npmrc").write_text("//registry.example.com/:_authToken=secret\n")
    (project / "pip.conf").write_text("[global]\nindex-url = https://secret\n")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("secret")
    (project / ".ssh").mkdir()
    (project / ".ssh" / "id_ed25519").write_text("secret")
    (project / ".fastmcp").mkdir()
    (project / ".fastmcp" / "project.json").write_text("secret")
    (project / "production.fastmcp.json").write_text(
        json.dumps({"deployment": {"env": {"TOKEN": "secret"}}})
    )

    bundle = await create_source_bundle("server.py", tmp_path / "source.tar.gz")

    names = archive_names(bundle.archive_path)
    assert {
        ".gitignore",
        "app/.gitignore",
        "server.py",
        "app/keep.txt",
        "app/env",
        "app/env/settings.py",
    } <= names
    assert "keep.txt" not in names
    assert "ignored.txt" not in names
    assert not any(name.startswith(".env") for name in names)
    assert not any(name.startswith(".git/") for name in names)
    assert not any(name.startswith(".ssh/") for name in names)
    assert not any(name.startswith(".fastmcp/") for name in names)
    assert {".netrc", ".npmrc", "pip.conf"}.isdisjoint(names)
    assert "production.fastmcp.json" not in names


async def test_archive_keeps_files_without_a_security_or_git_exclusion(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "server.pyc").write_bytes(b"cache")
    (project / "dist").mkdir()
    (project / "dist" / "model.bin").write_bytes(b"model")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "package.js").write_text("export {}\n")

    bundle = await create_source_bundle("server.py", tmp_path / "source.tar.gz")

    assert {
        ".venv/pyvenv.cfg",
        "__pycache__/server.pyc",
        "dist/model.bin",
        "node_modules/package.js",
    } <= archive_names(bundle.archive_path)


async def test_gitignore_negation_restores_an_ignored_file(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    distribution = project / "dist"
    distribution.mkdir()
    (distribution / "ignored.bin").write_bytes(b"ignored")
    (distribution / "model.bin").write_bytes(b"model")
    (project / ".gitignore").write_text("dist/*\n!dist/model.bin\n")

    bundle = await create_source_bundle("server.py", tmp_path / "source.tar.gz")

    names = archive_names(bundle.archive_path)
    assert "dist/model.bin" in names
    assert "dist/ignored.bin" not in names


async def test_gitignore_cannot_restore_hard_exclusion(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / ".env").write_text("TOKEN=secret\n")
    (project / ".gitignore").write_text("!.env\n")

    bundle = await create_source_bundle("server.py", tmp_path / "source.tar.gz")

    assert ".env" not in archive_names(bundle.archive_path)


async def test_archive_preserves_empty_directories(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / "data" / "uploads").mkdir(parents=True)

    bundle = await create_source_bundle("server.py", tmp_path / "source.tar.gz")

    with tarfile.open(bundle.archive_path, "r:gz") as archive:
        data = archive.getmember("data")
        uploads = archive.getmember("data/uploads")
        assert data.isdir()
        assert uploads.isdir()
        assert uploads.mode == 0o755
        assert uploads.mtime == 0


@pytest.mark.parametrize(
    ("ignored_path", "pattern"),
    [
        ("src/server.py", "src/\n"),
        ("requirements.txt", "requirements.txt\n"),
    ],
)
async def test_required_ignored_path_is_rejected(
    project: Path,
    tmp_path: Path,
    ignored_path: str,
    pattern: str,
) -> None:
    write_server(project / "src" / "server.py")
    (project / "requirements.txt").write_text("fastmcp>=4\n")
    (project / ".gitignore").write_text(pattern)

    with pytest.raises(SourceInvalidError, match="Required source"):
        await create_source_bundle(
            "src/server.py",
            tmp_path / f"{Path(ignored_path).name}.tar.gz",
        )


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs extra access")
async def test_external_symlink_is_rejected(project: Path, tmp_path: Path) -> None:
    write_server(project / "server.py")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (project / "outside-link").symlink_to(outside)

    with pytest.raises(SourceInvalidError, match="Symbolic link leaves"):
        await create_source_bundle("server.py", tmp_path / "source.tar.gz")


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs extra access")
async def test_server_below_internal_directory_symlink_is_archived(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "real" / "server.py")
    (project / "pyproject.toml").write_text("[project]\nname = 'test'\nversion = '0'\n")
    (project / "linked").symlink_to(project / "real", target_is_directory=True)

    bundle = await create_source_bundle(
        "linked/server.py",
        tmp_path / "source.tar.gz",
    )

    assert bundle.entrypoint == "linked/server.py"
    assert {"linked", "real", "real/server.py"} <= archive_names(bundle.archive_path)


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs extra access")
async def test_marker_free_symlinked_server_uses_resolved_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = tmp_path / "container"
    write_server(container / "real" / "server.py")
    (container / "unrelated-secret.txt").write_text("secret")
    (container / "linked").symlink_to(
        container / "real",
        target_is_directory=True,
    )
    monkeypatch.chdir(container)

    bundle = await create_source_bundle(
        "linked/server.py",
        tmp_path / "source.tar.gz",
    )

    assert bundle.entrypoint == "server.py"
    assert archive_names(bundle.archive_path) == {"server.py"}


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs extra access")
async def test_marker_free_file_symlink_uses_target_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = tmp_path / "container"
    target = container / "real"
    write_server(target / "server.py")
    (target / "requirements.txt").write_text("fastmcp>=4\n")
    (container / "unrelated-secret.txt").write_text("secret")
    (container / "server-link.py").symlink_to(target / "server.py")
    monkeypatch.chdir(container)

    bundle = await create_source_bundle(
        "server-link.py",
        tmp_path / "source.tar.gz",
    )

    assert bundle.entrypoint == "server.py"
    assert bundle.dependency_path == "requirements.txt"
    assert archive_names(bundle.archive_path) == {"requirements.txt", "server.py"}


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs extra access")
async def test_internal_symlink_is_relative_in_archive(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / "data.txt").write_text("data")
    (project / "data-link").symlink_to(project / "data.txt")

    bundle = await create_source_bundle("server.py", tmp_path / "source.tar.gz")

    with tarfile.open(bundle.archive_path, "r:gz") as archive:
        link = archive.getmember("data-link")
        assert link.issym()
        assert link.linkname == "data.txt"


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs extra access")
async def test_symlink_to_ignored_target_is_rejected(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / ".gitignore").write_text("private.txt\n")
    (project / "private.txt").write_text("private")
    (project / "data-link").symlink_to(project / "private.txt")

    with pytest.raises(SourceInvalidError, match="target is not archived: data-link"):
        await create_source_bundle("server.py", tmp_path / "source.tar.gz")


async def test_archive_path_inside_source_root_is_rejected(project: Path) -> None:
    write_server(project / "server.py")

    with pytest.raises(SourceInvalidError, match="outside the source root"):
        await create_source_bundle("server.py", project / "source.tar.gz")


async def test_existing_archive_path_is_preserved(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    archive_path = tmp_path / "source.tar.gz"
    archive_path.write_bytes(b"existing")

    with pytest.raises(SourceInvalidError, match="already exists"):
        await create_source_bundle("server.py", archive_path)

    assert archive_path.read_bytes() == b"existing"


async def test_competing_archive_file_is_preserved(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_server(project / "server.py")
    archive_path = tmp_path / "source.tar.gz"
    original_collect_entries = source_bundle_module._collect_entries

    def create_competing_file(
        source_root: Path,
        *,
        required_paths: tuple[Path, ...],
        excluded_paths: tuple[Path, ...],
    ) -> list[tuple[str, Path | bytes]]:
        entries = original_collect_entries(
            source_root,
            required_paths=required_paths,
            excluded_paths=excluded_paths,
        )
        archive_path.write_bytes(b"competing")
        return entries

    monkeypatch.setattr(
        source_bundle_module,
        "_collect_entries",
        create_competing_file,
    )

    with pytest.raises(SourceInvalidError, match="already exists"):
        await create_source_bundle("server.py", archive_path)

    assert archive_path.read_bytes() == b"competing"


async def test_archive_member_limit_matches_horizon(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_server(project / "server.py")
    (project / "extra.txt").write_text("extra")
    archive_path = tmp_path / "source.tar.gz"
    monkeypatch.setattr(source_archive_module, "MAX_SOURCE_ARCHIVE_MEMBERS", 1)

    with pytest.raises(ArchiveTooLargeError, match="25,000 member"):
        await create_source_bundle("server.py", archive_path)

    assert not archive_path.exists()


async def test_archive_extracted_size_limit_matches_horizon(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_server(project / "server.py")
    archive_path = tmp_path / "source.tar.gz"
    monkeypatch.setattr(source_archive_module, "MAX_SOURCE_EXTRACTED_BYTES", 1)

    with pytest.raises(ArchiveTooLargeError, match="1 GB extracted"):
        await create_source_bundle("server.py", archive_path)

    assert not archive_path.exists()


async def test_archive_size_limit_uses_compressed_size(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_server(project / "server.py")
    (project / "data.bin").write_bytes(os.urandom(1024 * 1024))
    (project / "z-after-limit.txt").write_text("not reached")
    archive_path = tmp_path / "source.tar.gz"
    visited: list[str] = []
    original_add_entry = source_archive_module._add_archive_entry

    def record_entry(
        archive: tarfile.TarFile,
        name: str,
        entry_source: Path | bytes,
        source_root: Path,
    ) -> None:
        visited.append(name)
        original_add_entry(archive, name, entry_source, source_root)

    monkeypatch.setattr(source_archive_module, "MAX_SOURCE_UPLOAD_BYTES", 100)
    monkeypatch.setattr(source_archive_module, "_add_archive_entry", record_entry)

    with pytest.raises(ArchiveTooLargeError, match="250 MB"):
        await create_source_bundle("server.py", archive_path)

    assert "z-after-limit.txt" not in visited
    assert not archive_path.exists()
