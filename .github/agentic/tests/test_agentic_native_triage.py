from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from prefect import flow
from prefect.client.orchestration import get_client
from prefect.states import Running
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_native_cache import find_native_result, native_cache_key
from flows.agentic_stack import digest
from flows.agentic_stack_runtime import AgentPlan, Runtime, record
from flows.agentic_triage_plan import assessment_branch
from flows.agentic_triage_records import (
    Admission,
    AdmissionRef,
    assessed_item,
    read_assessment,
    redirect_unadmitted,
    selected_output,
    worker_admission,
)


@flow
def native_owner():
    return {}


def admission():
    now = datetime.now(UTC)
    requested = {"issue_number": 1, "mode": "mre"}
    group = {
        "name": "bugs",
        "rationale": "Supported regression",
        "branch": "main",
        "base_sha": "a" * 40,
        "items": [requested],
    }
    runtime = Runtime(
        plans={
            role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
            for role in ("assess", "reproduce", "triage_review")
        },
        verification_id=uuid4(),
        source_sha="b" * 40,
        triage_plan=AgentPlan(flow_id=uuid4(), version_id=uuid4()),
        triage_verify_id=uuid4(),
        triage_finalize_id=uuid4(),
    )
    return Admission(
        batch={
            "id": "native-test",
            "started_at": now,
            "expires_at": now + timedelta(hours=1),
            "groups": [group],
        },
        group=group,
        requested=requested,
        runtime=runtime,
        item={"evidence": {"issue": {"number": 1}}, "route": {"route": "investigate"}},
        request={
            "instructions": {"base_sha": "a" * 40},
            "context": {"route": {"revision": "c" * 64}},
        },
        reproduce_request={},
    )


def test_deterministic_admission_rejects_competing_or_review_only_reproduction():
    value = admission()
    decision = {
        "decision": "reproduce",
        "reason": "Observed defect",
        "contract": "Supported API",
        "prior_art": [],
        "question": "",
    }
    assert assessment_branch(decision) == "reproduce"
    assert assessed_item(value, ("reproduce", decision))["assessment"] == decision
    assert "assessment" not in value.item
    existing = value.model_copy(
        update={"item": {**value.item, "route": {"route": "review_existing"}}}
    )
    review_only = value.model_copy(
        update={"requested": value.requested.model_copy(update={"mode": "review"})}
    )
    for denied in (existing, review_only):
        with pytest.raises(ValueError, match="bypass"):
            assessed_item(denied, ("reproduce", decision))
    with pytest.raises(ValueError, match="not admitted"):
        assessed_item(value, ("verify", {**decision, "decision": "existing_review"}))


def test_verification_without_admitted_contribution_becomes_maintainer_decision():
    value = admission()
    finding = {
        "decision": "existing_review",
        "reason": "Already supported by strict_input_validation=False.",
        "contract": "Server-wide validation opt-out",
        "prior_art": ["Commit e036cba3"],
        "question": "Close as already supported?",
    }
    branch, redirected = redirect_unadmitted(value, ("verify", finding))
    assert (branch, assessment_branch(redirected)) == ("record", "record")
    assert redirected["decision"] == "maintainer"
    assert redirected["reason"].endswith(finding["reason"])
    assert redirected["question"] == finding["question"]
    assert assessed_item(value, (branch, redirected))["assessment"] == redirected
    existing = value.model_copy(
        update={"item": {**value.item, "route": {"route": "review_existing"}}}
    )
    assert redirect_unadmitted(existing, ("verify", finding)) == ("verify", finding)


def test_native_records_reject_changed_admission_and_unrelated_assessment_runs():
    value = admission()
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(native_owner, state=Running())
        raw = value.model_dump(mode="json")
        ref = AdmissionRef(
            owner=owner.id,
            key="native-test-issue-1-native-initial",
            binding=digest(raw),
        )
        with pytest.raises(ValueError, match="missing or changed"):
            worker_admission(ref, "verify")
        record.fn(owner.id, ref.key, raw)
        with pytest.raises(ValueError, match="missing or changed"):
            worker_admission(ref.model_copy(update={"binding": "d" * 64}), "verify")
        changed = deepcopy(raw)
        changed["requested"]["mode"] = "review"
        with pytest.raises(ValueError, match="Conflicting durable"):
            record.fn(owner.id, ref.key, changed)
        with pytest.raises(ValueError, match="managed child"):
            worker_admission(ref, "verify")
        assert value.runtime.triage_plan is not None
        with pytest.raises(ValueError, match="different evidence"):
            read_assessment(owner.id, value.runtime.triage_plan, value.request)


def test_incomplete_or_multiple_node_outputs_never_become_decisions():
    run_id = uuid4()
    node = {
        "node": "assess",
        "status": "failed",
        "activation_id": str(uuid4()),
        "outputs": [],
    }
    assert selected_output(run_id, {"nodes": [node]}, "assess") is None
    node["outputs"] = [{"output": "verify", "status": "selected"}]
    with pytest.raises(ValueError, match="completed native"):
        selected_output(run_id, {"nodes": [node]}, "assess")
    node["status"] = "completed"
    node["outputs"].append({"output": "record", "status": "selected"})
    with pytest.raises(ValueError, match="one completed"):
        selected_output(run_id, {"nodes": [node]}, "assess")


def test_native_runtime_cannot_be_partially_configured():
    raw = admission().runtime.model_dump(mode="json")
    raw["triage_verify_id"] = None
    with pytest.raises(ValueError, match="both deployments"):
        Runtime.model_validate(raw)


def test_native_cache_binds_policy_revision_probes_and_worker_source():
    value = admission()
    key = native_cache_key(value)
    changed = deepcopy(value.request)
    changed["context"]["retained_intake_sha256"] = "f" * 64
    assert native_cache_key(value.model_copy(update={"request": changed})) == key
    changed["context"]["route"]["revision"] = "e" * 64
    assert native_cache_key(value.model_copy(update={"request": changed})) != key
    for altered in (
        value.model_copy(
            update={
                "runtime": value.runtime.model_copy(update={"source_sha": "e" * 40})
            }
        ),
        value.model_copy(
            update={"requested": value.requested.model_copy(update={"mode": "review"})}
        ),
        value.model_copy(
            update={
                "requested": value.requested.model_copy(
                    update={"verification_probes": {"test_probe.py": "assert False"}}
                )
            }
        ),
    ):
        assert native_cache_key(altered) != key
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(native_owner)
        record.fn(owner.id, key, {"ref": "invalid", "run_id": "deleted"})
        assert find_native_result(value) is None
