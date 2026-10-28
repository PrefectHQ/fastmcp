from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from prefect import flow, task
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import (
    ArtifactFilter,
    DeploymentFilter,
    FlowRunFilter,
)
from prefect.client.schemas.objects import State
from prefect.client.schemas.sorting import ArtifactSort
from prefect.context import FlowRunContext
from prefect.states import Completed

from flows.agentic_backlog import Intake, due, inventory, settle
from flows.agentic_freshness import revalidate
from flows.agentic_intake import evidence_revision, upgrade_policy_outcomes
from flows.agentic_review_queue import plain, retain_markdown, retain_review_queue
from flows.agentic_stack_policy import Batch
from flows.agentic_stack_runtime import (
    Runtime,
    collect,
    gh_api,
    intake_tools,
    secret_sync,
)


def latest(key: str, owner: UUID | None = None) -> dict | None:
    with get_client(sync_client=True) as client:
        records = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [key]}),
            flow_run_filter=FlowRunFilter(id={"any_": [owner]}) if owner else None,
            sort=ArtifactSort.CREATED_DESC,
            limit=1,
        )
    return records[0].data if records else None


def retain(owner: UUID, key: str, data: dict) -> None:
    previous = latest(key, owner)
    if previous is not None:
        if previous != data:
            raise ValueError("Conflicting intake progress record")
        return
    with get_client(sync_client=True) as client:
        client.create_artifact(
            ArtifactCreate(
                key=key,
                flow_run_id=owner,
                type="fastmcp-triage-intake",
                data=data,
            )
        )


def checkpoint(scope: str) -> tuple[UUID, dict] | None:
    with get_client(sync_client=True) as client:
        records = client.read_artifacts(
            artifact_filter=ArtifactFilter(
                key={"any_": [scope + "-admitted", scope + "-committed"]}
            ),
            sort=ArtifactSort.CREATED_DESC,
            limit=1,
        )
    if not records:
        return None
    record = records[0]
    if record.flow_run_id is None or not isinstance(record.data, dict):
        raise ValueError("Invalid intake checkpoint")
    return record.flow_run_id, record.data


@task(retries=3, retry_delay_seconds=[2, 5, 10], persist_result=False)
def github(endpoint: str) -> object:
    environment = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PREFECT_API_KEY", "PREFECT_API_AUTH_STRING", "GITHUB_TOKEN"}
    }
    environment["GH_TOKEN"] = secret_sync("fastmcp-triage-gh-token")
    return gh_api(endpoint, environment, 60)


def admit(
    config: Intake,
    runtime: Runtime,
    controller_id: UUID,
    owner: UUID,
    read: Callable[[str], object] = github,
) -> dict:
    scope = config.scope()
    previous = checkpoint(scope)
    state: dict = {"pending": [], "next_page": 1}
    if previous:
        previous_owner, state = previous
        with get_client(sync_client=True) as client:
            run = client.read_flow_run(previous_owner)
        if previous_owner != owner and (run.state is None or not run.state.is_final()):
            raise ValueError("Another intake run still owns this queue")
        if state["phase"] == "admitted" or previous_owner == owner:
            return state
    now = datetime.now(UTC)
    pending = state["pending"]
    coverage = {"scope": "retained queue", "metadata_rows": 0, "deferred": 0}
    page = state["next_page"]
    if not pending or config.scoped():
        scanned = inventory(read, config, page)
        page = scanned["next_page"]
        coverage = {**scanned["coverage"], "deferred": 0}
        queued = {item["issue_number"]: item for item in pending}
        admitted = []
        for item in scanned["pending"]:
            number = item["issue_number"]
            if number in queued:
                if config.scoped():
                    admitted.append(queued[number])
                continue
            receipt = latest(f"{scope}-issue-{number}")
            if due(receipt, item, config, now, runtime.source_sha):
                admitted.append(item)
            else:
                coverage["deferred"] += 1
        targets = {item["issue_number"] for item in admitted}
        pending = admitted + [
            item for item in pending if item["issue_number"] not in targets
        ]
    selected = pending[: config.max_items]
    batch = None
    if selected:
        base = read(f"repos/PrefectHQ/fastmcp/commits/{config.branch}")
        if not isinstance(base, dict):
            raise ValueError("Missing release target")
        batch = Batch.model_validate(
            {
                "id": "intake-" + owner.hex,
                "started_at": now,
                "expires_at": now + timedelta(hours=2),
                "groups": [
                    {
                        "name": "triage",
                        "rationale": "Triage selected backlog items using repository policy, preserve existing contributions and human holds, and retain evidence for maintainer decisions.",
                        "branch": config.branch,
                        "base_sha": base["sha"],
                        "items": [
                            {"issue_number": row["issue_number"], "mode": "mre"}
                            for row in selected
                        ],
                    }
                ],
            }
        ).model_dump(mode="json")
    plan = {
        "phase": "admitted",
        "pending": pending,
        "next_page": page,
        "coverage": coverage,
        "batch": batch,
        "runtime": runtime.model_dump(mode="json"),
        "controller_id": str(controller_id),
    }
    retain(owner, scope + "-admitted", plan)
    return plan


@task(retries=3, retry_delay_seconds=[2, 5, 10], persist_result=False)
def dispatch(plan: dict) -> UUID:
    batch = Batch.model_validate(plan["batch"])
    runtime = Runtime.model_validate(plan["runtime"])
    parameters = {"batch": plan["batch"], "runtime": plan["runtime"]}
    name = "fastmcp-stack-" + batch.id
    with get_client(sync_client=True) as client:
        matches = client.read_flow_runs(
            flow_run_filter=FlowRunFilter(name={"any_": [name]}),
            deployment_filter=DeploymentFilter(
                id={"any_": [UUID(plan["controller_id"])]}
            ),
            limit=2,
        )
        if matches:
            if (
                len(matches) != 1
                or matches[0].idempotency_key != name
                or matches[0].parameters != parameters
                or matches[0].deployment_version != runtime.source_sha
            ):
                raise ValueError("Controller identity or inputs changed")
            return matches[0].id
        deployment = client.read_deployment(UUID(plan["controller_id"]))
        if (
            deployment.version != runtime.source_sha
            or deployment.work_pool_name != "managed"
            or deployment.name != "cloud-stack-batch"
        ):
            raise ValueError("Controller deployment changed before intake dispatch")
        if datetime.now(UTC) >= batch.expires_at:
            raise ValueError("Undispatched intake admission expired")
        run = client.create_flow_run_from_deployment(
            deployment.id, name=name, parameters=parameters, idempotency_key=name
        )
        return run.id


def finish(
    plan: dict,
    owner: UUID,
    scope: str,
    run_id: UUID | None,
    error: str | None = None,
    read_evidence: Callable[[int, str], dict] = collect,
) -> dict:
    finalized = latest(scope + "-finalized", owner)
    if finalized is not None:
        return commit(owner, scope, finalized)
    result = None
    completed = []
    pending = plan["pending"]
    receipts = []
    if plan["batch"]:
        timestamp = plan["batch"]["started_at"]
        if run_id:
            with get_client(sync_client=True) as client:
                run = client.read_flow_run(run_id)
            if run.state is None or not run.state.is_final():
                raise ValueError("Controller still owns admitted work")
            if (
                run.deployment_id != UUID(plan["controller_id"])
                or run.deployment_version != plan["runtime"]["source_sha"]
                or run.parameters
                != {"batch": plan["batch"], "runtime": plan["runtime"]}
                or run.idempotency_key != "fastmcp-stack-" + plan["batch"]["id"]
            ):
                raise ValueError("Completed controller no longer matches the admission")
            timestamp = (
                run.end_time.isoformat()
                if run.end_time
                else run.state.timestamp.isoformat()
            )
            result = latest(f"{plan['batch']['id']}-review-bins", run_id)
            if result is None:
                group = plan["batch"]["groups"][0]
                outcome = latest(
                    f"{plan['batch']['id']}-{group['name']}-outcome", run_id
                )
                if outcome is not None:
                    result = {
                        **outcome,
                        "batch_id": plan["batch"]["id"],
                        "usage": {"status": "unavailable"},
                    }
            if result is not None:
                if result.get("batch_id") != plan["batch"]["id"]:
                    raise ValueError("Review bins describe a different admission")
                result = upgrade_policy_outcomes(result)
        selected = [
            item["issue_number"] for item in plan["batch"]["groups"][0]["items"]
        ]
        if result is None:
            result = {
                "batch_id": plan["batch"]["id"],
                "bins": {
                    "operational": [
                        {
                            "reason": error
                            or "Controller did not complete with saved review bins",
                            "run_id": str(run_id),
                        }
                    ]
                },
            }
        originals = {}
        for number in selected:
            reviewed = (
                latest(f"{plan['batch']['id']}-issue-{number}-review-evidence", run_id)
                if run_id
                else None
            )
            intake = (
                latest(f"{plan['batch']['id']}-issue-{number}-intake", run_id)
                if run_id
                else None
            )
            if reviewed is not None:
                originals[number] = reviewed
            elif intake:
                originals[number] = intake["evidence"]
        result = revalidate(
            result,
            originals,
            {number: plan["batch"]["groups"][0]["branch"] for number in selected},
            read_evidence,
        )
        pending, completed = settle(pending, selected, result)
        exhausted = [row["issue_number"] for row in pending if row["attempts"] >= 3]
        for item in plan["pending"]:
            number = item["issue_number"]
            if number not in completed and number not in exhausted:
                continue
            receipt = {
                **item,
                "status": "triaged" if number in completed else "operational",
                "processed_at": timestamp,
                "source_sha": plan["runtime"]["source_sha"],
                "controller_run_id": str(run_id),
                "evidence_revision": evidence_revision(originals[number])
                if number in originals
                else None,
                "baseline_sha": originals.get(number, {}).get("base_sha"),
            }
            receipts.append(receipt)
        pending = [row for row in pending if row["issue_number"] not in exhausted]
    state = {
        "phase": "committed",
        "pending": pending,
        "next_page": plan["next_page"],
        "coverage": plan["coverage"],
        "controller_run_id": str(run_id) if run_id else None,
        "completed": completed,
        "receipts": receipts,
        "review": result,
    }
    metrics = {
        "selected": len(plan["batch"]["groups"][0]["items"]) if plan["batch"] else 0,
        "triaged": len(completed),
        "pending": len(pending),
        "exhausted": sum(receipt["status"] == "operational" for receipt in receipts),
        "review_items": sum(
            len(items)
            for name, items in result["bins"].items()
            if name != "operational"
        )
        if result
        else 0,
    }
    state["metrics"] = metrics
    retain(owner, scope + "-finalized", state)
    return commit(owner, scope, state)


def commit(owner: UUID, scope: str, state: dict) -> dict:
    if state["review"] is not None:
        retain_review_queue(owner, state["review"])
    for receipt in state["receipts"]:
        retain(owner, f"{scope}-issue-{receipt['issue_number']}", receipt)
    lines = ["# FastMCP intake coverage", "", "| Measure | Observed |", "| --- | --- |"]
    lines.extend(
        f"| {plain(name)} | {plain(value)} |"
        for name, value in {**state["coverage"], **state["metrics"]}.items()
    )
    lines.extend(
        [
            "",
            f"Next backlog page: {state['next_page']}. A completed intake run does not mean the backlog is exhausted.",
            "",
            "Deferred metadata is a cooldown, not proof that linked PRs are unchanged. Full evidence is refreshed when an item is handled. Schedules are not enabled.",
        ]
    )
    if state["controller_run_id"]:
        lines.extend(
            [
                "",
                f"Controller run: `{state['controller_run_id']}`. Its review queue contains revision-bound evidence and observed usage.",
            ]
        )
    retain_markdown(owner, scope + "-coverage", "\n".join(lines) + "\n")
    retain(owner, scope + "-committed", state)
    return state


@flow(name="fastmcp-triage-intake", timeout_seconds=7500)
def triage_intake(
    config: Intake, runtime: Runtime, controller_id: UUID
) -> dict | State:
    context = FlowRunContext.get()
    if context is None or context.flow_run is None:
        raise ValueError("Managed intake run required")
    current = context.flow_run
    if (
        current.deployment_id is None
        or current.deployment_version != runtime.source_sha
        or current.work_pool_name != "managed"
    ):
        raise ValueError("Intake must run from its pinned managed deployment")
    with get_client(sync_client=True) as client:
        deployment = client.read_deployment(current.deployment_id)
    if (
        deployment.name != "cloud-triage-intake"
        or deployment.global_concurrency_limit is None
        or deployment.global_concurrency_limit.limit != 1
    ):
        raise ValueError("Intake requires its serial deployment")
    with tempfile.TemporaryDirectory(prefix="fastmcp-intake-") as temporary:
        intake_tools(Path(temporary))
        plan = admit(config, runtime, controller_id, current.id)
        if plan["phase"] == "committed":
            return plan
        try:
            run_id = dispatch(plan) if plan["batch"] else None
        except ValueError as error:
            result = finish(plan, current.id, config.scope(), None, str(error))
            return Completed(name="Degraded", data=result, message=str(error))
        while run_id:
            with get_client(sync_client=True) as client:
                child = client.read_flow_run(run_id)
            if child.state is not None and child.state.is_final():
                break
            time.sleep(5)
        result = finish(plan, current.id, config.scope(), run_id)
        if result["review"] and result["review"]["bins"]["operational"]:
            reasons = [
                f"#{item.get('issue_number', '?')}: {item.get('reason') or item.get('status', 'needs operational attention')}"
                for item in result["review"]["bins"]["operational"]
            ]
            return Completed(
                name="Degraded", data=result, message="; ".join(reasons)[:2000]
            )
        return result
