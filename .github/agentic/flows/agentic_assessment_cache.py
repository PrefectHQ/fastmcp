from __future__ import annotations

import re
from copy import deepcopy
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import ArtifactFilter, FlowFilter, FlowRunFilter
from prefect.client.schemas.sorting import FlowRunSort
from prefect.exceptions import ObjectNotFound

from flows.agentic_stack import digest


def assessment_fingerprint(parameters: dict) -> str | None:
    context = parameters.get("context", {})
    revision = context.get("route", {}).get("revision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
        return None
    value = deepcopy(parameters)
    value["context"].pop("retained_intake_sha256", None)
    return digest(value)


def assessment_key(flow_id: UUID, version_id: UUID, parameters: dict) -> str | None:
    fingerprint = assessment_fingerprint(parameters)
    if fingerprint is None:
        return None
    return "fastmcp-assessment-" + digest([str(flow_id), str(version_id), fingerprint])


def assessment_candidates(
    flow_id: UUID, version_id: UUID, parameters: dict
) -> list[UUID]:
    fingerprint = assessment_fingerprint(parameters)
    key = assessment_key(flow_id, version_id, parameters)
    if key is None:
        return []
    with get_client(sync_client=True) as client:
        records = client.read_artifacts(
            artifact_filter=ArtifactFilter(
                key={"any_": [key]}, type={"any_": ["fastmcp-stack-controller"]}
            ),
            limit=2,
        )
        indexed = []
        for record in records:
            if not isinstance(record.data, dict):
                continue
            try:
                run_id = UUID(str(record.data.get("run_id")))
                run = client.read_flow_run(run_id)
            except (ValueError, ObjectNotFound):
                continue
            if (
                run.flow_id == flow_id
                and run.state is not None
                and run.state.is_completed()
                and assessment_fingerprint(run.parameters) == fingerprint
                and run.id not in indexed
            ):
                indexed.append(run.id)
        if indexed:
            return indexed
        runs = client.read_flow_runs(
            flow_filter=FlowFilter(id={"any_": [flow_id]}),
            flow_run_filter=FlowRunFilter(state={"type": {"any_": ["COMPLETED"]}}),
            sort=FlowRunSort.END_TIME_DESC,
            limit=12,
        )
    return [
        run.id for run in runs if assessment_fingerprint(run.parameters) == fingerprint
    ]
