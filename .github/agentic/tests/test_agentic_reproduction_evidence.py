import hashlib
import subprocess
from pathlib import Path

import pytest

from flows.agentic_reproduction_evidence import materialize_regression
from flows.agentic_stack import git


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.name", "Test"],
        ["git", "config", "user.email", "test@example.invalid"],
    ):
        subprocess.run(args, cwd=repo, check=True, capture_output=True)
    (repo / "tests").mkdir()
    (repo / "tests" / "probe.py").write_text("assert True\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Base"], cwd=repo, check=True, capture_output=True
    )
    base = git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "first.txt").write_text("Layer 1\n")
    subprocess.run(
        ["git", "add", "--intent-to-add", "."],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    patch = subprocess.check_output(["git", "diff", "--binary"], cwd=repo, text=True)
    subprocess.run(
        ["git", "reset", "--hard", base], cwd=repo, check=True, capture_output=True
    )
    return repo, base, patch


def test_exact_regression_patch_materializes_on_recorded_base(repository):
    directory, base, patch = repository
    proof = materialize_regression(
        directory, {"base_sha": base, "regression_patch": patch}
    )
    assert proof["status"] == "materialized"
    assert proof["patch_sha256"] == hashlib.sha256(patch.encode()).hexdigest()
    assert proof["paths"] == ["tests/first.txt"]


def test_truncated_patch_is_not_accepted_as_reusable_evidence(repository):
    directory, base, patch = repository
    with pytest.raises(RuntimeError, match="corrupt patch"):
        materialize_regression(
            directory,
            {
                "base_sha": base,
                "regression_patch": patch.rstrip("\n"),
            },
        )


def test_reproduction_cannot_modify_library_code(repository):
    directory, base, patch = repository
    with pytest.raises(ValueError, match="tests only"):
        materialize_regression(
            directory,
            {
                "base_sha": base,
                "regression_patch": patch.replace("tests/", "fastmcp_slim/"),
            },
        )
