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


def test_native_stage_keeps_a_run_whose_parameters_round_trip_through_json(
    monkeypatch,
):
    """A finished run is kept when its parameters only differ by JSON round-trip.

    The API returns run parameters through JSON, so an int dict key comes back as
    a string. Comparing raw dicts used to discard the finished run as a changed
    admission.
    """
    import json
    from types import SimpleNamespace

    import flows.agentic_native_triage as native

    pinned = admission()
    request = deepcopy(pinned.request)
    request["context"]["people"] = {"reporter_prs_opened_hours_after_issue": {8: 1.5}}
    pinned = pinned.model_copy(update={"request": request})
    plan = pinned.runtime.triage_plan
    assert plan is not None
    created = {}

    def api(method, path, body=None):
        if method == "POST":
            created.update(body)
            return {"id": str(uuid4())}
        if path.startswith("/flow_runs/"):
            return {"snapshot": {"execution_plan_version_id": str(plan.version_id)}}
        return {"active_version": {"id": str(plan.version_id)}}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read_deployment(self, _):
            return SimpleNamespace(
                version=pinned.runtime.source_sha, work_pool_name="managed"
            )

        def read_flow_run(self, _):
            completed = SimpleNamespace(
                is_final=lambda: True, is_completed=lambda: True
            )
            return SimpleNamespace(
                state=completed,
                flow_id=plan.flow_id,
                parameters=json.loads(json.dumps(created["parameters"])),
            )

    outcome = {"destination": "evidence", "result": {"issue_number": 1}}
    monkeypatch.setattr(native, "api", api)
    monkeypatch.setattr(native, "get_client", lambda **_: Client())
    monkeypatch.setattr(native, "owner_alive", lambda *_: None)
    monkeypatch.setattr(native, "saved", lambda *_: None)
    monkeypatch.setattr(native, "record", lambda *_: "recorded")
    monkeypatch.setattr(native, "find_native_result", lambda _: None)
    monkeypatch.setattr(native, "settle_native", lambda *_: outcome)
    monkeypatch.setattr(native, "retain_native_result", lambda *_: None)

    destination, result, _ = native.run_native_stage(uuid4(), pinned, "initial")
    assert (destination, result) == ("evidence", {"issue_number": 1})


@pytest.mark.parametrize(
    "decision,destination",
    [
        ("reproduced", "evidence"),
        ("not_reproduced", "evidence"),
        ("evidence_needed", "evidence"),
        ("maintainer", "decisions"),
        ("environment_failure", "operational"),
    ],
)
def test_reproduction_outcomes_land_in_the_bin_a_maintainer_acts_on(
    decision, destination
):
    from flows.agentic_triage_nodes import reproduction_destination

    assert reproduction_destination(decision) == destination


def test_durable_records_compare_as_json(monkeypatch):
    """A rerun re-recording the same value must not conflict after a JSON trip."""
    import flows.agentic_stack_runtime as runtime
    from flows.agentic_stack import same_json

    assert same_json({"6477": 1.5}, {6477: 1.5})
    assert same_json([1, 2], (1, 2))
    assert not same_json({"a": 1}, {"a": 2})

    stored = SimpleNamespaceArtifact({"hours": {"6477": 1.5}})

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read_artifacts(self, **_):
            return [stored]

    monkeypatch.setattr(runtime, "get_client", lambda **_: Client())
    assert runtime.record.fn(uuid4(), "key", {"hours": {6477: 1.5}}) == "stored-id"


class SimpleNamespaceArtifact:
    def __init__(self, data):
        self.data = data
        self.id = "stored-id"


def test_stage_summary_says_which_stage_ran_and_how_it_came_out():
    from flows.agentic_triage_nodes import stage_summary

    assert stage_summary(
        ("reproduce", {"decision": "reproduce"}), {"decision": "reproduced"}, "evidence"
    ) == {
        "stage": "reproduce",
        "decision": "reproduce",
        "outcome": "reproduced",
        "destination": "evidence",
    }
    assert (
        stage_summary(
            ("verify", {"decision": "existing_review"}),
            {"status": "verification_recorded"},
            "existing_review",
        )["outcome"]
        == "verification_recorded"
    )
    assert stage_summary(
        ("record", {"decision": "maintainer"}), {"decision": "maintainer"}, "decisions"
    ) == {
        "stage": "assess",
        "decision": "maintainer",
        "outcome": None,
        "destination": "decisions",
    }
    assert stage_summary(None, {}, "operational")["stage"] == "assess"
