from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import GlobalConcurrencyLimitCreate
from prefect.client.schemas.objects import ConcurrencyLeaseHolder
from prefect.concurrency.sync import concurrency
from prefect.exceptions import ObjectAlreadyExists, ObjectNotFound

if TYPE_CHECKING:
    from prefect.client.schemas.objects import FlowRun

    from flows.agentic_stack_policy import Batch


def ensure_claim(name: str, slots: int = 1) -> None:
    if slots < 1:
        raise ValueError("Ownership capacity must be positive")
    with get_client(sync_client=True) as client:
        try:
            limit = client.read_global_concurrency_limit_by_name(name)
        except ObjectNotFound:
            try:
                client.create_global_concurrency_limit(
                    GlobalConcurrencyLimitCreate(name=name, limit=slots)
                )
            except ObjectAlreadyExists:
                pass
            limit = client.read_global_concurrency_limit_by_name(name)
        if not limit.active or limit.limit != slots or limit.slot_decay_per_second:
            raise ValueError(f"Exclusive claim is misconfigured: {name}")


@contextmanager
def claim_work(
    names: list[str], owner: UUID, deadline: datetime, *, slots: int = 1
) -> Generator[None]:
    names = sorted(set(names))
    if not names:
        raise ValueError("Work requires an explicit claim")
    for name in names:
        ensure_claim(name, slots)
    remaining = (deadline - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise TimeoutError("Work expired before ownership acquisition")
    with concurrency(
        names,
        timeout_seconds=remaining,
        lease_duration=120,
        strict=True,
        raise_on_lease_renewal_failure=True,
        holder=ConcurrencyLeaseHolder(type="flow_run", id=owner),
    ):
        if datetime.now(UTC) >= deadline:
            raise TimeoutError("Work expired while waiting for ownership")
        yield


def owned_child(run: FlowRun, owner: UUID, batch_id: str, runtime: dict) -> bool:
    parameters = run.parameters
    nested = parameters.get("parameters")
    ref = parameters.get("ref") or (
        nested.get("ref") if isinstance(nested, dict) else None
    )
    context = parameters.get("context")
    spec = parameters.get("spec")
    ref_matches = isinstance(ref, dict) and ref.get("owner") == str(owner)
    source_matches = run.deployment_version == runtime["source_sha"]
    native = runtime.get("triage_plan") or {}
    worker_ids = {
        runtime.get("triage_verify_id"),
        runtime.get("triage_finalize_id"),
    } - {None}
    role_flows = {plan["flow_id"] for plan in runtime["plans"].values()}
    return (
        str(run.flow_id) == native.get("flow_id")
        and ref_matches
        or str(run.deployment_id) in worker_ids
        and source_matches
        and ref_matches
        or str(run.flow_id) in role_flows
        and isinstance(context, dict)
        and context.get("controller_owner") == str(owner)
        or str(run.deployment_id) == runtime["validator_id"]
        and source_matches
        and isinstance(spec, dict)
        and spec.get("batch_id") == batch_id
        and (run.idempotency_key or "").startswith(f"fastmcp-validation-{owner}-")
    )


def drain_predecessor(owner: UUID, batch_id: str, deadline: datetime) -> None:
    import time

    from prefect.client.schemas.filters import FlowFilter, FlowRunFilter
    from prefect.states import Cancelling

    with get_client(sync_client=True) as client:
        prior = client.read_flow_run(owner)
        if prior.parameters.get("batch", {}).get("id") != batch_id:
            raise ValueError("Previous issue owner does not match its recorded batch")
        runtime = prior.parameters["runtime"]
        flow_ids = {UUID(plan["flow_id"]) for plan in runtime["plans"].values()}
        if runtime.get("triage_plan"):
            flow_ids.add(UUID(runtime["triage_plan"]["flow_id"]))
        for name in (
            "validator_id",
            "verification_id",
            "triage_verify_id",
            "triage_finalize_id",
        ):
            if runtime.get(name):
                flow_ids.add(client.read_deployment(UUID(runtime[name])).flow_id)
        numbers = {
            item["issue_number"]
            for group in prior.parameters["batch"]["groups"]
            for item in group["items"]
        }
        if prior.state is None or not prior.state.is_final():
            client.set_flow_run_state(owner, Cancelling(), force=True)
        while True:
            active = []
            seen = set()
            while True:
                page = client.read_flow_runs(
                    flow_filter=FlowFilter(id={"any_": list(flow_ids)}),
                    flow_run_filter=FlowRunFilter(
                        id={"not_any_": list(seen)},
                        state={
                            "type": {
                                "any_": [
                                    "SCHEDULED",
                                    "PENDING",
                                    "RUNNING",
                                    "CANCELLING",
                                    "PAUSED",
                                ]
                            }
                        },
                    ),
                    limit=200,
                )
                active.extend(
                    run
                    for run in page
                    if run.id == owner
                    or owned_child(run, owner, batch_id, runtime)
                    or (
                        str(run.deployment_id) == runtime.get("verification_id")
                        and run.deployment_version == runtime["source_sha"]
                        and isinstance(run.parameters.get("spec"), dict)
                        and run.parameters["spec"].get("issue_number") in numbers
                    )
                )
                if len(page) < 200:
                    break
                seen.update(run.id for run in page)
                if len(seen) >= 2000:
                    raise ValueError(
                        "Too many candidate children to establish safe recovery"
                    )
            prior = client.read_flow_run(owner)
            if prior.state is None or not prior.state.is_final():
                active.append(prior)
            if not active:
                return
            for run in active:
                owned = run.id == owner or owned_child(run, owner, batch_id, runtime)
                if owned and (run.state is None or not run.state.is_cancelling()):
                    client.set_flow_run_state(run.id, Cancelling(), force=True)
            if datetime.now(UTC) >= deadline:
                raise TimeoutError("Previous owner has not acknowledged cancellation")
            time.sleep(1)


@contextmanager
def claim_issues(batch: Batch, owner: UUID) -> Generator[None]:
    from prefect.client.schemas.actions import ArtifactCreate
    from prefect.client.schemas.filters import ArtifactFilter
    from prefect.client.schemas.sorting import ArtifactSort

    numbers = sorted(
        {item.issue_number for group in batch.groups for item in group.items}
    )
    names = [f"fastmcp-issue-{number}" for number in numbers]
    with (
        claim_work(names, owner, batch.expires_at),
        get_client(sync_client=True) as client,
    ):
        predecessors = set()
        for name in names:
            records = client.read_artifacts(
                artifact_filter=ArtifactFilter(
                    key={"any_": [name + "-assignment"]},
                    type={"any_": ["fastmcp-work-assignment"]},
                ),
                sort=ArtifactSort.CREATED_DESC,
                limit=1,
            )
            if records:
                prior = records[0].data
                if not isinstance(prior, dict):
                    raise ValueError("Previous assignment is invalid")
                if prior["owner"] != str(owner):
                    predecessors.add((UUID(prior["owner"]), prior["batch_id"]))
        for prior_owner, prior_batch in sorted(predecessors):
            drain_predecessor(prior_owner, prior_batch, batch.expires_at)
        for name in names:
            client.create_artifact(
                ArtifactCreate(
                    key=name + "-assignment",
                    type="fastmcp-work-assignment",
                    flow_run_id=owner,
                    data={"owner": str(owner), "batch_id": batch.id},
                )
            )
        yield
