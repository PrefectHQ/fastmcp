from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.states import Completed
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_stack import assemble_stack, read_stack_artifact, validate_stack
from tests.test_agentic_stack import probe


def test_artifact_handoff_checks_real_prefect_run_and_storage(repository):
    directory, spec = repository
    payload = assemble_stack(directory, spec, probe)
    version = "a" * 40
    with prefect_test_harness(), get_client(sync_client=True) as client:
        flow_id = client.create_flow(validate_stack)
        deployment_id = client.create_deployment(flow_id, "validation", version=version)
        run = client.create_flow_run_from_deployment(
            deployment_id, parameters={"spec": spec.model_dump(mode="json")}
        )

        def read(**overrides):
            return read_stack_artifact.fn(
                **{
                    "validation_run_id": run.id,
                    "validation_deployment_id": deployment_id,
                    "deployment_version": version,
                    "expected_spec_sha256": payload["spec_sha256"],
                    **overrides,
                }
            )

        with pytest.raises(ValueError, match="completed run"):
            read()
        client.set_flow_run_state(run.id, Completed(), force=True)
        with pytest.raises(ValueError, match="exactly one"):
            read()
        record = client.create_artifact(
            ArtifactCreate(
                key="fastmcp-stack-trial-client",
                type="fastmcp-stack-validation",
                flow_run_id=run.id,
                data=payload,
            )
        )
        assert read() == payload
        with pytest.raises(ValueError, match="pinned stack validator"):
            read(deployment_version="b" * 40)
        with pytest.raises(ValueError, match="pinned stack validator"):
            read(validation_deployment_id=uuid4())
        with pytest.raises(ValueError, match="different stack specification"):
            read(expected_spec_sha256="b" * 64)

        client.delete_artifact(record.id)
        mismatched = deepcopy(payload)
        mismatched["spec"]["layers"][0]["title"] = "Different patch"
        record = client.create_artifact(
            ArtifactCreate(
                key="fastmcp-stack-trial-client",
                type="fastmcp-stack-validation",
                flow_run_id=run.id,
                data=mismatched,
            )
        )
        with pytest.raises(ValueError, match="does not match"):
            read()
