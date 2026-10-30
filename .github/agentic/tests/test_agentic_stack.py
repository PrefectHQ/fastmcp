from __future__ import annotations

import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from flows.agentic_stack import StackSpec, assemble_stack, git, restore_stack
from flows.agentic_validate_patch import apply_patch


def command(repo: Path, *args: str) -> None:
    subprocess.run(args, cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, StackSpec]:
    repo = tmp_path / "source"
    repo.mkdir()
    command(repo, "git", "init", "-b", "main")
    command(repo, "git", "config", "user.name", "Test")
    command(repo, "git", "config", "user.email", "test@example.invalid")
    (repo / "tests").mkdir()
    (repo / "tests" / "probe.py").write_text("assert True\n")
    command(repo, "git", "add", ".")
    command(repo, "git", "commit", "-m", "Base")
    base = git(repo, "rev-parse", "HEAD")
    layers = []
    for number, name in [(1, "first"), (2, "second")]:
        (repo / "tests" / f"{name}.txt").write_text(f"Layer {number}\n")
        command(repo, "git", "add", "--intent-to-add", ".")
        patch = subprocess.check_output(
            ["git", "diff", "--binary"], cwd=repo, text=True
        )
        layers.append(
            {
                "issue_number": number,
                "patch": patch,
                "title": f"Fix {name}",
                "body": f"Fixes #{number}",
                "credits": [
                    f"Co-authored-by: reporter{number} <{number}+reporter{number}@users.noreply.github.com>"
                ],
                "evidence_revision": str(number) * 64,
            }
        )
        command(repo, "git", "reset", "--hard", base)
    spec = StackSpec.model_validate(
        {
            "batch_id": "trial",
            "group": "client",
            "trunk_ref": "main",
            "trunk_sha": base,
            "created_at": "2026-10-21T00:00:00Z",
            "publisher_login": "automation",
            "publisher_id": 10,
            "layers": layers,
        }
    )
    return repo, spec


def probe(repo: Path, paths: list[str]) -> None:
    command(repo, sys.executable, "tests/probe.py")


def clone_base(tmp_path: Path, repo: Path, spec: StackSpec) -> Path:
    destination = tmp_path / "publication"
    command(tmp_path, "git", "clone", "--no-checkout", str(repo), str(destination))
    command(destination, "git", "checkout", "--detach", spec.trunk_sha)
    return destination


def test_validated_chain_roundtrips_into_independent_checkout(repository, tmp_path):
    repo, spec = repository
    checks = []

    def check(directory, paths):
        probe(directory, paths)
        assert (directory / "tests/first.txt").exists()
        checks.append(git(directory, "rev-parse", "HEAD"))

    artifact = assemble_stack(repo, spec, check)
    assert checks == [spec.trunk_sha, artifact["layers"][0]["head_sha"]]
    destination = clone_base(tmp_path, repo, spec)
    assert restore_stack(destination, artifact) == spec
    assert git(destination, "rev-parse", "HEAD") == artifact["layers"][-1]["head_sha"]
    assert (destination / "tests/first.txt").read_text() == "Layer 1\n"
    assert (destination / "tests/second.txt").read_text() == "Layer 2\n"
    assert artifact["layers"][1]["base_ref"] == spec.branch(spec.layers[0])
    assert "Co-authored-by: reporter2" in git(
        destination, "show", "-s", "--format=%B", "HEAD"
    )


def test_identical_inputs_rebuild_identical_commits(repository, tmp_path):
    repo, spec = repository
    destination = clone_base(tmp_path, repo, spec)
    first = assemble_stack(repo, spec, probe)
    second = assemble_stack(destination, spec, probe)
    assert first["layers"] == second["layers"]
    assert first["spec_sha256"] == second["spec_sha256"]


def test_maintainer_report_does_not_require_supplemental_credit(repository, tmp_path):
    repo, spec = repository
    spec = spec.model_copy(
        update={
            "layers": [
                layer.model_copy(update={"credits": []}) for layer in spec.layers
            ]
        }
    )
    artifact = assemble_stack(repo, spec, probe)
    destination = clone_base(tmp_path, repo, spec)
    assert restore_stack(destination, artifact) == spec
    assert (
        git(destination, "show", "-s", "--format=%B", "HEAD") == spec.layers[-1].title
    )


@pytest.mark.parametrize(
    "corruption", ["bundle", "parent", "tree", "patch", "credit", "tip", "count"]
)
def test_restoration_rejects_tampered_artifacts(repository, tmp_path, corruption):
    repo, spec = repository
    artifact = assemble_stack(repo, spec, probe)
    destination = clone_base(tmp_path, repo, spec)
    artifact = deepcopy(artifact)
    if corruption == "bundle":
        artifact["bundle_sha256"] = "f" * 64
    elif corruption == "parent":
        artifact["layers"][1]["parent_sha"] = spec.trunk_sha
    elif corruption == "tree":
        artifact["layers"][1]["tree_sha"] = "f" * 40
    elif corruption == "patch":
        artifact["spec"]["layers"][1]["patch"] += "garbage\n"
    elif corruption == "credit":
        artifact["spec"]["layers"][1]["credits"] = [
            "Co-authored-by: Other <other@example.invalid>"
        ]
    elif corruption == "tip":
        artifact["layers"][1]["head_sha"] = artifact["layers"][0]["head_sha"]
    else:
        artifact["layers"].pop()
    with pytest.raises(ValueError):
        restore_stack(destination, artifact)


def test_combined_tip_failure_prevents_validated_artifact(repository):
    repo, spec = repository

    def incompatible(directory, paths):
        command(
            directory,
            sys.executable,
            "-c",
            "from pathlib import Path; assert not (Path('tests/first.txt').exists() and Path('tests/second.txt').exists())",
        )

    with pytest.raises(subprocess.CalledProcessError):
        assemble_stack(repo, spec, incompatible)


def test_formatter_changes_cannot_be_published_as_validated_input(repository):
    repo, spec = repository

    def formatter(directory, paths):
        (directory / paths[0]).write_text("Changed by formatter\n")

    with pytest.raises(ValueError, match="Validation changed"):
        assemble_stack(repo, spec, formatter)


def test_commit_hook_changes_cannot_escape_validation(repository):
    repo, spec = repository
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\nprintf 'changed\\n' > tests/first.txt\ngit add tests/first.txt\n"
    )
    hook.chmod(0o755)
    with pytest.raises(ValueError, match="Commit hooks changed"):
        assemble_stack(repo, spec, probe)


def test_wrong_trunk_is_rejected_before_applying_patches(repository):
    repo, spec = repository
    spec = spec.model_copy(update={"trunk_sha": "f" * 40})
    with pytest.raises(ValueError, match="trunk revision"):
        assemble_stack(repo, spec, probe)


def test_generated_docs_are_not_editable_by_a_stack(repository):
    repo, _ = repository
    path = repo / "docs/python-sdk/generated.mdx"
    path.parent.mkdir(parents=True)
    path.write_text("Generated\n")
    command(repo, "git", "add", "--intent-to-add", ".")
    patch = subprocess.check_output(["git", "diff", "--binary"], cwd=repo, text=True)
    command(repo, "git", "reset", "--hard", "HEAD")
    with pytest.raises(ValueError, match="maintained FastMCP"):
        apply_patch(repo, patch)
