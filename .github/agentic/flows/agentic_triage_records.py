from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.context import FlowRunContext
from pydantic import BaseModel, ConfigDict, Field

from flows.agentic_assessment_cache import assessment_fingerprint
from flows.agentic_stack import digest
from flows.agentic_stack_plans import Assessment
from flows.agentic_stack_policy import Batch, Group, Item
from flows.agentic_stack_runtime import (
    AgentPlan,
    Runtime,
    api,
    owner_alive,
    saved,
)
from flows.agentic_triage_plan import assessment_branch


class AdmissionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner: UUID
    key: str = Field(pattern=r"^[a-z0-9-]{1,120}$")
    binding: str = Field(pattern=r"^[a-f0-9]{64}$")


class Admission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    batch: Batch
    group: Group
    requested: Item
    runtime: Runtime
    item: dict
    request: dict
    reproduce_request: dict


def selected_output(
    run_id: UUID, observation: dict, node: str
) -> tuple[str, dict] | None:
    matches = [value for value in observation["nodes"] if value["node"] == node]
    if len(matches) != 1:
        raise ValueError("Expected one declared native node")
    observed = matches[0]
    outputs = [
        value["output"]
        for value in observed["outputs"]
        if value["status"] == "selected"
    ]
    if not outputs:
        return None
    if (
        len(outputs) != 1
        or observed["status"] != "completed"
        or not observed["activation_id"]
    ):
        raise ValueError("Expected one completed native node output")
    name = outputs[0]
    value = api(
        "GET",
        f"/flow_runs/{run_id}/execution-plan/activations/{observed['activation_id']}/outputs/{name}",
    )
    return name, value


def read_assessment(
    run_id: UUID, plan: AgentPlan, request: dict
) -> tuple[str, dict] | None:
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(run_id)
    if (
        assessment_fingerprint(request) is None
        or run.flow_id != plan.flow_id
        or assessment_fingerprint(run.parameters.get("assess_request", {}))
        != assessment_fingerprint(request)
    ):
        raise ValueError("Cached assessment has different evidence or instructions")
    observation = api("GET", f"/flow_runs/{run_id}/execution-plan")
    if observation["snapshot"]["execution_plan_version_id"] != str(plan.version_id):
        raise ValueError("Cached assessment has a different native plan version")
    output = selected_output(run_id, observation, "assess")
    if output is None:
        return None
    branch, value = output
    assessment = Assessment.model_validate(value).model_dump(mode="json")
    if assessment_branch(assessment) != branch:
        raise ValueError("Assessment decision does not match its selected branch")
    return branch, assessment


def worker_admission(
    ref: AdmissionRef, node: str
) -> tuple[Admission, UUID, dict, dict]:
    raw = saved(ref.owner, ref.key)
    if raw is None or digest(raw) != ref.binding:
        raise ValueError("Native admission is missing or changed")
    admission = Admission.model_validate(raw)
    runtime = admission.runtime
    plan = runtime.triage_plan
    if plan is None:
        raise ValueError("Native triage is not configured")
    owner_alive(ref.owner, admission.batch.expires_at)
    context = FlowRunContext.get()
    if context is None or context.flow_run is None:
        raise ValueError("Native worker requires a managed child run")
    with get_client(sync_client=True) as client:
        child = client.read_flow_run(context.flow_run.id)
        expected_deployment = {
            "verify": runtime.triage_verify_id,
            "finalize": runtime.triage_finalize_id,
        }[node]
        if (
            child.deployment_id != expected_deployment
            or child.deployment_version != runtime.source_sha
            or child.work_pool_name != "managed"
            or child.parameters != {"ref": ref.model_dump(mode="json")}
            or child.parent_task_run_id is None
        ):
            raise ValueError(
                "Native child does not match its pinned deployment and admission"
            )
        parent_task = client.read_task_run(child.parent_task_run_id)
        if (
            parent_task.flow_run_id is None
            or parent_task.labels.get("prefect.execution_plan.node_id") != node
        ):
            raise ValueError("Unexpected native parent node")
        parent = client.read_flow_run(parent_task.flow_run_id)
    intent = saved(ref.owner, ref.key + "-intent")
    if (
        intent is None
        or parent.flow_id != plan.flow_id
        or digest(parent.parameters) != intent["parameters_sha256"]
        or parent.idempotency_key != intent["idempotency_key"]
    ):
        raise ValueError("Native parent does not match the admitted execution")
    observation = api("GET", f"/flow_runs/{parent.id}/execution-plan")
    if observation["snapshot"]["execution_plan_version_id"] != str(plan.version_id):
        raise ValueError("Native parent selected an unpinned plan")
    return admission, parent.id, parent.parameters, observation


def resolve_assessment(
    admission: Admission, run_id: UUID, parameters: dict, observation: dict
) -> tuple[str, dict] | None:
    plan = admission.runtime.triage_plan
    if plan is None:
        raise ValueError("Native triage is not configured")
    return read_assessment(run_id, plan, admission.request)


def redirect_unadmitted(
    admission: Admission, output: tuple[str, dict]
) -> tuple[str, dict]:
    branch, assessment = output
    if branch != "verify" or admission.item["route"]["route"] == "review_existing":
        return output
    return "record", {
        **assessment,
        "decision": "maintainer",
        "reason": "Assessment selected contribution verification, but intake admitted "
        "no contribution to verify. " + assessment["reason"],
    }


def assessed_item(admission: Admission, output: tuple[str, dict]) -> dict:
    branch, assessment = output
    route = admission.item["route"]["route"]
    if branch == "verify" and route != "review_existing":
        raise ValueError("Verification was not admitted by deterministic intake")
    if branch == "reproduce" and (
        route == "review_existing" or admission.requested.mode == "review"
    ):
        raise ValueError(
            "Reproduction would bypass an existing contribution or review-only mode"
        )
    return {**deepcopy(admission.item), "assessment": assessment}
