from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from prefect import task
from prefect.blocks.system import Secret
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import ArtifactFilter, FlowRunFilter
from prefect.utilities.asyncutils import run_coro_as_sync
from pydantic import BaseModel, ConfigDict, Field, model_validator

from flows.agentic_assessment_cache import assessment_candidates, assessment_key
from flows.agentic_stack import digest
from flows.agentic_stack_context import assessment_context as project_assessment
from flows.agentic_stack_plans import OUTPUTS

ROOT = Path(__file__).resolve().parents[1]


def secret_sync(name: str) -> str:
    value = run_coro_as_sync(Secret.aload(name)).get()
    if not isinstance(value, str):
        raise TypeError("Expected a text Secret block")
    return value


def run_json(
    command: list[str], environment: dict[str, str] | None, timeout: int
) -> Any:
    result = subprocess.run(
        command,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(command[:4])} exited {result.returncode}: "
            f"{result.stderr.strip()[-500:]}"
        )
    return json.loads(result.stdout)


def gh_api(endpoint: str, environment: dict[str, str], timeout: int) -> Any:
    return run_json(["gh", "api", endpoint], environment, timeout)


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def intake_tools(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    downloads = (
        (
            "gh",
            "https://github.com/cli/cli/releases/download/v2.96.0/gh_2.96.0_linux_amd64.tar.gz",
            "83d5c2ccad5498f58bf6368acb1ab32588cf43ab3a4b1c301bf36328b1c8bd60",
        ),
        (
            "jq",
            "https://github.com/jqlang/jq/releases/download/jq-1.8.1/jq-linux-amd64",
            "020468de7539ce70ef1bceaf7cde2e8c4f2ca6c3afb84642aabc5c97d9fc2a0d",
        ),
    )
    for name, url, expected in downloads:
        if shutil.which(name):
            continue
        if platform.system() != "Linux" or platform.machine() not in {
            "x86_64",
            "amd64",
        }:
            raise RuntimeError("Install gh and jq for this development platform")
        with urllib.request.urlopen(url, timeout=120) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for {name}")
        destination = directory / name
        destination.write_bytes(content)
        if name == "gh":
            with tarfile.open(destination) as archive:
                archive.extractall(directory, filter="data")
            destination.unlink()
        else:
            destination.chmod(0o755)
    os.environ["PATH"] = os.pathsep.join(
        [
            str(directory),
            str(directory / "gh_2.96.0_linux_amd64/bin"),
            os.environ["PATH"],
        ]
    )
    if any(shutil.which(name) is None for name in ("gh", "jq", "git")):
        raise RuntimeError("Controller requires gh, jq and git")


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    flow_id: UUID
    version_id: UUID


class Runtime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plans: dict[str, AgentPlan]
    validator_id: UUID
    verification_id: UUID | None = None
    triage_plan: AgentPlan | None = None
    triage_verify_id: UUID | None = None
    triage_finalize_id: UUID | None = None
    source_sha: str = Field(pattern=r"^[a-f0-9]{40}$")

    @model_validator(mode="after")
    def check_roles(self) -> Runtime:
        if set(self.plans) != set(OUTPUTS):
            raise ValueError("Expected every stack agent role")
        native = (self.triage_plan, self.triage_verify_id, self.triage_finalize_id)
        if any(value is not None for value in native) and any(
            value is None for value in native
        ):
            raise ValueError("Native triage requires its plan and both deployments")
        return self


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def api(method: str, path: str, body: dict | None = None) -> dict:
    with get_client(sync_client=True) as client:
        response = client._client.request(method, path, json=body)
        response.raise_for_status()
        return response.json()


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def owner_alive(owner: UUID, deadline: datetime) -> None:
    if datetime.now(UTC) >= deadline:
        raise TimeoutError("Batch deadline expired; no further work or publication")
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(owner)
    if run.state is None or not run.state.is_running():
        raise ValueError("Batch execution no longer owns publication")


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def saved(owner: UUID, key: str) -> dict | None:
    with get_client(sync_client=True) as client:
        previous = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [key]}),
            flow_run_filter=FlowRunFilter(id={"any_": [owner]}),
            limit=2,
        )
    if not previous:
        return None
    if len(previous) != 1 or not isinstance(previous[0].data, dict):
        raise ValueError("Expected one immutable controller record")
    return previous[0].data


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def record(owner: UUID, key: str, data: dict) -> str:
    with get_client(sync_client=True) as client:
        previous = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [key]}),
            flow_run_filter=FlowRunFilter(id={"any_": [owner]}),
            limit=2,
        )
        if previous:
            if len(previous) != 1 or previous[0].data != data:
                raise ValueError("Conflicting durable controller record")
            return str(previous[0].id)
        artifact = client.create_artifact(
            ArtifactCreate(
                key=key, flow_run_id=owner, type="fastmcp-stack-controller", data=data
            )
        )
    return str(artifact.id)


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def collect(issue: int, branch: str, publication: bool = False) -> dict:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"PREFECT_API_KEY", "PREFECT_API_AUTH_STRING", "GITHUB_TOKEN"}
    }
    environment["GH_TOKEN"] = secret_sync("fastmcp-triage-gh-token")
    return run_json(
        [
            "bash",
            str(ROOT / "scripts/collect_fastmcp_stack_evidence.sh"),
            str(issue),
            branch,
            "publication" if publication else "route",
        ],
        environment,
        240,
    )


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def skills(base_sha: str) -> dict:
    return run_json(
        [
            "bash",
            str(ROOT / "scripts/load_fastmcp_skills.sh"),
            base_sha,
            "https://github.com/PrefectHQ/fastmcp.git",
            str(ROOT / "workflows/stack-skills.json"),
        ],
        None,
        180,
    )


@task(retries=2, retry_delay_seconds=[2, 5], persist_result=False)
def assessment_context(item: dict) -> dict:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"PREFECT_API_KEY", "PREFECT_API_AUTH_STRING", "GITHUB_TOKEN"}
    }
    environment["GH_TOKEN"] = secret_sync("fastmcp-triage-gh-token")
    cache: dict[str, dict] = {}

    def read(endpoint: str) -> dict:
        if endpoint not in cache:
            cache[endpoint] = gh_api(endpoint, environment, 30)
        return cache[endpoint]

    return project_assessment(item, read)


def judge(
    role: str,
    plan: AgentPlan,
    instructions: dict,
    context: dict,
    owner: UUID,
    deadline: datetime,
    key: str,
) -> tuple[dict, str]:
    owner_alive(owner, deadline)
    parameters = {"instructions": instructions, "context": context}
    cache_key = (
        assessment_key(plan.flow_id, plan.version_id, parameters)
        if role == "assess"
        else None
    )
    record(
        owner,
        f"{key}-intent",
        {"plan": plan.model_dump(mode="json"), "parameters_sha256": digest(parameters)},
    )
    active = api("GET", f"/flows/{plan.flow_id}/execution-plan")
    if active["active_version"]["id"] != str(plan.version_id):
        raise ValueError("Agent plan changed since batch configuration")
    previous = saved(owner, key)
    if previous is not None:
        if previous["parameters_sha256"] != digest(parameters):
            raise ValueError("Stored agent result used different inputs")
        return OUTPUTS[role].model_validate(previous["result"]).model_dump(
            mode="json"
        ), previous["run_id"]
    if role == "assess":
        for candidate_id in assessment_candidates(
            plan.flow_id, plan.version_id, parameters
        ):
            candidate = api("GET", f"/flow_runs/{candidate_id}/execution-plan")
            if candidate["status"] != "completed" or candidate["snapshot"][
                "execution_plan_version_id"
            ] != str(plan.version_id):
                continue
            output = api(
                "GET", f"/flow_runs/{candidate_id}/execution-plan/outputs/result"
            )["decision"]
            result = OUTPUTS[role].model_validate(output).model_dump(mode="json")
            if result["decision"] == "environment_failure":
                continue
            run_id = str(candidate_id)
            candidate_run = api("GET", f"/flow_runs/{run_id}")
            reused = candidate_run.get("idempotency_key") != key + "-" + digest(
                parameters
            )
            admission = {
                "role": role,
                "run_id": run_id,
                "plan_version": str(plan.version_id),
            }
            if reused:
                admission["reused"] = True
            record(
                owner,
                f"{key}-agent-run",
                admission,
            )
            if cache_key is not None:
                record(owner, cache_key, {"run_id": run_id})
            record(
                owner,
                key,
                {
                    "run_id": run_id,
                    "parameters_sha256": digest(parameters),
                    "result": result,
                    "reused": reused,
                },
            )
            return result, run_id
    created = api(
        "POST",
        f"/flows/{plan.flow_id}/execution-plan/runs",
        {
            "name": key,
            "parameters": parameters,
            "idempotency_key": key + "-" + digest(parameters),
        },
    )
    run_id = created["id"]
    record(
        owner,
        f"{key}-agent-run",
        {"role": role, "run_id": run_id, "plan_version": str(plan.version_id)},
    )
    while True:
        owner_alive(owner, deadline)
        observation = api("GET", f"/flow_runs/{run_id}/execution-plan")
        if observation["snapshot"]["execution_plan_version_id"] != str(plan.version_id):
            raise ValueError("Agent run selected a different plan version")
        if observation["status"] in {"completed", "failed", "cancelled", "blocked"}:
            break
        time.sleep(5)
    if observation["status"] != "completed":
        record(owner, f"{key}-failure", {"run_id": run_id, "observation": observation})
        raise RuntimeError(
            f"Agent role {role} ended {observation['status']}; run {run_id}"
        )
    output = api("GET", f"/flow_runs/{run_id}/execution-plan/outputs/result")[
        "decision"
    ]
    result = OUTPUTS[role].model_validate(output).model_dump(mode="json")
    if cache_key is not None and result["decision"] != "environment_failure":
        record(owner, cache_key, {"run_id": run_id})
    record(
        owner,
        key,
        {"run_id": run_id, "parameters_sha256": digest(parameters), "result": result},
    )
    return result, run_id
