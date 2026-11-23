"""Triage's Slack voice: an agent reads the day's state and decides what to say.

Intake no longer posts a line per issue. When it leaves an issue for a maintainer it
labels it `needs:triage` and records the next step under `fastmcp-triage-next-<n>`.
This flow gathers that queue, the last day of intake runs and what was already
posted, and hands them to the `digest` agent role. The agent decides whether anything
is worth a message and writes it; the flow only posts what the agent returns.

`morning` runs once a day for the digest. `check` runs every few hours and posts only
when the agent finds something that shouldn't wait for the morning.

The agent plan comes from `fastmcp_triage_config.digest` ({"flow_id", "version_id"});
without it the flow does nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from prefect import flow, get_run_logger
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import (
    ArtifactFilter,
    DeploymentFilter,
    FlowRunFilter,
)
from prefect.client.schemas.sorting import ArtifactSort
from prefect.context import FlowRunContext
from prefect.events import emit_event
from prefect.variables import Variable

from flows.agentic_github import GitHub
from flows.agentic_labels import CONFIG_VARIABLE, TRIAGE_LABEL
from flows.agentic_stack_runtime import AgentPlan, judge, secret_sync

REPOSITORY = "PrefectHQ/fastmcp"
QUEUE_URL = f"https://github.com/{REPOSITORY}/issues?q=" + quote(
    f'is:open label:"{TRIAGE_LABEL}"'
)
NEXT_KEY = "fastmcp-triage-next-{number}"
POSTED_KEY = "fastmcp-triage-digest-posted"


def next_key(number: int) -> str:
    return NEXT_KEY.format(number=number)


def record_next_step(owner: UUID, number: int, line: str) -> None:
    """Keep the latest next step for an issue so the digest can say it."""
    with get_client(sync_client=True) as client:
        client.create_artifact(
            ArtifactCreate(
                key=next_key(number),
                flow_run_id=owner,
                type="fastmcp-triage-next",
                data={"line": line, "at": datetime.now(UTC).isoformat()},
            )
        )


def artifacts(key: str, limit: int) -> list[dict]:
    with get_client(sync_client=True) as client:
        records = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [key]}),
            sort=ArtifactSort.CREATED_DESC,
            limit=limit,
        )
    return [record.data for record in records if isinstance(record.data, dict)]


def waiting(read: Callable[[str], object]) -> list[dict]:
    """Open issues labeled `needs:triage`, oldest first."""
    found = read(
        f"repos/{REPOSITORY}/issues?state=open&labels={quote(TRIAGE_LABEL)}"
        "&sort=created&direction=asc&per_page=100"
    )
    if not isinstance(found, list):
        return []
    return [item for item in found if "pull_request" not in item]


def queue_entry(item: dict, now: datetime) -> dict:
    number = item["number"]
    created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
    steps = artifacts(next_key(number), 1)
    return {
        "number": number,
        "title": item.get("title"),
        "author": (item.get("user") or {}).get("login"),
        "labels": sorted(label.get("name") for label in item.get("labels") or []),
        "age_days": (now - created).days,
        "comments": item.get("comments", 0),
        "next_step": steps[0].get("line") if steps else None,
    }


def intake_runs(since: datetime) -> dict:
    with get_client(sync_client=True) as client:
        runs = client.read_flow_runs(
            deployment_filter=DeploymentFilter(name={"any_": ["cloud-triage-intake"]}),
            flow_run_filter=FlowRunFilter(start_time={"after_": since}),
            limit=200,
        )
    states: dict[str, int] = {}
    problems = []
    for run in runs:
        if run.state is None:
            continue
        name = run.state.name or "Unknown"
        states[name] = states.get(name, 0) + 1
        if name != "Completed" and run.state.message:
            problems.append(
                {
                    "state": name,
                    "at": str(run.start_time),
                    "message": run.state.message[:600],
                }
            )
    return {"since": since.isoformat(), "states": states, "problems": problems[:20]}


def post(text: str, owner: UUID) -> None:
    emit_event(
        event="fastmcp.triage.outcome",
        # The Slack automation (fastmcp-triage-outcome) matches fastmcp.triage.intake.*
        resource={"prefect.resource.id": f"fastmcp.triage.intake.digest-{owner}"},
        payload={"text": text},
    )
    with get_client(sync_client=True) as client:
        client.create_artifact(
            ArtifactCreate(
                key=POSTED_KEY,
                flow_run_id=owner,
                type="fastmcp-triage-digest",
                data={"text": text, "at": datetime.now(UTC).isoformat()},
            )
        )


def digest_plan(raw: object) -> AgentPlan | None:
    value = raw.get("digest") if isinstance(raw, dict) else None
    return AgentPlan.model_validate(value) if isinstance(value, dict) else None


@flow(name="fastmcp-triage-digest", timeout_seconds=900)
def digest(mode: Literal["morning", "check"] = "morning") -> dict:
    logger = get_run_logger()
    plan = digest_plan(Variable.get(CONFIG_VARIABLE, default={}))
    if plan is None:
        logger.info("No digest plan configured; nothing to do")
        return {"mode": mode, "posted": False, "why": "no digest plan configured"}
    context = FlowRunContext.get()
    assert context is not None and context.flow_run is not None
    owner = context.flow_run.id
    now = datetime.now(UTC)
    read = GitHub(secret_sync("fastmcp-triage-gh-token"))
    state = {
        "mode": mode,
        "now": now.isoformat(),
        "queue_url": QUEUE_URL,
        "queue": [queue_entry(item, now) for item in waiting(read.request)],
        "runs": intake_runs(now - timedelta(hours=24)),
        "recent": artifacts(POSTED_KEY, 3),
    }
    result, run_id = judge(
        "digest", plan, {}, state, owner, now + timedelta(minutes=12), f"digest-{owner}"
    )
    logger.info("Digest agent (%s): post=%s; %s", run_id, result["post"], result["why"])
    if result["post"] and result["text"].strip():
        post(result["text"], owner)
    return {"mode": mode, "posted": bool(result["post"]), "why": result["why"]}
