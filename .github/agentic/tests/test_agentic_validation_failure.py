import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from prefect import flow
from prefect.client.orchestration import get_client
from prefect.states import Cancelled, Failed, Running
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_stack_controller import ValidationFailure, start_validation, validate
from flows.agentic_stack_policy import Batch
from flows.agentic_stack_runtime import AgentPlan, Runtime, saved
from flows.agentic_validate_patch import run_command


@flow
def failure_owner():
    return {}


def test_failed_check_reaches_durable_repair_evidence(repository):
    directory, spec = repository
    with pytest.raises(RuntimeError) as command_error:
        run_command(
            [
                sys.executable,
                "-c",
                "print('x'*25000); print('actual check failure'); raise SystemExit(7)",
            ],
            directory,
        )
    failure = str(command_error.value)
    assert "exited 7" in failure
    assert failure.endswith("actual check failure\n")
    assert len(failure) < 21000

    now = datetime.now(UTC)
    batch = Batch(
        id=spec.batch_id,
        started_at=now,
        expires_at=now + timedelta(minutes=1),
        groups=[
            {
                "name": spec.group,
                "rationale": "Retain failed check evidence",
                "branch": spec.trunk_ref,
                "base_sha": spec.trunk_sha,
                "items": [{"issue_number": 1}],
            }
        ],
    )
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(failure_owner, state=Running())
        flow_id = client.create_flow(failure_owner)
        deployment_id = client.create_deployment(flow_id, "validator", version="a" * 40)
        runtime = Runtime(
            plans={
                role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
                for role in ("assess", "reproduce", "prepare", "review")
            },
            validator_id=deployment_id,
            source_sha="a" * 40,
        )
        run_id = start_validation.fn(owner.id, batch, runtime, spec)
        client.set_flow_run_state(run_id, Failed(message=failure), force=True)
        with pytest.raises(ValidationFailure) as caught:
            validate(owner.id, batch, runtime, spec)
        assert caught.value.run_id == run_id
        assert str(caught.value) == failure
        assert saved.fn(
            owner.id, f"{batch.id}-{spec.group}-validation-failed-{run_id}"
        ) == {"run_id": str(run_id), "message": failure}

        client.set_flow_run_state(run_id, Cancelled(), force=True)
        with pytest.raises(ValueError, match="completed run"):
            validate(owner.id, batch, runtime, spec)
