import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import GlobalConcurrencyLimitUpdate
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_claims import claim_work, ensure_claim

WORKER = """
import sys, time
from pathlib import Path
from uuid import UUID
from datetime import UTC, datetime, timedelta
from flows.agentic_claims import claim_work
name, marker, release, owner, slots = sys.argv[1:]
with claim_work([name], UUID(owner), datetime.now(UTC)+timedelta(seconds=45), slots=int(slots)):
    Path(marker).touch()
    while not Path(release).exists():
        time.sleep(.02)
"""


def wait_for(path: Path, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 25
    while not path.exists():
        if process.poll() is not None:
            raise AssertionError(f"Worker exited {process.returncode}")
        if time.monotonic() > deadline:
            raise AssertionError(f"Worker did not reach {path.name}")
        time.sleep(0.05)


def test_same_issue_is_exclusive_while_other_issues_progress(tmp_path):
    processes = []
    with prefect_test_harness(), get_client(sync_client=True) as client:
        environment = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PREFECT_API_KEY", "PREFECT_API_AUTH_STRING"}
        }
        environment["PREFECT_API_URL"] = str(client.api_url)
        environment["PYTHONPATH"] = str(Path.cwd())

        def start(label, issue):
            marker, release = tmp_path / label, tmp_path / (label + "-release")
            output = (tmp_path / (label + ".log")).open("w")
            p = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    WORKER,
                    issue,
                    str(marker),
                    str(release),
                    str(uuid4()),
                    "1",
                ],
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            output.close()
            processes.append(p)
            return p, marker, release

        try:
            a, entered_a, release_a = start("first", "fastmcp-issue-1")
            wait_for(entered_a, a)
            b, entered_b, release_b = start("overlap", "fastmcp-issue-1")
            c, entered_c, release_c = start("independent", "fastmcp-issue-2")
            wait_for(entered_c, c)
            assert not entered_b.exists()
            assert b.poll() is None
            assert (
                client.read_global_concurrency_limit_by_name(
                    "fastmcp-issue-1"
                ).active_slots
                == 1
            )
            release_a.touch()
            assert a.wait(timeout=15) == 0
            wait_for(entered_b, b)
            release_b.touch()
            release_c.touch()
            assert b.wait(timeout=15) == 0
            assert c.wait(timeout=15) == 0
            assert (
                client.read_global_concurrency_limit_by_name(
                    "fastmcp-issue-1"
                ).active_slots
                == 0
            )
            assert (
                client.read_global_concurrency_limit_by_name(
                    "fastmcp-issue-2"
                ).active_slots
                == 0
            )

            ensure_claim("disabled")
            client.update_global_concurrency_limit(
                "disabled", GlobalConcurrencyLimitUpdate(active=False)
            )
            with pytest.raises(ValueError, match="misconfigured"):
                with claim_work(
                    ["disabled"], uuid4(), datetime.now(UTC) + timedelta(minutes=1)
                ):
                    pytest.fail("Inactive claim admitted work")
            with pytest.raises(RuntimeError, match="worker failed"):
                with claim_work(
                    ["fastmcp-issue-1"],
                    uuid4(),
                    datetime.now(UTC) + timedelta(minutes=1),
                ):
                    raise RuntimeError("worker failed")
            assert (
                client.read_global_concurrency_limit_by_name(
                    "fastmcp-issue-1"
                ).active_slots
                == 0
            )
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)


def test_takeover_drains_owned_children_and_waits_for_shared_verification(tmp_path):
    from prefect import flow
    from prefect.client.schemas.actions import ArtifactCreate
    from prefect.states import Cancelled, Completed, Failed, Running

    from flows.agentic_stack_policy import Batch

    @flow
    def controller(batch: dict | None = None, runtime: dict | None = None):
        return {}

    now = datetime.now(UTC)
    batch = Batch(
        id="takeover",
        started_at=now,
        expires_at=now + timedelta(seconds=60),
        groups=[
            {
                "name": "bugs",
                "rationale": "Recovery test",
                "branch": "main",
                "base_sha": "a" * 40,
                "items": [{"issue_number": 1}],
            }
        ],
    )
    marker, release = tmp_path / "takeover-entered", tmp_path / "takeover-release"
    worker = """
import json,sys,time
from pathlib import Path
from uuid import UUID
from flows.agentic_claims import claim_issues
from flows.agentic_stack_policy import Batch
with claim_issues(Batch.model_validate_json(sys.argv[1]),UUID(sys.argv[2])):
    Path(sys.argv[3]).touch()
    while not Path(sys.argv[4]).exists():time.sleep(.02)
"""
    with prefect_test_harness(), get_client(sync_client=True) as client:
        flow_id = client.create_flow(controller)
        verification_id = client.create_deployment(
            flow_id, "verification", version="a" * 40
        )
        runtime = {
            "source_sha": "a" * 40,
            "plans": {},
            "triage_plan": {"flow_id": str(flow_id)},
            "verification_id": str(verification_id),
        }
        prior = client.create_flow_run(
            controller,
            parameters={"batch": batch.model_dump(mode="json"), "runtime": runtime},
            state=Failed(),
        )
        owner = client.create_flow_run(controller, state=Running())
        child = client.create_flow_run(
            controller,
            parameters={"parameters": {"ref": {"owner": str(prior.id)}}},
            state=Running(),
        )
        shared = client.create_flow_run_from_deployment(
            verification_id, parameters={"spec": {"issue_number": 1}}
        )
        client.set_flow_run_state(shared.id, Running(), force=True)
        unrelated = client.create_flow_run(
            controller,
            parameters={
                "parameters": None,
                "context": None,
                "spec": {"issue_number": 1, "batch_id": batch.id},
            },
            state=Running(),
        )
        client.create_artifact(
            ArtifactCreate(
                key="fastmcp-issue-1-assignment",
                type="fastmcp-work-assignment",
                flow_run_id=prior.id,
                data={"owner": str(prior.id), "batch_id": batch.id},
            )
        )
        environment = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PREFECT_API_KEY", "PREFECT_API_AUTH_STRING"}
        }
        environment["PREFECT_API_URL"] = str(client.api_url)
        environment["PYTHONPATH"] = str(Path.cwd())
        with (tmp_path / "takeover.log").open("w") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    worker,
                    batch.model_dump_json(),
                    str(owner.id),
                    str(marker),
                    str(release),
                ],
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        try:
            until = time.monotonic() + 25
            while not client.read_flow_run(child.id).state.is_cancelling():
                assert process.poll() is None
                assert time.monotonic() < until
                time.sleep(0.05)
            assert not marker.exists()
            client.set_flow_run_state(child.id, Cancelled(), force=True)
            time.sleep(1.2)
            assert not marker.exists()
            assert client.read_flow_run(shared.id).state.is_running()
            assert client.read_flow_run(unrelated.id).state.is_running()
            client.set_flow_run_state(shared.id, Completed(), force=True)
            wait_for(marker, process)
            release.touch()
            assert process.wait(timeout=15) == 0
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
