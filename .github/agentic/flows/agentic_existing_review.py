from __future__ import annotations

import time
from collections import Counter
from uuid import UUID

from prefect import task
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    ArtifactFilter,
    DeploymentFilter,
    FlowRunFilter,
)
from prefect.client.schemas.objects import FlowRun
from prefect.client.schemas.sorting import FlowRunSort

from flows.agentic_freshness import change_kind
from flows.agentic_pr_verification import VerificationSpec
from flows.agentic_stack import digest
from flows.agentic_stack_policy import Batch, Group, publication_revision
from flows.agentic_stack_runtime import Runtime, collect, owner_alive, record, saved


def verification_result(run_id: UUID, runtime: Runtime, spec: VerificationSpec) -> dict:
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(run_id)
        if (
            run.state is None
            or not run.state.is_completed()
            or run.deployment_id != runtime.verification_id
            or run.deployment_version != runtime.source_sha
            or run.parameters != {"spec": spec.model_dump(mode="json")}
        ):
            raise ValueError(
                "Verification needs a completed run with the exact source and parameters"
            )
        records = client.read_artifacts(
            artifact_filter=ArtifactFilter(
                type={"any_": ["fastmcp-contribution-verification"]}
            ),
            flow_run_filter=FlowRunFilter(id={"any_": [run_id]}),
            limit=2,
        )
    if len(records) != 1 or not isinstance(records[0].data, dict):
        raise ValueError("Expected one contribution verification artifact")
    result = records[0].data
    if result.get("spec_sha256") != digest(spec.model_dump(mode="json")) or result.get(
        "spec"
    ) != spec.model_dump(mode="json"):
        raise ValueError("Verification artifact describes different inputs")
    return result


def summary(result: dict) -> dict:
    stages = {}
    for name in ("base", "head", "adjacent"):
        stage = result.get(name, {})
        stages[name] = {
            "status": stage.get("status", "not_run"),
            "counts": dict(Counter(case["state"] for case in stage.get("cases", []))),
            "failures": [
                case
                for case in stage.get("cases", [])
                if case["state"] in {"failure", "error"}
            ],
        }
    return {
        "run_id": result["run_id"],
        "pr_number": result["spec"]["pr_number"],
        "head_sha": result["spec"]["head_sha"],
        "baseline_sha": result.get("baseline_sha"),
        "status": result["status"],
        "tests": stages,
        "comparison": result.get("comparison", {}),
        "independent_probes": sorted(result["spec"]["probes"]),
        "limitations": result.get("limitations", []),
        "usage": result["usage"],
    }


def verification_specs(
    group: Group, item: dict, probes: dict[str, str]
) -> list[VerificationSpec]:
    evidence = item["evidence"]
    selected = {pr["number"] for pr in item["route"].get("pull_requests", [])}
    contributions = [pr for pr in evidence["prs"] if pr["number"] in selected]
    if not selected or {pr["number"] for pr in contributions} != selected:
        raise ValueError("Selected contribution evidence is missing")
    prs = [pr for pr in contributions if not pr["merged"]]
    if len(prs) > 3:
        raise ValueError("Existing verification supports at most three contributions")
    if probes and len(prs) != 1:
        raise ValueError("Independent probes require one selected contribution")
    specs = []
    for pr in prs:
        if pr["base_ref"] != group.branch:
            raise ValueError("Existing contribution targets a different release line")
        specs.append(
            VerificationSpec(
                issue_number=evidence["issue"]["number"],
                pr_number=pr["number"],
                head_sha=pr["head_sha"],
                trunk_sha=group.base_sha,
                trunk_ref=group.branch,
                evidence_revision=publication_revision(evidence),
                probes=probes,
            )
        )
    return specs


def start_verification(
    runtime: Runtime, spec: VerificationSpec, retry_key: str | None
) -> FlowRun:
    if runtime.verification_id is None:
        raise ValueError(
            "Existing contributions require a configured managed verification deployment"
        )
    with get_client(sync_client=True) as client:
        deployment = client.read_deployment(runtime.verification_id)
        if (
            deployment.version != runtime.source_sha
            or deployment.work_pool_name != "managed"
        ):
            raise ValueError("Verification deployment changed or is not managed")
        key = f"fastmcp-verify-{runtime.source_sha}-{digest(spec.model_dump(mode='json'))}"
        run = client.create_flow_run_from_deployment(
            runtime.verification_id,
            parameters={"spec": spec.model_dump(mode="json")},
            idempotency_key=key,
        )
        if (
            retry_key
            and run.state is not None
            and run.state.is_final()
            and not run.state.is_completed()
        ):
            completed = client.read_flow_runs(
                deployment_filter=DeploymentFilter(
                    id={"any_": [runtime.verification_id]}
                ),
                flow_run_filter=FlowRunFilter(state={"type": {"any_": ["COMPLETED"]}}),
                sort=FlowRunSort.END_TIME_DESC,
                limit=12,
            )
            for candidate in completed:
                if (
                    candidate.deployment_version == runtime.source_sha
                    and candidate.parameters == {"spec": spec.model_dump(mode="json")}
                ):
                    return candidate
            run = client.create_flow_run_from_deployment(
                runtime.verification_id,
                parameters={"spec": spec.model_dump(mode="json")},
                idempotency_key=key + "-retry-" + retry_key,
            )
    return run


@task(retries=2, retry_delay_seconds=[2, 5], persist_result=False)
def verify_existing(
    owner: UUID,
    batch: Batch,
    group: Group,
    runtime: Runtime,
    item: dict,
    probes: dict[str, str],
    stage: str = "initial",
    retry_key: str | None = None,
) -> dict:
    outcome_key = (
        f"{batch.id}-issue-{item['evidence']['issue']['number']}-verify-{stage}-outcome"
    )
    binding = digest(
        [group.model_dump(mode="json"), runtime.model_dump(mode="json"), item, probes]
    )
    previous = saved(owner, outcome_key)
    if previous is not None:
        if previous["binding"] != binding:
            raise ValueError("Verification stage inputs changed")
        return previous["outcome"]
    evidence = item["evidence"]
    specs = verification_specs(group, item, probes)
    results = []
    for spec in specs:
        owner_alive(owner, batch.expires_at)
        run = start_verification(runtime, spec, retry_key)
        record(
            owner,
            f"{batch.id}-{stage}-pr-{spec.pr_number}-verification-{run.id}",
            {"run_id": str(run.id), "spec": spec.model_dump(mode="json")},
        )
        while True:
            owner_alive(owner, batch.expires_at)
            with get_client(sync_client=True) as client:
                run = client.read_flow_run(run.id)
            if run.state is not None and run.state.is_final():
                break
            time.sleep(5)
        results.append(
            summary(
                {"run_id": str(run.id), **verification_result(run.id, runtime, spec)}
            )
        )
    current = collect(evidence["issue"]["number"], group.branch)
    change = change_kind(evidence, current)
    status = "verification_recorded" if results else "history_review_recorded"
    next_step = (
        "Review executed tests and failures against the established contract; full-suite and composition checks remain separate"
        if results
        else "Review the assessment of merged contributions and subsequent history; no unmerged contribution was sent to verification"
    )
    if change != "unchanged":
        status, next_step = change, "Refresh changed evidence"
    review = {
        "issue_number": evidence["issue"]["number"],
        "assessment": item["assessment"],
        "route": item["route"],
        "verification": results,
        "status": status,
        "merge_ready": False,
        "next_step": next_step,
    }
    outcome = {"review": review, "observed": current}
    record(owner, outcome_key, {"binding": binding, "outcome": outcome})
    return outcome
