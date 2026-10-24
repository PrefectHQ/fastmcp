from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from flows.agentic_existing_review import verification_specs
from flows.agentic_pr_verification import (
    VerificationSpec,
    environment,
    execute,
    outcomes,
    pytest_result,
    verify,
)
from flows.agentic_stack_policy import Group


def commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


@pytest.mark.parametrize("state", ["open", "closed"])
def test_real_base_head_execution_detects_fix_and_independent_regression(
    tmp_path, state, evidence
):
    repo = tmp_path / "origin"
    repo.mkdir()
    for args in (
        ("init",),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname="verification-fixture"\nversion="0.1"\nrequires-python=">=3.12"\ndependencies=["pytest>=8,<10", "pytest-xdist"]\n[tool.pytest.ini_options]\npythonpath=["."]\n'
    )
    (repo / "implementation.py").write_text("value = 1\nflag = True\n")
    subprocess.run(["uv", "lock"], cwd=repo, check=True, capture_output=True)
    base = commit(repo, "Broken value")
    (repo / "implementation.py").write_text("value = 2\nflag = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_value.py").write_text(
        "from implementation import value\ndef test_value():\n    assert value == 2\n"
    )
    head = commit(repo, "Fix value but regress flag")
    subprocess.run(
        ["git", "checkout", "--detach", base], cwd=repo, check=True, capture_output=True
    )
    (repo / "implementation.py").write_text("value = 0\nflag = True\n")
    trunk = commit(repo, "Advance target independently")
    directory = tmp_path / "verification"
    directory.mkdir()
    group = Group(
        name="contribution",
        rationale="Review an existing contribution without replacing or reopening it",
        branch="main",
        base_sha=trunk,
        items=[{"issue_number": 1, "mode": "mre"}],
    )
    item = {
        "evidence": {
            **evidence,
            "stacks": [],
            "issue": {"number": 1},
            "prs": [
                {
                    "number": 2,
                    "state": state,
                    "merged": False,
                    "head_sha": head,
                    "base_ref": "main",
                },
                {"number": 3, "state": "closed", "merged": True},
            ],
        },
        "route": {"pull_requests": [{"number": 2}, {"number": 3}]},
    }
    specs = verification_specs(
        group,
        item,
        {
            "tests/agentic_probes/test_flag.py": "from implementation import flag\ndef test_flag_type():\n    assert type(flag) is bool\n"
        },
    )
    assert len(specs) == 1
    spec = specs[0]
    result = verify(spec, directory, str(repo))
    assert result["status"] == "verification_recorded"
    assert result["merge_base_sha"] == base
    assert result["baseline_sha"] == trunk
    assert (
        directory / "base/implementation.py"
    ).read_text() == "value = 0\nflag = True\n"
    assert result["base"]["status"] == "test_failure"
    assert result["head"]["status"] == "test_failure"
    assert result["comparison"]["fixed_tests"] == ["tests.test_value::test_value"]
    assert result["comparison"]["regressed_tests"] == [
        "tests.agentic_probes.test_flag::test_flag_type"
    ]
    assert result["comparison"]["unmatched_tests"] == []
    assert result["usage"]["model_requests"] == 0


def test_real_pytest_setup_error_and_skip_do_not_establish_a_fix(tmp_path):
    (tmp_path / "test_probe.py").write_text(
        'import pytest\n@pytest.fixture\ndef broken():\n    raise RuntimeError("setup failed")\ndef test_error(broken):\n    pass\n@pytest.mark.skip(reason="not checked")\ndef test_skipped():\n    pass\n'
    )
    report = tmp_path / "junit.xml"
    command = execute(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path / "test_probe.py"),
            f"--junitxml={report}",
        ],
        tmp_path,
        environment(tmp_path / "home"),
    )
    result = pytest_result(command, report)
    assert result["status"] == "environment_failure"
    assert {case["state"] for case in result["cases"]} == {"error", "skipped"}
    assert not outcomes(result, result)["fixed_tests"]


def test_command_does_not_inherit_credentials_or_global_home(tmp_path):
    env = environment(tmp_path / "home")
    assert set(env).isdisjoint(
        {
            "PREFECT_API_KEY",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "SSH_AUTH_SOCK",
            "AWS_ACCESS_KEY_ID",
        }
    )
    result = execute(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['HOME']); print(os.environ.get('PREFECT_API_KEY', 'absent'))",
        ],
        tmp_path,
        env,
    )
    assert result["returncode"] == 0
    assert result["output_tail"].splitlines() == [str(tmp_path / "home"), "absent"]
    assert env["HOME"] != os.environ["HOME"]


def test_timeout_is_operational_even_if_junit_looks_successful(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase classname="test" name="example"/></testsuite>'
    )
    result = execute(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        tmp_path,
        environment(tmp_path / "home"),
        timeout=1,
    )
    assert result["timed_out"]
    assert pytest_result(result, report)["status"] == "environment_failure"


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "tests/conftest.py",
        "tests/agentic_probes/../test_escape.py",
        "/tests/agentic_probes/test_escape.py",
    ],
)
def test_probe_cannot_replace_test_configuration(path):
    with pytest.raises(ValueError, match="Probes must"):
        VerificationSpec(
            issue_number=1,
            pr_number=2,
            head_sha="a" * 40,
            trunk_sha="b" * 40,
            trunk_ref="main",
            evidence_revision="c" * 64,
            probes={path: "pass"},
        )


def test_old_contribution_fetches_history_before_computing_changed_tests(tmp_path):
    repo = tmp_path / "origin"
    repo.mkdir()
    for args in (
        ("init",),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "implementation.py").write_text("value = 1\n")
    base = commit(repo, "Common ancestor")
    (repo / "implementation.py").write_text("value = 2\n")
    head = commit(repo, "Old contribution without tests")
    tree = subprocess.check_output(
        ["git", "rev-parse", f"{base}^{{tree}}"], cwd=repo, text=True
    ).strip()
    trunk = base
    for number in range(270):
        trunk = subprocess.check_output(
            ["git", "commit-tree", tree, "-p", trunk, "-m", f"Advance {number}"],
            cwd=repo,
            text=True,
        ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/trunk", trunk], cwd=repo, check=True
    )
    spec = VerificationSpec(
        issue_number=1,
        pr_number=2,
        head_sha=head,
        trunk_sha=trunk,
        trunk_ref="main",
        evidence_revision="c" * 64,
    )
    directory = tmp_path / "verification"
    directory.mkdir()
    result = verify(spec, directory, str(repo))
    assert result["merge_base_sha"] == base
    assert result["baseline_sha"] == trunk
    assert result["status"] == "evidence_needed"
    assert result["changed_tests"] == []
