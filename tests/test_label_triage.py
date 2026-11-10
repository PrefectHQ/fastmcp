import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


@pytest.fixture
def triage_steps():
    workflow = yaml.load(
        (ROOT / ".github/workflows/marvin-label-triage.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    return {
        step["id"]: step
        for step in workflow["jobs"]["label-issue-or-pr"]["steps"]
        if "id" in step
    }


@pytest.mark.skipif(
    sys.platform == "win32", reason="Exercises the Ubuntu shell workflow"
)
@pytest.mark.parametrize("failure", [None, "api", "json"])
def test_label_collection_is_paginated_and_fails_before_prompt(
    triage_steps, tmp_path, failure
):
    labels = [{"name": f"label-{i}", "description": "A label"} for i in range(140)]
    labels[-1]["description"] = "A description\nEOF\n$(touch unexpected)"
    response = tmp_path / "response.json"
    response.write_text(
        "invalid JSON"
        if failure == "json"
        else json.dumps([labels[:100], labels[100:]]) + "\n"
    )
    calls = tmp_path / "gh-args.txt"
    output = tmp_path / "output.txt"
    gh = tmp_path / "gh"
    gh.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$GH_ARGS"\n'
        'if [ "$GH_FAIL" = 1 ]; then exit 1; fi\n'
        'cat "$GH_RESPONSE"\n'
    )
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "GH_TOKEN": "test-token",
        "REPO": "PrefectHQ/fastmcp",
        "GH_ARGS": str(calls),
        "GH_RESPONSE": str(response),
        "GH_FAIL": "1" if failure == "api" else "0",
        "GITHUB_OUTPUT": str(output),
    }
    assert triage_steps["repository-labels"]["shell"] == "bash"
    result = subprocess.run(
        [
            "bash",
            "-e",
            "-o",
            "pipefail",
            "-c",
            triage_steps["repository-labels"]["run"],
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
    )
    args = calls.read_text().splitlines()
    assert args[:4] == [
        "api",
        "--paginate",
        "--slurp",
        "repos/PrefectHQ/fastmcp/labels?per_page=100",
    ]
    assert len(args) == 4
    if failure:
        assert result.returncode != 0
        assert not output.exists()
        return
    assert result.returncode == 0, result.stderr
    lines = output.read_text().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("labels=")
    supplied = lines[0].removeprefix("labels=")
    assert json.loads(supplied) == labels
    prompt = triage_steps["triage-prompt"]["run"].replace(
        "${{ steps.repository-labels.outputs.labels }}", supplied
    )
    output.unlink()
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", prompt],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert supplied in output.read_text()
    assert not (tmp_path / "unexpected").exists()
