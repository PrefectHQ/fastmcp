from __future__ import annotations

import time
from uuid import UUID

from prefect.client.orchestration import get_client

from flows.agentic_baseline import refresh_context
from flows.agentic_native_cache import (
    find_native_result,
    retain_native_result,
    verified_outcome,
)
from flows.agentic_stack import digest
from flows.agentic_stack_publication import GitHub
from flows.agentic_stack_runtime import (
    api,
    owner_alive,
    record,
    saved,
    secret_sync,
    skills,
)
from flows.agentic_triage_records import Admission, AdmissionRef


def run_native_stage(
    owner: UUID, admission: Admission, stage: str
) -> tuple[str, dict, dict]:
    runtime = admission.runtime
    plan = runtime.triage_plan
    if plan is None:
        raise ValueError("Native triage plan is not configured")
    batch = admission.batch
    number = admission.requested.issue_number
    key = f"{batch.id}-issue-{number}-native-{stage}"
    owner_alive(owner, batch.expires_at)
    active = api("GET", f"/flows/{plan.flow_id}/execution-plan")
    if active["active_version"]["id"] != str(plan.version_id):
        raise ValueError("Native triage plan changed since batch configuration")
    with get_client(sync_client=True) as client:
        for deployment_id in (runtime.triage_verify_id, runtime.triage_finalize_id):
            assert deployment_id is not None
            deployment = client.read_deployment(deployment_id)
            if (
                deployment.version != runtime.source_sha
                or deployment.work_pool_name != "managed"
            ):
                raise ValueError("Native triage deployment changed or is not managed")
    prior_intent = saved(owner, key + "-intent")
    reused = find_native_result(admission) if prior_intent is None else None
    if reused is not None:
        call = {
            "role": "triage",
            "run_id": reused["native_run_id"],
            "plan_version": str(plan.version_id),
            "reused": True,
        }
        record(owner, key + "-agent-run", call)
        return reused["destination"], reused["result"], call
    raw = admission.model_dump(mode="json")
    record(owner, key, raw)
    ref = AdmissionRef(owner=owner, key=key, binding=digest(raw))
    intent: dict | None = prior_intent
    if intent is None:
        parameters = {
            "parameters": {"ref": ref.model_dump(mode="json")},
            "reproduce_request": {
                **admission.reproduce_request,
                "allowed": admission.requested.mode != "review"
                and admission.item["route"]["route"] != "review_existing",
            },
        }
        parameters["assess_request"] = admission.request
        intent = {
            "parameters": parameters,
            "parameters_sha256": digest(parameters),
            "idempotency_key": key + "-" + digest(parameters),
        }
        record(owner, key + "-intent", intent)
    created = api(
        "POST",
        f"/flows/{plan.flow_id}/execution-plan/runs",
        {
            "name": key,
            "parameters": intent["parameters"],
            "idempotency_key": intent["idempotency_key"],
        },
    )
    run_id = UUID(created["id"])
    call = {
        "role": "triage",
        "run_id": str(run_id),
        "plan_version": str(plan.version_id),
    }
    record(owner, key + "-agent-run", call)
    while True:
        owner_alive(owner, batch.expires_at)
        with get_client(sync_client=True) as client:
            run = client.read_flow_run(run_id)
        if run.state is not None and run.state.is_final():
            break
        time.sleep(5)
    observation = api("GET", f"/flow_runs/{run_id}/execution-plan")
    if (
        observation["snapshot"]["execution_plan_version_id"] != str(plan.version_id)
        or run.flow_id != plan.flow_id
        or run.parameters != intent["parameters"]
    ):
        raise ValueError("Native run changed the pinned admission")
    outcome = verified_outcome(ref, admission, run_id)
    if outcome is None:
        record(
            owner, key + "-failure", {"run_id": str(run_id), "observation": observation}
        )
        return (
            "operational",
            {
                "issue_number": number,
                "reason": "Native triage ended without a retained final decision",
                "native_run_id": str(run_id),
            },
            call,
        )
    if run.state is not None and run.state.is_completed():
        retain_native_result(ref, admission, outcome)
    return outcome["destination"], outcome["result"], call


def run_native_triage(
    owner: UUID, admission: Admission
) -> tuple[str, dict, list[dict]]:
    calls = []
    number = admission.requested.issue_number
    for attempt in range(3):
        stage = "initial" if attempt == 0 else f"refresh-{attempt}"
        destination, result, call = run_native_stage(owner, admission, stage)
        calls.append(call)
        if (
            destination != "existing_review"
            or result.get("status") != "baseline_changed"
            or attempt == 2
        ):
            break
        key = f"{admission.batch.id}-issue-{number}-native-{stage}-verification"
        verified = saved(owner, key)
        if verified is None:
            raise ValueError("Missing native baseline verification evidence")
        current = verified["result"]["observed"]
        instructions = skills(current["base_sha"])["existing_instructions"]
        if {k: v for k, v in instructions.items() if k != "base_sha"} != {
            k: v
            for k, v in admission.request["instructions"].items()
            if k != "base_sha"
        }:
            result["status"] = "stale"
            result["next_step"] = (
                "Contribution policy changed; reassess with current skills"
            )
            break
        github = GitHub(secret_sync("fastmcp-triage-gh-token"))

        def read(endpoint: str) -> dict:
            value = github.request(endpoint)
            if not isinstance(value, dict):
                raise ValueError("Expected a GitHub comparison object")
            return value

        try:
            projected = refresh_context(
                {**admission.item, "assessment": result["assessment"]}, current, read
            )
        except ValueError:
            result["next_step"] = (
                "Baseline diff needs a full assessment on the next intake"
            )
            break
        admission = admission.model_copy(
            update={
                "group": admission.group.model_copy(
                    update={"base_sha": current["base_sha"]}
                ),
                "item": {
                    "evidence": current,
                    "route": projected["route"],
                    "group_rationale": admission.item["group_rationale"],
                },
                "request": {
                    "instructions": instructions,
                    "context": {**projected, "mode": admission.requested.mode},
                },
            }
        )
    record(
        owner,
        f"{admission.batch.id}-issue-{number}-review-evidence",
        admission.item["evidence"],
    )
    return destination, result, calls
