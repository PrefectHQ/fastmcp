from __future__ import annotations

import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from prefect import flow
from prefect.client.schemas.objects import State
from prefect.context import FlowRunContext
from prefect.states import Completed

from flows.agentic_claims import claim_issues, claim_work
from flows.agentic_freshness import revalidate
from flows.agentic_intake import policy_outcome, route_intake
from flows.agentic_native_triage import run_native_triage
from flows.agentic_reproduction_evidence import validate_reproduction_patch
from flows.agentic_review_delivery import deliver_reviews
from flows.agentic_review_publication import EvidenceChanged
from flows.agentic_stack import digest
from flows.agentic_stack_policy import Batch
from flows.agentic_stack_runtime import (
    Runtime,
    assessment_context,
    collect,
    intake_tools,
    judge,
    owner_alive,
    record,
    saved,
    skills,
)
from flows.agentic_stack_usage import usage_report
from flows.agentic_triage_records import Admission
from flows.agentic_triage_review import apply_disposition, requires_review


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
        return previous_result
    bins: dict[str, list] = {
        name: []
        for name in (
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
                if (
                    requires_review(destination)
                    and "triage_review" not in runtime.plans
                ):
                    # Private sweeps run without the Sol reviewer; anything later
                    # proposed for public action gets its own adversarial review.
                    result = {**result, "adversarial_review": None}
                elif requires_review(destination):
                    if final_evidence != evidence:
                        review_item = {**item, "evidence": final_evidence}
                        projected = assessment_context(review_item)
                        item_bundles = skills(final_evidence["base_sha"])
                    review, review_run = ask(
                        "triage_review",
                        item_bundles["triage_review_instructions"],
                        {**projected, "outcome": result},
                        f"issue-{number}-triage-review",
                    )
                    destination, result = apply_disposition(
                        destination,
                        result,
                        review,
                        review_run,
                        route,
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
                        except EvidenceChanged as error:
                            # Freshness revalidation requeues the item, and the
                            # GitHub event for the change starts its own triage.
                            delivery = {
                                "status": "superseded",
                                "reason": str(error)[:1000],
                                "posted": getattr(error, "posted", []),
                            }
                        except (
                            ValueError,
                            RuntimeError,
                            TimeoutError,
                            httpx.HTTPError,
                            OSError,
                        ) as error:
                            delivery = {
                                "status": "failed",
                                "reason": str(error)[:1000],
                                "posted": getattr(error, "posted", []),
                            }
                            bins["operational"].append(
                                {
                                    "issue_number": number,
                                    "reason": "Review delivery failed",
                                    "detail": str(error)[:1000],
                                }
                            )
                        result["review_delivery"] = delivery
                        result["published_reviews"] = delivery.get("posted", [])
                bins[destination].append(result)
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
