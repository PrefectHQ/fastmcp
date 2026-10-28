from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from prefect import get_run_logger, task
from prefect.exceptions import MissingContextError


def run_command(
    command: list[str],
    directory: Path,
    timeout: int = 900,
    environment_overrides: dict[str, str] | None = None,
) -> None:
    try:
        logger = get_run_logger()
    except MissingContextError:
        logger = logging.getLogger(__name__)
    logger.info("Running %s", " ".join(command))
    environment = dict(os.environ)
    environment.update(environment_overrides or {})
    for name in (
        "PREFECT_API_KEY",
        "PREFECT_API_AUTH_STRING",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        environment.pop(name, None)
    result = subprocess.run(
        command,
        cwd=directory,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    logger.info("%s", result.stdout[-24000:])
    result.check_returncode()


def apply_patch(repository: Path, patch: str) -> list[str]:
    if not patch.strip() or len(patch.encode()) > 200_000:
        raise ValueError("Expected a nonempty patch of at most 200 KB")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch") as candidate:
        candidate.write(patch)
        candidate.flush()
        run_command(
            ["git", "apply", "--recount", "--check", candidate.name], repository
        )
        run_command(["git", "apply", "--recount", candidate.name], repository)
    run_command(["git", "add", "--intent-to-add", "."], repository)
    paths = (
        subprocess.check_output(["git", "diff", "--name-only", "-z"], cwd=repository)
        .decode()
        .split("\0")
    )
    paths = [path for path in paths if path]
    allowed = ("docs/", "tests/", "fastmcp_slim/", "fastmcp_remote/", "fastmcp_tasks/")
    generated = (
        "docs/python-sdk/",
        "docs/public/schemas/",
        "fastmcp_slim/fastmcp/utilities/mcp_server_config/v1/schema.json",
    )
    if not paths or any(
        not path.startswith(allowed) or path.startswith(generated) for path in paths
    ):
        raise ValueError(
            "Patch must only change maintained FastMCP source, tests, or docs"
        )
    run_command(["git", "diff", "--check"], repository)
    return paths


def validate_tree(repository: Path, paths: list[str]) -> None:
    before = subprocess.check_output(
        ["git", "diff", "HEAD", "--binary"], cwd=repository
    )
    run_command(["uv", "sync"], repository)
    run_command(["uv", "run", "pytest", "-n", "auto"], repository, timeout=1200)
    run_command(["uv", "run", "prek", "run", "--all-files"], repository)
    if any(path.startswith("docs/") for path in paths):
        run_command(["uv", "run", "pytest", "tests/docs", "-n", "0"], repository)
        run_command(
            ["npx", "--yes", "mint@latest", "broken-links"], repository / "docs"
        )
    run_command(["git", "diff", "--check"], repository)
    if (
        subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=repository)
        != before
    ):
        raise ValueError("Validation changed the candidate; submit the corrected patch")


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def install_tools(directory: Path) -> None:
    downloads = (
        (
            "uv",
            "https://github.com/astral-sh/uv/releases/download/0.11.14/",
            "uv-x86_64-unknown-linux-gnu.tar.gz",
            "uv-x86_64-unknown-linux-gnu.tar.gz.sha256",
        ),
        (
            "gitleaks",
            "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/",
            "gitleaks_8.30.1_linux_x64.tar.gz",
            "gitleaks_8.30.1_checksums.txt",
        ),
        (
            "node",
            "https://nodejs.org/dist/v22.16.0/",
            "node-v22.16.0-linux-x64.tar.xz",
            "SHASUMS256.txt",
        ),
    )
    directory.mkdir(exist_ok=True)
    for executable, prefix, filename, checksums in downloads:
        if shutil.which(executable):
            continue
        with urllib.request.urlopen(prefix + checksums, timeout=60) as response:
            lines = response.read().decode().splitlines()
        expected = next(
            line.split()[0]
            for line in lines
            if line.strip()
            and (len(line.split()) == 1 or line.split()[-1].lstrip("*") == filename)
        )
        with urllib.request.urlopen(prefix + filename, timeout=120) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for {filename}")
        archive = directory / filename
        archive.write_bytes(content)
        with tarfile.open(archive) as bundle:
            bundle.extractall(directory, filter="data")
    bins = [directory, directory / "uv-x86_64-unknown-linux-gnu"]
    bins.append(directory / "node-v22.16.0-linux-x64" / "bin")
    os.environ["PATH"] = (
        os.pathsep.join(map(str, bins)) + os.pathsep + os.environ["PATH"]
    )
    for executable in ("uv", "gitleaks", "node", "npx"):
        if not shutil.which(executable):
            raise RuntimeError(f"Missing required executable: {executable}")


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def fetch_source(directory: Path, base_sha: str) -> None:
    if not (directory / ".git").exists():
        run_command(["git", "init", str(directory)], directory.parent)
        run_command(
            [
                "git",
                "remote",
                "add",
                "origin",
                "https://github.com/PrefectHQ/fastmcp.git",
            ],
            directory,
        )
    run_command(["git", "fetch", "--depth=1", "origin", base_sha], directory)
    run_command(["git", "checkout", "--detach", "FETCH_HEAD"], directory)
