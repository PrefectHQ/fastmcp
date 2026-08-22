from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from fastmcp.cli.deploy.source_bundle import SourceInvalidError, create_source_bundle


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


def archived_json(path: Path, name: str) -> object:
    with tarfile.open(path, "r:gz") as archive:
        archived = archive.extractfile(name)
        assert archived is not None
        return json.loads(archived.read())


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source_root = tmp_path / "project"
    source_root.mkdir()
    monkeypatch.chdir(source_root)
    return source_root


async def test_explicit_server_resolves_entrypoint_and_dependency(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "src" / "server.py")
    (project / "requirements.txt").write_text("fastmcp>=4\n")

    bundle = await create_source_bundle(
        "src/server.py",
        tmp_path / "source.tar.gz",
    )

    assert bundle.entrypoint == "src/server.py"
    assert bundle.dependency_path == "requirements.txt"
    assert bundle.size_bytes == bundle.archive_path.stat().st_size
    assert len(bundle.checksum_sha256) == 64
    assert {"src/server.py", "requirements.txt"} <= archive_names(bundle.archive_path)


async def test_explicit_server_uses_nearest_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    write_server(project / "src" / "server.py")
    (project / "pyproject.toml").write_text("[project]\nname = 'test'\nversion = '0'\n")
    (tmp_path / "unrelated-secret.txt").write_text("secret")
    monkeypatch.chdir(tmp_path)

    bundle = await create_source_bundle(
        str(project / "src" / "server.py"),
        tmp_path / "source.tar.gz",
    )

    assert bundle.entrypoint == "src/server.py"
    assert bundle.dependency_path == "pyproject.toml"
    assert "pyproject.toml" in archive_names(bundle.archive_path)
    assert "unrelated-secret.txt" not in archive_names(bundle.archive_path)


async def test_explicit_server_without_dependency_has_no_dependency_input(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")

    bundle = await create_source_bundle("server.py", tmp_path / "source.tar.gz")

    assert bundle.dependency_path is None


async def test_explicit_server_prefers_uv_lock_for_project(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "src" / "server.py")
    (project / "pyproject.toml").write_text('[project]\nname = "project"\n')
    (project / "uv.lock").write_text("version = 1\n")

    bundle = await create_source_bundle("src/server.py", tmp_path / "source.tar.gz")

    assert bundle.dependency_path == "uv.lock"
    assert {"uv.lock", "pyproject.toml"} <= archive_names(bundle.archive_path)


async def test_explicit_object_input_uses_selected_entrypoint(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py", name="application")

    bundle = await create_source_bundle(
        "server.py:application",
        tmp_path / "source.tar.gz",
    )

    assert bundle.entrypoint == "server.py:application"


@pytest.mark.parametrize(
    ("file_name", "server_spec", "error"),
    [
        ("server.py", "server.py:not-valid", "Python identifier"),
        ("server file.py", "server file.py", "Horizon-compatible path"),
        ("server.txt", "server.txt", "Horizon-compatible path"),
    ],
)
async def test_invalid_entrypoint_is_rejected(
    project: Path,
    tmp_path: Path,
    file_name: str,
    server_spec: str,
    error: str,
) -> None:
    write_server(project / file_name)

    with pytest.raises(SourceInvalidError, match=error):
        await create_source_bundle(server_spec, tmp_path / "source.tar.gz")


async def test_selected_config_is_sanitized_for_horizon(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "src" / "server.py")
    (project / "src" / "requirements-base.txt").write_text("fastmcp>=4\n")
    (project / "src" / "package").mkdir()
    (project / "src" / "package" / "pyproject.toml").write_text(
        "[project]\nname = 'package'\n"
    )
    (project / "src" / "shared").mkdir()
    (project / "fastmcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://example.com/schema.json",
                "source": {"path": "server.py"},
                "environment": {
                    "python": ">=3.10",
                    "requirements": "requirements-base.txt",
                    "project": "package",
                    "dependencies": ["httpx>=0.27"],
                    "editable": ["shared"],
                },
                "deployment": {
                    "cwd": "src",
                    "env": {"API_KEY": "not-archived"},
                    "host": "127.0.0.1",
                },
            }
        )
    )

    bundle = await create_source_bundle(None, tmp_path / "source.tar.gz")

    assert bundle.entrypoint == "src/server.py"
    assert bundle.dependency_path is None
    assert archived_json(bundle.archive_path, "fastmcp.json") == {
        "deployment": {"cwd": "src"},
        "environment": {
            "dependencies": ["httpx>=0.27"],
            "editable": ["shared"],
            "project": "package",
            "python": ">=3.10",
            "requirements": "requirements-base.txt",
            "type": "uv",
        },
    }


async def test_explicit_config_is_archived_as_fastmcp_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "project"
    write_server(source_root / "src" / "server.py", name="app")
    (source_root / "requirements.txt").write_text("fastmcp>=4\n")
    config_path = source_root / "deploy.json"
    config_path.write_text(
        json.dumps(
            {
                "source": {"path": "src/server.py", "entrypoint": "app"},
                "environment": {"requirements": "requirements.txt"},
                "deployment": {"env": {"TOKEN": "not-archived"}},
            }
        )
    )
    monkeypatch.chdir(tmp_path)

    bundle = await create_source_bundle(
        "project/deploy.json",
        tmp_path / "source.tar.gz",
    )

    assert bundle.entrypoint == "src/server.py:app"
    assert bundle.dependency_path is None
    names = archive_names(bundle.archive_path)
    assert "deploy.json" not in names
    assert "fastmcp.json" in names
    assert archived_json(bundle.archive_path, "fastmcp.json") == {
        "environment": {"requirements": "requirements.txt", "type": "uv"}
    }


async def test_config_source_resolves_from_deployment_working_directory(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "src" / "server.py")
    (project / "src" / "requirements.txt").write_text("fastmcp>=4\n")
    (project / "requirements.txt").write_text("wrong-package\n")
    (project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"requirements": "requirements.txt"},
                "deployment": {"cwd": "src"},
            }
        )
    )

    bundle = await create_source_bundle(None, tmp_path / "source.tar.gz")

    assert bundle.entrypoint == "src/server.py"
    assert bundle.dependency_path is None
    assert archived_json(bundle.archive_path, "fastmcp.json") == {
        "deployment": {"cwd": "src"},
        "environment": {"requirements": "requirements.txt", "type": "uv"},
    }


async def test_configured_source_is_not_imported_locally(
    project: Path,
    tmp_path: Path,
) -> None:
    side_effect = project / "side-effect"
    (project / "server.py").write_text(
        "import deployment_only_dependency\n"
        "from pathlib import Path\n"
        f"Path({str(side_effect)!r}).touch()\n"
    )
    (project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"dependencies": ["deployment-only-dependency"]},
            }
        )
    )

    bundle = await create_source_bundle(None, tmp_path / "source.tar.gz")

    assert bundle.entrypoint == "server.py"
    assert bundle.dependency_path is None
    assert not side_effect.exists()


async def test_explicit_file_does_not_activate_nearby_config(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / "requirements.txt").write_text("fastmcp>=4\n")
    (project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "other.py"},
                "deployment": {"env": {"TOKEN": "not-archived"}},
            }
        )
    )

    bundle = await create_source_bundle("server.py", tmp_path / "source.tar.gz")

    assert bundle.dependency_path == "requirements.txt"
    assert "fastmcp.json" not in archive_names(bundle.archive_path)


@pytest.mark.parametrize("external_kind", ["source", "dependency"])
async def test_external_config_paths_are_rejected(
    project: Path,
    tmp_path: Path,
    external_kind: str,
) -> None:
    outside_server = tmp_path / "outside.py"
    write_server(outside_server)
    outside_requirements = tmp_path / "requirements.txt"
    outside_requirements.write_text("fastmcp>=4\n")

    if external_kind == "source":
        config = {"source": {"path": "../outside.py"}}
    else:
        write_server(project / "server.py")
        config = {
            "source": {"path": "server.py"},
            "environment": {"requirements": "../requirements.txt"},
        }
    (project / "fastmcp.json").write_text(json.dumps(config))

    with pytest.raises(SourceInvalidError, match="leaves the source root"):
        await create_source_bundle(None, tmp_path / "source.tar.gz")


@pytest.mark.parametrize(
    ("dependency", "error"),
    [
        (
            "private @ git+https://token@example.com/private.git",
            "must not contain credentials",
        ),
        (
            "private @ https://example.com/private.whl?token=secret",
            "must not contain URL query parameters",
        ),
        (
            "git+https://token@example.com/private.git",
            "must not contain credentials",
        ),
        (
            "https://example.com/private.whl?token=secret",
            "must not contain URL query parameters",
        ),
    ],
)
async def test_sensitive_dependency_urls_are_rejected(
    project: Path,
    tmp_path: Path,
    dependency: str,
    error: str,
) -> None:
    write_server(project / "server.py")
    (project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"dependencies": [dependency]},
            }
        )
    )

    archive_path = tmp_path / "source.tar.gz"
    with pytest.raises(SourceInvalidError, match=error):
        await create_source_bundle(None, archive_path)
    assert not archive_path.exists()


async def test_missing_config_dependency_path_is_deferred_to_horizon(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"requirements": "missing.txt"},
            }
        )
    )

    bundle = await create_source_bundle(None, tmp_path / "source.tar.gz")

    assert archived_json(bundle.archive_path, "fastmcp.json") == {
        "environment": {"requirements": "missing.txt", "type": "uv"}
    }


async def test_build_validation_is_deferred_to_horizon(
    project: Path,
    tmp_path: Path,
) -> None:
    write_server(project / "server.py")
    (project / "fastmcp.json").write_text(
        json.dumps(
            {
                "source": {"path": "server.py"},
                "environment": {"dependencies": ["not a requirement"]},
            }
        )
    )

    bundle = await create_source_bundle(None, tmp_path / "source.tar.gz")

    assert archived_json(bundle.archive_path, "fastmcp.json") == {
        "environment": {"dependencies": ["not a requirement"], "type": "uv"}
    }
