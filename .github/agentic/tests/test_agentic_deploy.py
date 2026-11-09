import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from prefect.server.events.schemas.automations import AutomationCore

ROOT = Path(__file__).resolve().parents[1]
TRIGGERS = ("fastmcp-triage-on-github-issue", "fastmcp-triage-on-github-pr")

FAKE_UV = """\
import json, os, pathlib, sys
state = pathlib.Path(os.environ["STATE"])
args = sys.argv[sys.argv.index("--no-prompt") + 1 :]
automations = json.loads((state / "automations.json").read_text())
if args[0] == "deploy":
    (state / "deploy.json").write_text(
        json.dumps({"ref": os.environ["FASTMCP_AGENTIC_REF"], "args": args[1:]})
    )
    sys.exit(0)
method, path = args[1], args[2]
body = json.loads(pathlib.Path(args[4][1:]).read_text()) if len(args) > 4 else None
if path == "/execution-plans/validate":
    print(json.dumps({"valid": True}))
elif path == "/automations/filter":
    print(json.dumps(automations))
elif path == "/flow_runs/filter":
    (state / "active-filter.json").write_text(json.dumps(body))
    print(os.environ.get("ACTIVE_RUNS", "[]"))
elif method == "PUT" and path.startswith("/automations/"):
    automation_id = path.rsplit("/", 1)[1]
    (state / f"put-{automation_id}.json").write_text(json.dumps(body))
    for automation in automations:
        if automation["id"] == automation_id:
            automation.update(body)
    (state / "automations.json").write_text(json.dumps(automations))
else:
    sys.exit(f"unexpected call {args}")
"""

FAKE_GH = """\
import sys
print(sys.argv[sys.argv.index("api") + 1].rsplit("/", 1)[1])
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def automation(name: str, number: int, source_sha: str) -> dict:
    return {
        "id": f"{number}1111111-1111-4111-8111-111111111111",
        "created": "2026-11-01T00:00:00Z",
        "workspace": "w",
        "name": name,
        "description": "",
        "enabled": True,
        "trigger": {
            "type": "event",
            "posture": "Reactive",
            "threshold": 1,
            "within": 60,
            "expect": ["fastmcp.github.issues.opened"],
            "match": {},
            "match_related": {},
            "after": [],
            "for_each": ["prefect.resource.id"],
        },
        "actions": [
            {
                "type": "run-deployment",
                "source": "selected",
                "deployment_id": "22222222-2222-4222-8222-222222222222",
                "job_variables": None,
                "parameters": {
                    "controller_id": "c",
                    "runtime": {"source_sha": source_sha, "plans": {}},
                },
            }
        ],
        "actions_on_trigger": [],
        "actions_on_resolve": [],
    }


@pytest.fixture
def stack(tmp_path):
    repo = tmp_path / "repo"
    agentic = repo / ".github/agentic"
    agentic.mkdir(parents=True)
    # The checkout under deploy has no copy of the driver, like a rollback target
    # that predates it.
    shutil.copytree(ROOT / "workflows", agentic / "workflows")
    shutil.copy(ROOT / "prefect.yaml", agentic)
    git(repo, "init", "-q")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "live")
    live = git(repo, "rev-parse", "HEAD")

    state = tmp_path / "state"
    state.mkdir()
    automations = [automation(name, i, live) for i, name in enumerate(TRIGGERS, 1)]
    (state / "automations.json").write_text(json.dumps(automations))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("uv", FAKE_UV), ("gh", FAKE_GH)):
        executable = bin_dir / name
        executable.write_text(f"#!{sys.executable}\n{source}")
        executable.chmod(0o755)

    def commit(path: str, content: str) -> str:
        (agentic / path).write_text(content)
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", path)
        return git(repo, "rev-parse", "HEAD")

    def pin(name: str, source_sha: str) -> None:
        current = json.loads((state / "automations.json").read_text())
        for item in current:
            if item["name"] == name:
                item["actions"][0]["parameters"]["runtime"]["source_sha"] = source_sha
        (state / "automations.json").write_text(json.dumps(current))

    def deploy(active_runs: list | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(ROOT / "scripts/agentic_stack.sh"), "deploy"],
            env={
                **os.environ,
                "FASTMCP_AGENTIC_DIR": str(agentic),
                "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                "STATE": str(state),
                "ACTIVE_RUNS": json.dumps(active_runs or []),
                "PREFECT_API_URL": "https://api.prefect.cloud/api/accounts/a/workspaces/w",
            },
            capture_output=True,
            text=True,
        )

    deploy.pin = pin
    return commit, deploy, state, live


def test_deploy_registers_and_repins_both_automations(stack):
    commit, deploy, state, live = stack
    revision = commit("notes.txt", "code change\n")

    result = deploy()

    assert result.returncode == 0, result.stderr
    assert f"Deployed {revision} (was {live})" in result.stdout
    registered = json.loads((state / "deploy.json").read_text())
    assert registered["ref"] == revision
    names = registered["args"][registered["args"].index("prefect.yaml") + 1 :][1::2]
    assert set(names) == set(
        json.loads((state / "active-filter.json").read_text())["deployments"]["name"][
            "any_"
        ]
    )
    puts = sorted(state.glob("put-*.json"))
    assert len(puts) == 2
    for put in puts:
        body = json.loads(put.read_text())
        assert body["actions"][0]["parameters"]["runtime"]["source_sha"] == revision
        assert "id" not in body and "workspace" not in body
        AutomationCore.model_validate(body)


def test_deploy_retry_finishes_a_partial_repin(stack):
    commit, deploy, state, live = stack
    revision = commit("notes.txt", "code change\n")
    deploy.pin(TRIGGERS[0], revision)

    result = deploy()

    assert result.returncode == 0, result.stderr
    assert f"Deployed {revision} (was {live})" in result.stdout
    assert len(list(state.glob("put-*.json"))) == 2


def test_deploy_refuses_automations_on_several_other_commits(stack):
    commit, deploy, state, _ = stack
    other = commit("other.txt", "x\n")
    commit("notes.txt", "code change\n")
    deploy.pin(TRIGGERS[0], other)

    result = deploy()

    assert result.returncode != 0
    assert "pinned to several commits" in result.stderr
    assert not (state / "deploy.json").exists()


def test_deploy_is_a_no_op_for_the_live_commit(stack):
    _, deploy, state, live = stack

    result = deploy()

    assert result.returncode == 0, result.stderr
    assert f"Already deployed {live}" in result.stdout
    assert not (state / "deploy.json").exists()


def test_deploy_waits_for_in_flight_runs(stack):
    commit, deploy, state, _ = stack
    commit("notes.txt", "code change\n")

    result = deploy([{"name": "fastmcp-stack-intake-1", "state_type": "RUNNING"}])

    assert result.returncode == 1
    assert "fastmcp-stack-intake-1 RUNNING" in result.stderr
    assert not (state / "deploy.json").exists()
    assert not list(state.glob("put-*.json"))


def test_deploy_refuses_changed_execution_plans(stack):
    commit, deploy, state, _ = stack
    commit("workflows/fastmcp-stack-assess.plan.json", "{}\n")

    result = deploy()

    assert result.returncode == 1
    assert "Execution plans changed" in result.stderr
    assert not (state / "deploy.json").exists()
