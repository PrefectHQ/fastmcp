from uuid import uuid4

import pytest
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate, WorkPoolCreate
from prefect.states import Completed, Failed
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_existing_review import (
    start_verification,
    verification_result,
    verification_specs,
)
from flows.agentic_pr_verification import VerificationSpec, verify_contribution
from flows.agentic_stack import digest
from flows.agentic_stack_policy import Group
from flows.agentic_stack_runtime import AgentPlan, Runtime, record


def test_merged_history_is_not_verified_and_other_release_lines_are_not_substituted(
    evidence,
):
    group = Group(
        name="history",
        rationale="Preserve contribution history",
        branch="main",
        base_sha="a" * 40,
        items=[{"issue_number": 2692, "mode": "mre"}],
    )
    pr = {"number": 2, "state": "closed", "merged": True, "base_ref": "release/3.x"}
    item = {
        "evidence": {**evidence, "stacks": [], "prs": [pr]},
        "route": {"pull_requests": [{"number": 2}]},
        "assessment": {"verification_prs": [2]},
    }
    assert verification_specs(group, item, {}) == []
    pr["merged"] = False
    with pytest.raises(ValueError, match="different release line"):
        verification_specs(group, item, {})
    item["route"]["pull_requests"].append({"number": 3})
    item["assessment"]["verification_prs"].append(3)
    with pytest.raises(ValueError, match="evidence is missing"):
        verification_specs(group, item, {})


@pytest.mark.parametrize("selected", [[6106], [4872], []])
def test_verification_selects_candidates_without_executing_all_history(
    evidence, selected
):
    group = Group(
        name="history",
        rationale="Review historical and current work",
        branch="main",
        base_sha="a" * 40,
        items=[{"issue_number": 4866, "mode": "mre"}],
    )
    prs = [
        {
            "number": n,
            "state": "open" if n == 6106 else "closed",
            "merged": False,
            "base_ref": "main",
            "head_sha": "b" * 40,
        }
        for n in [4872, 4874, 4876, 6106]
    ]
    item = {
        "evidence": {**evidence, "stacks": [], "prs": prs},
        "route": {"pull_requests": [{"number": p["number"]} for p in prs]},
        "assessment": {"verification_prs": selected},
    }
    assert [s.pr_number for s in verification_specs(group, item, {})] == selected
    assert len(item["evidence"]["prs"]) == 4
    item["assessment"]["verification_prs"] = [9999]
    with pytest.raises(ValueError, match="evidence is missing"):
        verification_specs(group, item, {})


def test_verification_handoff_binds_real_run_deployment_and_inputs():
    spec = VerificationSpec(
        issue_number=1,
        pr_number=2,
        head_sha="a" * 40,
        trunk_sha="b" * 40,
        trunk_ref="main",
        evidence_revision="c" * 64,
    )
    with prefect_test_harness(), get_client(sync_client=True) as client:
        flow_id = client.create_flow(verify_contribution)
        deployment_id = client.create_deployment(
            flow_id, "verification", version="d" * 40
        )
        runtime = Runtime(
            plans={
                role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
                for role in ("assess", "reproduce", "prepare", "review")
            },
            validator_id=uuid4(),
            verification_id=deployment_id,
            source_sha="d" * 40,
        )
        run = client.create_flow_run_from_deployment(
            deployment_id, parameters={"spec": spec.model_dump(mode="json")}
        )
        with pytest.raises(ValueError, match="completed run"):
            verification_result(run.id, runtime, spec)
        client.set_flow_run_state(run.id, Completed(), force=True)
        with pytest.raises(ValueError, match="one contribution"):
            verification_result(run.id, runtime, spec)
        payload = {
            "spec": spec.model_dump(mode="json"),
            "spec_sha256": digest(spec.model_dump(mode="json")),
            "status": "environment_failure",
        }
        artifact = client.create_artifact(
            ArtifactCreate(
                type="fastmcp-contribution-verification",
                flow_run_id=run.id,
                data=payload,
            )
        )
        assert verification_result(run.id, runtime, spec) == payload
        for altered in (
            runtime.model_copy(update={"source_sha": "e" * 40}),
            runtime.model_copy(update={"verification_id": uuid4()}),
        ):
            with pytest.raises(ValueError, match="completed run"):
                verification_result(run.id, altered, spec)
        with pytest.raises(ValueError, match="completed run"):
            verification_result(
                run.id, runtime, spec.model_copy(update={"head_sha": "f" * 40})
            )
        client.delete_artifact(artifact.id)
        client.create_artifact(
            ArtifactCreate(
                type="fastmcp-contribution-verification",
                flow_run_id=run.id,
                data={**payload, "spec_sha256": "f" * 64},
            )
        )
        with pytest.raises(ValueError, match="different inputs"):
            verification_result(run.id, runtime, spec)


def test_failed_verification_retry_is_idempotent_and_success_is_reused():
    spec = VerificationSpec(
        issue_number=1,
        pr_number=2,
        head_sha="a" * 40,
        trunk_sha="b" * 40,
        trunk_ref="main",
        evidence_revision="c" * 64,
    )
    with prefect_test_harness(), get_client(sync_client=True) as client:
        client.create_work_pool(WorkPoolCreate(name="managed", type="process"))
        flow_id = client.create_flow(verify_contribution)
        deployment_id = client.create_deployment(
            flow_id, "retry-verification", version="d" * 40, work_pool_name="managed"
        )
        runtime = Runtime(
            plans={
                role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
                for role in ("assess", "reproduce", "prepare", "review")
            },
            validator_id=uuid4(),
            verification_id=deployment_id,
            source_sha="d" * 40,
        )
        first = start_verification(runtime, spec, "native-parent")
        record.fn(
            first.id, f"test-pr-2-verification-{first.id}", {"run_id": str(first.id)}
        )
        assert start_verification(runtime, spec, "native-parent").id == first.id
        client.set_flow_run_state(first.id, Failed(), force=True)
        retried = start_verification(runtime, spec, "native-parent")
        assert retried.id != first.id
        record.fn(
            first.id,
            f"test-pr-2-verification-{retried.id}",
            {"run_id": str(retried.id)},
        )
        assert start_verification(runtime, spec, "native-parent").id == retried.id
        client.set_flow_run_state(retried.id, Completed(), force=True)
        assert start_verification(runtime, spec, "next-native-parent").id == retried.id
