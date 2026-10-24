from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from prefect import flow
from prefect.client.orchestration import get_client
from prefect.context import FlowRunContext

from flows.agentic_existing_review import verify_existing
from flows.agentic_review_queue import retain_review_queue
from flows.agentic_stack_plans import Reproduction
from flows.agentic_stack_runtime import intake_tools, record, saved
from flows.agentic_triage_records import (
    AdmissionRef,
    assessed_item,
    redirect_unadmitted,
    resolve_assessment,
    selected_output,
    worker_admission,
)


@flow(name="fastmcp-triage-verify", timeout_seconds=6900)
def triage_verify(ref: AdmissionRef) -> dict:
    admission, parent_id, parameters, observation = worker_admission(ref, "verify")
    output = resolve_assessment(admission, parent_id, parameters, observation)
    if output is None or output[0] != "verify":
        raise ValueError("Verification requires the selected verification branch")
    if redirect_unadmitted(admission, output)[0] != "verify":
        return {"status": "not_admitted"}
    item = assessed_item(admission, output)
    with tempfile.TemporaryDirectory(prefix="fastmcp-native-verify-") as temporary:
        intake_tools(Path(temporary))
        result = verify_existing(
            ref.owner,
            admission.batch,
            admission.group,
            admission.runtime,
            item,
            admission.requested.verification_probes,
            stage=ref.key.rsplit("-native-", 1)[1],
            retry_key=str(parent_id),
        )
    context = FlowRunContext.get()
    assert context is not None and context.flow_run is not None
    record(
        ref.owner,
        ref.key + "-verification",
        {"run_id": str(context.flow_run.id), "result": result},
    )
    return result


@flow(name="fastmcp-triage-finalize", timeout_seconds=300)
def triage_finalize(ref: AdmissionRef) -> dict:
    admission, parent_id, parameters, observation = worker_admission(ref, "finalize")
    number = admission.requested.issue_number
    output = resolve_assessment(admission, parent_id, parameters, observation)
    destination = "operational"
    result: dict = {
        "issue_number": number,
        "reason": "Assessment did not produce a usable decision",
        "native_run_id": str(parent_id),
    }
    if output is not None:
        output = redirect_unadmitted(admission, output)
        assessed_item(admission, output)
        branch, assessment = output
        if branch in {"record", "blocked"}:
            destination = {
                "prune": "pruning",
                "evidence_needed": "evidence",
                "skip": "unchanged",
                "environment_failure": "operational",
            }.get(assessment["decision"], "decisions")
            result = {"issue_number": number, **assessment}
        elif branch == "reproduce":
            reproduced = selected_output(parent_id, observation, "reproduce")
            if reproduced is None:
                result["reason"] = "Reproduction node did not produce evidence"
                result["assessment"] = assessment
            else:
                reproduction = Reproduction.model_validate(reproduced[1]).model_dump(
                    mode="json"
                )
                if reproduction["base_sha"] != admission.group.base_sha:
                    raise ValueError("Reproduction used a different baseline")
                destination = (
                    "operational"
                    if reproduction["decision"] == "environment_failure"
                    else "evidence"
                )
                result = {
                    "issue_number": number,
                    "assessment": assessment,
                    **reproduction,
                }
        elif branch == "verify":
            verified = selected_output(parent_id, observation, "verify")
            if verified is None or verified[0] != "completed":
                result["reason"] = "Contribution verification did not complete"
                result["assessment"] = assessment
            else:
                retained = saved(ref.owner, ref.key + "-verification")
                if (
                    retained is None
                    or retained["run_id"] != verified[1]["child_flow_run_id"]
                ):
                    raise ValueError("Missing exact verification child outcome")
                with get_client(sync_client=True) as client:
                    child = client.read_flow_run(UUID(retained["run_id"]))
                if (
                    child.state is None
                    or not child.state.is_completed()
                    or child.deployment_id != admission.runtime.triage_verify_id
                    or child.deployment_version != admission.runtime.source_sha
                    or child.parameters != {"ref": ref.model_dump(mode="json")}
                ):
                    raise ValueError(
                        "Verification outcome does not match its completed deployment"
                    )
                result = retained["result"]["review"]
                destination = "existing_review"
                if any(
                    value["status"] == "environment_failure"
                    for value in result["verification"]
                ):
                    destination = "operational"
                    result["status"] = "environment_failure"
    context = FlowRunContext.get()
    assert context is not None and context.flow_run is not None
    final = {
        "destination": destination,
        "result": result,
        "native_run_id": str(parent_id),
        "finalizer_run_id": str(context.flow_run.id),
    }
    record(ref.owner, ref.key + "-outcome", final)
    record(parent_id, ref.key + "-outcome", final)
    retain_review_queue(
        parent_id, {"batch_id": ref.key, "bins": {destination: [result]}}
    )
    return final
