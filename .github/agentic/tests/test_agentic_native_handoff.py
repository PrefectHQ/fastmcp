from copy import deepcopy

import pytest
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import ArtifactFilter, FlowRunFilter
from prefect.states import Running
from prefect.testing.utilities import prefect_test_harness

from flows import agentic_native_triage
from flows.agentic_stack_runtime import record, saved
from tests.test_agentic_baseline import configure_compare
from tests.test_agentic_baseline import review_item as review_item
from tests.test_agentic_native_triage import admission, native_owner


@pytest.mark.parametrize("destination", ["existing_review", "evidence", "operational"])
def test_handoff_retains_evidence_without_immediate_readback(monkeypatch, destination):
    value = admission()
    result = {"issue_number": 1, "status": "verification_recorded"}
    call = {"role": "triage", "run_id": "native-child"}

    def completed_stage(owner, current, stage):
        assert current == value
        assert stage == "initial"
        return destination, result, call

    monkeypatch.setattr(agentic_native_triage, "run_native_stage", completed_stage)
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(native_owner, state=Running()).id
        actual, outcome, calls, evidence = agentic_native_triage.run_native_triage(
            owner, value
        )
        assert (actual, outcome, calls) == (destination, result, [call])
        assert evidence == value.item["evidence"]
        key = "native-test-issue-1-review-evidence"
        artifacts = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [key]}),
            flow_run_filter=FlowRunFilter(id={"any_": [owner]}),
        )
        assert len(artifacts) == 1
        assert artifacts[0].data == evidence
        client.delete_artifact(artifacts[0].id)
        assert saved.fn(owner, key) is None
        assert evidence == value.item["evidence"]


def test_handoff_does_not_return_when_retained_evidence_conflicts(monkeypatch):
    value = admission()
    monkeypatch.setattr(
        agentic_native_triage,
        "run_native_stage",
        lambda *_: ("existing_review", {"status": "verification_recorded"}, {}),
    )
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(native_owner, state=Running()).id
        conflicting = deepcopy(value.item["evidence"])
        conflicting["issue"]["number"] = 2
        record.fn(owner, "native-test-issue-1-review-evidence", conflicting)
        with pytest.raises(ValueError, match="Conflicting durable controller record"):
            agentic_native_triage.run_native_triage(owner, value)


def test_handoff_returns_refreshed_baseline_evidence(
    monkeypatch, request, github_server
):
    read, responses, _ = github_server
    item = request.getfixturevalue("review_item")
    value = admission()
    value = value.model_copy(
        update={
            "item": item,
            "requested": value.requested.model_copy(update={"issue_number": 2692}),
        }
    )
    current = {**item["evidence"], "base_sha": "c" * 40}
    configure_compare(responses, "a" * 40, "c" * 40)
    monkeypatch.setattr(agentic_native_triage, "secret_sync", lambda _: "unused")
    monkeypatch.setattr(
        agentic_native_triage,
        "skills",
        lambda sha: {"existing_instructions": {"base_sha": sha}},
    )

    class LocalGitHub:
        def __init__(self, token):
            pass

        def request(self, endpoint):
            return read(endpoint)

    monkeypatch.setattr(agentic_native_triage, "GitHub", LocalGitHub)

    def stage(owner, current_admission, name):
        if name == "initial":
            record.fn(
                owner,
                "native-test-issue-2692-native-initial-verification",
                {"result": {"observed": current}},
            )
            status = "baseline_changed"
        else:
            assert name == "refresh-1"
            assert current_admission.group.base_sha == "c" * 40
            assert current_admission.item["evidence"] == current
            status = "verification_recorded"
        return (
            "existing_review",
            {"status": status, "assessment": item["assessment"]},
            {"run_id": name},
        )

    monkeypatch.setattr(agentic_native_triage, "run_native_stage", stage)
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(native_owner, state=Running()).id
        destination, outcome, calls, evidence = agentic_native_triage.run_native_triage(
            owner, value
        )
        assert destination == "existing_review"
        assert outcome["status"] == "verification_recorded"
        assert len(calls) == 2
        assert evidence == current
        assert evidence != value.item["evidence"]
        assert saved.fn(owner, "native-test-issue-2692-review-evidence") == current
