from __future__ import annotations

from uuid import UUID

import httpx
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import ArtifactFilter
from prefect.exceptions import ObjectNotFound

from flows.agentic_assessment_cache import assessment_fingerprint
from flows.agentic_stack import digest
from flows.agentic_stack_runtime import api, record, saved
from flows.agentic_triage_records import Admission, AdmissionRef


def native_cache_key(admission: Admission) -> str:
    fingerprint = assessment_fingerprint(admission.request)
    if fingerprint is None:
        raise ValueError("Native reuse requires a complete revision fingerprint")
    return "fastmcp-native-result-" + digest(
        [
            fingerprint,
            admission.runtime.model_dump(mode="json"),
            admission.requested.model_dump(mode="json"),
            admission.group.branch,
            admission.group.base_sha,
            admission.reproduce_request.get("instructions"),
        ]
    )


def verified_outcome(
    ref: AdmissionRef, admission: Admission, run_id: UUID
) -> dict | None:
    outcome = saved(ref.owner, ref.key + "-outcome")
    if outcome is None:
        return None
    runtime = admission.runtime
    with get_client(sync_client=True) as client:
        finalizer = client.read_flow_run(UUID(outcome["finalizer_run_id"]))
        task = (
            client.read_task_run(finalizer.parent_task_run_id)
            if finalizer.parent_task_run_id
            else None
        )
    if (
        outcome["native_run_id"] != str(run_id)
        or finalizer.state is None
        or not finalizer.state.is_completed()
        or finalizer.deployment_id != runtime.triage_finalize_id
        or finalizer.deployment_version != runtime.source_sha
        or finalizer.parameters != {"ref": ref.model_dump(mode="json")}
        or task is None
        or task.flow_run_id != run_id
        or task.labels.get("prefect.execution_plan.node_id") != "finalize"
    ):
        raise ValueError(
            "Retained native result does not match its completed finalizer"
        )
    return outcome


def reusable(outcome: dict) -> bool:
    return outcome["destination"] != "operational" and outcome["result"].get(
        "status"
    ) not in {"baseline_changed", "stale", "environment_failure"}


def retain_native_result(
    ref: AdmissionRef, admission: Admission, outcome: dict
) -> None:
    if reusable(outcome):
        record(
            ref.owner,
            native_cache_key(admission),
            {"ref": ref.model_dump(mode="json"), "run_id": outcome["native_run_id"]},
        )


def find_native_result(admission: Admission) -> dict | None:
    key = native_cache_key(admission)
    with get_client(sync_client=True) as client:
        records = client.read_artifacts(
            artifact_filter=ArtifactFilter(
                key={"any_": [key]}, type={"any_": ["fastmcp-stack-controller"]}
            ),
            limit=2,
        )
    for artifact in records:
        try:
            if not isinstance(artifact.data, dict):
                continue
            ref = AdmissionRef.model_validate(artifact.data["ref"])
            raw = saved(ref.owner, ref.key)
            if raw is None or digest(raw) != ref.binding:
                continue
            original = Admission.model_validate(raw)
            if native_cache_key(original) != key:
                continue
            run_id = UUID(artifact.data["run_id"])
            with get_client(sync_client=True) as client:
                run = client.read_flow_run(run_id)
            plan = admission.runtime.triage_plan
            intent = saved(ref.owner, ref.key + "-intent")
            if (
                plan is None
                or run.state is None
                or not run.state.is_completed()
                or run.flow_id != plan.flow_id
                or intent is None
                or digest(run.parameters) != intent["parameters_sha256"]
                or run.parameters.get("parameters")
                != {"ref": ref.model_dump(mode="json")}
            ):
                continue
            observation = api("GET", f"/flow_runs/{run_id}/execution-plan")
            if observation["status"] != "completed" or observation["snapshot"][
                "execution_plan_version_id"
            ] != str(plan.version_id):
                continue
            outcome = verified_outcome(ref, original, run_id)
            if outcome is not None and reusable(outcome):
                return outcome
        except (ValueError, TypeError, KeyError, ObjectNotFound):
            continue
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
    return None
