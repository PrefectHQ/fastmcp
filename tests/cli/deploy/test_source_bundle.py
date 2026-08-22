from __future__ import annotations

import json
import os
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest

import fastmcp.cli.deploy.source_bundle as source_bundle_module
from fastmcp.cli.deploy.source import SourceInvalidError, resolve_deploy_source
from fastmcp.cli.deploy.source_bundle import (
    ArchiveTooLargeError,
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


def archive_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        return {member.name for member in archive.getmembers()}


@pytest.fixture
def in_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


async def test_resolve_explicit_server_uses_current_directory(
    in_project: Path,
) -> None:
    write_server(in_project / "src" / "server.py")

    source = await resolve_deploy_source("src/server.py")

    assert source.source_root == in_project
    assert source.entrypoint == "src/server.py:mcp"
    assert source.dependency_path is None


async def test_explicit_source_detects_nearest_dependency_file(
    in_project: Path,
) -> None:
    write_server(in_project / "services" / "api" / "server.py")
    (in_project / "requirements.txt").write_text("fastmcp==4.*\n")

    source = await resolve_deploy_source("services/api/server.py")

    assert source.dependency_path == "requirements.txt"


async def test_resolve_config_uses_config_directory_and_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    write_server(project / "src" / "server.py", name="app")
    (project / "requirements.txt").write_text("fastmcp==4.*\n")
    (project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "src/server.py", "entrypoint": "app"},
                "environment": {"requirements": "requirements.txt"},
            }
        )
    )
    monkeypatch.chdir(tmp_path)

    source = await resolve_deploy_source("project/fastmcp.json")

    assert source.source_root == project
    assert source.entrypoint == "src/server.py:app"
    assert source.dependency_path == "requirements.txt"


async def test_explicit_entrypoint_does_not_import_declared_dependencies(
    in_project: Path,
) -> None:
    (in_project / "server.py").write_text(
        "import fastmcp_deployment_only_dependency\n"
        "from fastmcp import FastMCP\n"
        'app = FastMCP("Test")\n'
    )
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py", "entrypoint": "app"},
                "environment": {"dependencies": ["fastmcp-deployment-only-dependency"]},
            }
        )
    )

    source = await resolve_deploy_source(None)

    assert source.entrypoint == "server.py:app"
    assert source.dependency_path == ".fastmcp-deploy-requirements.txt"


async def test_inferred_entrypoint_ignores_unrelated_imports(
    in_project: Path,
) -> None:
    (in_project / "server.py").write_text(
        "import fastmcp_deployment_only_dependency\n"
        "import mcp\n"
        "from fastmcp import FastMCP\n"
        'server = FastMCP("Test")\n'
    )
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"dependencies": ["fastmcp-deployment-only-dependency"]},
            }
        )
    )

    source = await resolve_deploy_source(None)

    assert source.entrypoint == "server.py:server"


async def test_inferred_entrypoint_accepts_imported_server(
    in_project: Path,
) -> None:
    (in_project / "server.py").write_text(
        "from deployment_only_package import app as mcp\n"
    )

    source = await resolve_deploy_source("server.py")

    assert source.entrypoint == "server.py:mcp"


async def test_inferred_entrypoint_rejects_multiple_server_definitions(
    in_project: Path,
) -> None:
    (in_project / "server.py").write_text(
        "from fastmcp import FastMCP\n"
        'mcp = FastMCP("First")\n'
        'server = FastMCP("Second")\n'
    )

    with pytest.raises(SourceInvalidError, match="Multiple possible server objects"):
        await resolve_deploy_source("server.py")


async def test_inferred_entrypoint_ignores_function_local_names(
    in_project: Path,
) -> None:
    (in_project / "server.py").write_text(
        "from fastmcp import FastMCP\n"
        "def setup():\n"
        '    mcp = FastMCP("Nested")\n'
        "    return mcp\n"
    )

    with pytest.raises(SourceInvalidError, match="provide an entrypoint"):
        await resolve_deploy_source("server.py")


async def test_resolve_without_input_discovers_fastmcp_config(
    in_project: Path,
) -> None:
    write_server(in_project / "server.py")
    (in_project / "fastmcp.json").write_text(
        json.dumps({"source": {"path": "server.py"}})
    )

    source = await resolve_deploy_source(None)

    assert source.entrypoint == "server.py:mcp"


async def test_resolve_does_not_archive_deployment_environment(
    in_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_server(in_project / "server.py")
    (in_project / "work").mkdir()
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "deployment": {
                    "cwd": "work",
                    "env": {"FASTMCP_DEPLOY_TEST_SECRET": "not-applied"},
                },
            }
        )
    )
    monkeypatch.delenv("FASTMCP_DEPLOY_TEST_SECRET", raising=False)

    source = await resolve_deploy_source(None)
    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    assert Path.cwd() == in_project
    assert "FASTMCP_DEPLOY_TEST_SECRET" not in os.environ
    assert "fastmcp.json" not in archive_names(bundle.archive_path)


async def test_explicit_source_excludes_fastmcp_configs(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    config = {
        "source": {"path": "server.py"},
        "deployment": {"env": {"API_KEY": "secret"}},
    }
    (in_project / "fastmcp.json").write_text(json.dumps(config))
    (in_project / "production.fastmcp.json").write_text(json.dumps(config))
    source = await resolve_deploy_source("server.py")

    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    names = archive_names(bundle.archive_path)
    assert "fastmcp.json" not in names
    assert "production.fastmcp.json" not in names


async def test_inline_and_file_dependencies_create_generated_requirements(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    (in_project / "requirements-base.txt").write_text("fastmcp==4.*\n")
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {
                    "requirements": "requirements-base.txt",
                    "dependencies": ["httpx>=0.27", "pydantic>=2", "httpx>=0.27"],
                },
            }
        )
    )
    source = await resolve_deploy_source(None)

    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    assert bundle.dependency_path == ".fastmcp-deploy-requirements.txt"
    with tarfile.open(bundle.archive_path, "r:gz") as archive:
        generated = archive.extractfile(".fastmcp-deploy-requirements.txt")
        assert generated is not None
        assert generated.read().decode() == (
            "-r requirements-base.txt\nhttpx>=0.27\npydantic>=2\n"
        )


async def test_project_and_editable_dependencies_stay_inside_source_root(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    package = in_project / "package dir"
    package.mkdir()
    (package / "pyproject.toml").write_text(
        '[project]\nname = "package"\nversion = "0.1.0"\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    )
    (package / "package.py").write_text("PACKAGE = True\n")
    editable = in_project / "shared dir"
    editable.mkdir()
    (editable / "pyproject.toml").write_text(
        '[project]\nname = "shared"\nversion = "0.1.0"\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    )
    (editable / "shared.py").write_text("SHARED = True\n")
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {
                    "project": "package dir",
                    "editable": ["shared dir"],
                },
            }
        )
    )
    source = await resolve_deploy_source(None)

    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    with tarfile.open(bundle.archive_path, "r:gz") as archive:
        generated = archive.extractfile(".fastmcp-deploy-requirements.txt")
        assert generated is not None
        assert generated.read().decode() == (
            "-e file:package%20dir\n-e file:shared%20dir\n"
        )
    names = archive_names(bundle.archive_path)
    assert "package dir/package.py" in names
    assert "shared dir/shared.py" in names


async def test_ignored_explicit_editable_directory_is_rejected(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    editable = in_project / "shared"
    editable.mkdir()
    (editable / "pyproject.toml").write_text('[project]\nname = "shared"\n')
    (editable / "shared.py").write_text("SHARED = True\n")
    (in_project / ".gitignore").write_text("shared/\n")
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"editable": ["shared"]},
            }
        )
    )
    source = await resolve_deploy_source(None)

    with pytest.raises(SourceInvalidError, match="source directory is ignored"):
        create_source_bundle(source, tmp_path / "source.tar.gz")


async def test_project_with_inline_dependency_requires_pyproject(
    in_project: Path,
) -> None:
    write_server(in_project / "server.py")
    (in_project / "package").mkdir()
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {
                    "project": "package",
                    "dependencies": ["httpx"],
                },
            }
        )
    )

    with pytest.raises(SourceInvalidError, match="pyproject.toml does not exist"):
        await resolve_deploy_source(None)


async def test_generated_requirements_rejects_comment_in_include_path(
    in_project: Path,
) -> None:
    write_server(in_project / "server.py")
    (in_project / "requirements#prod.txt").write_text("fastmcp\n")
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {
                    "requirements": "requirements#prod.txt",
                    "dependencies": ["httpx"],
                },
            }
        )
    )

    with pytest.raises(SourceInvalidError, match="unsupported characters"):
        await resolve_deploy_source(None)


async def test_single_project_dependency_uses_project_file(
    in_project: Path,
) -> None:
    write_server(in_project / "src" / "server.py")
    (in_project / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (in_project / "uv.lock").write_text("version = 1\n")
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "src/server.py"},
                "environment": {"project": "."},
            }
        )
    )

    source = await resolve_deploy_source(None)

    assert source.dependency_path == "uv.lock"


async def test_archive_is_deterministic(in_project: Path, tmp_path: Path) -> None:
    write_server(in_project / "server.py")
    (in_project / "requirements.txt").write_text("fastmcp==4.*\n")
    source = await resolve_deploy_source("server.py:mcp")

    first = create_source_bundle(source, tmp_path / "first.tar.gz")
    second = create_source_bundle(source, tmp_path / "second.tar.gz")

    assert first.checksum_sha256 == second.checksum_sha256
    assert first.size_bytes == second.size_bytes
    assert first.archive_path.read_bytes() == second.archive_path.read_bytes()
    with tarfile.open(first.archive_path, "r:gz") as archive:
        server = archive.getmember("server.py")
        assert (server.uid, server.gid, server.uname, server.gname) == (0, 0, "", "")
        assert server.mtime == 0
        assert server.mode == 0o644


async def test_archive_path_inside_source_root_is_rejected(
    in_project: Path,
) -> None:
    write_server(in_project / "server.py")
    source = await resolve_deploy_source("server.py")

    with pytest.raises(SourceInvalidError, match="outside the source root"):
        create_source_bundle(source, in_project / "source.tar.gz")


@pytest.mark.skipif(
    os.name == "nt", reason="Symlink creation needs extra access on Windows"
)
async def test_archive_link_to_source_file_is_rejected(
    in_project: Path,
    tmp_path: Path,
) -> None:
    server_path = in_project / "server.py"
    write_server(server_path)
    original_source = server_path.read_bytes()
    source = await resolve_deploy_source("server.py")
    archive_path = tmp_path / "source.tar.gz"
    archive_path.symlink_to(server_path)

    with pytest.raises(SourceInvalidError, match="outside the source root"):
        create_source_bundle(source, archive_path)

    assert server_path.read_bytes() == original_source


async def test_archive_applies_fixed_and_gitignore_exclusions(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    (in_project / "keep.txt").write_text("keep")
    (in_project / "ignored.txt").write_text("ignored")
    (in_project / ".gitignore").write_text("ignored.txt\nignored-dir/\n")
    (in_project / "ignored-dir").mkdir()
    (in_project / "ignored-dir" / "data.txt").write_text("ignored")
    (in_project / ".env").write_text("TOKEN=secret")
    (in_project / ".env.production").write_text("TOKEN=secret")
    (in_project / ".git").mkdir()
    (in_project / ".git" / "config").write_text("secret")
    (in_project / ".fastmcp").mkdir()
    (in_project / ".fastmcp" / "project.json").write_text("secret")
    (in_project / "__pycache__").mkdir()
    (in_project / "__pycache__" / "server.pyc").write_bytes(b"cache")
    (in_project / ".venv").mkdir()
    (in_project / ".venv" / "secret.txt").write_text("secret")
    source = await resolve_deploy_source("server.py")

    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    names = archive_names(bundle.archive_path)
    assert {"server.py", "keep.txt", ".gitignore"} <= names
    assert "ignored.txt" not in names
    assert not any(name.startswith("ignored-dir/") for name in names)
    assert not any(name.startswith(".env") for name in names)
    assert not any(name.startswith(".git/") for name in names)
    assert not any(name.startswith(".fastmcp/") for name in names)
    assert not any("__pycache__" in name for name in names)
    assert not any(name.startswith(".venv/") for name in names)


async def test_unreadable_source_directory_is_rejected(
    in_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_server(in_project / "server.py")
    blocked = in_project / "blocked"
    blocked.mkdir()
    (blocked / "data.txt").write_text("data")
    source = await resolve_deploy_source("server.py")
    real_scandir = os.scandir

    def fail_for_blocked(path: Path) -> Iterator[os.DirEntry[str]]:
        if Path(path) == blocked:
            raise PermissionError(13, "Permission denied", str(blocked))
        return real_scandir(path)

    monkeypatch.setattr(source_bundle_module.os, "scandir", fail_for_blocked)

    with pytest.raises(SourceInvalidError, match="source directory could not be read"):
        create_source_bundle(source, tmp_path / "source.tar.gz")


async def test_archive_applies_nested_gitignore_rules(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    (in_project / ".gitignore").write_text("*.txt\n")
    app = in_project / "app"
    app.mkdir()
    (app / ".gitignore").write_text("credentials.json\n!keep.txt\n")
    (app / "credentials.json").write_text("secret")
    (app / "keep.txt").write_text("keep")
    source = await resolve_deploy_source("server.py")

    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    names = archive_names(bundle.archive_path)
    assert "app/credentials.json" not in names
    assert "app/keep.txt" in names


async def test_required_source_is_not_removed_by_gitignore(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    (in_project / ".gitignore").write_text("server.py\n")
    source = await resolve_deploy_source("server.py")

    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    assert "server.py" in archive_names(bundle.archive_path)


@pytest.mark.skipif(
    os.name == "nt", reason="Symlink creation needs extra access on Windows"
)
async def test_external_symlink_is_rejected(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (in_project / "outside-link").symlink_to(outside)
    source = await resolve_deploy_source("server.py")

    with pytest.raises(SourceInvalidError, match="leaves the source root"):
        create_source_bundle(source, tmp_path / "source.tar.gz")


@pytest.mark.skipif(
    os.name == "nt", reason="Symlink creation needs extra access on Windows"
)
async def test_internal_symlink_uses_a_relative_archive_target(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    (in_project / "data.txt").write_text("data")
    (in_project / "data-link").symlink_to(in_project / "data.txt")
    source = await resolve_deploy_source("server.py")

    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    with tarfile.open(bundle.archive_path, "r:gz") as archive:
        link = archive.getmember("data-link")
        assert link.issym()
        assert link.linkname == "data.txt"


@pytest.mark.skipif(
    os.name == "nt", reason="Symlink creation needs extra access on Windows"
)
async def test_required_symlink_target_overrides_gitignore(
    in_project: Path,
    tmp_path: Path,
) -> None:
    target = in_project / "actual.py"
    write_server(target)
    (in_project / "server.py").symlink_to(target)
    (in_project / ".gitignore").write_text("actual.py\n")
    source = await resolve_deploy_source("server.py")

    bundle = create_source_bundle(source, tmp_path / "source.tar.gz")

    names = archive_names(bundle.archive_path)
    assert "server.py" in names
    assert "actual.py" in names


async def test_dependency_outside_source_root_is_rejected(
    in_project: Path,
    tmp_path: Path,
) -> None:
    write_server(in_project / "server.py")
    outside = tmp_path / "requirements.txt"
    outside.write_text("fastmcp==4.*\n")
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"requirements": "../requirements.txt"},
            }
        )
    )

    with pytest.raises(SourceInvalidError, match="leaves the source root"):
        await resolve_deploy_source(None)


@pytest.mark.skipif(
    os.name == "nt", reason="Symlink creation needs extra access on Windows"
)
async def test_dependency_through_excluded_directory_link_is_rejected(
    in_project: Path,
) -> None:
    write_server(in_project / "server.py")
    virtual_environment = in_project / ".venv"
    virtual_environment.mkdir()
    (virtual_environment / "requirements.txt").write_text("secret-package\n")
    (in_project / "deps").symlink_to(virtual_environment, target_is_directory=True)
    (in_project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"requirements": "deps/requirements.txt"},
            }
        )
    )

    with pytest.raises(SourceInvalidError, match="excluded from deployment: .venv"):
        await resolve_deploy_source(None)


async def test_archive_size_limit_uses_compressed_size(
    in_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_server(in_project / "server.py")
    (in_project / "a-data.bin").write_bytes(os.urandom(1024 * 1024))
    (in_project / "z-after-limit.txt").write_text("not reached")
    source = await resolve_deploy_source("server.py")
    monkeypatch.setattr(source_bundle_module, "MAX_SOURCE_UPLOAD_BYTES", 100)
    archive_path = tmp_path / "source.tar.gz"
    visited: list[str] = []
    original_add_archive_entry = source_bundle_module._add_archive_entry

    def record_archive_entry(
        archive: tarfile.TarFile,
        name: str,
        entry_source: Path | bytes,
        source_root: Path,
    ) -> None:
        visited.append(name)
        original_add_archive_entry(archive, name, entry_source, source_root)

    monkeypatch.setattr(
        source_bundle_module,
        "_add_archive_entry",
        record_archive_entry,
    )

    with pytest.raises(ArchiveTooLargeError, match="250 MB"):
        create_source_bundle(source, archive_path)

    assert "z-after-limit.txt" not in visited
    assert not archive_path.exists()
