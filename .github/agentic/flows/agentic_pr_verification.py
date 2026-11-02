from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

from prefect import flow
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.runtime import flow_run
from pydantic import BaseModel, ConfigDict, Field, field_validator

from flows.agentic_stack import digest
from flows.agentic_validate_patch import install_tools
from flows.agentic_verification_process import isolated_verification


class VerificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    issue_number: int = Field(gt=0)
    pr_number: int = Field(gt=0)
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    trunk_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    trunk_ref: str = Field(pattern=r"^(main|release/[23]\.x)$")
    evidence_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    probes: dict[str, str] = Field(default_factory=dict, max_length=5)

    @field_validator("probes")
    @classmethod
    def test_files(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            path = PurePosixPath(name)
            if (
                str(path) != name
                or path.is_absolute()
                or ".." in path.parts
                or path.parts[:2] != ("tests", "agentic_probes")
                or not path.name.startswith("test_")
                or path.suffix != ".py"
            ):
                raise ValueError("Probes must be test files under tests/agentic_probes")
        if sum(len(text.encode()) for text in value.values()) > 50000:
            raise ValueError("Probe source exceeds 50 KB")
        return value


def environment(home: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "UV_PYTHON": sys.executable,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Verification",
        "GIT_AUTHOR_EMAIL": "verification@example.invalid",
        "GIT_COMMITTER_NAME": "Verification",
        "GIT_COMMITTER_EMAIL": "verification@example.invalid",
        "PYTEST_XDIST_AUTO_NUM_WORKERS": "4",
    }


def execute(
    command: list[str], directory: Path, env: dict[str, str], timeout: int = 180
) -> dict:
    started = time.monotonic()
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            cwd=directory,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        output.seek(0, os.SEEK_END)
        size = output.tell()
        output.seek(max(0, size - 24000))
        tail = output.read().decode(errors="replace")
    return {
        "command": command,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "seconds": round(time.monotonic() - started, 3),
        "output_tail": tail,
        "output_truncated": size > 24000,
    }


def git(directory: Path, env: dict[str, str], *args: str) -> str:
    result = execute(["git", *args], directory, env)
    if result["returncode"] or result["output_truncated"]:
        raise RuntimeError(f"Git verification failed: {result}")
    return result["output_tail"].strip()


def merge_base(directory: Path, env: dict[str, str], head: str, trunk: str) -> str:
    result = execute(["git", "merge-base", head, trunk], directory, env)
    if (
        result["returncode"] == 1
        and not result["timed_out"]
        and git(directory, env, "rev-parse", "--is-shallow-repository") == "true"
    ):
        git(directory, env, "fetch", "--unshallow", "origin", head, trunk)
        return git(directory, env, "merge-base", head, trunk)
    if result["returncode"] or result["output_truncated"]:
        raise RuntimeError(f"Git verification failed: {result}")
    return result["output_tail"].strip()


def pytest_result(command: dict, report: Path) -> dict:
    result = {**command, "status": "environment_failure", "cases": []}
    if command["timed_out"] or command["returncode"] not in {0, 1}:
        return result
    if not report.is_file() or report.stat().st_size > 5_000_000:
        return result
    try:
        root = ET.parse(report).getroot()
    except ET.ParseError:
        return result
    if root.tag != "testsuites" or not list(root):
        return result
    for case in root.iter("testcase"):
        state = "passed"
        detail = ""
        for kind in ("error", "failure", "skipped"):
            node = case.find(kind)
            if node is not None:
                state = kind
                detail = (node.text or node.get("message", ""))[-2000:]
                break
        result["cases"].append(
            {
                "id": f"{case.get('classname', '')}::{case.get('name', '')}",
                "state": state,
                "detail": detail,
            }
        )
    states = [case["state"] for case in result["cases"]]
    ids = [case["id"] for case in result["cases"]]
    if (
        not states
        or "error" in states
        or len(states) > 5000
        or len(ids) != len(set(ids))
    ):
        return result
    if command["returncode"] == 1 and "failure" in states:
        result["status"] = "test_failure"
    elif command["returncode"] == 0 and "failure" not in states and "passed" in states:
        result["status"] = "passed"
    return result


def run_tests(repo: Path, paths: list[str], env: dict[str, str], report: Path) -> dict:
    setup = execute(["uv", "sync", "--frozen"], repo, env, timeout=300)
    if setup["returncode"]:
        return {"status": "environment_failure", "setup": setup, "cases": []}
    lock = repo / "uv.lock"
    lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None
    result = execute(
        ["uv", "run", "--no-sync", "pytest", "-n", "0", f"--junitxml={report}", *paths],
        repo,
        env,
        timeout=600,
    )
    return {**pytest_result(result, report), "setup": setup, "lock_sha256": lock_sha}


def outcomes(base: dict, head: dict) -> dict:
    before = {case["id"]: case for case in base["cases"]}
    after = {case["id"]: case for case in head["cases"]}
    return {
        "fixed_tests": sorted(
            name
            for name in before.keys() & after.keys()
            if before[name]["state"] == "failure" and after[name]["state"] == "passed"
        ),
        "regressed_tests": sorted(
            name
            for name in before.keys() & after.keys()
            if before[name]["state"] == "passed" and after[name]["state"] == "failure"
        ),
        "unmatched_tests": sorted(before.keys() ^ after.keys()),
        "interpretation": "Test transitions are evidence for review, not proof of the contract or merge readiness. Errors, skips and missing cases never establish a fix.",
    }


def verify(
    spec: VerificationSpec,
    directory: Path,
    source: str = "https://github.com/PrefectHQ/fastmcp.git",
) -> dict:
    env = environment(directory / "home")
    head = directory / "head"
    head.mkdir()
    git(head, env, "init")
    git(head, env, "remote", "add", "origin", source)
    git(head, env, "fetch", "--depth=256", "origin", spec.head_sha, spec.trunk_sha)
    git(head, env, "checkout", "--detach", spec.head_sha)
    base_sha = merge_base(head, env, spec.head_sha, spec.trunk_sha)
    changed = git(
        head, env, "diff", "--name-only", "--diff-filter=AM", base_sha, spec.head_sha
    ).splitlines()
    paths = [
        p
        for p in changed
        if p.startswith("tests/")
        and PurePosixPath(p).name.startswith("test_")
        and p.endswith(".py")
    ]
    if len(paths) > 20:
        raise ValueError(
            "More than twenty changed test modules need a narrower verification"
        )
    for path in paths:
        if git(head, env, "ls-tree", "HEAD", "--", path).split()[0] != "100644":
            raise ValueError("Changed tests must be regular files")
    result = {
        "spec": spec.model_dump(mode="json"),
        "spec_sha256": digest(spec.model_dump(mode="json")),
        "merge_base_sha": base_sha,
        "baseline_sha": spec.trunk_sha,
        "changed_tests": paths,
        "usage": {
            "model_requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "compute_cost": None,
        },
    }
    if not paths and not spec.probes:
        return {
            **result,
            "status": "evidence_needed",
            "reason": "No changed test modules or independent probe; author a regression before verification.",
        }
    base = directory / "base"
    git(head, env, "worktree", "add", "--detach", str(base), spec.trunk_sha)
    for path in paths:
        target = base / path
        relative_parents = [base / parent for parent in PurePosixPath(path).parents]
        if target.is_symlink() or any(p.is_symlink() for p in relative_parents):
            raise ValueError("Test overlay must not follow symlinks")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((head / path).read_bytes())
    for repo in (base, head):
        for path, source in spec.probes.items():
            target = repo / path
            if target.exists() or (repo / "tests/agentic_probes").is_symlink():
                raise ValueError("Independent probes must not replace existing files")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
    selected = [*paths, *spec.probes]
    baseline = run_tests(base, selected, env, directory / "base.xml")
    candidate = run_tests(head, selected, env, directory / "head.xml")
    parents = sorted({str(PurePosixPath(p).parent) for p in paths})
    tracked = git(
        head, env, "ls-tree", "-r", "--name-only", "HEAD", "--", "tests"
    ).splitlines()
    neighbors = [
        path
        for path in tracked
        if str(PurePosixPath(path).parent) in parents
        and PurePosixPath(path).name.startswith("test_")
        and path.endswith(".py")
        and path not in paths
    ]
    limitations = [
        "Current target-branch composition and full-suite/static validation remain separate checks.",
        "Author tests are not independent contract verification; supplied probes are identified separately.",
    ]
    if len(neighbors) > 20:
        limitations.append(
            f"Adjacent verification omitted: {len(neighbors)} sibling modules exceeds the twenty-module budget."
        )
        neighbors = []
    adjacent = (
        run_tests(head, neighbors, env, directory / "adjacent.xml")
        if neighbors
        else {
            "status": "not_run",
            "cases": [],
            "reason": "No sibling modules within the verification budget",
        }
    )
    status = "verification_recorded"
    if any(
        r["status"] == "environment_failure" for r in (baseline, candidate, adjacent)
    ):
        status = "environment_failure"
    return {
        **result,
        "status": status,
        "base": baseline,
        "head": candidate,
        "adjacent": adjacent,
        "adjacent_tests": neighbors,
        "comparison": outcomes(baseline, candidate),
        "limitations": limitations,
    }


@flow(name="fastmcp-verify-contribution", timeout_seconds=3000)
def verify_contribution(spec: VerificationSpec) -> dict:
    with tempfile.TemporaryDirectory(prefix="fastmcp-contribution-") as temporary:
        directory = Path(temporary)
        install_tools(directory / "tools")
        result = isolated_verification(spec, directory)
    with get_client(sync_client=True) as client:
        client.create_artifact(
            ArtifactCreate(
                key=f"fastmcp-pr-{spec.pr_number}-{digest(spec.model_dump(mode='json'))[:16]}",
                flow_run_id=flow_run.id,
                type="fastmcp-contribution-verification",
                data=result,
            )
        )
    return result


if __name__ == "__main__":
    import argparse
    import resource

    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (2400, 2400))
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))

    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    args = parser.parse_args()
    spec = VerificationSpec.model_validate_json(args.spec.read_text())
    with tempfile.TemporaryDirectory(prefix="fastmcp-contribution-") as temporary:
        result = verify(spec, Path(temporary))
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered)
