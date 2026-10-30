from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from prefect import flow, task
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import State
from prefect.context import FlowRunContext
from prefect.states import Completed

from flows.agentic_baseline import review_existing
from flows.agentic_freshness import revalidate
from flows.agentic_intake import policy_outcome, route_intake
from flows.agentic_native_triage import run_native_triage
from flows.agentic_review_queue import retain_review_queue
from flows.agentic_stack import StackLayer, StackSpec, digest, read_stack_artifact
from flows.agentic_stack_plans import Review
from flows.agentic_stack_policy import Batch, Group, publication_revision
from flows.agentic_stack_publication import GitHub, reconcile_stack
from flows.agentic_stack_runtime import (
    Runtime,
    api,
    assessment_context,
    collect,
    intake_tools,
    judge,
    owner_alive,
    record,
    saved,
    secret_sync,
    skills,
)
from flows.agentic_stack_usage import usage_report
from flows.agentic_triage_records import Admission
from flows.agentic_validate_patch import fetch_source


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def identities(evidence: dict | None = None) -> dict:
    github = GitHub(secret_sync("fastmcp-triage-gh-token"))
    actor = github.request("user")
    if not isinstance(actor, dict):
        raise ValueError("Publisher identity unavailable")
    credits = []
    if evidence is not None:
        reporter = evidence["issue"]["user"]
        permission = github.request(
            f"repos/PrefectHQ/fastmcp/collaborators/{reporter['login']}/permission"
        )
        maintainer = isinstance(permission, dict) and (
            permission.get("permission") in {"admin", "maintain"}
            or (
                permission.get("permission") == "write"
                and evidence["issue"].get("author_association") in {"MEMBER", "OWNER"}
            )
        )
        if not maintainer:
            credits.append(
                f"Co-authored-by: {reporter['login']} <{reporter['id']}+{reporter['login']}@users.noreply.github.com>"
            )
    return {"login": actor["login"], "id": actor["id"], "credits": credits}


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def start_validation(
    owner: UUID, batch: Batch, runtime: Runtime, spec: StackSpec
) -> UUID:
    owner_alive.fn(owner, batch.expires_at)
    with get_client(sync_client=True) as client:
        deployment = client.read_deployment(runtime.validator_id)
        if deployment.version != runtime.source_sha:
            raise ValueError("Validator deployment changed since configuration")
        run = client.create_flow_run_from_deployment(
            runtime.validator_id,
            parameters={"spec": spec.model_dump(mode="json")},
            idempotency_key=f"{batch.id}-{spec.group}-{digest(spec.model_dump(mode='json'))}",
        )
    record.fn(
        owner,
        f"{batch.id}-{spec.group}-validation-{digest(spec.model_dump(mode='json'))[:12]}",
        {"run_id": str(run.id)},
    )
    return run.id


def validate(
    owner: UUID, batch: Batch, runtime: Runtime, spec: StackSpec
) -> tuple[dict, UUID]:
    run_id = start_validation(owner, batch, runtime, spec)
    while True:
        owner_alive(owner, batch.expires_at)
        run = api("GET", f"/flow_runs/{run_id}")
        if run["state_type"] in {"COMPLETED", "FAILED", "CRASHED", "CANCELLED"}:
            break
        time.sleep(5)
    artifact = read_stack_artifact(
        run_id,
        runtime.validator_id,
        runtime.source_sha,
        digest(spec.model_dump(mode="json")),
    )
    return artifact, run_id


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def publish(
    owner: UUID,
    batch: Batch,
    runtime: Runtime,
    validation_run_id: UUID,
    review_run_id: str,
    review_parameters: dict,
) -> dict:
    spec = StackSpec.model_validate(review_parameters["context"]["spec"])
    artifact = read_stack_artifact.fn(
        validation_run_id,
        runtime.validator_id,
        runtime.source_sha,
        digest(spec.model_dump(mode="json")),
    )
    if (
        artifact["layers"] != review_parameters["context"]["layers"]
        or str(validation_run_id) != review_parameters["context"]["validation_run_id"]
    ):
        raise ValueError("Review does not describe the validated chain")
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(UUID(review_run_id))
        if (
            run.flow_id != runtime.plans["review"].flow_id
            or run.parameters != review_parameters
        ):
            raise ValueError("Stored review used different inputs")
    observation = api.fn("GET", f"/flow_runs/{review_run_id}/execution-plan")
    if observation["status"] != "completed" or observation["snapshot"][
        "execution_plan_version_id"
    ] != str(runtime.plans["review"].version_id):
        raise ValueError("Expected the completed pinned independent review")
    approval = Review.model_validate(
        api.fn("GET", f"/flow_runs/{review_run_id}/execution-plan/outputs/result")[
            "decision"
        ]
    )
    if approval.decision != "approved" or approval.findings:
        raise ValueError("Independent review did not approve this stack")
    record.fn(
        owner,
        f"{batch.id}-{spec.group}-publication-intent",
        {
            "owner": str(owner),
            "batch": batch.model_dump(mode="json"),
            "validation_run_id": str(validation_run_id),
            "review_run_id": review_run_id,
            "spec_sha256": artifact["spec_sha256"],
            "bundle_sha256": artifact["bundle_sha256"],
            "layers": artifact["layers"],
            "review_parameters_sha256": digest(review_parameters),
        },
    )

    def guard() -> None:
        owner_alive.fn(owner, batch.expires_at)
        for item in review_parameters["context"]["items"]:
            current = collect.fn(
                item["evidence"]["issue"]["number"], spec.trunk_ref, publication=True
            )
            if publication_revision(
                current, spec, artifact["layers"]
            ) != publication_revision(item["evidence"]):
                raise ValueError("Issue or contribution evidence changed after review")

    token = secret_sync("fastmcp-triage-gh-token")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PREFECT_API_KEY",
            "PREFECT_API_AUTH_STRING",
            "GH_TOKEN",
            "GITHUB_TOKEN",
        }
    }
    environment.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic "
            + base64.b64encode(("x-access-token:" + token).encode()).decode(),
        }
    )
    guard()
    with tempfile.TemporaryDirectory(prefix="fastmcp-stack-publish-") as temporary:
        repository = Path(temporary) / "source"
        fetch_source(repository, spec.trunk_sha)
        result = reconcile_stack(
            repository, artifact, GitHub(token), guard, environment
        )
    record.fn(owner, f"{batch.id}-{spec.group}-published", result)
    return result


def make_spec(batch: Batch, group: Group, items: list[dict], actor: dict) -> StackSpec:
    layers = []
    for item in items:
        prepared = item["preparation"]
        if (
            prepared["decision"] != "prepared"
            or prepared["base_sha"] != group.base_sha
            or not prepared["patch"].strip()
        ):
            raise ValueError("Preparation did not produce a patch on the pinned base")
        layers.append(
            StackLayer(
                issue_number=item["evidence"]["issue"]["number"],
                patch=prepared["patch"],
                title=prepared["title"],
                body=prepared["body"],
                credits=item["credits"],
                evidence_revision=publication_revision(item["evidence"]),
            )
        )
    return StackSpec(
        batch_id=batch.id,
        group=group.name,
        trunk_ref=group.branch,
        trunk_sha=group.base_sha,
        created_at=batch.started_at,
        publisher_login=actor["login"],
        publisher_id=actor["id"],
        layers=layers,
    )


def run_batch(batch: Batch, runtime: Runtime) -> dict:
    context = FlowRunContext.get()
    if context is None or context.flow_run is None:
        raise ValueError("A managed controller run is required")
    owner = context.flow_run.id
    if (
        context.flow_run.deployment_version != runtime.source_sha
        or context.flow_run.work_pool_name != "managed"
    ):
        raise ValueError("Controller must run from its pinned managed deployment")
    if context.flow_run.idempotency_key != f"fastmcp-stack-{batch.id}":
        raise ValueError("Start the batch with its stable idempotency key")
    if datetime.now(UTC) < batch.started_at:
        raise ValueError("Batch window has not started")
    owner_alive(owner, batch.expires_at)
    record(
        owner,
        f"{batch.id}-configuration",
        {
            "batch": batch.model_dump(mode="json"),
            "runtime": runtime.model_dump(mode="json"),
        },
    )
    previous_result = saved(owner, f"{batch.id}-review-bins")
    if previous_result is not None:
        retain_review_queue(owner, previous_result)
        return previous_result
    actor = identities()
    bins: dict[str, list] = {
        name: []
        for name in (
            "stacks",
            "existing_review",
            "evidence",
            "pruning",
            "decisions",
            "operational",
            "unchanged",
        )
    }
    calls = []

    def ask(role: str, bundle: dict, payload: dict, key: str) -> tuple[dict, str]:
        result, run_id = judge(
            role,
            runtime.plans[role],
            bundle,
            payload,
            owner,
            batch.expires_at,
            f"{batch.id}-{key}",
        )
        calls.append(
            {
                "role": role,
                "run_id": run_id,
                "plan_version": str(runtime.plans[role].version_id),
            }
        )
        return result, run_id

    for group in batch.groups:
        previous_group = saved(owner, f"{batch.id}-{group.name}-outcome")
        if previous_group is not None:
            bins = previous_group["bins"]
            calls = previous_group["agent_runs"]
            continue
        if datetime.now(UTC) >= batch.expires_at:
            bins["operational"].append(
                {
                    "group": group.name,
                    "reason": "Batch deadline expired before group admission",
                }
            )
            continue
        bundles = skills(group.base_sha)
        ready: list[dict] = []
        for requested in group.items:
            number = requested.issue_number
            try:
                owner_alive(owner, batch.expires_at)
                intake_key = f"{batch.id}-issue-{number}-intake"
                item: dict | None = saved(owner, intake_key)
                if item is None:
                    evidence = collect(number, group.branch)
                    item = {
                        "evidence": evidence,
                        "route": route_intake(evidence),
                        "group_rationale": group.rationale,
                    }
                    record(owner, intake_key, item)
                evidence = item["evidence"]
                item_group = group
                item_bundles = bundles
                if evidence["base_sha"] != group.base_sha:
                    if requested.mode == "fix":
                        raise ValueError("Release branch moved after batch selection")
                    item_group = group.model_copy(
                        update={"base_sha": evidence["base_sha"]}
                    )
                    item_bundles = skills(evidence["base_sha"])
                route = item["route"]
                if route["route"] in {
                    "closed",
                    "claimed",
                    "restricted_review",
                    "evidence_needed",
                    "retry",
                }:
                    destination = {
                        "closed": "unchanged",
                        "evidence_needed": "evidence",
                        "retry": "operational",
                    }.get(route["route"], "decisions")
                    bins[destination].append(policy_outcome(number, route))
                    continue
                bundle = (
                    item_bundles["existing_instructions"]
                    if route["route"] == "review_existing"
                    else item_bundles["assess_instructions"]
                )
                assessment_key = f"{batch.id}-issue-{number}-assessment-context"
                projected = saved(owner, assessment_key)
                if projected is None:
                    projected = assessment_context(item)
                    record(owner, assessment_key, projected)
                if requested.mode != "fix":
                    destination, result, native_calls = run_native_triage(
                        owner,
                        Admission(
                            batch=batch,
                            group=item_group,
                            requested=requested,
                            runtime=runtime,
                            item=item,
                            request={
                                "instructions": bundle,
                                "context": {**projected, "mode": requested.mode},
                            },
                            reproduce_request={
                                "instructions": item_bundles["reproduce_instructions"],
                                "context": projected,
                            },
                        ),
                    )
                    bins[destination].append(result)
                    calls.extend(native_calls)
                    continue
                assessment, _ = ask(
                    "assess", bundle, projected, f"issue-{number}-assess"
                )
                item["assessment"] = assessment
                if (
                    route["route"] == "review_existing"
                    and assessment["decision"] == "existing_review"
                ):
                    destination, review = review_existing(
                        owner,
                        batch,
                        item_group,
                        runtime,
                        item,
                        requested.verification_probes,
                        bundle,
                        ask,
                    )
                    bins[destination].append(review)
                    continue
                if (
                    assessment["decision"] != "reproduce"
                    or route["route"] == "review_existing"
                    or requested.mode == "review"
                ):
                    destination = {
                        "prune": "pruning",
                        "evidence_needed": "evidence",
                        "existing_review": "existing_review",
                        "skip": "unchanged",
                        "environment_failure": "operational",
                    }.get(assessment["decision"], "decisions")
                    bins[destination].append({"issue_number": number, **assessment})
                    continue
                reproduction, _ = ask(
                    "reproduce",
                    item_bundles["reproduce_instructions"],
                    item,
                    f"issue-{number}-reproduce",
                )
                item["reproduction"] = reproduction
                if reproduction["decision"] != "reproduced" or requested.mode == "mre":
                    destination = (
                        "operational"
                        if reproduction["decision"] == "environment_failure"
                        else "evidence"
                    )
                    bins[destination].append({"issue_number": number, **reproduction})
                    continue
                prepared, _ = ask(
                    "prepare",
                    bundles["prepare_instructions"],
                    item,
                    f"issue-{number}-prepare",
                )
                item["preparation"] = prepared
                if prepared["decision"] != "prepared":
                    destination = (
                        "operational"
                        if prepared["decision"] == "environment_failure"
                        else "decisions"
                    )
                    bins[destination].append({"issue_number": number, **prepared})
                    continue
                item["credits"] = identities(evidence)["credits"]
                ready.append(item)
            except (
                ValueError,
                RuntimeError,
                TimeoutError,
                subprocess.SubprocessError,
                httpx.HTTPError,
                OSError,
            ) as error:
                bins["operational"].append(
                    {
                        "issue_number": number,
                        "error": type(error).__name__,
                        "detail": str(error)[:1000],
                    }
                )
        if len(ready) < 2:
            for item in ready:
                bins["decisions"].append(
                    {
                        "issue_number": item["evidence"]["issue"]["number"],
                        "reason": "A scoped patch is retained, but no coherent second layer is ready",
                        "preparation": item["preparation"],
                    }
                )
            record(
                owner,
                f"{batch.id}-{group.name}-outcome",
                {"bins": bins, "agent_runs": calls},
            )
            continue
        try:
            for attempt in range(2):
                spec = make_spec(batch, group, ready, actor)
                artifact, validation_run = validate(owner, batch, runtime, spec)
                review_context = {
                    "spec": spec.model_dump(mode="json"),
                    "layers": artifact["layers"],
                    "validation_run_id": str(validation_run),
                    "items": ready,
                }
                review, review_run = ask(
                    "review",
                    bundles["review_instructions"],
                    review_context,
                    f"{group.name}-review-{attempt}",
                )
                if review["decision"] == "approved" and not review["findings"]:
                    result = publish(
                        owner,
                        batch,
                        runtime,
                        validation_run,
                        review_run,
                        {
                            "instructions": bundles["review_instructions"],
                            "context": review_context,
                        },
                    )
                    bins["stacks"].append(result)
                    break
                if review["decision"] != "changes_requested" or attempt == 1:
                    destination = (
                        "operational"
                        if review["decision"] == "environment_failure"
                        else "decisions"
                    )
                    bins[destination].append(
                        {
                            "group": group.name,
                            "review": review,
                            "review_run_id": review_run,
                        }
                    )
                    break
                for item in ready:
                    number = item["evidence"]["issue"]["number"]
                    payload = {
                        **item,
                        "previous": item["preparation"],
                        "repair": review,
                    }
                    item["preparation"], _ = ask(
                        "prepare",
                        bundles["prepare_instructions"],
                        payload,
                        f"issue-{number}-repair",
                    )
        except (
            ValueError,
            RuntimeError,
            TimeoutError,
            subprocess.SubprocessError,
            httpx.HTTPError,
            OSError,
        ) as error:
            bins["operational"].append(
                {
                    "group": group.name,
                    "error": type(error).__name__,
                    "detail": str(error)[:1000],
                }
            )
        record(
            owner,
            f"{batch.id}-{group.name}-outcome",
            {"bins": bins, "agent_runs": calls},
        )
    try:
        usage = usage_report(owner)
    except (ValueError, RuntimeError, httpx.HTTPError, OSError) as error:
        usage = {"status": "unavailable", "detail": str(error)[:1000]}
    result = {
        "batch_id": batch.id,
        "bins": bins,
        "agent_runs": calls,
        "usage": usage,
    }
    branches = {
        item.issue_number: group.branch
        for group in batch.groups
        for item in group.items
        if item.mode != "fix"
    }
    originals = {}
    for number in branches:
        reviewed = saved(owner, f"{batch.id}-issue-{number}-review-evidence")
        intake = saved(owner, f"{batch.id}-issue-{number}-intake")
        if reviewed is not None:
            originals[number] = reviewed
        elif intake is not None:
            originals[number] = intake["evidence"]
    result = revalidate(result, originals, branches, collect)
    record(owner, f"{batch.id}-review-bins", result)
    retain_review_queue(owner, result)
    return result


@flow(name="fastmcp-stack-batch", timeout_seconds=7200)
def stack_batch(batch: Batch, runtime: Runtime) -> dict | State:
    with tempfile.TemporaryDirectory(prefix="fastmcp-stack-tools-") as temporary:
        intake_tools(Path(temporary))
        result = run_batch(batch, runtime)
        if result["bins"]["operational"]:
            return Completed(
                name="Degraded",
                data=result,
                message="Review bins retained; some work needs operational attention",
            )
        return result
