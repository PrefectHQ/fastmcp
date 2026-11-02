from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from prefect.client.orchestration import get_client
from prefect.states import Cancelled, Running
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_stack_controller import stack_batch
from flows.agentic_stack_policy import Batch, publication_revision
from flows.agentic_stack_runtime import owner_alive, record, saved
from flows.agentic_stack_usage import usage_report


def complete(evidence):
    return {**evidence, "stacks": [], "searches": [], "repository_labels": []}


def test_incomplete_publication_snapshot_is_not_an_empty_snapshot(evidence):
    evidence = complete(evidence)
    evidence["complete"] = False
    with pytest.raises(ValueError, match="complete evidence"):
        publication_revision(evidence)


def test_batch_bounds_and_release_line_are_enforced():
    batch = {
        "id": "trial",
        "started_at": "2026-10-21T00:00:00Z",
        "expires_at": "2026-10-21T02:00:00Z",
        "groups": [
            {
                "name": "client",
                "rationale": "Client hydration",
                "branch": "release/3.x",
                "base_sha": "a" * 40,
                "items": [{"issue_number": 1}],
            }
        ],
    }
    assert Batch.model_validate(batch).groups[0].branch == "release/3.x"
    changed = deepcopy(batch)
    changed["expires_at"] = "2026-10-21T02:00:01Z"
    with pytest.raises(ValueError):
        Batch.model_validate(changed)
    changed = deepcopy(batch)
    changed["groups"][0]["items"] *= 2
    with pytest.raises(ValueError):
        Batch.model_validate(changed)
    changed = deepcopy(batch)
    changed["groups"][0]["branch"] = "published-docs"
    with pytest.raises(ValueError):
        Batch.model_validate(changed)


def test_real_prefect_owner_and_immutable_records():
    with prefect_test_harness(), get_client(sync_client=True) as client:
        run = client.create_flow_run(stack_batch, parameters={}, state=Running())
        deadline = datetime.now(UTC) + timedelta(minutes=1)
        owner_alive.fn(run.id, deadline)
        first = record.fn(run.id, "publication-intent", {"head": "a" * 40})
        assert saved.fn(run.id, "publication-intent") == {"head": "a" * 40}
        assert saved.fn(run.id, "absent") is None
        assert record.fn(run.id, "publication-intent", {"head": "a" * 40}) == first
        with pytest.raises(ValueError, match="Conflicting"):
            record.fn(run.id, "publication-intent", {"head": "b" * 40})
        with pytest.raises(TimeoutError):
            owner_alive.fn(run.id, datetime.now(UTC) - timedelta(seconds=1))
        client.set_flow_run_state(run.id, Cancelled(), force=True)
        with pytest.raises(ValueError, match="no longer owns"):
            owner_alive.fn(run.id, deadline)


def test_real_prefect_usage_preserves_unknown_and_includes_failed_requests():
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(stack_batch, parameters={})
        measured = client.create_flow_run(stack_batch, parameters={})
        missing = client.create_flow_run(stack_batch, parameters={})
        for role, run in (("assess", measured), ("reproduce", missing)):
            record.fn(
                owner.id,
                f"trial-{role}-agent-run",
                {"role": role, "run_id": str(run.id), "plan_version": "pinned"},
            )
        client.create_logs(
            [
                {
                    "name": name,
                    "level": 20,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "flow_run_id": str(measured.id),
                    "message": message,
                }
                for name, message in (
                    (
                        "prefect.agent.activity",
                        "[attempt 1] Model request completed in 1s (input_tokens=10, output_tokens=2)",
                    ),
                    (
                        "prefect.agent.activity",
                        "[attempt 1] Model request failed in 2s (input_tokens=3, output_tokens=1)",
                    ),
                    (
                        "prefect.agent.activity",
                        "[attempt 1] Model request failed in 2s",
                    ),
                    (
                        "prefect.agent.activity",
                        "[attempt 1] Agent invocation completed in 4s (input_tokens=13, output_tokens=3)",
                    ),
                    (
                        "user.output",
                        "[attempt 1] Model request completed in 1s (input_tokens=999, output_tokens=999)",
                    ),
                )
            ]
        )
        usage = usage_report.fn(owner.id)
        assert usage["status"] == "observed_partial"
        assert (usage["input_tokens"], usage["output_tokens"]) == (13, 3)
        by_run = {run["run_id"]: run for run in usage["runs"]}
        assert by_run[str(measured.id)]["requests_missing_usage"] == 1
        assert by_run[str(missing.id)]["input_tokens"] is None
        assert usage["cost"] is None
        other_owner = client.create_flow_run(stack_batch, parameters={})
        record.fn(
            other_owner.id,
            "trial-assess-agent-run",
            {
                "role": "assess",
                "run_id": str(measured.id),
                "plan_version": "pinned",
                "reused": True,
            },
        )
        reused = usage_report.fn(other_owner.id)
        assert (reused["input_tokens"], reused["output_tokens"]) == (0, 0)
        assert reused["runs"][0]["observed_requests"] == 0
        assert reused["runs"][0]["reused"] is True
