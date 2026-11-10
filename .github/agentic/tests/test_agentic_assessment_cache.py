from copy import deepcopy
from uuid import uuid4

from prefect import flow
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.states import Completed, Failed
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_assessment_cache import (
    assessment_candidates,
    assessment_fingerprint,
    assessment_key,
)


@flow
def assessment_stub(instructions: dict, context: dict):
    return {}


def test_cached_assessment_requires_same_complete_revision_and_skills():
    original = {
        "instructions": {
            "base_sha": "a" * 40,
            "files": {"skill": {"sha256": "b" * 64}},
        },
        "context": {
            "route": {"revision": "c" * 64},
            "evidence": {"issue": {"number": 1}},
            "retained_intake_sha256": "d" * 64,
        },
    }
    refreshed = deepcopy(original)
    refreshed["context"]["retained_intake_sha256"] = "e" * 64
    assert assessment_fingerprint(original) == assessment_fingerprint(refreshed)
    assert original["context"]["retained_intake_sha256"] == "d" * 64
    changed = deepcopy(refreshed)
    changed["context"]["route"]["revision"] = "f" * 64
    assert assessment_fingerprint(original) != assessment_fingerprint(changed)
    changed = deepcopy(refreshed)
    changed["instructions"]["files"]["skill"]["sha256"] = "f" * 64
    assert assessment_fingerprint(original) != assessment_fingerprint(changed)
    assert assessment_fingerprint({"context": {}}) is None
    with prefect_test_harness(), get_client(sync_client=True) as client:
        match = client.create_flow_run(
            assessment_stub, parameters=original, state=Completed()
        )
        client.create_flow_run(assessment_stub, parameters=changed, state=Completed())
        client.create_flow_run(assessment_stub, parameters=original, state=Failed())
        version = uuid4()
        assert assessment_candidates(match.flow_id, version, refreshed) == [match.id]
        cache_key = assessment_key(match.flow_id, version, original)
        assert cache_key != assessment_key(match.flow_id, uuid4(), original)
        client.create_artifact(
            ArtifactCreate(
                key=cache_key,
                type="fastmcp-stack-controller",
                data={"run_id": str(match.id)},
            )
        )
        for _ in range(13):
            client.create_flow_run(
                assessment_stub, parameters=changed, state=Completed()
            )
        assert assessment_candidates(match.flow_id, version, refreshed) == [match.id]
        assert assessment_candidates(match.flow_id, uuid4(), refreshed) == []
