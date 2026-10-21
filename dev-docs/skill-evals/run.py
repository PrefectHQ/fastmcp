"""Run one isolated-context Pi authoring trial and independent protocol checks."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=["catalog", "inventory", "composition"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--skill", type=Path)
    parser.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    workspace.mkdir()
    fixture = root / "fixtures" / args.case
    for source in fixture.glob("*.py"):
        shutil.copy(source, workspace / source.name)
    expected = "3.4.11" if args.case == "composition" else "4.1.3"
    python = args.python.absolute()
    version = subprocess.check_output(
        [str(python), "-c", "import fastmcp; print(fastmcp.__version__)"], text=True
    ).strip()
    if version != expected:
        parser.error(f"{args.case} requires FastMCP {expected}, got {version}")
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "authoring-trial"\nversion = "0.0.0"\n'
        f'dependencies = ["fastmcp=={expected}"]\n'
    )
    prompt = (fixture / "prompt.txt").read_text()
    (output / "prompt.txt").write_text(prompt)
    pi = shutil.which("pi")
    if pi is None:
        parser.error("pi must be on PATH")
    command = [
        pi,
        "--provider",
        "openai-codex",
        "--model",
        args.model,
        "--thinking",
        "medium",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--no-themes",
        "--no-approve",
        "--offline",
        "--no-session",
        "--mode",
        "json",
        "--print",
    ]
    skill_hash = None
    if args.skill:
        snapshot = output / "skill" / "SKILL.md"
        snapshot.parent.mkdir()
        shutil.copy(args.skill, snapshot)
        skill_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        command.extend(["--skill", str(snapshot)])
    command.append(prompt)
    env = {
        key: os.environ[key]
        for key in ["HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG"]
        if key in os.environ
    }
    env.update(
        PATH=os.pathsep.join(
            [
                str(python.parent),
                str(Path(pi).parent),
                os.environ.get("PATH", os.defpath),
            ]
        ),
        VIRTUAL_ENV=str(python.parent.parent),
        PI_TELEMETRY="0",
    )
    started = time.monotonic()
    with (
        (output / "transcript.jsonl").open("w") as out,
        (output / "stderr.txt").open("w") as err,
    ):
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=env,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            process.wait(timeout=args.timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    elapsed = round(time.monotonic() - started, 2)
    metadata = {
        "model": args.model,
        "fastmcp": version,
        "skill_sha256": skill_hash,
        "elapsed_seconds": elapsed,
        "exit_code": process.returncode,
        "timed_out": timed_out,
    }
    (output / "run.json").write_text(json.dumps(metadata, indent=2))
    script = (
        "acceptance_composition.py" if args.case == "composition" else "acceptance.py"
    )
    acceptance = [str(python), str(root / script), str(workspace)]
    if args.case != "composition":
        acceptance.append(args.case)
    result = subprocess.run(
        acceptance, cwd=workspace, capture_output=True, text=True, timeout=60
    )
    (output / "score.json").write_text(result.stdout)
    (output / "score.stderr").write_text(result.stderr)
    supplemental_code = 0
    if args.case == "composition":
        supplemental = subprocess.run(
            [str(python), str(root / "check_prompt_links.py"), str(workspace)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        supplemental_code = supplemental.returncode
        (output / "supplemental.json").write_text(supplemental.stdout)
        (output / "supplemental.stderr").write_text(supplemental.stderr)
    print(json.dumps(metadata, indent=2))
    print(result.stdout)
    if timed_out or process.returncode or result.returncode or supplemental_code:
        sys.exit(1)


if __name__ == "__main__":
    main()
