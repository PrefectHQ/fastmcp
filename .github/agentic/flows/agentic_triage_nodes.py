from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from prefect import flow, get_run_logger
from prefect.client.orchestration import get_client
from prefect.context import FlowRunContext
from prefect.events import emit_event

from flows import agentic_telemetry  # noqa: F401 - sends spans to Logfire
from flows.agentic_existing_review import verify_existing
from flows.agentic_stack_plans import Reproduction
from flows.agentic_stack_runtime import intake_tools, record, saved
from flows.agentic_triage_records import (
    Admission,
    AdmissionRef,
    assessed_item,
    redirect_unadmitted,
    resolve_assessment,
    selected_output,
    worker_admission,
)


def verify_branch(ref: AdmissionRef, admission: Admission, parent_id: UUID) -> dict:
    """Verify the contribution an assessment pointed to, and retain the result."""
    output = resolve_assessment(admission, parent_id)
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
    record(
        ref.owner,
        ref.key + "-verification",
        {"run_id": current_run(), "result": result},
    )
    return result


def current_run() -> str:
    context = FlowRunContext.get()
    assert context is not None and context.flow_run is not None
    return str(context.flow_run.id)


@flow(name="fastmcp-triage-verify", timeout_seconds=6900)
def triage_verify(ref: AdmissionRef) -> dict:
    admission, parent_id, _, _ = worker_admission(ref, "verify")
    return verify_branch(ref, admission, parent_id)


def reproduction_destination(decision: str) -> str:
    """Review bin for a reproduction outcome.

    A reproduction that hands the call to a maintainer is a decision, not missing
    evidence; the Slack outcome labels each evidence item by its decision.
    """
    return {"environment_failure": "operational", "maintainer": "decisions"}.get(
        decision, "evidence"
    )


def finalize(
    ref: AdmissionRef,
    admission: Admission,
    parent_id: UUID,
    observation: dict,
    verification: dict | None = None,
) -> dict:
    """Bin the plan's outcome for one issue and retain it.

    `verification` is the verify branch's result when it ran in this process;
    without it, the result a separate verify deployment retained is checked
    against that completed child run.
    """
    number = admission.requested.issue_number
    output = resolve_assessment(admission, parent_id)
    destination = "operational"
    stage, outcome, decision = "assess", None, None
    result: dict = {
        "issue_number": number,
        "reason": "Assessment did not produce a usable decision",
        "native_run_id": str(parent_id),
    }
    if output is not None:
        output = redirect_unadmitted(admission, output)
        assessed_item(admission, output)
        branch, assessment = output
        decision = assessment.get("decision")
        if branch in {"record", "blocked"}:
            destination = {
                "prune": "pruning",
                "evidence_needed": "evidence",
                "skip": "unchanged",
                "environment_failure": "operational",
            }.get(assessment["decision"], "decisions")
            result = {"issue_number": number, **assessment}
        elif branch == "reproduce":
            stage = "reproduce"
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
                destination = reproduction_destination(reproduction["decision"])
                outcome = reproduction["decision"]
                result = {
                    "issue_number": number,
                    "assessment": assessment,
                    **reproduction,
                }
        elif branch == "verify" and verification is not None:
            stage = "verify"
            result = verification["review"]
            destination = verified_destination(result)
            outcome = result.get("status")
        elif branch == "verify":
            stage = "verify"
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
                destination = verified_destination(result)
                outcome = result.get("status")
    final = {
        "destination": destination,
        "result": result,
        "native_run_id": str(parent_id),
        "finalizer_run_id": current_run(),
    }
    record(ref.owner, ref.key + "-outcome", final)
    record(parent_id, ref.key + "-outcome", final)
    announce_stage(
        number,
        {
            "stage": stage,
            "decision": decision,
            "outcome": outcome if isinstance(outcome, str) else None,
            "destination": destination,
        },
    )
    return final


def announce_stage(number: int, summary: dict) -> None:
    """Tell the floor which stage an item last went through and how it came out.

    Reproduce runs as a node inside the plan and verify runs inside intake, so
    neither shows up as its own flow run; finalize sets the stage in the same
    branch that handles it. Only enum values leave: no free text from the issue.
    """
    try:
        emit_event(
            event="fastmcp.triage.stage",
            resource={"prefect.resource.id": f"fastmcp.triage.stage.{number}"},
            payload={"number": number, **summary},
        )
    except Exception as error:  # noqa: BLE001 - visibility only, never fatal
        get_run_logger().warning("Stage event failed: %s", error)


def verified_destination(review: dict) -> str:
    if any(
        value["status"] == "environment_failure" for value in review["verification"]
    ):
        review["status"] = "environment_failure"
        return "operational"
    return "existing_review"


@flow(name="fastmcp-triage-finalize", timeout_seconds=300)
def triage_finalize(ref: AdmissionRef) -> dict:
    admission, parent_id, _, observation = worker_admission(ref, "finalize")
    return finalize(ref, admission, parent_id, observation)
