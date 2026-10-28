from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import UUID

from prefect import flow, task
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import ArtifactFilter, FlowRunFilter
from prefect.runtime import flow_run
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flows.agentic_validate_patch import (
    apply_patch,
    fetch_source,
    install_tools,
    run_command,
    validate_tree,
)


class StackLayer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_number: int = Field(gt=0)
    patch: str = Field(min_length=1, max_length=200_000)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    credits: list[str] = Field(max_length=10)
    evidence_revision: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("title")
    @classmethod
    def one_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value or not value.strip():
            raise ValueError("Expected a one-line commit title")
        return value

    @field_validator("credits")
    @classmethod
    def credit_trailers(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(
            re.fullmatch(r"Co-authored-by: [^<>\r\n]+ <[^<>\s]+@[^<>\s]+>", credit)
            is None
            for credit in value
        ):
            raise ValueError("Expected unique co-author trailers")
        return value


class StackSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(pattern=r"^[a-z0-9-]{1,50}$")
    group: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    trunk_ref: str = Field(pattern=r"^(main|release/[23]\.x)$")
    trunk_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    created_at: datetime
    publisher_login: str = Field(pattern=r"^[A-Za-z0-9-]+$")
    publisher_id: int = Field(gt=0)
    layers: list[StackLayer] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def check_layers(self) -> StackSpec:
        if self.created_at.tzinfo is None:
            raise ValueError("Stack creation time requires a timezone")
        if len({layer.issue_number for layer in self.layers}) != len(self.layers):
            raise ValueError("Stack layers must address different issues")
        return self

    def branch(self, layer: StackLayer) -> str:
        return f"agentic/{self.batch_id}/stack-{self.group}/issue-{layer.issue_number}"


def git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=repository, text=True
    ).strip()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def assemble_stack(
    repository: Path,
    spec: StackSpec,
    check: Callable[[Path, list[str]], None] = validate_tree,
) -> dict:
    if git(repository, "rev-parse", "HEAD") != spec.trunk_sha:
        raise ValueError("Checkout is not the specified trunk revision")
    if git(repository, "status", "--porcelain"):
        raise ValueError("Stack assembly requires a clean checkout")
    identity = {
        "GIT_AUTHOR_NAME": spec.publisher_login,
        "GIT_COMMITTER_NAME": spec.publisher_login,
        "GIT_AUTHOR_EMAIL": f"{spec.publisher_id}+{spec.publisher_login}@users.noreply.github.com",
        "GIT_COMMITTER_EMAIL": f"{spec.publisher_id}+{spec.publisher_login}@users.noreply.github.com",
        "GIT_AUTHOR_DATE": spec.created_at.isoformat(),
        "GIT_COMMITTER_DATE": spec.created_at.isoformat(),
    }
    parent = spec.trunk_sha
    base_ref = spec.trunk_ref
    layers = []
    for layer in spec.layers:
        branch = spec.branch(layer)
        run_command(["git", "switch", "-c", branch], repository)
        paths = apply_patch(repository, layer.patch)
        run_command(["git", "add", "--all"], repository)
        tree = git(repository, "write-tree")
        if tree == git(repository, "rev-parse", "HEAD^{tree}"):
            raise ValueError("A stack layer must contain a real change")
        check(repository, paths)
        run_command(["git", "add", "--all"], repository)
        if git(repository, "write-tree") != tree:
            raise ValueError("Validation changed a stack layer")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt") as message:
            message.write(
                (layer.title + "\n\n" + "\n".join(layer.credits)).rstrip() + "\n"
            )
            message.flush()
            run_command(
                ["git", "commit", "--file", message.name],
                repository,
                environment_overrides=identity,
            )
        head = git(repository, "rev-parse", "HEAD")
        if (
            git(repository, "status", "--porcelain")
            or git(repository, "rev-parse", "HEAD^{tree}") != tree
        ):
            raise ValueError("Commit hooks changed the validated layer")
        if git(repository, "show", "-s", "--format=%P", head) != parent:
            raise ValueError("Stack layer has an unexpected parent")
        layers.append(
            {
                "issue_number": layer.issue_number,
                "branch": branch,
                "base_ref": base_ref,
                "parent_sha": parent,
                "head_sha": head,
                "tree_sha": tree,
                "patch_sha256": hashlib.sha256(layer.patch.encode()).hexdigest(),
            }
        )
        parent, base_ref = head, branch
    with tempfile.TemporaryDirectory(prefix="fastmcp-stack-bundle-") as temporary:
        path = Path(temporary) / "stack.bundle"
        run_command(
            ["git", "bundle", "create", str(path), f"{spec.trunk_sha}..HEAD"],
            repository,
        )
        content = path.read_bytes()
    if len(content) > 1_000_000:
        raise ValueError("Validated stack bundle exceeds 1 MB")
    return {
        "schema_version": 1,
        "spec": spec.model_dump(mode="json"),
        "layers": layers,
        "spec_sha256": digest(spec.model_dump(mode="json")),
        "bundle_sha256": hashlib.sha256(content).hexdigest(),
        "bundle_base64": base64.b64encode(content).decode(),
        "status": "validated",
    }


def restore_stack(repository: Path, artifact: dict) -> StackSpec:
    spec = StackSpec.model_validate(artifact["spec"])
    if artifact.get("schema_version") != 1 or artifact.get("status") != "validated":
        raise ValueError("Expected a validated stack artifact")
    if digest(spec.model_dump(mode="json")) != artifact["spec_sha256"]:
        raise ValueError("Stack specification digest mismatch")
    content = base64.b64decode(artifact["bundle_base64"], validate=True)
    if (
        len(content) > 1_000_000
        or hashlib.sha256(content).hexdigest() != artifact["bundle_sha256"]
    ):
        raise ValueError("Stack bundle digest mismatch")
    if len(artifact["layers"]) != len(spec.layers):
        raise ValueError("Stack manifest has a different layer count")
    with tempfile.NamedTemporaryFile(suffix=".bundle") as bundle:
        bundle.write(content)
        bundle.flush()
        run_command(["git", "bundle", "verify", bundle.name], repository)
        run_command(["git", "fetch", bundle.name, "HEAD"], repository)
    parent = spec.trunk_sha
    base_ref = spec.trunk_ref
    for layer, manifest in zip(spec.layers, artifact["layers"], strict=True):
        expected = {
            "issue_number": layer.issue_number,
            "branch": spec.branch(layer),
            "base_ref": base_ref,
            "parent_sha": parent,
            "patch_sha256": hashlib.sha256(layer.patch.encode()).hexdigest(),
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise ValueError("Stack layer metadata changed")
        head = manifest["head_sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", head):
            raise ValueError("Expected a full stack commit SHA")
        if git(repository, "show", "-s", "--format=%P", head) != parent:
            raise ValueError("Stack commit parent mismatch")
        if git(repository, "rev-parse", f"{head}^{{tree}}") != manifest["tree_sha"]:
            raise ValueError("Stack tree mismatch")
        message = (layer.title + "\n\n" + "\n".join(layer.credits)).rstrip()
        if git(repository, "show", "-s", "--format=%B", head) != message:
            raise ValueError("Stack commit message or contributor credit changed")
        run_command(["git", "checkout", "--detach", parent], repository)
        apply_patch(repository, layer.patch)
        run_command(["git", "add", "--all"], repository)
        if git(repository, "write-tree") != manifest["tree_sha"]:
            raise ValueError("Stack bundle differs from the reviewed patch")
        run_command(["git", "reset", "--hard", head], repository)
        parent, base_ref = head, spec.branch(layer)
    if git(repository, "rev-parse", "FETCH_HEAD") != parent:
        raise ValueError("Bundle tip differs from the validated stack tip")
    return spec


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def save_stack_artifact(spec: StackSpec, result: dict) -> str:
    key = f"fastmcp-stack-{spec.batch_id}-{spec.group}"
    run_id = UUID(flow_run.id)
    with get_client(sync_client=True) as client:
        existing = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [key]}),
            flow_run_filter=FlowRunFilter(id={"any_": [run_id]}),
            limit=10,
        )
        if existing:
            if any(artifact.data != result for artifact in existing):
                raise ValueError(
                    "Conflicting stack artifact exists for this validation run"
                )
            return str(existing[0].id)
        artifact = client.create_artifact(
            ArtifactCreate(
                key=key,
                type="fastmcp-stack-validation",
                flow_run_id=run_id,
                data=result,
                description="Validated linear stack, including every layer and the combined tip",
            )
        )
    return str(artifact.id)


@flow(name="fastmcp-validate-stack", timeout_seconds=6600)
def validate_stack(spec: StackSpec) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="fastmcp-stack-validation-") as temporary:
        root = Path(temporary)
        install_tools(root / "tools")
        repository = root / "fastmcp"
        fetch_source(repository, spec.trunk_sha)
        run_command(["uv", "sync"], repository)
        run_command(["uv", "run", "prek", "install"], repository)
        result = assemble_stack(repository, spec)
    artifact_id = save_stack_artifact(spec, result)
    return {
        "artifact_id": artifact_id,
        "spec_sha256": result["spec_sha256"],
        "status": "validated",
    }


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def read_stack_artifact(
    validation_run_id: UUID,
    validation_deployment_id: UUID,
    deployment_version: str,
    expected_spec_sha256: str,
) -> dict:
    if not re.fullmatch(r"[a-f0-9]{40}", deployment_version):
        raise ValueError("Expected the pinned validator source commit")
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(validation_run_id)
        if (
            run.deployment_id != validation_deployment_id
            or run.deployment_version != deployment_version
            or run.state is None
            or not run.state.is_completed()
        ):
            raise ValueError("Expected a completed run of the pinned stack validator")
        spec = StackSpec.model_validate(run.parameters["spec"])
        if digest(spec.model_dump(mode="json")) != expected_spec_sha256:
            raise ValueError("Validation run has a different stack specification")
        artifacts = client.read_artifacts(
            artifact_filter=ArtifactFilter(
                key={"any_": [f"fastmcp-stack-{spec.batch_id}-{spec.group}"]}
            ),
            flow_run_filter=FlowRunFilter(id={"any_": [validation_run_id]}),
            limit=2,
        )
    if len(artifacts) != 1:
        raise ValueError("Expected exactly one durable stack validation artifact")
    artifact = artifacts[0]
    data = artifact.data
    if (
        artifact.type != "fastmcp-stack-validation"
        or artifact.flow_run_id != validation_run_id
        or not isinstance(data, dict)
        or data.get("schema_version") != 1
        or data.get("status") != "validated"
        or data.get("spec") != spec.model_dump(mode="json")
        or data.get("spec_sha256") != expected_spec_sha256
    ):
        raise ValueError("Stored artifact does not match the validated stack")
    return data
