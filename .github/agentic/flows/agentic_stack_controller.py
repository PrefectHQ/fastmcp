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

from flows.agentic_claims import claim_issues, claim_work
from flows.agentic_freshness import revalidate
from flows.agentic_generated_review import deliver_published_reviews, reviewed_stack
from flows.agentic_intake import policy_outcome, route_intake
from flows.agentic_native_triage import run_native_triage
from flows.agentic_reproduction_evidence import validate_reproduction_patch
from flows.agentic_review_delivery import deliver_reviews
from flows.agentic_review_queue import retain_review_queue
from flows.agentic_stack import StackLayer, StackSpec, digest, read_stack_artifact
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
from flows.agentic_triage_review import apply_disposition, requires_review
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
            idempotency_key=f"fastmcp-validation-{owner}-{digest(spec.model_dump(mode='json'))}",
        )
    record.fn(
        owner,
        f"{batch.id}-{spec.group}-validation-{digest(spec.model_dump(mode='json'))[:12]}",
        {"run_id": str(run.id)},
    )
    return run.id


class ValidationFailure(RuntimeError):
    def __init__(self, run_id: UUID, message: str):
        self.run_id = run_id
        super().__init__(message)


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
    if run["state_type"] == "FAILED":
        message = (run.get("state") or {}).get("message") or "Validator failed"
        record(
            owner,
            f"{batch.id}-{spec.group}-validation-failed-{run_id}",
            {"run_id": str(run_id), "message": message[-24000:]},
        )
        raise ValidationFailure(run_id, message[-24000:])
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
    spec, artifact, _ = reviewed_stack(
        runtime, validation_run_id, review_run_id, review_parameters
    )
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
    result.update(
        {
            "validation_run_id": str(validation_run_id),
            "review_run_id": review_run_id,
            "controller_run_id": str(owner),
        }
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


def batch_owner(batch: Batch, runtime: Runtime) -> UUID:
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
    return owner


def run_batch(batch: Batch, runtime: Runtime) -> dict:
    owner = batch_owner(batch, runtime)
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
        payload["controller_owner"] = str(owner)
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
                destination, result, native_calls, final_evidence = run_native_triage(
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
                calls.extend(native_calls)
                if result.get("decision") == "reproduced":
                    proof_digest = digest(
                        [result["base_sha"], result.get("regression_patch", "")]
                    )
                    proof_key = f"{batch.id}-issue-{number}-regression-{proof_digest}"
                    proof = saved(owner, proof_key)
                    if proof is None:
                        proof = validate_reproduction_patch(result)
                        record(owner, proof_key, proof)
                    result = {**result, "regression_validation": proof}
                if requires_review(destination):
                    if final_evidence != evidence:
                        review_item = {**item, "evidence": final_evidence}
                        projected = assessment_context(review_item)
                        item_bundles = skills(final_evidence["base_sha"])
                    review, review_run = ask(
                        "triage_review",
                        item_bundles["review_instructions"],
                        {**projected, "outcome": result},
                        f"issue-{number}-triage-review",
                    )
                    mutation_reader = None
                    if any(
                        proposed["action"]
                        in {"close_duplicate", "close_completed", "close_pr"}
                        for proposed in review.get("recommendations", [])
                    ):
                        mutation_github = GitHub(secret_sync("fastmcp-triage-gh-token"))

                        def mutation_reader(endpoint: str) -> dict | None:
                            value = mutation_github.request(endpoint)
                            if value is not None and not isinstance(value, dict):
                                raise ValueError("Expected a closure evidence object")
                            return value

                    destination, result = apply_disposition(
                        destination,
                        result,
                        review,
                        review_run,
                        evidence=final_evidence,
                        read=mutation_reader,
                    )
                    if runtime.publish_reviews and destination == "existing_review":
                        try:
                            delivery = deliver_reviews(
                                owner,
                                batch,
                                item_group,
                                runtime,
                                result,
                                final_evidence,
                            )
                        except (
                            ValueError,
                            RuntimeError,
                            TimeoutError,
                            httpx.HTTPError,
                            OSError,
                        ) as error:
                            delivery = {"status": "failed", "reason": str(error)[:1000]}
                            bins["operational"].append(
                                {
                                    "issue_number": number,
                                    "reason": "Review delivery failed",
                                    "detail": str(error)[:1000],
                                }
                            )
                        result["review_delivery"] = delivery
                        result["published_reviews"] = delivery.get("posted", [])
                if not (
                    requested.mode == "fix"
                    and destination == "evidence"
                    and result.get("decision") == "reproduced"
                    and route["route"] != "review_existing"
                ):
                    bins[destination].append(result)
                    continue
                item["assessment"] = result["assessment"]
                item["reproduction"] = {
                    key: value
                    for key, value in result.items()
                    if key not in {"issue_number", "assessment"}
                }
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
        if not ready:
            record(
                owner,
                f"{batch.id}-{group.name}-outcome",
                {"bins": bins, "agent_runs": calls},
            )
            continue
        try:
            for attempt in range(2):
                spec = make_spec(batch, group, ready, actor)
                try:
                    artifact, validation_run = validate(owner, batch, runtime, spec)
                except ValidationFailure as error:
                    review_run = None
                    review = {
                        "decision": "changes_requested",
                        "reason": "Exact candidate failed deterministic validation",
                        "findings": [str(error)],
                        "validation_run_id": str(error.run_id),
                    }
                else:
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
                    if review_run is None:
                        raise ValueError("Publication requires independent review")
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
                    if runtime.publish_reviews:
                        try:
                            result = {
                                **result,
                                "review_delivery": deliver_published_reviews(
                                    owner,
                                    batch,
                                    runtime,
                                    result,
                                    {
                                        "instructions": bundles["review_instructions"],
                                        "context": review_context,
                                    },
                                ),
                            }
                        except (
                            ValueError,
                            RuntimeError,
                            TimeoutError,
                            httpx.HTTPError,
                            OSError,
                        ) as error:
                            result = {
                                **result,
                                "review_delivery": {
                                    "status": "failed",
                                    "reason": str(error)[:1000],
                                },
                            }
                            bins["operational"].append(
                                {
                                    "group": group.name,
                                    "reason": "Generated draft review delivery failed",
                                    "detail": str(error)[:1000],
                                }
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
    proposed_issues = {
        item["issue_number"]
        for values in bins.values()
        for item in values
        if item.get("mutation_recommendations")
        and isinstance(item.get("issue_number"), int)
    }
    branches = {
        item.issue_number: group.branch
        for group in batch.groups
        for item in group.items
        if item.mode != "fix" or item.issue_number in proposed_issues
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
    owner = batch_owner(batch, runtime)
    with (
        claim_issues(batch, owner),
        claim_work(["fastmcp-triage-workers"], owner, batch.expires_at, slots=3),
        tempfile.TemporaryDirectory(prefix="fastmcp-stack-tools-") as temporary,
    ):
        record(
            owner,
            f"{batch.id}-ownership",
            {
                "issues": sorted(
                    item.issue_number for group in batch.groups for item in group.items
                ),
                "owner": str(owner),
            },
        )
        intake_tools(Path(temporary))
        result = run_batch(batch, runtime)
        if result["bins"]["operational"]:
            return Completed(
                name="Degraded",
                data=result,
                message="Review bins retained; some work needs operational attention",
            )
        return result
