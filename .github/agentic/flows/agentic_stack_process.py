from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from flows.agentic_verification_process import isolated_process

if TYPE_CHECKING:
    from flows.agentic_stack import StackSpec


def isolated_stack_validation(spec: StackSpec, directory: Path) -> dict:
    tools = directory / "tools"
    return isolated_process(
        spec,
        directory,
        "flows.agentic_stack_process",
        6300,
        (tools, tools / "node-v22.16.0-linux-x64/bin"),
    )


def validate(spec: StackSpec, directory: Path) -> dict:
    from flows.agentic_stack import assemble_stack
    from flows.agentic_validate_patch import fetch_source, run_command

    repository = directory / "fastmcp"
    fetch_source.fn(repository, spec.trunk_sha)
    run_command(["uv", "sync"], repository)
    run_command(["uv", "run", "prek", "install"], repository)
    return assemble_stack(repository, spec)


if __name__ == "__main__":
    import resource

    from flows.agentic_stack import StackSpec

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
    spec = StackSpec.model_validate_json(Path(sys.argv[1]).read_text())
    with tempfile.TemporaryDirectory(
        prefix="fastmcp-stack-", dir=os.environ["TMPDIR"]
    ) as temporary:
        result = validate(spec, Path(temporary))
    sys.stdout.write(json.dumps(result) + "\n")
