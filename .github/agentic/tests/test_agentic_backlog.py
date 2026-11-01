import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen
from uuid import uuid4

import pytest
from prefect import flow
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.client.schemas.filters import FlowRunFilter
from prefect.states import Completed, Failed, Running
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_backlog import Intake, due, inventory, settle
from flows.agentic_freshness import revalidate
from flows.agentic_intake import evidence_revision, policy_outcome, route_intake
from flows.agentic_stack_runtime import AgentPlan, Runtime, gh_api, record
from flows.agentic_triage import admit, checkpoint, dispatch, finish, latest, retain


def issue(number, **extra):
    return {
        "number": number,
        "title": f"Report {number}",
        "state": "open",
        "updated_at": "2026-10-21T00:00:00Z",
        "labels": [],
        "assignees": [],
        **extra,
    }


@pytest.fixture
def github_server():
    requests = []
    responses = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            response = responses[self.path]
            status, body = response if isinstance(response, tuple) else (200, response)
            content = json.dumps(body).encode()
            self.send_response(status)
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read(path):
        with urlopen(f"http://127.0.0.1:{server.server_port}/{path}") as response:
            return json.load(response)

    yield read, responses, requests
    server.shutdown()
    server.server_close()
    thread.join()


def test_bounded_inventory_retains_backlog_priority_and_reports_partial_coverage(
    github_server,
):
    read, responses, requests = github_server
    responses[
        "/repos/PrefectHQ/fastmcp/issues?state=open&sort=created&direction=asc&per_page=30&page=2"
    ] = [issue(n) for n in range(31, 60)] + [issue(60, pull_request={"url": "pr"})]
    responses[
        "/repos/PrefectHQ/fastmcp/issues?state=open&sort=updated&direction=desc&per_page=30&page=1"
    ] = [issue(35), issue(100)]
    result = inventory(read, Intake(), 2)
    assert len(requests) == 2
    assert result["next_page"] == 3
    assert [row["issue_number"] for row in result["pending"]] == [*range(31, 60), 100]
    assert result["coverage"]["metadata_rows"] == 32
    assert result["coverage"]["unique_open_issues"] == 30
    assert "not a snapshot census" in result["coverage"]["limitation"]


def test_event_intake_resolves_pull_requests_to_referenced_open_issues(github_server):
    read, responses, requests = github_server
    responses["/repos/PrefectHQ/fastmcp/pulls/7"] = {
        "number": 7,
        "body": "Fixes #5 and https://github.com/PrefectHQ/fastmcp/issues/6; not other/repo#8",
    }
    responses["/repos/PrefectHQ/fastmcp/issues/5"] = issue(5)
    responses["/repos/PrefectHQ/fastmcp/issues/6"] = issue(6, state="closed")
    responses["/repos/PrefectHQ/fastmcp/issues/9"] = issue(9)
    config = Intake(issues=[9], pull_requests=[7], queue="events")
    result = inventory(read, config, 1)
    assert [row["issue_number"] for row in result["pending"]] == [5, 9]
    assert result["coverage"]["pull_requests"] == 1
    assert "/repos/PrefectHQ/fastmcp/issues/8" not in requests
    assert config.scope() != Intake(issues=[1], queue="events").scope()
    assert Intake(issues=[9]).scope() != config.scope()


def test_receipts_defer_but_do_not_certify_unchanged_contribution_history():
    now = datetime.now(UTC)
    config = Intake(refresh_minutes=60)
    item = {"metadata_revision": "one"}
    receipt = {
        **item,
        "processed_at": now.isoformat(),
        "status": "triaged",
        "source_sha": "a",
    }
    assert not due(receipt, item, config, now + timedelta(minutes=59), "a")
    assert due(receipt, item, config, now + timedelta(minutes=60), "a")
    assert due(receipt, {"metadata_revision": "two"}, config, now, "a")
    receipt["status"] = "operational"
    assert not due(receipt, item, config, now + timedelta(days=7), "a")
    assert due(receipt, item, config, now, "b")
    assert due(
        receipt, item, config.model_copy(update={"retry_failed": True}), now, "a"
    )


def test_failed_stale_and_unreported_items_remain_pending():
    pending = [{"issue_number": n, "attempts": 0} for n in range(1, 6)]
    result = {
        "bins": {
            "existing_review": [
                {"issue_number": 1, "status": "stale"},
                {
                    "issue_number": 2,
                    "verification": [{"status": "environment_failure"}],
                },
                {
                    "issue_number": 3,
                    "verification": [{"status": "verification_recorded"}],
                },
            ],
            "operational": [],
        }
    }
    remaining, completed = settle(pending, [1, 2, 3, 4], result)
    assert completed == [3]
    assert remaining == [
        {"issue_number": 1, "attempts": 1},
        {"issue_number": 2, "attempts": 1},
        {"issue_number": 4, "attempts": 1},
        {"issue_number": 5, "attempts": 0},
    ]


def test_late_human_reply_invalidates_mre_and_prevents_acknowledgement(
    github_server, evidence
):
    read, responses, _ = github_server
    original = {**evidence, "stacks": []}
    number = original["issue"]["number"]
    responses[f"/fresh/{number}"] = original
    result = {
        "batch_id": "mre",
        "bins": {
            "evidence": [{"issue_number": number, "reason": "Reproduction recorded"}],
            "operational": [],
        },
    }

    def read_evidence(issue_number, branch):
        assert branch == "main"
        return read(f"fresh/{issue_number}")

    assert (
        revalidate(result, {number: original}, {number: "main"}, read_evidence)
        == result
    )
    responses[f"/fresh/{number}"] = {
        **original,
        "comments": [
            {"id": 7, "body": "Correction: only occurs on the maintenance line"}
        ],
    }
    changed = revalidate(result, {number: original}, {number: "main"}, read_evidence)
    assert not changed["bins"]["evidence"]
    assert changed["bins"]["operational"][0]["status"] == "stale"
    assert (
        changed["bins"]["operational"][0]["retained_outcomes"][0]["result"]
        == result["bins"]["evidence"][0]
    )
    pending, completed = settle(
        [{"issue_number": number, "attempts": 0}], [number], changed
    )
    assert completed == [] and pending[0]["attempts"] == 1


@flow
def controller_stub(batch: dict, runtime: dict):
    return {}


def test_refresh_outage_preserves_other_completed_outcomes(github_server, evidence):
    read, responses, _ = github_server
    originals = {
        number: {
            **evidence,
            "stacks": [],
            "issue": {**evidence["issue"], "number": number},
        }
        for number in (1, 2)
    }
    responses["/fresh/1"] = (503, {"message": "Temporarily unavailable"})
    responses["/fresh/2"] = originals[2]
    result = {
        "bins": {
            "evidence": [
                {"issue_number": number, "reason": "Reproduction recorded"}
                for number in (1, 2)
            ],
            "operational": [],
        }
    }
    refreshed = revalidate(
        result, originals, {1: "main", 2: "main"}, lambda n, _: read(f"fresh/{n}")
    )
    assert refreshed["bins"]["evidence"] == [result["bins"]["evidence"][1]]
    failure = refreshed["bins"]["operational"][0]
    assert failure["issue_number"] == 1
    assert failure["status"] == "refresh_failed"
    assert failure["retained_outcomes"][0]["result"] == result["bins"]["evidence"][0]
    assert len(result["bins"]["evidence"]) == 2
    remaining, completed = settle(
        [{"issue_number": n, "attempts": 0} for n in (1, 2)], [1, 2], refreshed
    )
    assert completed == [2]
    assert remaining == [{"issue_number": 1, "attempts": 1}]


@pytest.mark.parametrize("saved_stage", ["review-bins", "triage-outcome"])
def test_queue_recovers_same_controller_and_commits_only_saved_outcomes(
    github_server, evidence, saved_stage
):
    read, responses, requests = github_server
    for number in (1, 2, 3):
        responses[f"/repos/PrefectHQ/fastmcp/issues/{number}"] = issue(number)
    responses["/repos/PrefectHQ/fastmcp/commits/main"] = {"sha": "a" * 40}
    config = Intake(issues=[1, 2, 3], max_items=2)
    with prefect_test_harness(), get_client(sync_client=True) as client:
        client.create_work_pool(WorkPoolCreate(name="managed", type="process"))
        flow_id = client.create_flow(controller_stub)
        deployment = client.create_deployment(
            flow_id, "cloud-stack-batch", version="b" * 40, work_pool_name="managed"
        )
        runtime = Runtime(
            plans={
                role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
                for role in (
                    "assess",
                    "reproduce",
                    "triage_review",
                )
            },
            source_sha="b" * 40,
        )
        owner = client.create_flow_run(controller_stub, state=Running()).id
        plan = admit(config, runtime, deployment, owner, read)
        assert len(plan["pending"]) == 3
        assert len(plan["batch"]["groups"][0]["items"]) == 2
        assert {item["mode"] for item in plan["batch"]["groups"][0]["items"]} == {"mre"}
        assert checkpoint(config.scope())[1] == plan
        child = dispatch.fn(plan)
        assert dispatch.fn(plan) == child
        with pytest.raises(ValueError, match="still owns"):
            finish(plan, owner, config.scope(), child)
        replacement = client.create_flow_run(controller_stub, state=Running()).id
        with pytest.raises(ValueError, match="still owns"):
            admit(config, runtime, deployment, replacement, read)
        client.set_flow_run_state(owner, Failed(), force=True)
        before = len(requests)
        recovered = admit(config, runtime, deployment, replacement, read)
        assert recovered == plan and len(requests) == before
        assert dispatch.fn(recovered) == child
        result = {
            "batch_id": plan["batch"]["id"],
            "bins": {
                "evidence": [
                    {"issue_number": 1, "reason": "Needs a concrete reproduction"}
                ],
                "operational": [
                    {"issue_number": 2, "reason": "Verification setup failed"}
                ],
            },
        }
        record.fn(child, f"{plan['batch']['id']}-{saved_stage}", result)
        original = {
            **evidence,
            "stacks": [],
            "issue": {**evidence["issue"], "number": 1},
        }
        record.fn(
            child,
            f"{plan['batch']['id']}-issue-1-intake",
            {"evidence": original, "route": {"revision": "a" * 64}},
        )
        accepted = {**original, "base_sha": "b" * 40}
        record.fn(child, f"{plan['batch']['id']}-issue-1-review-evidence", accepted)
        responses["/fresh/1"] = accepted

        def read_evidence(number, branch):
            return read(f"fresh/{number}")

        client.set_flow_run_state(
            child, Failed(message="Result saved, rendering interrupted"), force=True
        )
        state = finish(
            plan, replacement, config.scope(), child, read_evidence=read_evidence
        )
        assert state["completed"] == [1]
        assert [row["issue_number"] for row in state["pending"]] == [2, 3]
        assert state["pending"][0]["attempts"] == 1
        assert latest(f"{config.scope()}-issue-1")["status"] == "triaged"
        assert latest(f"{config.scope()}-issue-1")[
            "evidence_revision"
        ] == evidence_revision(accepted)
        assert latest(f"{config.scope()}-issue-1")["baseline_sha"] == "b" * 40
        assert latest(f"{config.scope()}-issue-2") is None
        for artifact in client.read_artifacts(
            flow_run_filter=FlowRunFilter(id={"any_": [replacement]})
        ):
            if artifact.key.endswith(("-committed", "-coverage")):
                client.delete_artifact(artifact.id)
        before = len(requests)
        responses["/fresh/1"] = (503, {"message": "Unavailable after finalization"})
        assert (
            finish(
                plan, replacement, config.scope(), child, read_evidence=read_evidence
            )
            == state
        )
        assert len(requests) == before
        assert checkpoint(config.scope()) == (replacement, state)


def test_event_queue_adds_new_issues_to_retained_pending(github_server):
    read, responses, _ = github_server
    for number in (1, 2):
        responses[f"/repos/PrefectHQ/fastmcp/issues/{number}"] = issue(number)
    responses["/repos/PrefectHQ/fastmcp/commits/main"] = {"sha": "a" * 40}
    config = Intake(issues=[2], queue="events")
    with prefect_test_harness(), get_client(sync_client=True) as client:
        client.create_work_pool(WorkPoolCreate(name="managed", type="process"))
        flow_id = client.create_flow(controller_stub)
        deployment = client.create_deployment(
            flow_id, "cloud-stack-batch", version="b" * 40, work_pool_name="managed"
        )
        runtime = Runtime(
            plans={
                role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
                for role in ("assess", "reproduce")
            },
            source_sha="b" * 40,
        )
        previous = client.create_flow_run(controller_stub, state=Completed()).id
        retained = inventory(read, Intake(issues=[1]), 1)["pending"]
        retain(
            previous,
            config.scope() + "-committed",
            {"phase": "committed", "pending": retained, "next_page": 1},
        )
        owner = client.create_flow_run(controller_stub, state=Running()).id
        plan = admit(config, runtime, deployment, owner, read)
        assert [row["issue_number"] for row in plan["pending"]] == [2, 1]
        assert [
            item["issue_number"] for item in plan["batch"]["groups"][0]["items"]
        ] == [2, 1]


def test_event_target_preserves_a_separate_retained_backlog(github_server):
    read, responses, _ = github_server
    for number in (1, 2, 3, 4):
        responses[f"/repos/PrefectHQ/fastmcp/issues/{number}"] = issue(number)
    responses["/repos/PrefectHQ/fastmcp/pulls/9"] = {"body": "Fixes #3"}
    responses["/repos/PrefectHQ/fastmcp/commits/main"] = {"sha": "a" * 40}
    with prefect_test_harness(), get_client(sync_client=True) as client:
        client.create_work_pool(WorkPoolCreate(name="managed", type="process"))
        flow_id = client.create_flow(controller_stub)
        deployment = client.create_deployment(
            flow_id, "cloud-stack-batch", version="b" * 40, work_pool_name="managed"
        )
        runtime = Runtime(
            plans={
                role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
                for role in ("assess", "reproduce")
            },
            source_sha="b" * 40,
        )
        scope = Intake(queue="events").scope()
        previous = client.create_flow_run(controller_stub, state=Completed()).id
        retained = inventory(read, Intake(issues=[1, 2, 3, 4]), 1)["pending"]
        retain(
            previous,
            scope + "-committed",
            {"phase": "committed", "pending": retained, "next_page": 1},
        )
        owner = client.create_flow_run(controller_stub, state=Running()).id
        config = Intake(pull_requests=[9], queue="events", max_items=1)
        plan = admit(config, runtime, deployment, owner, read)
        assert [row["issue_number"] for row in plan["pending"]] == [3]
        assert checkpoint(scope)[1]["pending"] == retained
        assert [
            item["issue_number"] for item in plan["batch"]["groups"][0]["items"]
        ] == [3]


def test_gh_api_failure_reports_stderr(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho 'gh: Bad credentials (HTTP 401)' >&2\nexit 1\n")
    gh.chmod(0o755)

    with pytest.raises(RuntimeError, match=r"issues/2026 exited 1: .*Bad credentials"):
        gh_api("repos/PrefectHQ/fastmcp/issues/2026", {"PATH": str(tmp_path)}, 5)


def test_finish_commits_policy_decisions_saved_by_an_older_controller(
    github_server, evidence
):
    read, responses, _ = github_server
    responses["/repos/PrefectHQ/fastmcp/issues/1"] = issue(1)
    responses["/repos/PrefectHQ/fastmcp/commits/main"] = {"sha": "a" * 40}
    config = Intake(issues=[1], queue="events")
    with prefect_test_harness(), get_client(sync_client=True) as client:
        client.create_work_pool(WorkPoolCreate(name="managed", type="process"))
        flow_id = client.create_flow(controller_stub)
        deployment = client.create_deployment(
            flow_id, "cloud-stack-batch", version="b" * 40, work_pool_name="managed"
        )
        runtime = Runtime(
            plans={
                role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
                for role in ("assess", "reproduce")
            },
            source_sha="b" * 40,
        )
        owner = client.create_flow_run(controller_stub, state=Running()).id
        plan = admit(config, runtime, deployment, owner, read)
        child = dispatch.fn(plan)
        original = {
            **evidence,
            "stacks": [],
            "issue": {**evidence["issue"], "number": 1, "labels": ["security"]},
        }
        legacy = route_intake(original)
        record.fn(
            child, f"{plan['batch']['id']}-issue-1-intake", {"evidence": original}
        )
        record.fn(
            child,
            f"{plan['batch']['id']}-review-bins",
            {
                "batch_id": plan["batch"]["id"],
                "bins": {"decisions": [legacy], "operational": []},
            },
        )
        client.set_flow_run_state(child, Completed(), force=True)
        state = finish(
            plan, owner, config.scope(), child, read_evidence=lambda *_: original
        )
        assert state["completed"] == [1]
        assert state["review"]["bins"]["decisions"] == [policy_outcome(1, legacy)]
        assert checkpoint(config.scope()) == (owner, state)


def test_different_manual_pr_request_does_not_resume_previous_pr(github_server):
    read, responses, requests = github_server
    responses["/repos/PrefectHQ/fastmcp/pulls/10"] = {"body": "Fixes #1"}
    responses["/repos/PrefectHQ/fastmcp/pulls/20"] = {"body": "Fixes #2"}
    for number in (1, 2):
        responses[f"/repos/PrefectHQ/fastmcp/issues/{number}"] = issue(number)
    responses["/repos/PrefectHQ/fastmcp/commits/main"] = {"sha": "a" * 40}
    with prefect_test_harness(), get_client(sync_client=True) as client:
        runtime = Runtime(
            plans={
                role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
                for role in ("assess", "reproduce")
            },
            source_sha="b" * 40,
        )
        old = client.create_flow_run(controller_stub, state=Running())
        config = Intake(pull_requests=[10])
        first = admit(config, runtime, uuid4(), old.id, read)
        client.set_flow_run_state(old.id, Completed(), force=True)
        new = client.create_flow_run(controller_stub, state=Running())
        second = admit(Intake(pull_requests=[20]), runtime, uuid4(), new.id, read)
        assert first["batch"]["groups"][0]["items"][0]["issue_number"] == 1
        assert second["batch"]["groups"][0]["items"][0]["issue_number"] == 2
        assert "/repos/PrefectHQ/fastmcp/pulls/20" in requests
        assert (
            Intake(queue="events", branch="main").scope()
            != Intake(queue="events", branch="release/3.x").scope()
        )
