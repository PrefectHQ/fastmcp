"""Triage keeps owning an item after it hands it off, until a person clears it."""

from __future__ import annotations

from flows.agentic_backlog import addressed, handoff_revision, handoffs, open_pulls
from flows.agentic_followup import busy, owed, plan, stale
from flows.agentic_labels import TRIAGE_LABEL, cleared_by_person, triage_labels

SHA = "a" * 40
OLD_SHA = "b" * 40


def issue(number: int, labels: tuple[str, ...] = (), **extra) -> dict:
    return {
        "number": number,
        "state": "open",
        "title": f"Issue {number}",
        "body": "Something breaks",
        "comments": 0,
        "user": {"type": "User", "login": "reporter"},
        "labels": [{"name": name} for name in labels],
        "assignees": [],
        **extra,
    }


def pull(
    number: int,
    fixes: int,
    *,
    head: str = "1" * 40,
    association: str = "CONTRIBUTOR",
    kind: str = "User",
    draft: bool = False,
) -> dict:
    return {
        "number": number,
        "state": "open",
        "draft": draft,
        "body": f"Fixes #{fixes}",
        "head": {"sha": head},
        "user": {"type": kind, "login": "contributor"},
        "author_association": association,
    }


def test_the_6585_sequence_changes_the_hand_off() -> None:
    """Gate closes the PR, a maintainer assigns, the gate bot reopens it.

    No event automation sees the last two steps (bot sender, `assigned`), so the
    hand-off revision has to.
    """
    gated = handoff_revision(issue(6584), [])
    assigned = handoff_revision(issue(6584, assignees=[{"login": "AP24110011695"}]), [])
    reopened = handoff_revision(
        issue(6584, assignees=[{"login": "AP24110011695"}]), [pull(6585, 6584)]
    )
    assert len({gated, assigned, reopened}) == 3


def test_hand_off_moves_with_people_and_contributions() -> None:
    base = handoff_revision(issue(1), [pull(10, 1)])
    for changed in (
        handoff_revision(issue(1, comments=1), [pull(10, 1)]),
        handoff_revision(issue(1, body="Now with a reproducer"), [pull(10, 1)]),
        handoff_revision(issue(1, labels=("needs MRE",)), [pull(10, 1)]),
        handoff_revision(issue(1, state="closed"), [pull(10, 1)]),
        handoff_revision(issue(1), [pull(10, 1, head="2" * 40)]),
        handoff_revision(issue(1), [pull(10, 1, draft=True)]),
        # The contributor answered on the PR without pushing.
        handoff_revision(issue(1), [{**pull(10, 1), "comments": 1}]),
        handoff_revision(issue(1), []),
    ):
        assert changed != base


def test_triage_never_makes_its_own_issue_look_changed() -> None:
    before = handoff_revision(issue(1), [])
    after = handoff_revision(issue(1, labels=(TRIAGE_LABEL, "openapi")), [])
    assert after == before


def test_only_outside_ready_contributions_count() -> None:
    pulls = [
        pull(10, 1),
        pull(11, 2, association="MEMBER"),
        pull(12, 3, kind="Bot"),
        pull(13, 4, draft=True),
        {**pull(14, 5), "body": "Related to #5"},
    ]
    assert set(addressed(pulls)) == {1}


def test_owed_is_the_labeled_queue_plus_issues_with_waiting_contributions() -> None:
    issues = {
        1: issue(1, labels=(TRIAGE_LABEL,)),
        2: issue(2),
        3: issue(3, state="closed"),
    }
    paths = []

    def read(path: str) -> dict | None:
        paths.append(path)
        return issues.get(int(path.rsplit("/", 1)[1]))

    def pages(path: str) -> list[dict]:
        assert "labels=needs%3Atriage" in path
        return [issues[1], {**issue(9), "pull_request": {}}]

    found = owed(read, pages, [pull(20, 2), pull(21, 3), pull(22, 1)])
    assert sorted(found) == [1, 2]
    # The labeled issue is already in hand; no second read.
    assert paths == [
        "repos/PrefectHQ/fastmcp/issues/2",
        "repos/PrefectHQ/fastmcp/issues/3",
    ]


def test_stale_reasons() -> None:
    record = {"handoff_revision": "r1", "status": "triaged", "source_sha": OLD_SHA}
    assert stale(None, "r1", SHA) == "never followed"
    assert stale({"status": "triaged"}, "r1", SHA) == "never followed"
    assert stale(record, "r2", SHA) == "changed since triage"
    assert stale(record, "r1", SHA) is None
    failed = {**record, "status": "operational"}
    assert stale(failed, "r1", SHA) == "failed on older code"
    assert stale({**failed, "source_sha": SHA}, "r1", SHA) is None


def test_plan_starts_the_oldest_changes_first_and_bounds_the_pass() -> None:
    issues = {number: issue(number) for number in (5, 1, 3, 4)}
    settled = handoff_revision(issues[4], [])
    records = {4: {"handoff_revision": settled, "status": "triaged"}}
    found = plan(issues, [], records.get, SHA, 2)
    assert found["owed"] == 4
    assert [item["issue_number"] for item in found["due"]] == [1, 3, 5]
    assert [item["issue_number"] for item in found["dispatch"]] == [1, 3]
    assert {item["why"] for item in found["due"]} == {"never followed"}


def test_changes_after_a_hand_off_go_before_the_backfill() -> None:
    issues = {number: issue(number) for number in (1, 2, 3, 9)}
    records = {
        9: {"handoff_revision": "before the assign", "status": "triaged"},
        3: {
            "handoff_revision": handoff_revision(issues[3], []),
            "status": "operational",
            "source_sha": OLD_SHA,
        },
    }
    found = plan(issues, [], records.get, SHA, 2)
    assert [(item["issue_number"], item["why"]) for item in found["due"]] == [
        (9, "changed since triage"),
        (3, "failed on older code"),
        (1, "never followed"),
        (2, "never followed"),
    ]
    assert [item["issue_number"] for item in found["dispatch"]] == [9, 3]


def test_handoffs_snapshot_each_admitted_issue() -> None:
    def read(path: str):
        if "/pulls?" in path:
            return [pull(10, 1)]
        if "/issues?" in path:
            return [{"number": 10, "pull_request": {}, "comments": 2}, issue(1)]
        return issue(int(path.rsplit("/", 1)[1]))

    assert handoffs(read, [1, 2]) == {
        "1": handoff_revision(issue(1), [{**pull(10, 1), "comments": 2}]),
        "2": handoff_revision(issue(2), []),
    }
    assert handoffs(read, []) == {}


def test_open_pulls_pages_until_short() -> None:
    seen = []

    def read(path: str):
        seen.append(path)
        if "/issues?" in path:
            return [{"number": 5, "pull_request": {}, "comments": 4}]
        return [pull(n, 1) for n in range(100)] if path.endswith("page=1") else []

    pulls = open_pulls(read)
    assert len(pulls) == 100
    assert pulls[5]["comments"] == 4 and pulls[6]["comments"] is None
    assert len(seen) == 3


def test_a_person_clearing_needs_triage_sticks() -> None:
    removed = {
        "event": "unlabeled",
        "label": {"name": TRIAGE_LABEL},
        "actor": {"type": "User", "login": "zzstoatzz"},
    }
    assert cleared_by_person([removed])
    assert not cleared_by_person([{**removed, "actor": {"type": "Bot"}}])
    assert not cleared_by_person([{**removed, "label": {"name": "bug"}}])
    assert not cleared_by_person([{**removed, "event": "labeled"}])

    review = {"bins": {"existing_review": [{"issue_number": 1, "area": "client"}]}}
    writes = triage_labels(review, lambda n: issue(n), lambda n: True)
    # Triage still files the area; it just doesn't reopen the hand-off.
    assert [(w["issue_number"], w["label"]) for w in writes] == [(1, "client")]


def test_re_entry_runs_what_the_issue_automation_runs() -> None:
    from uuid import uuid4

    from prefect import flow
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.actions import WorkPoolCreate
    from prefect.events.actions import RunDeployment
    from prefect.events.schemas.automations import AutomationCore, EventTrigger
    from prefect.testing.utilities import prefect_test_harness

    from flows.agentic_backlog import Intake
    from flows.agentic_followup import (
        attempts,
        busy,
        dispatch,
        intake_target,
        unfinished,
    )

    @flow(name="intake-stub")
    def intake_stub(config: dict, runtime: dict, controller_id: str) -> None:
        return None

    with prefect_test_harness(), get_client(sync_client=True) as client:
        client.create_work_pool(WorkPoolCreate(name="managed", type="process"))
        flow_id = client.create_flow(intake_stub)
        deployment = client.create_deployment(
            flow_id, "cloud-triage-intake", work_pool_name="managed"
        )
        controller = str(uuid4())
        runtime = {"source_sha": SHA, "plans": {}}
        client.create_automation(
            AutomationCore(
                name="fastmcp-triage-on-github-issue",
                trigger=EventTrigger(expect={"fastmcp.github.issues.opened"}),
                actions=[
                    RunDeployment(
                        source="selected",
                        deployment_id=deployment,
                        parameters={
                            "controller_id": controller,
                            "runtime": runtime,
                            "config": {"queue": "events", "sender": "someone"},
                        },
                    )
                ],
            )
        )
        target, pinned = intake_target()
        assert target == deployment
        assert pinned == {"controller_id": controller, "runtime": runtime}
        item = {"issue_number": 6584, "revision": "c" * 64}
        first = dispatch(target, pinned, item)
        assert dispatch(target, pinned, item) == first
        run = client.read_flow_run(first)
        assert run.parameters["runtime"] == runtime
        config = Intake.model_validate(run.parameters["config"])
        # The same queue scope as the issue's own events, without the cooldown.
        assert config.scope() == Intake(issues=[6584], queue="events").scope()
        assert config.refresh_minutes == 1 and config.sender is None
        # A moved hand-off gets a fresh look even after a failed receipt.
        assert config.retry_failed
        changed = dispatch(target, pinned, {**item, "revision": "d" * 64})
        assert changed != first
        assert attempts(target, SHA)(6584, "c" * 64) == 1
        retry = dispatch(target, pinned, {**item, "attempt": 1})
        assert retry != first
        assert attempts(target, SHA)(6584, "c" * 64) == 2
        # A deploy resets the tries for the same issue revision.
        assert attempts(target, OLD_SHA)(6584, "c" * 64) == 0
        # Scheduled runs count as in flight, so the next pass waits for them.
        assert busy(unfinished(target), []) == {6584}


def test_issues_with_an_unfinished_intake_wait() -> None:
    pulls = [pull(30, 7)]
    running = [
        {"config": {"issues": [1]}},
        {"config": {"pull_requests": [30, 99]}},
        {},
    ]
    assert busy(running, pulls) == {1, 7}


def test_plan_holds_in_flight_and_repeatedly_failing_revisions() -> None:
    issues = {number: issue(number) for number in (1, 2, 3)}
    tries = {2: 3, 3: 1}
    found = plan(
        issues,
        [],
        lambda number: None,
        SHA,
        3,
        running={1},
        tries=lambda number, revision: tries.get(number, 0),
    )
    assert [(item["issue_number"], item["attempt"]) for item in found["dispatch"]] == [
        (3, 1)
    ]
    assert [(item["issue_number"], item["held"]) for item in found["held"]] == [
        (1, "intake in flight"),
        (2, "3 runs on this revision failed"),
    ]
