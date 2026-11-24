"""Follow-through: triage keeps an item until a person clears it. No model runs.

Event automations start triage when a person opens or edits an issue or a ready PR.
Most of what happens after triage hands an item off doesn't look like that: a
maintainer assigns the issue, the issue-link gate (a bot) reopens the PR, the
reporter answers in a comment, a contributor pushes a fix. None of those start an
intake, so the hand-off line went stale and the item dropped out of view (#6585).

This pass runs every hour over every item triage owes a person something on:

- open issues labeled `needs:triage` (a person removing the label clears it), and
- open issues an outside contribution says it fixes.

For each, it compares the hand-off revision (`agentic_backlog.handoff_revision`:
the issue and the contributions that fix it, without triage's own labels) to the
one the last intake recorded under `fastmcp-triage-handled-<n>`. When they differ,
or the issue has no record yet, or the last run failed on an older revision of the
code, it starts the same intake an issue event would, through the issue
automation's pinned deployment and runtime. It starts at most `max_dispatch` per
pass, so a backlog drains over a few hours instead of all at once.

Nothing here writes to GitHub.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote
from uuid import UUID

from prefect import flow, get_run_logger
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import (
    ArtifactFilter,
    DeploymentFilter,
    FlowRunFilter,
    FlowRunFilterName,
    FlowRunFilterState,
    FlowRunFilterStateType,
)
from prefect.client.schemas.objects import StateType
from prefect.client.schemas.sorting import ArtifactSort
from prefect.context import FlowRunContext
from prefect.events.actions import RunDeployment

from flows.agentic_backlog import addressed, handled_key, handoff_revision, open_pulls
from flows.agentic_github import GitHub
from flows.agentic_labels import TRIAGE_LABEL
from flows.agentic_stack_runtime import secret_sync

REPOSITORY = "PrefectHQ/fastmcp"
ISSUE_AUTOMATION = "fastmcp-triage-on-github-issue"
INTAKE_DEPLOYMENT = "cloud-triage-intake"


def owed(
    read: Callable[[str], object],
    pages: Callable[[str], list[dict]],
    pulls: list[dict],
) -> dict[int, dict]:
    """Every open issue waiting on a person, as it is on GitHub now."""
    labeled = pages(
        f"repos/{REPOSITORY}/issues?state=open&labels={quote(TRIAGE_LABEL)}"
        "&sort=created&direction=asc"
    )
    issues = {
        item["number"]: item
        for item in labeled
        if isinstance(item, dict) and "pull_request" not in item
    }
    for number in sorted(addressed(pulls)):
        if number in issues:
            continue
        item = read(f"repos/{REPOSITORY}/issues/{number}")
        if (
            isinstance(item, dict)
            and item.get("state") == "open"
            and "pull_request" not in item
        ):
            issues[number] = item
    return issues


def stale(record: dict | None, revision: str, source_sha: str) -> str | None:
    """Why the issue needs triage again, or None when the last run still holds."""
    if record is None or not record.get("handoff_revision"):
        return "never followed"
    if record["handoff_revision"] != revision:
        return "changed since triage"
    if record.get("status") == "operational" and record.get("source_sha") != source_sha:
        return "failed on older code"
    return None


# Follow-through runs for one revision of one issue. Intake retries a failed issue
# up to three times itself; past that the failure alert has fired, and the next
# change to the issue starts over.
MAX_TRIES = 3


def run_name(number: int, revision: str, source_sha: str) -> str:
    """One name per issue revision and code revision, so a deploy resets the tries."""
    return f"follow-through-{number}-{revision[:12]}-{source_sha[:8]}"


def busy(running: list[dict], pulls: list[dict]) -> set[int]:
    """Issues an unfinished intake already covers, from that intake's parameters.

    A second intake on the same queue would refuse to start, so wait for it.
    """
    numbers: set[int] = set()
    by_pull = {
        pull["number"]: number
        for number, found in addressed(pulls).items()
        for pull in found
    }
    for parameters in running:
        config = parameters.get("config") or {}
        numbers.update(n for n in config.get("issues") or [] if isinstance(n, int))
        numbers.update(
            by_pull[n] for n in config.get("pull_requests") or [] if n in by_pull
        )
    return numbers


def plan(
    issues: dict[int, dict],
    pulls: list[dict],
    records: Callable[[int], dict | None],
    source_sha: str,
    limit: int,
    running: frozenset[int] | set[int] = frozenset(),
    tries: Callable[[int, str], int] = lambda number, revision: 0,
) -> dict:
    """Which issues to send back through intake this pass, oldest first."""
    due, held = [], []
    for number in sorted(issues):
        revision = handoff_revision(issues[number], pulls)
        why = stale(records(number), revision, source_sha)
        if not why:
            continue
        item = {"issue_number": number, "revision": revision, "why": why}
        if number in running:
            held.append({**item, "held": "intake in flight"})
            continue
        attempt = tries(number, revision)
        if attempt >= MAX_TRIES:
            held.append({**item, "held": f"{attempt} runs on this revision failed"})
            continue
        due.append({**item, "attempt": attempt})
    return {"owed": len(issues), "due": due, "held": held, "dispatch": due[:limit]}


def handled(number: int) -> dict | None:
    with get_client(sync_client=True) as client:
        found = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [handled_key(number)]}),
            sort=ArtifactSort.CREATED_DESC,
            limit=1,
        )
    data = found[0].data if found else None
    return data if isinstance(data, dict) else None


def intake_target() -> tuple[UUID, dict]:
    """The deployment and pinned parameters the issue automation runs intake with.

    Re-entry runs exactly what an issue event would, so a deploy repins it with
    the automations and nothing here can drift from them.
    """
    with get_client(sync_client=True) as client:
        automations = client.read_automations_by_name(ISSUE_AUTOMATION)
    if len(automations) != 1 or not automations[0].enabled:
        raise ValueError(f"Expected one enabled {ISSUE_AUTOMATION} automation")
    actions = automations[0].actions
    action = actions[0] if len(actions) == 1 else None
    if not isinstance(action, RunDeployment) or action.deployment_id is None:
        raise ValueError(f"{ISSUE_AUTOMATION} no longer runs one deployment")
    parameters = action.parameters or {}
    if not {"controller_id", "runtime"} <= set(parameters):
        raise ValueError(f"{ISSUE_AUTOMATION} lost its pinned intake parameters")
    return action.deployment_id, {
        "controller_id": parameters["controller_id"],
        "runtime": parameters["runtime"],
    }


def unfinished(deployment_id: UUID) -> list[dict]:
    """Parameters of every intake run that hasn't finished."""
    with get_client(sync_client=True) as client:
        runs = client.read_flow_runs(
            deployment_filter=DeploymentFilter(id={"any_": [deployment_id]}),
            flow_run_filter=FlowRunFilter(
                state=FlowRunFilterState(
                    type=FlowRunFilterStateType(
                        any_=[
                            StateType.SCHEDULED,
                            StateType.PENDING,
                            StateType.RUNNING,
                            StateType.PAUSED,
                            StateType.CANCELLING,
                        ]
                    )
                )
            ),
            limit=200,
        )
    return [run.parameters or {} for run in runs]


def attempts(deployment_id: UUID, source_sha: str) -> Callable[[int, str], int]:
    def count(number: int, revision: str) -> int:
        with get_client(sync_client=True) as client:
            return len(
                client.read_flow_runs(
                    deployment_filter=DeploymentFilter(id={"any_": [deployment_id]}),
                    flow_run_filter=FlowRunFilter(
                        name=FlowRunFilterName(
                            any_=[run_name(number, revision, source_sha)]
                        )
                    ),
                    limit=MAX_TRIES,
                )
            )

    return count


def dispatch(deployment_id: UUID, pinned: dict, item: dict) -> UUID:
    number = item["issue_number"]
    with get_client(sync_client=True) as client:
        deployment = client.read_deployment(deployment_id)
        if deployment.name != INTAKE_DEPLOYMENT:
            raise ValueError(f"{ISSUE_AUTOMATION} points at {deployment.name}")
        run = client.create_flow_run_from_deployment(
            deployment_id,
            name=run_name(number, item["revision"], pinned["runtime"]["source_sha"]),
            parameters={
                **pinned,
                # The same queue as the issue's own events, so they share receipts.
                # A changed hand-off is the reason to run, so skip the hourly
                # cooldown intake keeps for unchanged issue metadata.
                "config": {
                    "queue": "events",
                    "max_items": 3,
                    "issues": [number],
                    "refresh_minutes": 1,
                    # And past a failed receipt on the same code: the hand-off moved,
                    # so the last failure says nothing about this revision.
                    "retry_failed": True,
                },
            },
            # One run per attempt, however many passes race to start it.
            idempotency_key=(
                f"fastmcp-follow-through-{number}-{item['revision'][:24]}"
                f"-{pinned['runtime']['source_sha'][:8]}-{item.get('attempt', 0)}"
            ),
        )
    return run.id


@flow(name="fastmcp-triage-follow-through", timeout_seconds=900)
def follow_through(max_dispatch: int = 3) -> dict:
    logger = get_run_logger()
    context = FlowRunContext.get()
    assert context is not None and context.flow_run is not None
    github = GitHub(secret_sync("fastmcp-triage-gh-token"))
    read = github.request
    pulls = open_pulls(read)
    deployment_id, pinned = intake_target()
    source_sha = pinned["runtime"]["source_sha"]
    found = plan(
        owed(read, github.pages, pulls),
        pulls,
        handled,
        source_sha,
        max_dispatch,
        busy(unfinished(deployment_id), pulls),
        attempts(deployment_id, source_sha),
    )
    started = []
    for item in found["dispatch"]:
        run_id = dispatch(deployment_id, pinned, item)
        started.append({**item, "flow_run_id": str(run_id)})
        logger.info("#%s %s: intake %s", item["issue_number"], item["why"], run_id)
    summary = {
        "owed": found["owed"],
        "due": len(found["due"]),
        "started": started,
        "waiting": [item["issue_number"] for item in found["due"][max_dispatch:]],
        "held": found["held"],
    }
    with get_client(sync_client=True) as client:
        client.create_artifact(
            ArtifactCreate(
                key="fastmcp-triage-follow-through",
                flow_run_id=context.flow_run.id,
                type="fastmcp-triage-follow-through",
                data=summary,
            )
        )
    return summary
